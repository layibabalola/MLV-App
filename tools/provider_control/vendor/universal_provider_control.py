#!/usr/bin/env python3
"""Deployment-inert universal provider-control reference contract.

This module never launches, resumes, kills, authenticates, or contacts a provider.  It supplies
strict contract intake, version-bound provider normalization, a transactional quota-domain
reservation/attestation interface, and a bounded streaming evidence-capsule builder.  A project
launcher remains a separately reviewed boundary and may consume an attestation only while its
suspended child and the broker's full-child-lifetime lease remain exact.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import uuid
from typing import Any, Iterable, Sequence

try:
    import jsonschema
except Exception:  # pragma: no cover - mapped to a stable contract result at runtime
    jsonschema = None


UTC = dt.timezone.utc
SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"
MAX_INPUT_BYTES = 1_048_576
MAX_JSON_DEPTH = 64
MAX_OBJECT_MEMBERS = 256
MAX_ARRAY_ITEMS = 4096
MAX_TOTAL_NODES = 16_384
STREAM_CHUNK_BYTES = 65_536
MAX_STATE_BYTES = 16_777_216
MAX_ARTIFACT_BYTES = 16_777_216
MAX_CAPSULE_SOURCE_BYTES = 16_777_216
MAX_CAPSULE_TEMP_BACKLOG = 1
MAX_CAPSULE_POISON_OWNERS = 259  # 256 sources plus temp/public/target-directory owners.
MAX_PREPARED_LEASES_PER_STATE_ROOT = 4  # Conservative root quarantine ceiling before acquisition.
ARTIFACT_HANDLES_PER_LEASE = 6
MAX_BROKER_ARTIFACT_POISON_OWNERS = (
    MAX_PREPARED_LEASES_PER_STATE_ROOT * ARTIFACT_HANDLES_PER_LEASE
)
MAX_CAPACITY_WINDOW_SECONDS = 31_622_400
MAX_CLOCK_SKEW_SECONDS = 5
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UNPROVEN_CAPSULE_OWNERS: dict[str, list[Any]] = {}
_BROKER_ARTIFACT_CLEANUP_POISON: dict[str, list[Any]] = {}
_CAPSULE_PROCESS_LOCK = threading.RLock()
_BROKER_PROCESS_LOCK = threading.RLock()
_BROKER_ROOT_RUNTIMES: dict[str, "_BrokerRootRuntime"] = {}


class _BrokerRootRuntime:
    """Process-local singleton owners for one canonical state root."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.os_locks: dict[str, Any] = {}
        self.os_lock_release_attempted: dict[str, set[str]] = {}
        self.unproven_os_locks: dict[str, Any] = {}
        self.artifact_handles: dict[
            str, list[tuple[Path, Any, tuple[int, int, int, int], str, int]]
        ] = {}
        self.artifact_close_attempted: dict[str, set[int]] = {}
        self.unproven_artifact_handles: dict[str, list[Any]] = {}


def _broker_root_runtime(poison_key: str) -> _BrokerRootRuntime:
    with _BROKER_PROCESS_LOCK:
        runtime = _BROKER_ROOT_RUNTIMES.get(poison_key)
        if runtime is None:
            runtime = _BrokerRootRuntime()
            _BROKER_ROOT_RUNTIMES[poison_key] = runtime
        return runtime

SCHEMAS = {
    "profile": "universal-project-profile-v1.schema.json",
    "native": "provider-native-capacity-evidence-v1.schema.json",
    "observation": "universal-capacity-observation-v1.schema.json",
    "request": "universal-control-request-v1.schema.json",
    "inventory": "universal-launcher-inventory-v1.schema.json",
    "health": "universal-broker-health-v1.schema.json",
    "transition": "universal-gate-transition-v1.schema.json",
    "canary_authorization": "universal-manual-canary-authorization-v1.schema.json",
    "process_observation": "universal-process-observation-v1.schema.json",
    "attestation": "universal-launch-attestation-v1.schema.json",
    "capsule_request": "universal-evidence-capsule-request-v1.schema.json",
    "capsule": "universal-evidence-capsule-v1.schema.json",
}

ADAPTERS: dict[tuple[str, str], tuple[tuple[str, str, str, str], ...]] = {
    ("claude", "claude-code/1.0"): (
        ("session", "sessionUtilization", "sessionResetAt", "sessionLastResetAt"),
        ("weekly", "weeklyUtilization", "weeklyResetAt", "weeklyLastResetAt"),
    ),
    ("openai", "openai-responses/1.0"): (
        ("primary", "primaryUtilization", "primaryResetAt", "primaryLastResetAt"),
        ("secondary", "secondaryUtilization", "secondaryResetAt", "secondaryLastResetAt"),
    ),
    ("kimi", "kimi-code/1.0"): (
        ("context", "contextUtilization", "contextResetAt", "contextLastResetAt"),
        ("monthly", "monthlyUtilization", "monthlyResetAt", "monthlyLastResetAt"),
    ),
    ("grok", "xai-api/1.0"): (
        ("requests", "requestUtilization", "requestResetAt", "requestLastResetAt"),
        ("tokens", "tokenUtilization", "tokenResetAt", "tokenLastResetAt"),
    ),
}

PRIORITY_ORDER = (
    "OWNER_FOREGROUND",
    "REQUIRED_REVIEW",
    "PRODUCT_WORK",
    "ADJUDICATION",
    "MAINTENANCE",
)


class ControlError(ValueError):
    """Stable, value-redacted contract failure."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: ARG002
        raise ControlError("ARGUMENT_ERROR")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest_json(value: Any) -> str:
    hasher = hashlib.sha256()
    total = 0
    try:
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        for piece in encoder.iterencode(value):
            encoded = piece.encode("ascii")
            total += len(encoded)
            if total > MAX_INPUT_BYTES:
                raise ControlError("INPUT_SIZE_LIMIT")
            hasher.update(encoded)
    except ControlError:
        raise
    except (TypeError, ValueError, RecursionError, MemoryError, OverflowError) as exc:
        raise ControlError("JSON_INVALID") from exc
    return "sha256:" + hasher.hexdigest()


def contract_hmac(domain: str, value: dict[str, Any], fleet_secret: bytes, signature_field: str) -> str:
    if not isinstance(fleet_secret, bytes) or len(fleet_secret) < 32:
        raise ControlError("FLEET_SECRET_INVALID")
    unsigned = {key: child for key, child in value.items() if key != signature_field}
    material = domain.encode("ascii") + b"\x00" + digest_json(unsigned).encode("ascii")
    return "hmac-sha256:" + hmac.new(fleet_secret, material, hashlib.sha256).hexdigest()


def verify_contract_hmac(
    domain: str, value: dict[str, Any], fleet_secret: bytes, signature_field: str
) -> None:
    supplied = value.get(signature_field)
    expected = contract_hmac(domain, value, fleet_secret, signature_field)
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
        raise ControlError("CONTRACT_HMAC_INVALID")


def require_sha256(value: Any, reason: str = "DIGEST_INVALID") -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise ControlError(reason)
    return value


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError as exc:
        raise ControlError("PATH_IDENTITY_UNEVALUABLE") from exc


def _stable_file_identity(stat_result: Any) -> tuple[int, int]:
    """Return the cross-platform stable identity exposed by fstat/stat.

    CPython maps Windows file indexes to st_ino.  A filesystem that cannot provide a non-zero
    identity is not eligible for an authority-bearing artifact operation.
    """

    device = int(stat_result.st_dev)
    inode = int(stat_result.st_ino)
    if device < 0 or inode <= 0:
        raise ControlError("FILE_IDENTITY_UNAVAILABLE")
    return device, inode


def _path_has_identity(path: Path, identity: tuple[int, int]) -> bool:
    try:
        return not _is_reparse(path) and _stable_file_identity(path.stat(follow_symlinks=False)) == identity
    except (ControlError, FileNotFoundError, OSError):
        return False


def _windows_arm_native_handle_discard(native_handle: int) -> bool:
    """Attach delete disposition to the current Windows native-handle owner."""

    import ctypes
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    disposition = FileDispositionInfo(True)
    succeeded = bool(
        kernel.SetFileInformationByHandle(
            native_handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)
        )
    )
    if not succeeded:
        raise OSError(ctypes.get_last_error(), "native discard failed")
    return True


def _windows_close_native_handle(native_handle: int) -> bool:
    """Close a Windows handle while it is still owned by the native-handle state."""

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    succeeded = bool(kernel.CloseHandle(native_handle))
    if not succeeded:
        raise OSError(ctypes.get_last_error(), "native close failed")
    return True


def _close_owned_descriptor(descriptor: int) -> bool:
    """Close a descriptor exactly once while it, rather than a file object, owns the handle."""

    os.close(descriptor)
    return True


def _close_file_handle_verified(handle: Any) -> bool:
    """Attempt one file-object close and require an observable closed state."""

    try:
        handle.close()
    except BaseException:
        pass
    if bool(getattr(handle, "closed", False)):
        return True
    wrapped = getattr(handle, "handle", None)
    if wrapped is not None and bool(getattr(wrapped, "closed", False)):
        return True
    return False


def _attempt_file_close_verified(handle: Any) -> bool:
    """Contain every close exception class while retaining an unproven owner."""

    try:
        return _close_file_handle_verified(handle)
    except BaseException:
        return False


def _unlock_os_lock_handle(handle: Any) -> None:
    """Attempt the platform unlock while the retained file object remains the sole owner."""

    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - exercised by the Ubuntu workflow
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _retain_unproven_capsule_owner(output_path: Path, owner: Any) -> None:
    """Retain an unproven owner behind the deterministic per-output retry fence."""

    with _CAPSULE_PROCESS_LOCK:
        key = os.path.normcase(os.path.abspath(str(output_path)))
        owners = _UNPROVEN_CAPSULE_OWNERS.setdefault(key, [])
        if not any(
            existing is owner
            or (isinstance(existing, tuple) and isinstance(owner, tuple) and existing == owner)
            for existing in owners
        ):
            if sum(len(values) for values in _UNPROVEN_CAPSULE_OWNERS.values()) >= MAX_CAPSULE_POISON_OWNERS:
                raise ControlError("CAPSULE_CLEANUP_POISON_OVERFLOW")
            owners.append(owner)


def assert_process_cleanup_clear() -> None:
    """Surface process-wide capsule poison at an explicit shutdown boundary."""

    with _CAPSULE_PROCESS_LOCK:
        if _UNPROVEN_CAPSULE_OWNERS:
            raise ControlError("CAPSULE_CLEANUP_POISONED")


def _retain_broker_artifact_owner(poison_key: str, owner: Any) -> None:
    with _BROKER_PROCESS_LOCK:
        owners = _BROKER_ARTIFACT_CLEANUP_POISON.setdefault(poison_key, [])
        if not any(existing is owner for existing in owners):
            if len(owners) >= MAX_BROKER_ARTIFACT_POISON_OWNERS:
                raise ControlError("ARTIFACT_CLEANUP_POISON_OVERFLOW")
            owners.append(owner)


def _broker_artifact_cleanup_poisoned(poison_key: str) -> bool:
    with _BROKER_PROCESS_LOCK:
        return poison_key in _BROKER_ARTIFACT_CLEANUP_POISON


def _open_posix_anonymous_temporary(
    parent: Path, flags: int, anonymous_flag: int, refusal_path: Path | None = None
) -> Any:
    """Open O_TMPFILE with explicit fd-to-file-object ownership transfer."""

    descriptor = os.open(str(parent), flags | anonymous_flag, 0o600)
    descriptor_owned = True
    try:
        handle = os.fdopen(descriptor, "w+b", closefd=True)
        descriptor_owned = False
        return handle
    except BaseException:
        if descriptor_owned:
            try:
                close_proven = _close_owned_descriptor(descriptor)
            except BaseException as close_error:
                if refusal_path is not None:
                    _surface_temp_cleanup_refusal(refusal_path)
                    _retain_unproven_capsule_owner(refusal_path, ("descriptor", descriptor))
                # An unproven close forbids named fallback.  The outer public boundary replaces
                # this private error topology with the stable cleanup-refusal reason.
                raise ControlError("CAPSULE_TEMP_CLEANUP_REFUSED") from close_error
            if not close_proven:
                if refusal_path is not None:
                    _surface_temp_cleanup_refusal(refusal_path)
                    _retain_unproven_capsule_owner(refusal_path, ("descriptor", descriptor))
                raise ControlError("CAPSULE_TEMP_CLEANUP_REFUSED")
        raise


def _open_owned_temporary(temporary: Path, output_path: Path) -> tuple[Any, bool]:
    """Create a retained temp whose cleanup is handle-bound or explicitly bounded.

    Windows opens a DELETE-capable handle so disposition targets the opened file object, never a
    later pathname occupant. Linux/Unix prefers O_TMPFILE, which has no pathname to race. A named
    POSIX fallback is allowed only with a one-item surfaced backlog bound.
    """

    refusal_marker = output_path.with_name(output_path.name + ".cleanup-blocked")
    if refusal_marker.exists():
        raise ControlError("CAPSULE_TEMP_BACKLOG")
    if temporary.exists():
        raise ControlError("CAPSULE_TEMP_COLLISION")
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        handle = kernel.CreateFileW(
            str(temporary), 0x80000000 | 0x40000000 | 0x00010000,
            0x00000001 | 0x00000002 | 0x00000004, None, 1, 0x00000100, None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (0, invalid):
            code = ctypes.get_last_error()
            if code in (80, 183):
                raise ControlError("CAPSULE_TEMP_COLLISION")
            raise OSError(code, "owned temporary create failed")
        native_handle_owned = True
        descriptor: int | None = None
        descriptor_owned = False
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle), os.O_RDWR | getattr(os, "O_BINARY", 0)
            )
            native_handle_owned = False  # CRT descriptor now owns the same native handle.
            descriptor_owned = True
            file_object = os.fdopen(descriptor, "w+b", closefd=True)
            descriptor_owned = False  # File object now owns the descriptor and native handle.
            return file_object, True
        except BaseException:
            cleanup_proven = True
            if native_handle_owned:
                try:
                    cleanup_proven = _windows_arm_native_handle_discard(int(handle))
                except BaseException:
                    cleanup_proven = False
                try:
                    close_proven = _windows_close_native_handle(int(handle))
                    cleanup_proven = close_proven and cleanup_proven
                    if not close_proven:
                        _retain_unproven_capsule_owner(
                            output_path, ("native-handle", int(handle))
                        )
                except BaseException:
                    cleanup_proven = False
                    _retain_unproven_capsule_owner(output_path, ("native-handle", int(handle)))
            elif descriptor_owned and descriptor is not None:
                try:
                    cleanup_proven = _windows_arm_native_handle_discard(
                        msvcrt.get_osfhandle(descriptor)
                    )
                except BaseException:
                    cleanup_proven = False
                try:
                    close_proven = _close_owned_descriptor(descriptor)
                    cleanup_proven = close_proven and cleanup_proven
                    if not close_proven:
                        _retain_unproven_capsule_owner(
                            output_path, ("descriptor", descriptor)
                        )
                except BaseException:
                    cleanup_proven = False
                    _retain_unproven_capsule_owner(output_path, ("descriptor", descriptor))
            if not cleanup_proven:
                _surface_temp_cleanup_refusal(output_path)
                raise ControlError("CAPSULE_TEMP_CLEANUP_REFUSED")
            raise

    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    anonymous_flag = getattr(os, "O_TMPFILE", 0)
    # Select the capability-free publication route before any capsule bytes are written.  The
    # /proc/self/fd route uses linkat(AT_SYMLINK_FOLLOW), unlike AT_EMPTY_PATH, and therefore does
    # not require CAP_DAC_READ_SEARCH from an ordinary Linux runner.  If procfs is unavailable we
    # choose the bounded named fallback now; there is no post-write route fallback.
    proc_fd_root = Path("/proc/self/fd")
    if anonymous_flag and proc_fd_root.is_dir():
        try:
            return _open_posix_anonymous_temporary(
                output_path.parent, flags, anonymous_flag, output_path
            ), False
        except OSError as exc:
            import errno

            if exc.errno not in (errno.EINVAL, errno.EISDIR, errno.ENOSYS, errno.EOPNOTSUPP):
                raise
    try:
        return temporary.open("x+b"), True
    except FileExistsError as exc:
        raise ControlError("CAPSULE_TEMP_COLLISION") from exc


def _publication_syscall(
    source: str | Path,
    output_path: Path,
    *,
    target_directory_fd: int | None = None,
    flags: int = 0,
) -> None:
    """Single injectable no-clobber syscall seam for named and anonymous publication routes."""

    if target_directory_fd is None:
        os.link(source, output_path)
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    libc.linkat.restype = ctypes.c_int
    if libc.linkat(
        -100, os.fsencode(source), target_directory_fd, os.fsencode(output_path.name), flags
    ) != 0:
        error = ctypes.get_errno()
        if error == 17:
            raise FileExistsError(error, "capsule output exists", str(output_path))
        raise OSError(error, "anonymous retained publication failed", str(output_path))


def _publish_owned_temporary(handle: Any, temporary: Path, named: bool, output_path: Path) -> None:
    """Atomically create the public no-clobber link from the retained file object."""

    if named:
        _publication_syscall(temporary, output_path)
        return

    retained_identity = _stable_file_identity(os.fstat(handle.fileno()))
    proc_source = f"/proc/self/fd/{handle.fileno()}"
    try:
        proc_identity = _stable_file_identity(os.stat(proc_source, follow_symlinks=True))
    except OSError as exc:
        raise ControlError("CAPSULE_PUBLICATION_ROUTE_UNAVAILABLE") from exc
    if proc_identity != retained_identity:
        raise ControlError("CAPSULE_PUBLICATION_ROUTE_DRIFT")

    directory = os.open(str(output_path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    directory_owned = True
    directory_close_attempted = False
    try:
        # AT_FDCWD + /proc/self/fd + AT_SYMLINK_FOLLOW is the documented unprivileged O_TMPFILE
        # publication route.  Do not fall back after bytes have been written.
        _publication_syscall(
            proc_source, output_path, target_directory_fd=directory, flags=0x400
        )
    finally:
        # The target-directory descriptor is authority-bearing publication state.  Detach it only
        # after one verified close outcome; an exception or false outcome retains the exact owner,
        # poisons every later capsule acquisition in this process, and can never be hidden by a
        # successfully created/verified public link.
        if directory_owned and not directory_close_attempted:
            directory_close_attempted = True
            close_proven = False
            try:
                close_proven = bool(_close_owned_descriptor(directory))
            except BaseException:
                close_proven = False
            if close_proven:
                directory_owned = False
            else:
                _retain_unproven_capsule_owner(
                    output_path, ("target-directory-descriptor", directory)
                )
                _surface_temp_cleanup_refusal(output_path)
                raise ControlError("CAPSULE_TEMP_CLEANUP_REFUSED")


def _arm_owned_temp_discard(handle: Any, named: bool) -> bool:
    """Arrange deletion by retained file object; never check then unlink a pathname."""

    if not named:
        return True  # O_TMPFILE is reclaimed when the retained handle closes.
    if os.name != "nt":
        return False  # Named POSIX fallback is surfaced and bounded; it is never path-unlinked.
    import msvcrt
    return _windows_arm_native_handle_discard(msvcrt.get_osfhandle(handle.fileno()))


def _attempt_arm_owned_temp_discard(handle: Any, named: bool) -> bool:
    """Contain cleanup-helper failures in the stable, no-echo refusal contract."""

    try:
        return bool(_arm_owned_temp_discard(handle, named))
    except BaseException:
        return False


def _surface_temp_cleanup_refusal(output_path: Path) -> None:
    """Persist a bounded, no-value marker that blocks repetition after cleanup refusal."""

    marker = output_path.with_name(output_path.name + ".cleanup-blocked")
    try:
        with marker.open("xb"):
            pass
    except FileExistsError:
        pass
    except OSError:
        # The deterministic owned temp name itself remains as a second O(1) repetition fence.
        pass


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControlError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:  # noqa: ARG001
    raise ControlError("JSON_NONFINITE_NUMBER")


def _enforce_complexity(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_TOTAL_NODES or depth > MAX_JSON_DEPTH:
            raise ControlError("JSON_COMPLEXITY_LIMIT")
        if isinstance(current, dict):
            if len(current) > MAX_OBJECT_MEMBERS:
                raise ControlError("JSON_COMPLEXITY_LIMIT")
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            if len(current) > MAX_ARRAY_ITEMS:
                raise ControlError("JSON_COMPLEXITY_LIMIT")
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ControlError("JSON_NONFINITE_NUMBER")


def strict_json_bytes(raw: bytes) -> Any:
    if len(raw) > MAX_INPUT_BYTES:
        raise ControlError("INPUT_SIZE_LIMIT")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ControlError("UTF8_INVALID") from exc
    try:
        value = json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except ControlError:
        raise
    except (json.JSONDecodeError, RecursionError, MemoryError, OverflowError) as exc:
        raise ControlError("JSON_INVALID") from exc
    _enforce_complexity(value)
    return value


def strict_json_file(path: Path) -> Any:
    try:
        size = path.stat().st_size
        if size > MAX_INPUT_BYTES:
            raise ControlError("INPUT_SIZE_LIMIT")
        with path.open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except ControlError:
        raise
    except OSError as exc:
        raise ControlError("INPUT_UNREADABLE") from exc
    return strict_json_bytes(raw)


def parse_time(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise ControlError("DATE_TIME_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise ControlError("DATE_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise ControlError("DATE_TIME_INVALID")
    return parsed.astimezone(UTC)


def iso(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _iter_time_values(value: Any) -> Iterable[Any]:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                if key.endswith("At") or key in {"issuedAt", "expiresAt", "capturedAt", "observedAt"}:
                    yield child
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)


def _load_schema(name: str) -> dict[str, Any]:
    filename = SCHEMAS.get(name)
    if filename is None:
        raise ControlError("SCHEMA_KIND_UNKNOWN")
    schema = strict_json_file(SCHEMA_ROOT / filename)
    if not isinstance(schema, dict):
        raise ControlError("SCHEMA_INVALID")
    return schema


def validate_contract(name: str, value: Any) -> None:
    if jsonschema is None:
        raise ControlError("SCHEMA_VALIDATOR_UNAVAILABLE")
    schema = _load_schema(name)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        first = next(iter(validator.iter_errors(value)), None)
    except (RecursionError, MemoryError, OverflowError) as exc:
        raise ControlError("SCHEMA_RESOURCE_FAILURE") from exc
    except Exception as exc:
        raise ControlError("SCHEMA_INVALID") from exc
    if first is not None:
        raise ControlError("SCHEMA_VALIDATION_FAILED")
    for time_value in _iter_time_values(value):
        parse_time(time_value)


def derive_quota_domain_id(provider: str, local_stable_identity: bytes, fleet_secret: bytes) -> str:
    if provider not in {key[0] for key in ADAPTERS}:
        raise ControlError("PROVIDER_UNSUPPORTED")
    if not isinstance(local_stable_identity, bytes) or not local_stable_identity:
        raise ControlError("QUOTA_IDENTITY_UNAVAILABLE")
    if not isinstance(fleet_secret, bytes) or len(fleet_secret) < 32:
        raise ControlError("FLEET_SECRET_INVALID")
    material = b"fleet-quota-domain-v1\x00" + provider.encode("ascii") + b"\x00" + local_stable_identity
    return f"{provider}/hmac-sha256:{hmac.new(fleet_secret, material, hashlib.sha256).hexdigest()}"


def normalize_native_evidence(evidence: Any) -> dict[str, Any]:
    validate_contract("native", evidence)
    adapter_key = (evidence["provider"], evidence["adapterVersion"])
    mapping = ADAPTERS.get(adapter_key)
    if mapping is None:
        raise ControlError("ADAPTER_VERSION_UNSUPPORTED")
    payload = evidence["payload"]
    dimensions = []
    for name, utilization_key, reset_key, last_reset_key in mapping:
        dimensions.append(
            {
                "name": name,
                "usedFraction": payload[utilization_key],
                "resetsAt": iso(parse_time(payload[reset_key])),
                "lastResetAt": iso(parse_time(payload[last_reset_key])),
            }
        )
    observation = {
        "schema": "fleet-universal-capacity-observation/v1",
        "provider": evidence["provider"],
        "adapterVersion": evidence["adapterVersion"],
        "observedAt": iso(parse_time(evidence["capturedAt"])),
        "quotaDomainId": evidence["quotaDomainId"],
        "sourceArtifactSha256": evidence["sourceArtifactSha256"],
        "dimensions": dimensions,
    }
    validate_contract("observation", observation)
    observed = parse_time(observation["observedAt"])
    for dimension in observation["dimensions"]:
        last_reset = parse_time(dimension["lastResetAt"])
        resets = parse_time(dimension["resetsAt"])
        if not last_reset <= observed < resets:
            raise ControlError("CAPACITY_TIME_INVALID")
        if (resets - observed).total_seconds() > MAX_CAPACITY_WINDOW_SECONDS:
            raise ControlError("CAPACITY_TIME_INVALID")
    return observation


def normalize_evidence_files(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Strictly parse every file before selecting an observation.

    There is deliberately no "skip malformed and use older" branch.  Any malformed member makes
    the whole supplied evidence set UNEVALUABLE.
    """

    if not paths or len(paths) > 256:
        raise ControlError("EVIDENCE_SET_INVALID")
    values = [strict_json_file(path) for path in paths]
    return [normalize_native_evidence(value) for value in values]


def _hash_file(path: Path, max_bytes: int = MAX_ARTIFACT_BYTES) -> str:
    hasher = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ControlError("ARTIFACT_SIZE_LIMIT")
                hasher.update(chunk)
    except ControlError:
        raise
    except OSError as exc:
        raise ControlError("ARTIFACT_UNREADABLE") from exc
    return "sha256:" + hasher.hexdigest()


def _canonical_executable(path_value: str) -> Path:
    try:
        candidate = Path(path_value)
        if not candidate.is_absolute() or not candidate.is_file():
            raise ControlError("EXECUTABLE_IDENTITY_INVALID")
        resolved = candidate.resolve(strict=True)
        if os.path.normcase(os.path.abspath(str(candidate))) != os.path.normcase(str(resolved)):
            raise ControlError("EXECUTABLE_REPARSE_REJECTED")
        current = candidate
        while True:
            if _is_reparse(current):
                raise ControlError("EXECUTABLE_REPARSE_REJECTED")
            parent = current.parent
            if parent == current:
                break
            current = parent
        return resolved
    except ControlError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ControlError("EXECUTABLE_IDENTITY_INVALID") from exc


def validate_project_profile(profile: Any) -> None:
    validate_contract("profile", profile)
    if profile["independenceClass"] == "UNKNOWN":
        raise ControlError("INDEPENDENCE_CLASS_UNKNOWN")
    coordination = profile["coordination"]
    if coordination["quotaDomainHostCount"] != 1 or coordination["mode"] != "HOST_LOCAL":
        raise ControlError("MULTI_HOST_BACKEND_UNAVAILABLE")
    if coordination["sharedBrokerIdentitySha256"] is not None:
        raise ControlError("COORDINATION_BOUNDARY_INVALID")
    required = profile["policy"]["requiredCapacityDimensions"]
    for (_provider, adapter), mapping in ADAPTERS.items():
        mandatory = {item[0] for item in mapping}
        configured = set(required[adapter])
        if not mandatory.issubset(configured):
            raise ControlError("PROJECT_PROFILE_WEAKENS_UNIVERSAL")
    floors = profile["policy"]["reserveFloorByPriority"]
    prior = -1.0
    for priority in PRIORITY_ORDER:
        current = float(floors[priority])
        if current < prior:
            raise ControlError("PROJECT_PROFILE_WEAKENS_UNIVERSAL")
        prior = current


def route_demand_tick(
    current_fingerprint: str,
    prior_idle_fingerprint: str | None,
    admission_callable: Any,
) -> dict[str, Any]:
    """Route one model-free work tick; unchanged input never calls the admission boundary."""

    for value in (current_fingerprint, prior_idle_fingerprint):
        if value is not None and (
            not isinstance(value, str)
            or len(value) != 71
            or not value.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in value[7:])
        ):
            raise ControlError("DEMAND_FINGERPRINT_INVALID")
    if current_fingerprint == prior_idle_fingerprint:
        return {
            "status": "IDLE_SKIPPED",
            "demandFingerprint": current_fingerprint,
            "providerCalls": 0,
            "providerProcesses": 0,
            "inputTokens": 0,
            "cachedInputTokens": 0,
            "reasoningTokens": 0,
            "outputTokens": 0,
        }
    if not callable(admission_callable):
        raise ControlError("ADMISSION_BOUNDARY_UNAVAILABLE")
    return admission_callable()


def canary_request_binding(request: dict[str, Any]) -> str:
    # The authorization digest cannot include the field that contains the authorization's own
    # digest.  Every other canonical request member is covered.
    fields = dict(request)
    fields.pop("manualAuthorizationSha256", None)
    return digest_json(fields)


def _validate_inventory(inventory: Any) -> None:
    validate_contract("inventory", inventory)
    if inventory["configuredLauncherCount"] != inventory["observedLauncherCount"]:
        raise ControlError("INVENTORY_INCOMPLETE")
    if inventory["configuredLauncherCount"] != len(inventory["launchers"]):
        raise ControlError("INVENTORY_INCOMPLETE")
    expected_counts = inventory["configuredSurfaceCounts"]
    observed_counts = inventory["observedSurfaceCounts"]
    if expected_counts != observed_counts or sum(expected_counts.values()) != len(inventory["launchers"]):
        raise ControlError("INVENTORY_INCOMPLETE")
    actual_counts = {surface: 0 for surface in inventory["surfaceClasses"]}
    identities: set[str] = set()
    for launcher in inventory["launchers"]:
        path = str(_canonical_executable(launcher["executablePath"]))
        identity = os.path.normcase(path)
        if identity in identities:
            raise ControlError("INVENTORY_AMBIGUOUS")
        identities.add(identity)
        actual_counts[launcher["surfaceClass"]] += 1
        if _hash_file(Path(path)) != launcher["executableSha256"]:
            raise ControlError("INVENTORY_LAUNCHER_DRIFT")
    if actual_counts != expected_counts:
        raise ControlError("INVENTORY_INCOMPLETE")


def _latest_observation(
    observations: Sequence[Any], request: dict[str, Any], fleet_secret: bytes
) -> dict[str, Any]:
    if not observations or len(observations) > 256:
        raise ControlError("CAPACITY_UNEVALUABLE")
    normalized: list[dict[str, Any]] = []
    for raw in observations:
        verify_contract_hmac("provider-capacity-evidence-v1", raw, fleet_secret, "observerHmacSha256")
        observation = normalize_native_evidence(raw)
        if (
            observation["provider"] != request["provider"]
            or observation["adapterVersion"] != request["adapterVersion"]
            or observation["quotaDomainId"] != request["quotaDomainId"]
        ):
            raise ControlError("CAPACITY_IDENTITY_MISMATCH")
        normalized.append(observation)
    return max(normalized, key=lambda item: parse_time(item["observedAt"]))


def _verify_process_observation(
    observation: Any,
    *,
    fleet_secret: bytes,
    now: dt.datetime,
    phase: str,
    request: dict[str, Any] | None = None,
    lease: sqlite3.Row | None = None,
) -> None:
    validate_contract("process_observation", observation)
    verify_contract_hmac("process-observation-v1", observation, fleet_secret, "observerHmacSha256")
    observed = parse_time(observation["observedAt"])
    if observed > now + dt.timedelta(seconds=MAX_CLOCK_SKEW_SECONDS) or now - observed > dt.timedelta(seconds=30):
        raise ControlError("PROCESS_OBSERVATION_STALE")
    if observation["phase"] != phase:
        raise ControlError("PROCESS_OBSERVATION_PHASE_INVALID")
    if observation["actualArgvSha256"] != digest_json(observation["actualArgv"]):
        raise ControlError("PROCESS_OBSERVATION_ARGV_INVALID")
    image = _canonical_executable(observation["imagePath"])
    if _hash_file(image) != observation["imageSha256"]:
        raise ControlError("PROCESS_OBSERVATION_IMAGE_INVALID")
    if request is not None:
        if (
            observation["requestId"] != request["requestId"]
            or observation["leaseId"] is not None
            or observation["status"] != "SUSPENDED"
            or observation["imageSha256"] != request["executableSha256"]
            or os.path.normcase(str(image)) != os.path.normcase(str(_canonical_executable(request["executablePath"])))
            or observation["actualArgv"] != request["argv"]
            or observation["actualArgvSha256"] != request["argvSha256"]
            or observation["seatIdHash"] != request["seatIdHash"]
            or observation["seatEpoch"] != request["seatEpoch"]
            or observation["sessionIdHash"] != request["sessionIdHash"]
        ):
            raise ControlError("PROCESS_OBSERVATION_BINDING_DRIFT")
    if lease is not None:
        exact = (
            observation["leaseId"] == lease["lease_id"]
            and observation["requestId"] == lease["request_id"]
            and observation["processId"] == lease["process_id"]
            and iso(parse_time(observation["processStartTime"])) == lease["process_start_time"]
            and observation["seatIdHash"] == lease["seat_id_hash"]
            and observation["seatEpoch"] == lease["seat_epoch"]
            and observation["sessionIdHash"] == lease["session_id_hash"]
        )
        if not exact:
            raise ControlError("LEASE_PROCESS_MISMATCH")


def _build_evidence_capsule_private(request: Any, output_path: Path) -> dict[str, Any]:
    """Build an exact-slice capsule without allocating unbounded source or result bytes."""

    output_path = Path(output_path)
    output_key = os.path.normcase(os.path.abspath(str(output_path)))
    if (
        _UNPROVEN_CAPSULE_OWNERS
        or output_path.with_name(output_path.name + ".cleanup-blocked").exists()
    ):
        raise ControlError("CAPSULE_TEMP_BACKLOG")
    validate_contract("capsule_request", request)
    total = sum(int(item["length"]) for item in request["slices"])
    if total > int(request["maxBytes"]):
        raise ControlError("CAPSULE_SIZE_LIMIT")
    # One deterministic private name per output is itself an O(1) crash/refusal fence: a failed
    # cleanup cannot cause successive random hardlinks to accumulate.
    temporary = output_path.with_name(output_path.name + ".tmp-owned")
    sources: dict[tuple[int, int], dict[str, Any]] = {}
    lexical_identities: dict[str, tuple[int, int]] = {}
    pending_handle: Any | None = None
    pending_close_attempted = False
    temporary_handle: Any | None = None
    published_handle: Any | None = None
    temporary_close_attempted = False
    published_close_attempted = False
    temporary_identity: tuple[int, int] | None = None
    temporary_named = True
    temporary_owned = False
    public_error: ControlError | None = None
    discard_ok = True
    slice_payloads: list[bytearray] = [bytearray(int(item["length"])) for item in request["slices"]]
    capsule_hash = hashlib.sha256()
    results: list[dict[str, Any]] = []

    def close_publication_handles() -> bool:
        nonlocal temporary_handle, published_handle
        nonlocal temporary_close_attempted, published_close_attempted
        proven = True
        if published_handle is not None:
            if not published_close_attempted:
                published_close_attempted = True
                if _attempt_file_close_verified(published_handle):
                    published_handle = None
            if published_handle is not None:
                _retain_unproven_capsule_owner(output_path, published_handle)
                proven = False
        if temporary_handle is not None:
            if not temporary_close_attempted:
                temporary_close_attempted = True
                if _attempt_file_close_verified(temporary_handle):
                    temporary_handle = None
            if temporary_handle is not None:
                _retain_unproven_capsule_owner(output_path, temporary_handle)
                proven = False
        return proven

    def map_publication_error(exc: BaseException) -> ControlError:
        if isinstance(exc, ControlError):
            return exc
        if isinstance(exc, OSError):
            return ControlError("CAPSULE_IO_FAILURE")
        return ControlError("CAPSULE_INTERNAL_FAILURE")
    try:
        # Group aliases by stable filesystem identity before opening a retained handle. Hard links
        # and lexical parent aliases therefore consume one open/hash/pass, not one per spelling.
        for index, item in enumerate(request["slices"]):
            source = Path(item["localPath"])
            if _is_reparse(source):
                raise ControlError("CAPSULE_SOURCE_DRIFT")
            observed = source.stat(follow_symlinks=False)
            identity = _stable_file_identity(observed)
            expected_size = int(observed.st_size)
            if expected_size < 0:
                raise ControlError("CAPSULE_SOURCE_DRIFT")
            lexical = os.path.normcase(os.path.abspath(str(source)))
            prior_identity = lexical_identities.setdefault(lexical, identity)
            if prior_identity != identity:
                raise ControlError("CAPSULE_SOURCE_DRIFT")
            alias = (source, identity, expected_size)
            entry = sources.get(identity)
            if entry is not None:
                if entry["digest"] != item["sourceSha256"]:
                    raise ControlError("CAPSULE_SOURCE_DIGEST_CONFLICT")
                if entry["expected_size"] != expected_size:
                    raise ControlError("CAPSULE_SOURCE_SIZE_CONFLICT")
                entry["slices"].append((index, int(item["offset"]), int(item["length"])))
                entry["aliases"].append(alias)
                continue
            sources[identity] = {
                "path": source,
                "identity": identity,
                "expected_size": expected_size,
                "digest": item["sourceSha256"],
                "slices": [(index, int(item["offset"]), int(item["length"]))],
                "aliases": [alias],
                "handle": None,
                "closeAttempted": False,
            }

        # Open one representative per stable identity and prove it is the grouped file.
        for entry in sources.values():
            handle = entry["path"].open("rb")
            pending_handle = handle
            opened = os.fstat(handle.fileno())
            if (
                _stable_file_identity(opened) != entry["identity"]
                or int(opened.st_size) != entry["expected_size"]
                or not _path_has_identity(entry["path"], entry["identity"])
            ):
                raise ControlError("CAPSULE_SOURCE_DRIFT")
            entry["handle"] = handle
            pending_handle = None

        source_total = sum(entry["expected_size"] for entry in sources.values())
        source_ceiling = min(int(request["maxSourceBytes"]), MAX_CAPSULE_SOURCE_BYTES)
        amplification_ceiling = max(1, total) * int(request["maxAmplificationRatio"])
        if source_total > source_ceiling or source_total > amplification_ceiling:
            raise ControlError("CAPSULE_SOURCE_SIZE_LIMIT")

        for entry in sources.values():
            if any(
                offset + length > entry["expected_size"]
                for _index, offset, length in entry["slices"]
            ):
                raise ControlError("CAPSULE_SLICE_RANGE_INVALID")

        for entry in sources.values():
            handle = entry["handle"]
            source_hash = hashlib.sha256()
            position = 0
            remaining = entry["expected_size"]
            while remaining:
                chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
                if not chunk:
                    raise ControlError("CAPSULE_SOURCE_DRIFT")
                if len(chunk) > remaining:
                    raise ControlError("CAPSULE_SOURCE_DRIFT")
                source_hash.update(chunk)
                chunk_end = position + len(chunk)
                for slice_index, offset, length in entry["slices"]:
                    slice_end = offset + length
                    overlap_start = max(position, offset)
                    overlap_end = min(chunk_end, slice_end)
                    if overlap_start < overlap_end:
                        source_start = overlap_start - position
                        target_start = overlap_start - offset
                        slice_payloads[slice_index][target_start:target_start + overlap_end - overlap_start] = (
                            chunk[source_start:source_start + overlap_end - overlap_start]
                        )
                position = chunk_end
                remaining -= len(chunk)
            # At most one byte beyond the fixed initial budget is read.  A growing source can never
            # extend CPU or I/O beyond expected_size + 1.
            if handle.read(1):
                raise ControlError("CAPSULE_SOURCE_GROWTH")
            if "sha256:" + source_hash.hexdigest() != entry["digest"]:
                raise ControlError("CAPSULE_SOURCE_DRIFT")
            opened = os.fstat(handle.fileno())
            if (
                _stable_file_identity(opened) != entry["identity"]
                or int(opened.st_size) != entry["expected_size"]
                or any(
                    alias_size != entry["expected_size"]
                    or not _path_has_identity(alias_path, alias_identity)
                    for alias_path, alias_identity, alias_size in entry["aliases"]
                )
            ):
                raise ControlError("CAPSULE_SOURCE_DRIFT")

        temporary_handle, temporary_named = _open_owned_temporary(temporary, output_path)
        temporary_owned = True
        temporary_opened = os.fstat(temporary_handle.fileno())
        temporary_identity = _stable_file_identity(temporary_opened)
        if int(temporary_opened.st_size) != 0 or (
            temporary_named and not _path_has_identity(temporary, temporary_identity)
        ):
            raise ControlError("CAPSULE_TEMP_IDENTITY_DRIFT")

        for index, item in enumerate(request["slices"]):
            offset = int(item["offset"])
            length = int(item["length"])
            payload = bytes(slice_payloads[index])
            slice_hash = hashlib.sha256(payload)
            temporary_handle.write(payload)
            capsule_hash.update(payload)
            results.append(
                {
                    "reference": item["reference"],
                    "offset": offset,
                    "length": length,
                    "sliceSha256": "sha256:" + slice_hash.hexdigest(),
                }
            )
        temporary_handle.flush()
        os.fsync(temporary_handle.fileno())
        result = {
            "schema": "fleet-universal-evidence-capsule/v1",
            "capsuleSha256": "sha256:" + capsule_hash.hexdigest(),
            "payloadBytes": total,
            "sliceCount": len(results),
            # Pre-publication schema validation uses the conservative non-clean state.  CLEAN is
            # assigned only after discard and every required owner close prove successful.
            "temporaryCleanup": "REFUSED_BOUNDED",
            "slices": results,
        }
        validate_contract("capsule", result)

        completed_temp = os.fstat(temporary_handle.fileno())
        if (
            _stable_file_identity(completed_temp) != temporary_identity
            or int(completed_temp.st_size) != total
            or (temporary_named and not _path_has_identity(temporary, temporary_identity))
        ):
            raise ControlError("CAPSULE_TEMP_IDENTITY_DRIFT")

        # A wrapper can raise after the no-clobber link actually completed. Authority derives from
        # the retained file identity and bytes, not from that wrapper's return value.
        link_error: BaseException | None = None
        try:
            _publish_owned_temporary(temporary_handle, temporary, temporary_named, output_path)
        except BaseException as exc:
            link_error = exc

        try:
            published_handle = output_path.open("rb")
        except (FileNotFoundError, OSError) as exc:
            if isinstance(link_error, FileExistsError):
                raise ControlError("CAPSULE_OUTPUT_EXISTS") from exc
            if link_error is not None:
                raise map_publication_error(link_error) from exc
            raise ControlError("CAPSULE_PUBLICATION_MISSING") from exc

        published_opened = os.fstat(published_handle.fileno())
        published_identity = _stable_file_identity(published_opened)
        if published_identity != temporary_identity:
            if isinstance(link_error, FileExistsError):
                raise ControlError("CAPSULE_OUTPUT_EXISTS")
            raise ControlError("CAPSULE_PUBLICATION_IDENTITY_DRIFT")
        if int(published_opened.st_size) != total or not _path_has_identity(output_path, temporary_identity):
            raise ControlError("CAPSULE_PUBLICATION_IDENTITY_DRIFT")

        published_hash = hashlib.sha256()
        remaining = total
        while remaining:
            chunk = published_handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                raise ControlError("CAPSULE_PUBLICATION_BYTES_DRIFT")
            if len(chunk) > remaining:
                raise ControlError("CAPSULE_PUBLICATION_BYTES_DRIFT")
            published_hash.update(chunk)
            remaining -= len(chunk)
        if published_handle.read(1):
            raise ControlError("CAPSULE_PUBLICATION_BYTES_DRIFT")
        published_after = os.fstat(published_handle.fileno())
        if (
            _stable_file_identity(published_after) != temporary_identity
            or int(published_after.st_size) != total
            or "sha256:" + published_hash.hexdigest() != result["capsuleSha256"]
            or not _path_has_identity(output_path, temporary_identity)
        ):
            raise ControlError("CAPSULE_PUBLICATION_BYTES_DRIFT")

        # A syscall wrapper may legitimately raise after a proven link and still be accepted, but
        # an unproven target-directory close is retained process authority, not a link result.  It
        # must remain a cleanup refusal even after exact public identity and bytes are verified.
        if (
            isinstance(link_error, ControlError)
            and link_error.reason == "CAPSULE_TEMP_CLEANUP_REFUSED"
        ):
            raise link_error

        discard_ok = not temporary_owned or _attempt_arm_owned_temp_discard(
            temporary_handle, temporary_named
        )
        if not discard_ok:
            _surface_temp_cleanup_refusal(output_path)
    except BaseException as exc:
        discard_ok = True
        if temporary_owned and temporary_handle is not None:
            discard_ok = _attempt_arm_owned_temp_discard(temporary_handle, temporary_named)
        if not discard_ok:
            _surface_temp_cleanup_refusal(output_path)
        mapped = (
            map_publication_error(exc) if discard_ok
            else ControlError("CAPSULE_TEMP_CLEANUP_REFUSED")
        )
        # Do not raise while the private exception is active: even ``from None`` retains a private
        # __context__.  Only the stable reason crosses this boundary; the new public exception is
        # raised after the handler and finally suite have cleared private exception state.
        public_error = ControlError(mapped.reason)
    finally:
        finalizers_proven = close_publication_handles()
        if pending_handle is not None:
            if not pending_close_attempted:
                pending_close_attempted = True
                if _attempt_file_close_verified(pending_handle):
                    pending_handle = None
            if pending_handle is not None:
                _retain_unproven_capsule_owner(output_path, pending_handle)
                finalizers_proven = False
        for entry in sources.values():
            handle = entry.get("handle")
            if handle is None:
                continue
            if not entry["closeAttempted"]:
                entry["closeAttempted"] = True
                if _attempt_file_close_verified(handle):
                    entry["handle"] = None
            if entry["handle"] is not None:
                _retain_unproven_capsule_owner(output_path, handle)
                finalizers_proven = False
        if not finalizers_proven:
            _surface_temp_cleanup_refusal(output_path)
            public_error = ControlError("CAPSULE_TEMP_CLEANUP_REFUSED")
    if public_error is not None:
        raise public_error from None
    result["temporaryCleanup"] = "CLEAN" if discard_ok else "REFUSED_BOUNDED"
    validate_contract("capsule", result)
    return result


def build_evidence_capsule(request: Any, output_path: Path) -> dict[str, Any]:
    """Sanitized public boundary for every capsule validation, handler, and finalizer failure."""

    public_reason: str | None = None
    try:
        with _CAPSULE_PROCESS_LOCK:
            return _build_evidence_capsule_private(request, output_path)
    except BaseException as exc:
        if isinstance(exc, ControlError):
            public_reason = exc.reason
        elif isinstance(exc, OSError):
            public_reason = "CAPSULE_IO_FAILURE"
        else:
            public_reason = "CAPSULE_INTERNAL_FAILURE"
    # This raise is deliberately outside the handler.  The public exception retains no private
    # cause/context chain or inner formatted traceback from any preflight/finalizer exception class.
    raise ControlError(public_reason or "CAPSULE_INTERNAL_FAILURE") from None


class UniversalProviderBroker:
    """SQLite reference broker with a persisted fail-closed gate and request replay law."""

    def __init__(self, state_root: Path):
        self.state_root = Path(state_root)
        try:
            if self.state_root.exists() and (not self.state_root.is_dir() or _is_reparse(self.state_root)):
                raise ControlError("STATE_BOUNDARY_INVALID")
            self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.state_root = self.state_root.resolve(strict=True)
        except ControlError:
            raise
        except OSError as exc:
            raise ControlError("STATE_UNEVALUABLE") from exc
        self.database = self.state_root / "universal-provider-control-v1.db"
        self._artifact_poison_key = os.path.normcase(os.path.abspath(str(self.state_root)))
        self._root_runtime = _broker_root_runtime(self._artifact_poison_key)
        self._root_lock = self._root_runtime.lock
        # These aliases intentionally point at root-singleton maps shared by every broker instance
        # in this process.  A second object cannot lose or bypass the first object's live owners.
        self._os_locks = self._root_runtime.os_locks
        self._os_lock_release_attempted = self._root_runtime.os_lock_release_attempted
        self._unproven_os_locks = self._root_runtime.unproven_os_locks
        self._artifact_handles = self._root_runtime.artifact_handles
        self._artifact_close_attempted = self._root_runtime.artifact_close_attempted
        self._unproven_artifact_handles = self._root_runtime.unproven_artifact_handles
        try:
            with self._connect() as connection:
                connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gate_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    state TEXT NOT NULL CHECK (state IN ('CLOSED','SHADOW','CANARY','OPEN')),
                    transition_epoch INTEGER NOT NULL CHECK (transition_epoch >= 0),
                    transition_digest TEXT,
                    transition_bytes BLOB,
                    transition_hmac TEXT,
                    expires_at TEXT,
                    broker_digest TEXT,
                    profile_digest TEXT,
                    inventory_digest TEXT,
                    health_digest TEXT
                );
                INSERT OR IGNORE INTO gate_state(
                    singleton, state, transition_epoch, transition_digest, expires_at,
                    broker_digest, profile_digest, inventory_digest, health_digest
                ) VALUES (1, 'CLOSED', 0, NULL, NULL, NULL, NULL, NULL, NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leases (
                    lease_id TEXT PRIMARY KEY,
                    request_id TEXT UNIQUE NOT NULL,
                    quota_domain_id TEXT NOT NULL,
                    process_id INTEGER NOT NULL,
                    process_start_time TEXT NOT NULL,
                    seat_id_hash TEXT NOT NULL,
                    seat_epoch INTEGER NOT NULL,
                    session_id_hash TEXT NOT NULL,
                    binding_digest TEXT NOT NULL,
                    reservations_json TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    terminal_digest TEXT,
                    gate_epoch INTEGER NOT NULL DEFAULT 0,
                    is_canary INTEGER NOT NULL DEFAULT 0 CHECK (is_canary IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS provider_signals (
                    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    evidence_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS canary_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    authorization_digest TEXT NOT NULL,
                    request_id TEXT UNIQUE NOT NULL,
                    gate_epoch INTEGER NOT NULL UNIQUE
                );
                """
                )
                gate_columns = {row[1] for row in connection.execute("PRAGMA table_info(gate_state)")}
                for name, declaration in (
                    ("transition_bytes", "BLOB"),
                    ("transition_hmac", "TEXT"),
                ):
                    if name not in gate_columns:
                        connection.execute(f"ALTER TABLE gate_state ADD COLUMN {name} {declaration}")
                lease_columns = {row[1] for row in connection.execute("PRAGMA table_info(leases)")}
                for name, declaration in (
                    ("gate_epoch", "INTEGER NOT NULL DEFAULT 0"),
                    ("is_canary", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    if name not in lease_columns:
                        connection.execute(f"ALTER TABLE leases ADD COLUMN {name} {declaration}")
                canary_columns = {row[1] for row in connection.execute("PRAGMA table_info(canary_authorizations)")}
                if "gate_epoch" not in canary_columns:
                    connection.execute("ALTER TABLE canary_authorizations ADD COLUMN gate_epoch INTEGER")
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS one_canary_per_gate_epoch ON canary_authorizations(gate_epoch)"
                )
        except ControlError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise ControlError("STATE_UNEVALUABLE") from exc

    def close(self) -> None:
        """Administrative assertion only; never a substitute for authenticated terminal release."""

        with self._root_lock:
            with self._connect() as connection:
                active = connection.execute(
                    "SELECT COUNT(*) FROM leases WHERE state IN ('ACTIVE','RESUME_ATTESTED')"
                ).fetchone()[0]
            if active:
                raise ControlError("ACTIVE_LEASES_REMAIN")
            if self._unproven_os_locks:
                raise ControlError("OS_LOCK_CLEANUP_POISONED")
            if (
                self._unproven_artifact_handles
                or _broker_artifact_cleanup_poisoned(self._artifact_poison_key)
            ):
                raise ControlError("ARTIFACT_CLEANUP_POISONED")
            if self._os_locks or self._artifact_handles:
                raise ControlError("BROKER_OWNERS_REMAIN")
            assert_process_cleanup_clear()

    def __del__(self) -> None:  # pragma: no cover - defensive interpreter cleanup
        # Deliberately assertion-only: garbage collection cannot release child authority.
        try:
            self.close()
        except BaseException:
            pass

    def _validate_state_boundary(self) -> None:
        try:
            parent = self.state_root.resolve(strict=True)
            if self.database.exists():
                if _is_reparse(self.database) or self.database.stat().st_size > MAX_STATE_BYTES:
                    raise ControlError("STATE_BOUNDARY_INVALID")
                resolved = self.database.resolve(strict=True)
                if os.path.normcase(os.path.abspath(str(self.database))) != os.path.normcase(str(resolved)):
                    raise ControlError("STATE_BOUNDARY_INVALID")
            current = self.state_root
            while True:
                if _is_reparse(current):
                    raise ControlError("STATE_BOUNDARY_INVALID")
                if current.parent == current:
                    break
                current = current.parent
        except ControlError:
            raise
        except (OSError, RuntimeError) as exc:
            raise ControlError("STATE_UNEVALUABLE") from exc

    def state_root_identity(self, fleet_secret: bytes) -> str:
        if not isinstance(fleet_secret, bytes) or len(fleet_secret) < 32:
            raise ControlError("FLEET_SECRET_INVALID")
        material = b"fleet-state-root-v1\x00" + os.path.normcase(str(self.state_root)).encode("utf-8")
        return "hmac-sha256:" + hmac.new(fleet_secret, material, hashlib.sha256).hexdigest()

    @contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        self._validate_state_boundary()
        try:
            connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
        except sqlite3.Error as exc:
            raise ControlError("STATE_UNEVALUABLE") from exc
        try:
            yield connection
        except sqlite3.Error as exc:
            raise ControlError("STATE_UNEVALUABLE") from exc
        finally:
            connection.close()

    def _lock_path(self, quota_domain_id: str) -> Path:
        name = hashlib.sha256(quota_domain_id.encode("ascii")).hexdigest() + ".lock"
        directory = self.database.parent / (self.database.name + ".quota-locks")
        try:
            directory.mkdir(mode=0o700, exist_ok=True)
            if _is_reparse(directory) or directory.resolve(strict=True).parent != self.state_root:
                raise ControlError("QUOTA_LOCK_BOUNDARY_INVALID")
            path = directory / name
            if path.exists() and (_is_reparse(path) or path.stat().st_size > 1):
                raise ControlError("QUOTA_LOCK_BOUNDARY_INVALID")
            return path
        except ControlError:
            raise
        except OSError as exc:
            raise ControlError("QUOTA_LOCK_BOUNDARY_INVALID") from exc

    def _acquire_os_lock(self, lease_id: str, quota_domain_id: str) -> None:
        path = self._lock_path(quota_domain_id)
        handle: Any | None = None
        public_reason: str | None = None
        try:
            handle = path.open("a+b")
            opened = os.fstat(handle.fileno())
            current = path.stat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or _is_reparse(path):
                raise ControlError("QUOTA_LOCK_BOUNDARY_INVALID")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - Windows is the canonical host, retained for portability
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            public_reason = (
                exc.reason if isinstance(exc, ControlError) else "QUOTA_DOMAIN_OS_LOCK_HELD"
            )
        if public_reason is not None:
            if handle is not None and not _attempt_file_close_verified(handle):
                self._os_locks[lease_id] = handle
                self._os_lock_release_attempted[lease_id] = {"close-attempted", "close-refused"}
                self._unproven_os_locks[lease_id] = handle
                public_reason = "OS_LOCK_CLEANUP_REFUSED"
            raise ControlError(public_reason) from None
        self._os_locks[lease_id] = handle
        self._os_lock_release_attempted[lease_id] = set()

    def _release_os_lock(self, lease_id: str) -> None:
        handle = self._os_locks.get(lease_id)
        if handle is None:
            return
        attempted = self._os_lock_release_attempted.setdefault(lease_id, set())
        if lease_id in self._unproven_os_locks or "close-refused" in attempted:
            raise ControlError("OS_LOCK_CLEANUP_POISONED")

        if "unlock-attempted" not in attempted:
            attempted.add("unlock-attempted")
            try:
                _unlock_os_lock_handle(handle)
            except BaseException:
                attempted.add("unlock-refused")

        close_proven = False
        if "close-attempted" not in attempted:
            attempted.add("close-attempted")
            close_proven = _attempt_file_close_verified(handle)
            if not close_proven:
                attempted.add("close-refused")
        if not close_proven:
            self._unproven_os_locks[lease_id] = handle
            raise ControlError("OS_LOCK_CLEANUP_REFUSED") from None

        unlock_refused = "unlock-refused" in attempted
        self._os_locks.pop(lease_id, None)
        self._os_lock_release_attempted.pop(lease_id, None)
        self._unproven_os_locks.pop(lease_id, None)
        if unlock_refused:
            raise ControlError("OS_LOCK_UNLOCK_REFUSED") from None

    def _os_lock_is_current(self, lease_id: str, quota_domain_id: str) -> bool:
        handle = self._os_locks.get(lease_id)
        if handle is None or handle.closed:
            return False
        path = self._lock_path(quota_domain_id)
        try:
            opened = os.fstat(handle.fileno())
            current = path.stat()
            return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino) and not _is_reparse(path)
        except OSError:
            return False

    def _open_artifact_handles(
        self, lease_id: str, artifacts: Sequence[tuple[Path, str, int]]
    ) -> str:
        if _broker_artifact_cleanup_poisoned(self._artifact_poison_key):
            raise ControlError("ARTIFACT_CLEANUP_POISONED")
        retained: list[tuple[Path, Any, tuple[int, int, int, int], str, int]] = []
        identities: list[dict[str, Any]] = []
        current_handle: Any | None = None
        try:
            for path, expected_digest, ceiling in artifacts:
                require_sha256(expected_digest)
                handle = path.open("rb")
                current_handle = handle
                before = os.fstat(handle.fileno())
                if before.st_size < 0 or before.st_size > ceiling:
                    raise ControlError("ARTIFACT_SIZE_LIMIT")
                expected_size = int(before.st_size)
                hasher = hashlib.sha256()
                total = 0
                remaining = expected_size
                while remaining:
                    chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ControlError("ARTIFACT_IDENTITY_DRIFT")
                    if len(chunk) > remaining:
                        raise ControlError("ARTIFACT_IDENTITY_DRIFT")
                    total += len(chunk)
                    remaining -= len(chunk)
                    hasher.update(chunk)
                if handle.read(1):
                    raise ControlError("ARTIFACT_IDENTITY_DRIFT")
                actual = "sha256:" + hasher.hexdigest()
                after = os.fstat(handle.fileno())
                current = path.stat()
                before_id = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                after_id = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                if before_id != after_id or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
                    raise ControlError("ARTIFACT_IDENTITY_DRIFT")
                if actual != expected_digest:
                    raise ControlError("ARTIFACT_DIGEST_DRIFT")
                handle.seek(0)
                retained.append((path, handle, after_id, actual, ceiling))
                current_handle = None
                identities.append({"path": os.path.normcase(str(path)), "sha256": actual, "bytes": total})
        except BaseException:
            cleanup_proven = True
            if current_handle is not None:
                if not _attempt_file_close_verified(current_handle):
                    self._unproven_artifact_handles.setdefault(lease_id, []).append(current_handle)
                    _retain_broker_artifact_owner(self._artifact_poison_key, current_handle)
                    cleanup_proven = False
            for _path, handle, _identity, _digest, _ceiling in retained:
                if not _attempt_file_close_verified(handle):
                    self._unproven_artifact_handles.setdefault(lease_id, []).append(handle)
                    _retain_broker_artifact_owner(self._artifact_poison_key, handle)
                    cleanup_proven = False
            if not cleanup_proven:
                raise ControlError("ARTIFACT_HANDLE_CLEANUP_REFUSED")
            raise
        self._artifact_handles[lease_id] = retained
        self._artifact_close_attempted[lease_id] = set()
        return digest_json(identities)

    def _artifact_handles_are_current(self, lease_id: str) -> bool:
        retained = self._artifact_handles.get(lease_id)
        if not retained:
            return False
        try:
            for path, handle, identity, digest, ceiling in retained:
                if handle.closed or _is_reparse(path):
                    return False
                opened = os.fstat(handle.fileno())
                current = path.stat()
                observed = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                if observed != identity or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    return False
                expected_size = int(identity[2])
                if expected_size > ceiling:
                    return False
                handle.seek(0)
                hasher = hashlib.sha256()
                remaining = expected_size
                while remaining:
                    chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        return False
                    if len(chunk) > remaining:
                        return False
                    hasher.update(chunk)
                    remaining -= len(chunk)
                if handle.read(1):
                    return False
                handle.seek(0)
                after = os.fstat(handle.fileno())
                current_after = path.stat()
                after_observed = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                if (
                    after_observed != identity
                    or _is_reparse(path)
                    or (after.st_dev, after.st_ino) != (current_after.st_dev, current_after.st_ino)
                    or "sha256:" + hasher.hexdigest() != digest
                ):
                    return False
            return True
        except (ControlError, OSError):
            return False

    def _release_artifact_handles(self, lease_id: str) -> None:
        retained = self._artifact_handles.get(lease_id, [])
        attempted = self._artifact_close_attempted.setdefault(lease_id, set())
        unproven: list[tuple[Path, Any, tuple[int, int, int, int], str, int]] = []
        for record in retained:
            handle = record[1]
            owner_key = id(handle)
            if owner_key not in attempted:
                attempted.add(owner_key)
                if _attempt_file_close_verified(handle):
                    continue
            unproven.append(record)
        if unproven:
            self._artifact_handles[lease_id] = unproven
            for record in unproven:
                _retain_broker_artifact_owner(self._artifact_poison_key, record[1])
            raise ControlError("ARTIFACT_HANDLE_CLEANUP_REFUSED")
        self._artifact_handles.pop(lease_id, None)
        self._artifact_close_attempted.pop(lease_id, None)

    def _release_terminal_owners(self, lease_id: str) -> None:
        """Attempt every terminal owner once, then surface the strongest stable refusal."""

        reasons: list[str] = []
        try:
            self._release_os_lock(lease_id)
        except ControlError as exc:
            reasons.append(exc.reason)
        try:
            self._release_artifact_handles(lease_id)
        except ControlError as exc:
            reasons.append(exc.reason)
        if reasons:
            if any(reason in {"OS_LOCK_CLEANUP_REFUSED", "OS_LOCK_CLEANUP_POISONED"} for reason in reasons):
                raise ControlError("OS_LOCK_CLEANUP_POISONED") from None
            if "ARTIFACT_HANDLE_CLEANUP_REFUSED" in reasons:
                raise ControlError("ARTIFACT_HANDLE_CLEANUP_REFUSED") from None
            raise ControlError(reasons[0]) from None

    def _verified_gate_row(
        self, connection: sqlite3.Connection, *, fleet_secret: bytes | None, now: dt.datetime
    ) -> tuple[sqlite3.Row, dict[str, Any] | None]:
        row = connection.execute("SELECT * FROM gate_state WHERE singleton=1").fetchone()
        if row is None or row["state"] not in {"CLOSED", "SHADOW", "CANARY", "OPEN"}:
            raise ControlError("GATE_STATE_INVALID")
        raw = row["transition_bytes"]
        # The sole unsigned state is the initial or automatic fail-closed seal.
        if raw is None:
            if row["state"] != "CLOSED" or any(
                row[name] is not None
                for name in ("transition_digest", "transition_hmac", "expires_at", "broker_digest", "profile_digest", "inventory_digest", "health_digest")
            ):
                raise ControlError("GATE_TRANSITION_RECORD_INVALID")
            return row, None
        if fleet_secret is None:
            raise ControlError("GATE_TRANSITION_SECRET_REQUIRED")
        if not isinstance(raw, bytes) or len(raw) > MAX_INPUT_BYTES:
            raise ControlError("GATE_TRANSITION_RECORD_INVALID")
        transition = strict_json_bytes(raw)
        validate_contract("transition", transition)
        canonical = canonical_json(transition).encode("utf-8")
        if raw != canonical:
            raise ControlError("GATE_TRANSITION_BYTES_INVALID")
        verify_contract_hmac("gate-transition-v1", transition, fleet_secret, "authorizationHmacSha256")
        expected_columns = {
            "state": transition["to"],
            "transition_epoch": transition["transitionEpoch"],
            "transition_digest": digest_json(transition),
            "transition_hmac": transition["authorizationHmacSha256"],
            "expires_at": iso(parse_time(transition["expiresAt"])),
            "broker_digest": transition["brokerExecutableSha256"],
            "profile_digest": transition["projectProfileSha256"],
            "inventory_digest": transition["inventorySha256"],
            "health_digest": transition["brokerHealthSha256"],
        }
        if any(row[key] != value for key, value in expected_columns.items()):
            raise ControlError("GATE_TRANSITION_RECORD_INVALID")
        if parse_time(transition["expiresAt"]) <= now:
            raise ControlError("GATE_TRANSITION_EXPIRED")
        return row, transition

    def gate_state(
        self, *, fleet_secret: bytes | None = None, now: dt.datetime | None = None
    ) -> str:
        try:
            with self._connect() as connection:
                row, _transition = self._verified_gate_row(
                    connection, fleet_secret=fleet_secret, now=(now or dt.datetime.now(UTC)).astimezone(UTC)
                )
            return str(row["state"])
        except ControlError:
            return "CLOSED"

    def transition_gate(
        self, transition: Any, *, fleet_secret: bytes, now: dt.datetime | None = None
    ) -> str:
        validate_contract("transition", transition)
        verify_contract_hmac("gate-transition-v1", transition, fleet_secret, "authorizationHmacSha256")
        at = (now or dt.datetime.now(UTC)).astimezone(UTC)
        issued = parse_time(transition["issuedAt"])
        expires = parse_time(transition["expiresAt"])
        if issued > at + dt.timedelta(seconds=5) or expires <= at or expires <= issued:
            raise ControlError("GATE_TRANSITION_TIME_INVALID")
        if expires - issued > dt.timedelta(minutes=15):
            raise ControlError("GATE_TRANSITION_TIME_INVALID")
        if transition["brokerExecutableSha256"] != _hash_file(Path(__file__).resolve()):
            raise ControlError("GATE_TRANSITION_BINDING_DRIFT")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row, _prior_transition = self._verified_gate_row(
                    connection, fleet_secret=fleet_secret, now=at
                )
                if row["state"] != transition["from"]:
                    raise ControlError("GATE_TRANSITION_CONFLICT")
                if transition["transitionEpoch"] != int(row["transition_epoch"]) + 1:
                    raise ControlError("GATE_TRANSITION_EPOCH_INVALID")
                if transition["to"] == "CLOSED":
                    if transition["cause"] != "SAFETY_CLOSE":
                        raise ControlError("GATE_TRANSITION_UNAUTHORIZED")
                elif transition["cause"] != "INDEPENDENT_ADJUDICATION":
                    raise ControlError("GATE_TRANSITION_UNAUTHORIZED")
                connection.execute(
                    """UPDATE gate_state SET
                        state=?, transition_epoch=?, transition_digest=?, transition_bytes=?, transition_hmac=?, expires_at=?, broker_digest=?,
                        profile_digest=?, inventory_digest=?, health_digest=? WHERE singleton=1""",
                    (
                        transition["to"], transition["transitionEpoch"], digest_json(transition),
                        canonical_json(transition).encode("utf-8"), transition["authorizationHmacSha256"],
                        iso(expires), transition["brokerExecutableSha256"],
                        transition["projectProfileSha256"], transition["inventorySha256"],
                        transition["brokerHealthSha256"],
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return transition["to"]

    def record_provider_signal(
        self, kind: str, observed_at: str, evidence_sha256: str, *, fleet_secret: bytes | None = None,
        now: dt.datetime | None = None
    ) -> str:
        if kind not in {"RESET", "AUTH_SUCCESS", "CAPACITY_RETURN", "QUOTA_REFUSAL"}:
            raise ControlError("PROVIDER_SIGNAL_INVALID")
        parse_time(observed_at)
        require_sha256(evidence_sha256, "PROVIDER_SIGNAL_INVALID")
        before = self.gate_state(fleet_secret=fleet_secret, now=now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO provider_signals(kind, observed_at, evidence_digest) VALUES (?, ?, ?)",
                    (kind, observed_at, evidence_sha256),
                )
                after = self.gate_state(fleet_secret=fleet_secret, now=now)
                if after != before:
                    raise ControlError("PROVIDER_SIGNAL_GATE_MUTATION")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return before

    def _cleanup_poison_reason(self) -> str | None:
        if self._unproven_os_locks:
            return "OS_LOCK_CLEANUP_POISONED"
        if _broker_artifact_cleanup_poisoned(self._artifact_poison_key):
            return "ARTIFACT_CLEANUP_POISONED"
        return None

    def authorize_suspended_child(
        self,
        *,
        request: Any,
        profile: Any,
        inventory: Any,
        health: Any,
        native_evidence: Sequence[Any],
        manual_authorization: Any | None,
        local_stable_identity: bytes,
        fleet_secret: bytes,
        process_observation: Any,
        now: dt.datetime,
    ) -> dict[str, Any]:
        """Serialize poison check, acquisition, lease publication, and result publication per root."""

        with self._root_lock:
            return self._authorize_suspended_child_root_locked(
                request=request,
                profile=profile,
                inventory=inventory,
                health=health,
                native_evidence=native_evidence,
                manual_authorization=manual_authorization,
                local_stable_identity=local_stable_identity,
                fleet_secret=fleet_secret,
                process_observation=process_observation,
                now=now,
            )

    def _authorize_suspended_child_root_locked(
        self,
        *,
        request: Any,
        profile: Any,
        inventory: Any,
        health: Any,
        native_evidence: Sequence[Any],
        manual_authorization: Any | None,
        local_stable_identity: bytes,
        fleet_secret: bytes,
        process_observation: Any,
        now: dt.datetime,
    ) -> dict[str, Any]:
        """Final revalidation and reservation under one SQLite write lock.

        The caller creates a child suspended, calls this interface, and may resume it only for an
        ALLOW_ATTESTED result.  This function itself cannot create or resume any process.
        """

        poison_reason = self._cleanup_poison_reason()
        if poison_reason is not None:
            return {"status": "UNEVALUABLE", "reason": poison_reason}

        try:
            _enforce_complexity(request)
            _enforce_complexity(profile)
            _enforce_complexity(inventory)
            _enforce_complexity(health)
            if not isinstance(native_evidence, (list, tuple)) or not 1 <= len(native_evidence) <= 256:
                raise ControlError("EVIDENCE_SET_INVALID")
            for evidence in native_evidence:
                _enforce_complexity(evidence)
            _enforce_complexity(process_observation)
            validate_contract("request", request)
            validate_project_profile(profile)
            _validate_inventory(inventory)
            validate_contract("health", health)
            verify_contract_hmac("broker-health-v1", health, fleet_secret, "observerHmacSha256")
            for evidence in native_evidence:
                validate_contract("native", evidence)
                verify_contract_hmac(
                    "provider-capacity-evidence-v1", evidence, fleet_secret, "observerHmacSha256"
                )
            _verify_process_observation(
                process_observation, fleet_secret=fleet_secret, now=now.astimezone(UTC),
                phase="ADMISSION", request=request,
            )
            if manual_authorization is not None:
                _enforce_complexity(manual_authorization)
                validate_contract("canary_authorization", manual_authorization)
                verify_contract_hmac(
                    "manual-canary-authorization-v1",
                    manual_authorization,
                    fleet_secret,
                    "authorizationHmacSha256",
                )
            request_id = request["requestId"]
            identity_digest = derive_quota_domain_id(request["provider"], local_stable_identity, fleet_secret)
        except ControlError as exc:
            return {"status": "UNEVALUABLE", "reason": exc.reason}
        replay_material = {
            "request": request,
            "profile": profile,
            "inventory": inventory,
            "health": health,
            "nativeEvidence": native_evidence,
            "manualAuthorization": manual_authorization,
            "quotaIdentity": identity_digest,
            "processObservation": process_observation,
        }
        request_digest = digest_json(replay_material)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            held_lease_id: str | None = None
            try:
                prior = connection.execute(
                    "SELECT request_digest, result_json FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                if prior is not None:
                    if prior["request_digest"] != request_digest:
                        connection.execute("ROLLBACK")
                        return {"status": "UNEVALUABLE", "reason": "REQUEST_REPLAY_CONFLICT"}
                    result = strict_json_bytes(prior["result_json"].encode("utf-8"))
                    if result.get("status") in {"PREPARED_SUSPENDED", "ALLOW_ATTESTED"}:
                        connection.execute("COMMIT")
                        return {"status": "UNEVALUABLE", "reason": "ACTIVE_AUTHORITY_NOT_REPLAYABLE"}
                    connection.execute("COMMIT")
                    return result
                prior_artifact_handles = set(self._artifact_handles)
                try:
                    result = self._authorize_locked(
                        connection=connection,
                        request=request,
                        profile=profile,
                        inventory=inventory,
                        health=health,
                        native_evidence=native_evidence,
                        manual_authorization=manual_authorization,
                        identity_digest=identity_digest,
                        fleet_secret=fleet_secret,
                        process_observation=process_observation,
                        now=now.astimezone(UTC),
                    )
                except ControlError as exc:
                    for leaked_lease in set(self._artifact_handles) - prior_artifact_handles:
                        self._release_terminal_owners(leaked_lease)
                    result = {"status": "UNEVALUABLE", "reason": exc.reason}
                if result.get("status") == "PREPARED_SUSPENDED":
                    held_lease_id = result["leaseId"]
                connection.execute(
                    "INSERT INTO requests(request_id, request_digest, result_json) VALUES (?, ?, ?)",
                    (request_id, request_digest, canonical_json(result)),
                )
                connection.execute("COMMIT")
                return result
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if held_lease_id is not None:
                    self._release_terminal_owners(held_lease_id)
                raise

    def _authorize_locked(
        self,
        *,
        connection: sqlite3.Connection,
        request: Any,
        profile: Any,
        inventory: Any,
        health: Any,
        native_evidence: Sequence[Any],
        manual_authorization: Any | None,
        identity_digest: str,
        fleet_secret: bytes,
        process_observation: Any,
        now: dt.datetime,
    ) -> dict[str, Any]:
        # Every contract, artifact, capacity value, path and digest is revalidated after BEGIN
        # IMMEDIATE.  Pre-lock checks are never used as launch authority.
        validate_contract("request", request)
        validate_project_profile(profile)
        _validate_inventory(inventory)
        validate_contract("health", health)
        verify_contract_hmac("broker-health-v1", health, fleet_secret, "observerHmacSha256")
        if manual_authorization is not None:
            validate_contract("canary_authorization", manual_authorization)
            verify_contract_hmac(
                "manual-canary-authorization-v1",
                manual_authorization,
                fleet_secret,
                "authorizationHmacSha256",
            )
        observation = _latest_observation(native_evidence, request, fleet_secret)
        _verify_process_observation(
            process_observation, fleet_secret=fleet_secret, now=now, phase="ADMISSION", request=request
        )

        if request["project"] != profile["project"]:
            raise ControlError("PROJECT_PROFILE_MISMATCH")
        if request["quotaDomainId"] != identity_digest:
            raise ControlError("QUOTA_IDENTITY_MISMATCH")
        if profile["coordination"]["stateRootIdentity"] != self.state_root_identity(fleet_secret):
            raise ControlError("STATE_ROOT_IDENTITY_MISMATCH")
        if request["maxWallSeconds"] > profile["policy"]["leaseMaxSeconds"]:
            raise ControlError("LEASE_BOUND_EXCEEDED")
        if request["maxTurns"] > profile["efficiency"]["maxTurns"]:
            raise ControlError("TURN_BOUND_EXCEEDED")
        if request["maxContextTokens"] > profile["efficiency"]["maxContextTokens"]:
            raise ControlError("CONTEXT_BOUND_EXCEEDED")
        issued = parse_time(request["issuedAt"])
        expires = parse_time(request["expiresAt"])
        if (
            issued > now + dt.timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)
            or expires <= now
            or expires <= issued
            or now - issued > dt.timedelta(seconds=profile["policy"]["maxRequestAgeSeconds"])
            or expires - issued > dt.timedelta(seconds=profile["policy"]["maxRequestValiditySeconds"])
        ):
            raise ControlError("REQUEST_TIME_INVALID")
        process_id = process_observation["processId"]
        start = parse_time(process_observation["processStartTime"])
        if start > now + dt.timedelta(seconds=5) or now - start > dt.timedelta(seconds=120):
            raise ControlError("PROCESS_IDENTITY_INVALID")

        profile_digest = digest_json(profile)
        inventory_digest = digest_json(inventory)
        broker_digest = _hash_file(Path(__file__).resolve())
        if inventory["brokerExecutableSha256"] != broker_digest:
            raise ControlError("BROKER_BINARY_DRIFT")
        if health["brokerExecutableSha256"] != broker_digest:
            raise ControlError("BROKER_HEALTH_BINDING_DRIFT")
        if health["projectProfileSha256"] != profile_digest:
            raise ControlError("BROKER_HEALTH_BINDING_DRIFT")
        if health["inventorySha256"] != inventory_digest:
            raise ControlError("BROKER_HEALTH_BINDING_DRIFT")

        policy = profile["policy"]
        observation_age = (now - parse_time(observation["observedAt"])).total_seconds()
        inventory_age = (now - parse_time(inventory["capturedAt"])).total_seconds()
        health_age = (now - parse_time(health["observedAt"])).total_seconds()
        if observation_age < -5 or observation_age > policy["maxObservationAgeSeconds"]:
            raise ControlError("CAPACITY_STALE")
        if inventory_age < -5 or inventory_age > policy["maxInventoryAgeSeconds"]:
            raise ControlError("INVENTORY_STALE")
        if health_age < -5 or health_age > policy["maxBrokerHealthAgeSeconds"]:
            raise ControlError("BROKER_HEALTH_STALE")

        gate, gate_transition = self._verified_gate_row(connection, fleet_secret=fleet_secret, now=now)
        if gate["state"] in {"CLOSED", "SHADOW"}:
            raise ControlError("AUTOMATIC_LAUNCH_GATE_CLOSED")
        if gate_transition is None:
            raise ControlError("GATE_TRANSITION_RECORD_INVALID")
        if gate["broker_digest"] != broker_digest:
            raise ControlError("GATE_BINDING_DRIFT")
        if gate["profile_digest"] != profile_digest:
            raise ControlError("GATE_PROFILE_DRIFT")
        if gate["inventory_digest"] != inventory_digest or gate["health_digest"] != digest_json(health):
            raise ControlError("GATE_BINDING_DRIFT")
        if gate["state"] == "CANARY":
            if request["canary"] is not True or request["manualAuthorizationSha256"] is None or manual_authorization is None:
                raise ControlError("CANARY_AUTHORIZATION_REQUIRED")
        elif request["canary"] and (request["manualAuthorizationSha256"] is None or manual_authorization is None):
            raise ControlError("CANARY_AUTHORIZATION_REQUIRED")

        if manual_authorization is not None:
            if digest_json(manual_authorization) != request["manualAuthorizationSha256"]:
                raise ControlError("CANARY_AUTHORIZATION_BINDING_DRIFT")
            auth_issued = parse_time(manual_authorization["issuedAt"])
            auth_expires = parse_time(manual_authorization["expiresAt"])
            if auth_issued > now + dt.timedelta(seconds=5) or auth_expires <= now or auth_expires <= auth_issued:
                raise ControlError("CANARY_AUTHORIZATION_STALE")
            if auth_expires - auth_issued > dt.timedelta(minutes=10):
                raise ControlError("CANARY_AUTHORIZATION_STALE")
            if (
                manual_authorization["requestBindingSha256"] != canary_request_binding(request)
                or manual_authorization["quotaDomainId"] != request["quotaDomainId"]
                or manual_authorization["projectProfileSha256"] != profile_digest
            ):
                raise ControlError("CANARY_AUTHORIZATION_BINDING_DRIFT")
            if connection.execute(
                "SELECT 1 FROM canary_authorizations WHERE authorization_id=? OR gate_epoch=?",
                (manual_authorization["authorizationId"], int(gate["transition_epoch"])),
            ).fetchone() is not None:
                raise ControlError("CANARY_EPOCH_ALREADY_CONSUMED")

        executable = _canonical_executable(request["executablePath"])
        executable_digest = _hash_file(executable)
        if executable_digest != request["executableSha256"]:
            raise ControlError("EXECUTABLE_DIGEST_DRIFT")
        matching = [
            launcher
            for launcher in inventory["launchers"]
            if os.path.normcase(str(_canonical_executable(launcher["executablePath"])))
            == os.path.normcase(str(executable))
            and launcher["executableSha256"] == executable_digest
        ]
        if len(matching) != 1:
            raise ControlError("LAUNCHER_NOT_IN_COMPLETE_INVENTORY")

        argv = request["argv"]
        if request["argvSha256"] != digest_json(argv):
            raise ControlError("ARGV_BINDING_DRIFT")
        if os.path.normcase(str(_canonical_executable(argv[0]))) != os.path.normcase(str(executable)):
            raise ControlError("ARGV_BINDING_DRIFT")
        bindings = request["argvBindings"]
        try:
            if (
                argv[bindings["modelIndex"]] != request["model"]
                or argv[bindings["effortIndex"]] != request["effort"]
                or argv[bindings["subjectIndex"]] != request["subjectSha256"]
            ):
                raise ControlError("ARGV_BINDING_DRIFT")
        except IndexError as exc:
            raise ControlError("ARGV_BINDING_DRIFT") from exc

        launcher_config = _canonical_executable(request["launcherConfigPath"])
        capsule = _canonical_executable(request["contextCapsulePath"])
        checkpoint = _canonical_executable(request["compactionCheckpointPath"])
        cache_manifest = _canonical_executable(request["cacheAffinityManifestPath"])
        if _hash_file(launcher_config) != request["launcherConfigSha256"]:
            raise ControlError("LAUNCHER_CONFIG_DRIFT")
        if _hash_file(capsule) != request["contextCapsuleSha256"]:
            raise ControlError("CONTEXT_CAPSULE_DRIFT")
        if capsule.stat().st_size > profile["policy"]["evidenceCapsuleMaxBytes"]:
            raise ControlError("CAPSULE_SIZE_LIMIT")
        if _hash_file(checkpoint) != request["compactionCheckpointSha256"]:
            raise ControlError("COMPACTION_CHECKPOINT_DRIFT")
        if _hash_file(cache_manifest) != request["cacheAffinityKeySha256"]:
            raise ControlError("CACHE_AFFINITY_DRIFT")

        subject = _canonical_executable(request["subjectPath"])
        if _hash_file(subject) != request["subjectSha256"]:
            raise ControlError("FROZEN_SUBJECT_DRIFT")
        if request["priorIdleFingerprint"] == request["demandFingerprint"]:
            raise ControlError("NO_ACTIONABLE_WORK")

        prepared_count = int(connection.execute(
            "SELECT COUNT(*) FROM leases WHERE state IN ('ACTIVE','RESUME_ATTESTED')"
        ).fetchone()[0])
        if prepared_count >= MAX_PREPARED_LEASES_PER_STATE_ROOT:
            raise ControlError("STATE_ROOT_LEASE_LIMIT")

        active = connection.execute(
            """SELECT reservations_json FROM leases WHERE state IN ('ACTIVE','RESUME_ATTESTED') AND (
                quota_domain_id=? OR session_id_hash=? OR (seat_id_hash=? AND seat_epoch=?))""",
            (request["quotaDomainId"], request["sessionIdHash"], request["seatIdHash"], request["seatEpoch"]),
        ).fetchall()
        if active:
            raise ControlError("QUOTA_DOMAIN_LEASE_HELD")

        required_dimensions = set(policy["requiredCapacityDimensions"][request["adapterVersion"]])
        estimates = request["windowEstimates"]
        if not required_dimensions.issubset(estimates):
            raise ControlError("CAPACITY_DIMENSION_MISSING")
        dimensions = {dimension["name"]: dimension for dimension in observation["dimensions"]}
        if not set(estimates).issubset(dimensions) or not required_dimensions.issubset(dimensions):
            raise ControlError("CAPACITY_DIMENSION_MISSING")
        quiet = dt.timedelta(seconds=policy["postResetQuietSeconds"])
        reserve = float(policy["reserveFloorByPriority"][request["priority"]])
        for name, estimate in estimates.items():
            dimension = dimensions[name]
            last_reset = parse_time(dimension["lastResetAt"])
            resets = parse_time(dimension["resetsAt"])
            if last_reset > now + dt.timedelta(seconds=5):
                raise ControlError("CAPACITY_TIME_INVALID")
            if now >= resets:
                raise ControlError("CAPACITY_WINDOW_ROLLED_OVER")
            if now - last_reset < quiet:
                raise ControlError("POST_RESET_QUIET")
            projected = float(dimension["usedFraction"]) + float(estimate)
            if projected > 1.0:
                raise ControlError("HARD_CAP_FORECAST")
            if projected > 1.0 - reserve:
                raise ControlError("PRIORITY_RESERVE_FORECAST")

        binding = {
            "requestId": request["requestId"],
            "quotaDomainId": request["quotaDomainId"],
            "executablePath": os.path.normcase(str(executable)),
            "executableSha256": executable_digest,
            "provider": request["provider"],
            "adapterVersion": request["adapterVersion"],
            "model": request["model"],
            "effort": request["effort"],
            "role": request["role"],
            "seatIdHash": request["seatIdHash"],
            "seatEpoch": request["seatEpoch"],
            "sessionIdHash": request["sessionIdHash"],
            "argvSha256": request["argvSha256"],
            "launcherConfigSha256": request["launcherConfigSha256"],
            "contextCapsuleSha256": request["contextCapsuleSha256"],
            "compactionCheckpointSha256": request["compactionCheckpointSha256"],
            "cacheAffinityKeySha256": request["cacheAffinityKeySha256"],
            "windowEstimates": estimates,
            "windowEstimatesSha256": digest_json(estimates),
            "subjectPath": os.path.normcase(str(subject)),
            "subjectSha256": request["subjectSha256"],
            "processId": process_id,
            "processStartTime": iso(start),
        }
        binding_digest = digest_json(binding)
        lease_id = "lease-" + uuid.uuid4().hex
        lease_expires = min(expires, now + dt.timedelta(seconds=request["maxWallSeconds"]))
        artifacts = (
            (executable, request["executableSha256"], MAX_ARTIFACT_BYTES),
            (launcher_config, request["launcherConfigSha256"], MAX_ARTIFACT_BYTES),
            (capsule, request["contextCapsuleSha256"], policy["evidenceCapsuleMaxBytes"]),
            (checkpoint, request["compactionCheckpointSha256"], MAX_ARTIFACT_BYTES),
            (cache_manifest, request["cacheAffinityKeySha256"], MAX_ARTIFACT_BYTES),
            (subject, request["subjectSha256"], MAX_ARTIFACT_BYTES),
        )
        artifact_handle_digest = self._open_artifact_handles(lease_id, artifacts)
        attestation = {
            "schema": "fleet-universal-launch-attestation/v1",
            "status": "PREPARED_SUSPENDED",
            "requestId": request["requestId"],
            "leaseId": lease_id,
            "quotaDomainId": request["quotaDomainId"],
            "issuedAt": iso(now),
            "expiresAt": iso(lease_expires),
            "gateEpoch": int(gate["transition_epoch"]),
            "gateTransitionSha256": digest_json(gate_transition),
            "processId": process_id,
            "processStartTime": iso(start),
            "seatIdHash": request["seatIdHash"],
            "seatEpoch": request["seatEpoch"],
            "sessionIdHash": request["sessionIdHash"],
            "executablePath": str(executable),
            "executableSha256": executable_digest,
            "argvSha256": request["argvSha256"],
            "launcherConfigSha256": request["launcherConfigSha256"],
            "windowEstimates": estimates,
            "windowEstimatesSha256": digest_json(estimates),
            "bindingSha256": binding_digest,
            "profileSha256": profile_digest,
            "inventorySha256": inventory_digest,
            "observationSha256": digest_json(observation),
            "processObservationSha256": digest_json(process_observation),
            "artifactHandleSetSha256": artifact_handle_digest,
        }
        validate_contract("attestation", attestation)
        self._acquire_os_lock(lease_id, request["quotaDomainId"])
        try:
            connection.execute(
                """INSERT INTO leases(
                    lease_id, request_id, quota_domain_id, process_id, process_start_time,
                    seat_id_hash, seat_epoch, session_id_hash, binding_digest, reservations_json,
                    issued_at, expires_at, state, terminal_digest, gate_epoch, is_canary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', NULL, ?, ?)""",
                (
                    lease_id, request["requestId"], request["quotaDomainId"], process_id, iso(start),
                    request["seatIdHash"], request["seatEpoch"], request["sessionIdHash"],
                    binding_digest, canonical_json(estimates), iso(now), iso(lease_expires),
                    int(gate["transition_epoch"]), 1 if request["canary"] else 0,
                ),
            )
            if manual_authorization is not None:
                connection.execute(
                    "INSERT INTO canary_authorizations(authorization_id, authorization_digest, request_id, gate_epoch) VALUES (?, ?, ?, ?)",
                    (manual_authorization["authorizationId"], digest_json(manual_authorization), request["requestId"], int(gate["transition_epoch"])),
                )
        except BaseException:
            self._release_terminal_owners(lease_id)
            raise
        return attestation

    def confirm_resume_boundary(
        self, *, lease_id: str, process_observation: Any, fleet_secret: bytes, now: dt.datetime
    ) -> dict[str, Any]:
        """Linearizable final admission boundary for one canonical state root."""

        with self._root_lock:
            poison_reason = self._cleanup_poison_reason()
            if poison_reason is not None:
                raise ControlError(poison_reason)
            return self._confirm_resume_boundary_root_locked(
                lease_id=lease_id,
                process_observation=process_observation,
                fleet_secret=fleet_secret,
                now=now,
            )

    def _confirm_resume_boundary_root_locked(
        self, *, lease_id: str, process_observation: Any, fleet_secret: bytes, now: dt.datetime
    ) -> dict[str, Any]:
        """Return launch authority only after a second fresh suspended-process/handle check."""

        at = now.astimezone(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                lease = connection.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
                if lease is None or lease["state"] != "ACTIVE":
                    raise ControlError("LEASE_NOT_PREPARED")
                if at >= parse_time(lease["expires_at"]):
                    if lease["is_canary"]:
                        self._seal_closed(connection)
                    # The claimant, reservation, OS lock, and retained handles remain fenced until
                    # authenticated terminal or DEAD recovery proof.  Only launch authority dies.
                    connection.execute("COMMIT")
                    raise ControlError("LEASE_EXPIRED_BEFORE_RESUME")
                gate, transition = self._verified_gate_row(connection, fleet_secret=fleet_secret, now=at)
                if transition is None or int(gate["transition_epoch"]) != int(lease["gate_epoch"]):
                    raise ControlError("GATE_BINDING_DRIFT")
                _verify_process_observation(
                    process_observation, fleet_secret=fleet_secret, now=at, phase="RESUME", lease=lease
                )
                prior = connection.execute(
                    "SELECT result_json FROM requests WHERE request_id=?", (lease["request_id"],)
                ).fetchone()
                if prior is None:
                    raise ControlError("REQUEST_REPLAY_STATE_INVALID")
                result = strict_json_bytes(prior["result_json"].encode("utf-8"))
                if result.get("status") != "PREPARED_SUSPENDED" or result.get("leaseId") != lease_id:
                    raise ControlError("REQUEST_REPLAY_STATE_INVALID")
                if (
                    process_observation["imageSha256"] != result["executableSha256"]
                    or os.path.normcase(str(_canonical_executable(process_observation["imagePath"])))
                    != os.path.normcase(result["executablePath"])
                    or process_observation["actualArgvSha256"] != result["argvSha256"]
                    or not self._os_lock_is_current(lease_id, lease["quota_domain_id"])
                    or not self._artifact_handles_are_current(lease_id)
                ):
                    raise ControlError("RESUME_BOUNDARY_DRIFT")
                result["status"] = "ALLOW_ATTESTED"
                result["processObservationSha256"] = digest_json(process_observation)
                validate_contract("attestation", result)
                connection.execute("UPDATE leases SET state='RESUME_ATTESTED' WHERE lease_id=?", (lease_id,))
                connection.execute(
                    "UPDATE requests SET result_json=? WHERE request_id=?",
                    (canonical_json(result), lease["request_id"]),
                )
                connection.execute("COMMIT")
                return result
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _seal_closed(connection: sqlite3.Connection) -> None:
        connection.execute(
            """UPDATE gate_state SET state='CLOSED', transition_digest=NULL,
            transition_bytes=NULL, transition_hmac=NULL, expires_at=NULL, broker_digest=NULL,
            profile_digest=NULL, inventory_digest=NULL, health_digest=NULL WHERE singleton=1"""
        )

    @staticmethod
    def _verify_terminal_artifact_binding(
        connection: sqlite3.Connection, lease: sqlite3.Row, process_observation: dict[str, Any]
    ) -> None:
        prior = connection.execute(
            "SELECT result_json FROM requests WHERE request_id=?", (lease["request_id"],)
        ).fetchone()
        if prior is None:
            raise ControlError("REQUEST_REPLAY_STATE_INVALID")
        attestation = strict_json_bytes(prior["result_json"].encode("utf-8"))
        if (
            attestation.get("leaseId") != lease["lease_id"]
            or process_observation["imageSha256"] != attestation.get("executableSha256")
            or process_observation["actualArgvSha256"] != attestation.get("argvSha256")
            or os.path.normcase(str(_canonical_executable(process_observation["imagePath"])))
            != os.path.normcase(str(attestation.get("executablePath")))
        ):
            raise ControlError("PROCESS_OBSERVATION_BINDING_DRIFT")

    def release_child(
        self, *, process_observation: Any, fleet_secret: bytes, now: dt.datetime
    ) -> dict[str, Any]:
        """Serialize terminal proof, lease publication, and cleanup-poison publication per root."""

        with self._root_lock:
            return self._release_child_root_locked(
                process_observation=process_observation, fleet_secret=fleet_secret, now=now
            )

    def _release_child_root_locked(
        self, *, process_observation: Any, fleet_secret: bytes, now: dt.datetime
    ) -> dict[str, Any]:
        """Release only from a fresh authenticated terminal observation of the exact claimant."""

        lease_id = process_observation.get("leaseId") if isinstance(process_observation, dict) else None
        if not isinstance(lease_id, str):
            raise ControlError("TERMINAL_EVIDENCE_INVALID")
        at = now.astimezone(UTC)
        ambiguous = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
                if row is None:
                    raise ControlError("LEASE_UNKNOWN")
                _verify_process_observation(
                    process_observation, fleet_secret=fleet_secret, now=at, phase="TERMINAL", lease=row
                )
                self._verify_terminal_artifact_binding(connection, row, process_observation)
                terminal_digest = digest_json(process_observation)
                if row["state"] == "RELEASED":
                    if row["terminal_digest"] != terminal_digest:
                        raise ControlError("LEASE_RELEASE_CONFLICT")
                elif process_observation["status"] == "AMBIGUOUS":
                    if row["is_canary"]:
                        self._seal_closed(connection)
                    ambiguous = True
                else:
                    connection.execute(
                        "UPDATE leases SET state='RELEASED', terminal_digest=? WHERE lease_id=?",
                        (terminal_digest, lease_id),
                    )
                    if row["is_canary"]:
                        self._seal_closed(connection)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        if ambiguous:
            raise ControlError("TERMINAL_PROCESS_AMBIGUOUS")
        self._release_terminal_owners(lease_id)
        return {"status": "RELEASED", "leaseId": lease_id}

    def recover_orphan(
        self,
        *,
        process_observation: Any,
        fleet_secret: bytes,
        now: dt.datetime,
    ) -> dict[str, Any]:
        """Serialize authenticated dead recovery and owner disposition per canonical root."""

        with self._root_lock:
            return self._recover_orphan_root_locked(
                process_observation=process_observation, fleet_secret=fleet_secret, now=now
            )

    def _recover_orphan_root_locked(
        self,
        *,
        process_observation: Any,
        fleet_secret: bytes,
        now: dt.datetime,
    ) -> dict[str, Any]:
        """Release only a proven-dead exact claimant; LIVE/AMBIGUOUS remains fenced."""

        at = now.astimezone(UTC)
        _enforce_complexity(process_observation)
        validate_contract("process_observation", process_observation)
        verify_contract_hmac(
            "process-observation-v1", process_observation, fleet_secret, "observerHmacSha256"
        )
        lease_id = process_observation["leaseId"]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
                if row is None:
                    raise ControlError("LEASE_UNKNOWN")
                _verify_process_observation(
                    process_observation, fleet_secret=fleet_secret, now=at, phase="RECOVERY", lease=row
                )
                self._verify_terminal_artifact_binding(connection, row, process_observation)
                if process_observation["status"] == "AMBIGUOUS" and row["is_canary"]:
                    self._seal_closed(connection)
                    connection.execute("COMMIT")
                    raise ControlError("ORPHAN_NOT_PROVEN_DEAD")
                if process_observation["status"] != "DEAD":
                    raise ControlError("ORPHAN_NOT_PROVEN_DEAD")
                connection.execute(
                    "UPDATE leases SET state='RELEASED', terminal_digest=? WHERE lease_id=?",
                    (digest_json(process_observation), lease_id),
                )
                if row["is_canary"]:
                    self._seal_closed(connection)
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        self._release_terminal_owners(lease_id)
        return {"status": "RELEASED", "leaseId": lease_id}


def _parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("kind", choices=sorted(SCHEMAS))
    validate.add_argument("input")
    normalize = sub.add_parser("normalize")
    normalize.add_argument("input")
    capsule = sub.add_parser("capsule")
    capsule.add_argument("request")
    capsule.add_argument("output")
    gate = sub.add_parser("gate-state")
    gate.add_argument("database")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "validate":
            value = strict_json_file(Path(args.input))
            validate_contract(args.kind, value)
            result = {"status": "PASS", "schema": SCHEMAS[args.kind]}
        elif args.command == "normalize":
            value = strict_json_file(Path(args.input))
            result = normalize_native_evidence(value)
        elif args.command == "capsule":
            value = strict_json_file(Path(args.request))
            result = build_evidence_capsule(value, Path(args.output))
        elif args.command == "gate-state":
            result = {"status": "PASS", "automaticLaunchGate": UniversalProviderBroker(Path(args.database)).gate_state()}
        else:  # pragma: no cover
            raise ControlError("ARGUMENT_ERROR")
        sys.stdout.write(canonical_json(result) + "\n")
        return 0
    except ControlError as exc:
        sys.stdout.write(canonical_json({"status": "UNEVALUABLE", "reason": exc.reason}) + "\n")
        return 2
    except BaseException:
        sys.stdout.write(canonical_json({"status": "UNEVALUABLE", "reason": "INTERNAL_FAILURE"}) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
