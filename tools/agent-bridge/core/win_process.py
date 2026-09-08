"""In-process Windows process probes that spawn nothing.

The bridge identifies a supervised process by a fingerprint tuple (creation
date, executable path, command-line hash).  Historically every probe shelled
out to ``pwsh -Command "Get-CimInstance Win32_Process ..."``, which costs a
PowerShell cold start plus a conhost plus a WMI round trip -- roughly a
quarter-second of CPU per call.  The supervisor loops call it on a two- and
five-second cadence, per instance, so the cost is paid continuously.

Every field those probes read is available from kernel32/ntdll directly, so
this module answers the same question with a handful of syscalls.

The ``creation_date`` string produced here is byte-identical to what
``Get-CimInstance ... | ConvertTo-Json`` emits (local time, microsecond
precision, UTC offset).  That parity is load-bearing: the expected value in a
lease or an environment variable may have been written by the PowerShell path,
and a formatting difference would read as a fingerprint mismatch -- which the
supervisors treat as "my host was replaced" and exit on.  ``test_win_process.py``
pins the parity against a live CIM query.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

__all__ = [
    "NATIVE_PROCESS_PROBE_AVAILABLE",
    "filetime_to_cim_string",
    "native_probe_denied",
    "native_process_entry",
    "native_process_may_exist",
    "native_process_table",
    "normalize_command_line",
]

NATIVE_PROCESS_PROBE_AVAILABLE = sys.platform == "win32"

# Spelled ``chr(0)`` rather than as an escape inside a literal, deliberately: a
# real NUL byte in a source file makes that file ungreppable (this repository
# has already had to remove one), and an escape written into a source file by a
# tool that re-interprets escapes silently becomes the byte it was meant to
# name.  Both failures are invisible on a diff.
NUL = chr(0)

# Win32 constants.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

# NtQueryInformationProcess classes.
_ProcessBasicInformation = 0
_ProcessCommandLineInformation = 60  # Windows 8.1+; needs only QUERY_LIMITED.

# FILETIME epoch (1601-01-01) to Unix epoch (1970-01-01), in 100ns ticks.
_FILETIME_EPOCH_DELTA_TICKS = 116444736000000000
_TICKS_PER_MICROSECOND = 10

_EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)


if NATIVE_PROCESS_PROBE_AVAILABLE:

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", ctypes.c_void_p),
        ]

    class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Reserved1", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("Reserved2", ctypes.c_void_p * 2),
            ("UniqueProcessId", ctypes.c_void_p),
            ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    _ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    _ntdll.NtQueryInformationProcess.restype = ctypes.c_long


def filetime_to_cim_string(low: int, high: int) -> str:
    """Format a FILETIME the way ``ConvertTo-Json`` renders a CIM ``CreationDate``.

    Local wall time, ISO-8601, UTC offset -- ``2026-08-30T15:28:25.084365-05:00``.
    FILETIME carries 100ns ticks, so the sub-microsecond remainder is truncated,
    matching WMI.

    The fractional part needs care.  ``ConvertTo-Json`` renders the .NET
    ``DateTime`` with **trailing zeros stripped**, so a process created at
    ``...869240`` microseconds serializes as ``.86924`` -- five digits, not six --
    and one created on a whole second carries no fractional part at all.
    ``datetime.isoformat()`` instead pads to a fixed six digits, so a naive
    conversion disagrees with the stored fingerprint for roughly one process in
    ten.  Since ``creation_date`` is compared byte-exactly and a mismatch is read
    as "this pid was reused", that padding would make supervisors terminate
    themselves at random.  Trim to match.
    """
    ticks = (int(high) << 32) | (int(low) & 0xFFFFFFFF)
    if ticks <= 0:
        return ""
    microseconds, _remainder = divmod(ticks, _TICKS_PER_MICROSECOND)
    moment = _EPOCH_1601 + timedelta(microseconds=microseconds)
    text = moment.astimezone().isoformat()
    if "." not in text:
        return text
    head, _, tail = text.partition(".")
    fraction, sign, offset = tail.partition("+") if "+" in tail else tail.partition("-")
    fraction = fraction.rstrip("0")
    if not fraction:
        return "%s%s%s" % (head, sign, offset)
    return "%s.%s%s%s" % (head, fraction, sign, offset)


def normalize_executable_path(path: str) -> str:
    """Reduce an executable path to a form two different producers agree on.

    ``Win32_Process.ExecutablePath`` and ``QueryFullProcessImageNameW`` describe
    the same file in different words.  Measured against 307 live processes, they
    disagree three ways:

    * the extended-length prefix -- CIM reports conhost as
      ``\\\\?\\C:\\WINDOWS\\system32\\conhost.exe``, the API drops the prefix;
    * unresolved relative segments -- CIM echoes the spawning command verbatim
      (``C:\\Program Files\\Git\\bin\\..\\usr\\bin\\bash.exe``) while the API
      returns the resolved path;
    * separator and case, which the callers already casefold away.

    Left alone these read as "the executable changed underneath this pid", which
    the fingerprint comparators score as DEAD.  This collapses all three.  It is
    pure string work -- no filesystem access -- because it runs on a supervisor's
    hot path and must not block on a stalled disk.
    """
    if not path:
        return ""
    text = path.replace("/", "\\")
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\") or text.startswith("\\??\\"):
        text = text[4:]
    leading = ""
    if text.startswith("\\\\"):
        leading, text = "\\\\", text[2:]
    parts: list = []
    for segment in text.split("\\"):
        if segment == "." or (not segment and parts):
            continue
        if segment == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(segment)
    return leading + "\\".join(parts)


# Windows Installer renames an in-use image to ``<hex>.rbf`` under Config.Msi
# while servicing it.  The process keeps running, but the image-name API then
# reports the rollback file rather than the original executable, while a
# fingerprint recorded earlier still names the real one.  That is a genuine
# divergence, not a formatting one, so the probe reports the path as *unknown*
# rather than handing the comparators a value that would score DEAD.
_SERVICING_ARTIFACT_SUFFIXES = (".rbf", ".rbs")


def _is_servicing_artifact(path: str) -> bool:
    lowered = path.casefold()
    return lowered.endswith(_SERVICING_ARTIFACT_SUFFIXES) or "\\config.msi\\" in lowered


def _open_process(pid: int):
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    return handle or None


def _creation_date(handle) -> str:
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    if not _kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        return ""
    return filetime_to_cim_string(creation.dwLowDateTime, creation.dwHighDateTime)


def _executable_path(handle) -> str:
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return ""
    resolved = normalize_executable_path(buffer.value or "")
    return "" if _is_servicing_artifact(resolved) else resolved


def _parent_pid(handle) -> int:
    info = _PROCESS_BASIC_INFORMATION()
    written = ctypes.c_ulong(0)
    status = _ntdll.NtQueryInformationProcess(
        handle,
        _ProcessBasicInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(written),
    )
    if status != 0:
        return 0
    return int(info.InheritedFromUniqueProcessId or 0)


def normalize_command_line(raw: str) -> str:
    r"""Cut a PEB command line at its first NUL, the way every other reader does.

    ``RTL_USER_PROCESS_PARAMETERS.CommandLine`` is a ``UNICODE_STRING`` whose
    ``Length`` can cover characters *past* the terminating NUL.  WMI provider
    hosts are the routine case, measured on a GitHub Windows runner: the buffer
    holds ``C:\Windows\system32\wbem\wmiprvse.exe\x00-Embedding`` (and, for a
    second pid, ``...wmiprvse.exe\x00-secured\x00-Embedding``) while
    ``Win32_Process.CommandLine`` -- and ``GetCommandLineW``, which hands back a
    NUL-terminated pointer into that same buffer -- report only
    ``C:\Windows\system32\wbem\wmiprvse.exe``.

    Reading the full ``Length`` therefore produced a string no other reader on
    the system agrees with, carrying an embedded NUL: the fingerprint
    comparators scored it "this pid was reused", and every consumer downstream
    inherited a value that cannot round-trip through JSON, a log line or a
    shell.  Truncating restores parity with CIM, which is this probe's stated
    contract.

    A buffer whose *first* character is NUL yields ``""``, which the callers
    already read as *unknown* rather than *empty* -- the safe direction.
    """
    if not raw:
        return ""
    nul = raw.find(NUL)
    return raw if nul < 0 else raw[:nul]


def _command_line(handle) -> str:
    """Read the target's command line via ``ProcessCommandLineInformation``.

    Returns "" when the kernel declines (protected processes, or a pre-8.1
    kernel that does not know the class).  Callers must treat "" as *unknown*
    rather than *changed* -- see ``native_process_entry``'s docstring.
    """
    needed = ctypes.c_ulong(0)
    status = _ntdll.NtQueryInformationProcess(
        handle, _ProcessCommandLineInformation, None, 0, ctypes.byref(needed)
    )
    if status != _STATUS_INFO_LENGTH_MISMATCH and needed.value == 0:
        return ""
    size = max(int(needed.value), ctypes.sizeof(_UNICODE_STRING))
    buffer = (ctypes.c_byte * size)()
    status = _ntdll.NtQueryInformationProcess(
        handle,
        _ProcessCommandLineInformation,
        ctypes.byref(buffer),
        size,
        ctypes.byref(needed),
    )
    if status != 0:
        return ""
    unicode_string = ctypes.cast(buffer, ctypes.POINTER(_UNICODE_STRING)).contents
    if not unicode_string.Buffer or not unicode_string.Length:
        return ""
    return normalize_command_line(
        ctypes.wstring_at(unicode_string.Buffer, unicode_string.Length // 2)
    )


def native_process_entry(pid: int) -> Optional[Dict[str, Any]]:
    """Return one process-table entry, or ``None`` when native identity is unavailable.

    Field-for-field compatible with the CIM probe it replaces.  A field the
    kernel refuses to disclose comes back as ``""``; that is *unknown*, not
    *empty*, and the fingerprint comparators already skip an unknown field
    rather than scoring it a mismatch.

    ``None`` can mean gone, unopenable, or temporarily missing identity metadata.
    Use ``native_process_may_exist`` to distinguish a confirmed absence from a
    case needing CIM fallback; a native miss alone is not evidence of exit.
    """
    if not NATIVE_PROCESS_PROBE_AVAILABLE:
        return None
    pid = int(pid)
    if pid <= 0:
        return None
    handle = _open_process(pid)
    if handle is None:
        return None
    try:
        exit_code = wintypes.DWORD(0)
        if _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            if exit_code.value != _STILL_ACTIVE:
                return None
        executable_path = _executable_path(handle)
        if not executable_path:
            # DEFER TO CIM RATHER THAN ANSWER PARTIALLY.
            # _executable_path deliberately blanks servicing artifacts: while an
            # installer is replacing a running binary the kernel reports the image
            # as e.g. C:\Config.Msi\<guid>.rbf, which is not a durable identity.
            # CIM meanwhile still reports the original path. Returning a dict with
            # an empty executable_path would be worse than returning nothing:
            #   * host_key is built from a hash of executable_path, so the entry
            #     silently loses its "exe:" segment - and host_key names the
            #     on-disk host-slot lease FILE. Two probes would disagree about
            #     which lease a host owns.
            #   * callers treat a non-None result as authoritative and skip the
            #     CIM fallback, so the real path is never recovered.
            #   * _wrapper_process_matches_env only compares paths when BOTH sides
            #     are non-empty, so an empty side silently disables one arm of the
            #     pid-reuse check.
            # Measured 2026-08-30: 3 of 92 candidate host processes diverged this
            # way, all pwsh.exe mid-update. Returning None costs one CIM query for
            # a process that is being serviced, and keeps identity stable.
            return None
        return {
            "pid": pid,
            "parent_pid": _parent_pid(handle),
            # Win32_Process.Name is the image file name, not the full path.
            "name": os.path.basename(executable_path) if executable_path else "",
            "command_line": _command_line(handle),
            "executable_path": executable_path,
            "creation_date": _creation_date(handle),
        }
    finally:
        _kernel32.CloseHandle(handle)


def native_process_may_exist(pid: int) -> bool:
    """False only when native evidence confirms that this PID is not running.

    A live process whose image is being serviced may be openable but lack a
    stable native identity. Access denial and failed liveness queries are also
    uncertain, so callers must try CIM rather than cache any of them as absent.
    With the fixed query flags used here, OpenProcess ERROR_INVALID_PARAMETER
    (87) identifies an invalid PID and therefore also confirms absence.
    This check spawns nothing and is needed only after a native identity miss.
    """
    pid = int(pid)
    if pid <= 0:
        return False
    if not NATIVE_PROCESS_PROBE_AVAILABLE:
        return True
    ctypes.set_last_error(0)
    handle = _open_process(pid)
    if handle is None:
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        exit_code = wintypes.DWORD(0)
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def native_probe_denied(pid: int) -> bool:
    """True when the process exists but this token may not open it.

    This reports access denial only. A False result does not establish absence:
    openable processes can also have unavailable identity metadata. Callers
    deciding whether to try CIM should use ``native_process_may_exist``.
    """
    if not NATIVE_PROCESS_PROBE_AVAILABLE or int(pid) <= 0:
        return False
    ctypes.set_last_error(0)
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if handle:
        _kernel32.CloseHandle(handle)
        return False
    return ctypes.get_last_error() == _ERROR_ACCESS_DENIED


def native_process_table() -> Dict[int, Dict[str, Any]]:
    """Enumerate every visible process without spawning anything.

    Uses ``EnumProcesses`` for the pid list and ``native_process_entry`` per
    pid.  Processes that exit mid-enumeration are skipped, exactly as the CIM
    snapshot would have missed them.
    """
    if not NATIVE_PROCESS_PROBE_AVAILABLE:
        return {}
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    capacity = 4096
    while True:
        array = (wintypes.DWORD * capacity)()
        needed = wintypes.DWORD(0)
        if not psapi.EnumProcesses(
            ctypes.byref(array), ctypes.sizeof(array), ctypes.byref(needed)
        ):
            return {}
        returned = needed.value // ctypes.sizeof(wintypes.DWORD)
        if returned < capacity:
            pids = list(array[:returned])
            break
        capacity *= 2
        if capacity > 1 << 20:
            pids = list(array[:returned])
            break
    table: Dict[int, Dict[str, Any]] = {}
    for pid in pids:
        if pid <= 0:
            continue
        entry = native_process_entry(pid)
        if entry is not None:
            table[pid] = entry
    return table
