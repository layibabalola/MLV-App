"""Parity tests for the in-process Windows process probe.

The probe replaces a ``pwsh -Command "Get-CimInstance Win32_Process ..."`` call
on two supervisor hot paths.  Its output feeds fingerprint comparators that
score a mismatch as "this pid was reused" and terminate the process, so the
tests that matter are not "does it return something" but "does it return the
*same* thing the PowerShell probe returned".

The live-parity tests query real CIM and compare against real processes.  They
are skipped off Windows and skipped when CIM is unavailable, but they are the
only tests that can catch a formatting regression, so they are not replaced by
fixtures.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Match the other bridge suites before the first bare core import. Named
# unittest loading starts at the repo root; an ambient or editable bridge install
# otherwise wins and remains cached when the wrapper suite imports core.storage.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.win_process import (
    NATIVE_PROCESS_PROBE_AVAILABLE,
    NUL,
    filetime_to_cim_string,
    native_process_entry,
    native_process_table,
    normalize_command_line,
    normalize_executable_path,
)
from powershell_runtime import powershell_cim_command

WINDOWS_ONLY = unittest.skipUnless(sys.platform == "win32", "Windows-only probe")


class SuiteImportIsolationTests(unittest.TestCase):
    def test_named_suites_use_this_checkout_with_foreign_pythonpath(self):
        """Named unittest loading must not bind core to another bridge checkout."""
        bridge = Path(__file__).resolve().parent
        suites = [
            "tools.agent-bridge.test_win_process",
            "tools.agent-bridge.test_server_wrapper_phase2",
        ]
        probe = """
import importlib
import json
import sys
from pathlib import Path

for name in sys.argv[1:]:
    importlib.import_module(name)
from core.storage import StorageCapability
names = ('core', 'core.win_process', 'core.storage', 'powershell_runtime',
         'server', 'server_wrapper', 'server_wrapper_trampoline')
print(json.dumps({name: str(Path(importlib.import_module(name).__file__).resolve())
                  for name in names}))
"""
        # Inert disposable stand-in; never import or modify the real sibling repo.
        with tempfile.TemporaryDirectory(prefix="bridge-import-fixture-") as temp:
            foreign = Path(temp) / "core"
            foreign.mkdir()
            (foreign / "__init__.py").write_text(
                "raise RuntimeError('foreign core selected')\n", encoding="utf-8"
            )
            env = dict(os.environ, PYTHONPATH=temp, PYTHONDONTWRITEBYTECODE="1")
            for order in (suites, list(reversed(suites))):
                with self.subTest(first=order[0]):
                    result = subprocess.run(
                        [sys.executable, "-c", probe, *order],
                        cwd=bridge.parents[1], env=env, capture_output=True,
                        text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    origins = json.loads(result.stdout)
                    for name, origin in origins.items():
                        self.assertTrue(
                            Path(origin).is_relative_to(bridge),
                            "%s imported from %s instead of %s" % (name, origin, bridge),
                        )


def _cim_process_table():
    command = powershell_cim_command(
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine,ExecutablePath,CreationDate | "
        "ConvertTo-Json -Compress"
    )
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "text": True, "timeout": 120}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(command, **kwargs)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    rows = json.loads(proc.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    return {int(r["ProcessId"]): r for r in rows if r.get("ProcessId")}


class NormalizeExecutablePathTests(unittest.TestCase):
    def test_strips_extended_length_prefix(self):
        # CIM reports conhost with this prefix; QueryFullProcessImageNameW does not.
        self.assertEqual(
            normalize_executable_path(r"\\?\C:\WINDOWS\system32\conhost.exe"),
            r"C:\WINDOWS\system32\conhost.exe",
        )

    def test_strips_nt_object_prefix(self):
        self.assertEqual(
            normalize_executable_path(r"\??\C:\WINDOWS\system32\conhost.exe"),
            r"C:\WINDOWS\system32\conhost.exe",
        )

    def test_resolves_relative_segments(self):
        # CIM echoes the spawning command verbatim, including "..".
        self.assertEqual(
            normalize_executable_path(r"C:\Program Files\Git\bin\..\usr\bin\bash.exe"),
            r"C:\Program Files\Git\usr\bin\bash.exe",
        )

    def test_preserves_unc_paths(self):
        self.assertEqual(normalize_executable_path(r"\\server\share\app.exe"), r"\\server\share\app.exe")
        self.assertEqual(normalize_executable_path(r"\\?\UNC\server\share\app.exe"), r"\\server\share\app.exe")

    def test_empty_stays_empty(self):
        self.assertEqual(normalize_executable_path(""), "")

    def test_is_idempotent(self):
        once = normalize_executable_path(r"\\?\C:\Program Files\Git\bin\..\usr\bin\bash.exe")
        self.assertEqual(normalize_executable_path(once), once)


class NormalizeCommandLineTests(unittest.TestCase):
    """The PEB buffer can hold characters past the terminating NUL; CIM cannot.

    Both fixtures below are the *verbatim* strings a GitHub Windows runner
    produced on 2026-09-05 (run 33968940417): the native reader returned the
    full ``UNICODE_STRING.Length`` while ``Win32_Process.CommandLine`` returned
    only the leading path, and the parity arm reddened for two pids.
    """

    def test_wmi_provider_host_matches_cim(self):
        self.assertEqual(
            normalize_command_line(NUL.join([r"C:\Windows\system32\wbem\wmiprvse.exe", "-Embedding"])),
            r"C:\Windows\system32\wbem\wmiprvse.exe",
        )

    def test_cut_is_at_the_FIRST_nul_not_the_last(self):
        # The second runner pid carried two NULs.  Splitting on the last one
        # would have returned "...wmiprvse.exe\0-secured", which still differs
        # from CIM *and* still carries a NUL -- a fix that passes one fixture
        # and reproduces the defect on the other.
        self.assertEqual(
            normalize_command_line(
                NUL.join([r"C:\Windows\system32\wbem\wmiprvse.exe", "-secured", "-Embedding"])
            ),
            r"C:\Windows\system32\wbem\wmiprvse.exe",
        )

    def test_ordinary_command_line_is_untouched(self):
        line = r'"C:\Program Files\Git\bin\bash.exe" --login -i'
        self.assertEqual(normalize_command_line(line), line)

    def test_empty_stays_empty(self):
        self.assertEqual(normalize_command_line(""), "")

    def test_leading_nul_reads_as_unknown_not_as_content(self):
        # "" is *unknown* to every caller, which is the safe direction; the
        # unsafe one would be handing the comparators a NUL-prefixed string.
        self.assertEqual(normalize_command_line(NUL + "-Embedding"), "")

    def test_is_idempotent(self):
        once = normalize_command_line(NUL.join(["a.exe", "-x"]))
        self.assertEqual(normalize_command_line(once), once)


@WINDOWS_ONLY
class CommandLineCallSiteTests(unittest.TestCase):
    """Prove the reader *calls* the normalizer, not merely that it exists.

    Written because it was measured missing: with the normalizer defined and
    fully unit-tested but its call removed from ``_command_line`` -- the whole
    of the defect, restored -- this file still passed 35/35 on a developer
    host.  Every other arm that can catch that needs a live process whose PEB
    command line carries a NUL, and there were none among 362 live rows here;
    on CI there were two.  A fix whose *wiring* is only covered by an arm that
    cannot fire on the machine doing the editing is a fix that can be reverted
    by accident and stay green until the next runner sees it.

    So the ntdll path runs for real against this process's own handle, and only
    ``wstring_at`` -- the single call that materialises the buffer as a str --
    is stubbed, with the verbatim runner fixture.
    """

    def test_command_line_truncates_whatever_the_buffer_yields(self):
        from core import win_process

        handle = win_process._open_process(os.getpid())
        self.assertIsNotNone(handle, "could not open own process")
        raw = NUL.join([r"C:\Windows\system32\wbem\wmiprvse.exe", "-Embedding"])
        real = win_process.ctypes.wstring_at
        win_process.ctypes.wstring_at = lambda *a, **k: raw
        try:
            got = win_process._command_line(handle)
        finally:
            win_process.ctypes.wstring_at = real
        self.assertEqual(got, r"C:\Windows\system32\wbem\wmiprvse.exe")

    def test_the_stub_would_have_shown_the_defect(self):
        """A positive control: the same stub, normalizer bypassed, reproduces it.

        Without this, a `_command_line` that returned "" for an unrelated
        reason would pass the arm above and prove nothing.
        """
        from core import win_process

        handle = win_process._open_process(os.getpid())
        raw = NUL.join([r"C:\Windows\system32\wbem\wmiprvse.exe", "-Embedding"])
        real_wstring_at = win_process.ctypes.wstring_at
        real_normalize = win_process.normalize_command_line
        win_process.ctypes.wstring_at = lambda *a, **k: raw
        win_process.normalize_command_line = lambda text: text
        try:
            got = win_process._command_line(handle)
        finally:
            win_process.ctypes.wstring_at = real_wstring_at
            win_process.normalize_command_line = real_normalize
        self.assertEqual(got, raw)
        self.assertIn(NUL, got)


@WINDOWS_ONLY
class LiveCommandLineHygieneTests(unittest.TestCase):
    """No live row may carry a NUL, and this arm needs no CIM to say so.

    The parity arm that caught this in CI is gated on CIM being readable *and*
    on the two readers sharing a pid.  This one is gated on neither, so it
    reddens on any host where a WMI provider host (or anything else with a
    multi-part PEB command line) is running -- including a developer laptop,
    where the original defect was invisible for months.
    """

    def test_no_native_command_line_contains_a_nul(self):
        table = native_process_table()
        self.assertTrue(table, "native process table came back empty")
        offenders = sorted(
            (pid, repr(row.get("command_line") or ""))
            for pid, row in table.items()
            if NUL in (row.get("command_line") or "")
        )
        self.assertEqual(offenders, [], "%d row(s) carry an embedded NUL" % len(offenders))


class FiletimeFormatTests(unittest.TestCase):
    """CIM strips trailing zeros from the fractional second; isoformat() pads."""

    @staticmethod
    def _ticks(value):
        return value & 0xFFFFFFFF, value >> 32

    def test_zero_filetime_is_empty(self):
        self.assertEqual(filetime_to_cim_string(0, 0), "")

    def test_whole_second_carries_no_fraction(self):
        # A FILETIME landing exactly on a second must render with no fractional
        # part at all -- isoformat() would have written ".000000".
        base = 132000000000000000  # a whole second, in 100ns ticks
        text = filetime_to_cim_string(*self._ticks(base))
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

    def test_partial_fraction_is_kept(self):
        # 869240 microseconds -> ".86924", the five digits CIM emits.
        base = 132000000000000000 + 8692400
        text = filetime_to_cim_string(*self._ticks(base))
        self.assertIn(".86924", text)
        self.assertNotIn(".869240", text)

    def test_fraction_never_ends_in_zero(self):
        base = 132000000000000000
        for offset in range(0, 10000, 7):
            text = filetime_to_cim_string(*self._ticks(base + offset))
            time_part = text.split("+")[0].split("-")[0] if "+" in text else text.rsplit("-", 1)[0]
            if "." in time_part:
                fraction = time_part.split(".")[1]
                self.assertFalse(
                    fraction.endswith("0"),
                    "fraction %r keeps a trailing zero; CIM would have stripped it" % fraction,
                )


@WINDOWS_ONLY
class NativeProbeTests(unittest.TestCase):
    def test_probes_self(self):
        entry = native_process_entry(os.getpid())
        self.assertIsNotNone(entry)
        self.assertEqual(entry["pid"], os.getpid())
        self.assertTrue(entry["creation_date"])
        self.assertTrue(entry["executable_path"])
        self.assertEqual(entry["parent_pid"], os.getppid())

    def test_absent_pid_returns_none(self):
        self.assertIsNone(native_process_entry(0))
        self.assertIsNone(native_process_entry(-1))
        # A pid that cannot plausibly be live.
        self.assertIsNone(native_process_entry(0x7FFFFFF0))

    def test_spawns_no_child_process(self):
        """The whole point: this must not shell out."""
        real_run, real_popen = subprocess.run, subprocess.Popen

        def explode(*a, **k):
            raise AssertionError("native probe spawned a subprocess")

        subprocess.run, subprocess.Popen = explode, explode
        try:
            self.assertIsNotNone(native_process_entry(os.getpid()))
        finally:
            subprocess.run, subprocess.Popen = real_run, real_popen


@WINDOWS_ONLY
class LiveCimParityTests(unittest.TestCase):
    """The regression guard: native output must agree with CIM field-for-field.

    A drift here is what makes supervisors falsely conclude their host was
    replaced, so this compares against a live CIM snapshot rather than a
    recorded fixture.
    """

    @classmethod
    def setUpClass(cls):
        cls.cim = _cim_process_table()
        if not cls.cim:
            raise unittest.SkipTest("CIM query unavailable")
        cls.native = native_process_table()
        cls.common = sorted(set(cls.cim) & set(cls.native))
        if len(cls.common) < 20:
            raise unittest.SkipTest("too few comparable processes")

    def test_creation_date_is_byte_identical(self):
        """Compared byte-exactly by both comparators -- no normalization applies."""
        mismatches = [
            (pid, self.native[pid]["creation_date"], str(self.cim[pid].get("CreationDate") or ""))
            for pid in self.common
            if self.native[pid]["creation_date"] != str(self.cim[pid].get("CreationDate") or "")
        ]
        self.assertEqual(mismatches, [], "creation_date drifted from CIM for %d process(es)" % len(mismatches))

    def test_parent_pid_matches(self):
        mismatches = [
            pid for pid in self.common
            if self.native[pid]["parent_pid"] != int(self.cim[pid].get("ParentProcessId") or 0)
        ]
        self.assertEqual(mismatches, [])

    def test_executable_path_matches_after_normalization(self):
        mismatches = []
        for pid in self.common:
            want = str(self.cim[pid].get("ExecutablePath") or "")
            got = self.native[pid]["executable_path"]
            if not want or not got:
                continue  # an unknown on either side is skipped by the comparators
            if normalize_executable_path(got).casefold() != normalize_executable_path(want).casefold():
                mismatches.append((pid, got, want))
        self.assertEqual(mismatches, [], "executable_path drifted for %d process(es)" % len(mismatches))

    def test_command_line_matches_where_cim_could_read_it(self):
        """Native reads more command lines than CIM; it must never read one differently."""
        mismatches = []
        for pid in self.common:
            want = str(self.cim[pid].get("CommandLine") or "")
            got = self.native[pid]["command_line"]
            if not want or not got:
                continue
            if got != want:
                mismatches.append((pid, got, want))
        self.assertEqual(mismatches, [], "command_line drifted for %d process(es)" % len(mismatches))


@WINDOWS_ONLY
class ProbeDeniedTests(unittest.TestCase):
    """`None` from the probe means two different things; they must not be conflated."""

    def test_own_process_is_not_denied(self):
        from core.win_process import native_probe_denied

        self.assertFalse(native_probe_denied(os.getpid()))

    def test_absent_pid_is_not_denied(self):
        """A pid that is simply gone must not be reported as protected.

        Getting this backwards makes the lazy process table pay a CIM query --
        a PowerShell spawn -- on every poll for every stale lease.
        """
        from core.win_process import native_probe_denied

        self.assertFalse(native_probe_denied(0x7FFFFFF0))
        self.assertFalse(native_probe_denied(0))


@WINDOWS_ONLY
class NativeLivenessFallbackDecisionTests(unittest.TestCase):
    def test_unavailable_native_probe_is_uncertain(self):
        import core.win_process as probe

        with mock.patch.object(probe, "NATIVE_PROCESS_PROBE_AVAILABLE", False):
            self.assertTrue(probe.native_process_may_exist(42))
            self.assertFalse(probe.native_process_may_exist(0))
            self.assertFalse(probe.native_process_may_exist(-1))

    def test_only_invalid_pid_open_failure_confirms_absence(self):
        import core.win_process as probe

        for error, expected in ((87, False), (5, True), (6, True), (0, True)):
            with self.subTest(error=error), \
                    mock.patch.object(probe, "_open_process", return_value=None), \
                    mock.patch.object(probe.ctypes, "get_last_error", return_value=error):
                self.assertEqual(probe.native_process_may_exist(42), expected)

    def test_successful_exit_query_distinguishes_running_from_exited(self):
        import core.win_process as probe

        for exit_code, expected in ((259, True), (0, False), (7, False)):
            with self.subTest(exit_code=exit_code), \
                    mock.patch.object(probe, "_open_process", return_value=123), \
                    mock.patch.object(probe, "_kernel32") as kernel:
                def query(_handle, output):
                    output._obj.value = exit_code
                    return 1

                kernel.GetExitCodeProcess.side_effect = query
                self.assertEqual(probe.native_process_may_exist(42), expected)
                kernel.CloseHandle.assert_called_once_with(123)

    def test_failed_exit_query_preserves_uncertainty_and_closes_handle(self):
        import core.win_process as probe

        with mock.patch.object(probe, "_open_process", return_value=123), \
                mock.patch.object(probe, "_kernel32") as kernel:
            kernel.GetExitCodeProcess.return_value = 0
            self.assertTrue(probe.native_process_may_exist(42))
            kernel.CloseHandle.assert_called_once_with(123)


@WINDOWS_ONLY
class LazyProcessFallbackTests(unittest.TestCase):
    def test_live_process_without_native_identity_uses_cim(self):
        import core.win_process as probe
        import server_wrapper as wrapper

        row = {"pid": os.getpid(), "executable_path": sys.executable}
        # The real probe deliberately defers to CIM when servicing blanks the
        # image path. The still-live, openable PID must not become "absent".
        with mock.patch.object(probe, "_executable_path", return_value=""), \
                mock.patch.object(wrapper, "_cim_process_entry_from_system", return_value=(row, True)) as fallback:
            table = wrapper._LazyWindowsProcessTable()
            self.assertEqual(table.get(os.getpid()), row)
            fallback.assert_called_once_with(os.getpid())
            self.assertEqual(table.get(os.getpid()), row)
            fallback.assert_called_once()

    def test_failed_cim_does_not_cache_an_uncertain_pid_as_absent(self):
        import core.win_process as probe
        import server_wrapper as wrapper

        row = {"pid": os.getpid(), "executable_path": sys.executable}
        with mock.patch.object(probe, "_executable_path", return_value=""), \
                mock.patch.object(wrapper, "_cim_process_entry_from_system", side_effect=[(None, False), (row, True)]) as fallback:
            table = wrapper._LazyWindowsProcessTable()
            self.assertIsNone(table.get(os.getpid()))
            self.assertEqual(table.get(os.getpid()), row)
            self.assertEqual(fallback.call_count, 2)

    def test_successful_cim_no_row_is_cached_as_absent(self):
        import core.win_process as probe
        import server_wrapper as wrapper

        with mock.patch.object(probe, "_executable_path", return_value=""), \
                mock.patch.object(wrapper, "_cim_process_entry_from_system", return_value=(None, True)) as fallback:
            table = wrapper._LazyWindowsProcessTable()
            for _ in range(3):
                self.assertIsNone(table.get(os.getpid()))
            fallback.assert_called_once_with(os.getpid())

    def test_native_identity_can_recover_after_a_failed_cim_query(self):
        import core.win_process as probe
        import server_wrapper as wrapper

        table = wrapper._LazyWindowsProcessTable()
        with mock.patch.object(probe, "_executable_path", return_value=""), \
                mock.patch.object(wrapper, "_cim_process_entry_from_system", return_value=(None, False)) as fallback:
            self.assertIsNone(table.get(os.getpid()))
        self.assertEqual(table.get(os.getpid())["pid"], os.getpid())
        fallback.assert_called_once()

    def test_access_denied_still_uses_cim(self):
        import core.win_process as probe
        import server_wrapper as wrapper

        row = {"pid": 42, "executable_path": "protected.exe"}
        with mock.patch.object(probe, "_open_process", return_value=None), \
                mock.patch.object(probe.ctypes, "get_last_error", return_value=5), \
                mock.patch.object(wrapper, "_cim_process_entry_from_system", return_value=(row, True)) as fallback:
            self.assertEqual(wrapper._LazyWindowsProcessTable().get(42), row)
            fallback.assert_called_once_with(42)

    def test_cim_query_distinguishes_failures_from_successful_empty_result(self):
        import server_wrapper as wrapper

        cases = ((0, "", True), (1, "", False), (0, "broken-json", False),
                 (0, "[]", False))
        for code, output, succeeded in cases:
            with self.subTest(code=code, output=output), \
                    mock.patch.object(wrapper.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output)) as run:
                self.assertEqual(wrapper._cim_process_entry_from_system(42), (None, succeeded))
                self.assertIn("-ErrorAction Stop", run.call_args.args[0][-1])
        with mock.patch.object(wrapper.subprocess, "run", side_effect=subprocess.TimeoutExpired("cim", 5)):
            self.assertEqual(wrapper._cim_process_entry_from_system(42), (None, False))

    def test_lazy_table_does_not_cache_malformed_or_mismatched_cim_identity(self):
        import server_wrapper as wrapper

        for payload in ("{}", '{"ProcessId": 43}'):
            with self.subTest(payload=payload), \
                    mock.patch.object(wrapper, "native_process_entry", return_value=None), \
                    mock.patch.object(wrapper, "native_process_may_exist", return_value=True), \
                    mock.patch.object(wrapper.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload)) as run:
                table = wrapper._LazyWindowsProcessTable()
                self.assertIsNone(table.get(42))
                self.assertIsNone(table.get(42))
                self.assertEqual(run.call_count, 2)

    def test_cim_query_preserves_a_successful_identity_row(self):
        import server_wrapper as wrapper

        payload = json.dumps({"ProcessId": 42, "ParentProcessId": 7, "Name": "host.exe",
                              "ExecutablePath": "original.exe", "CommandLine": "host.exe --mcp",
                              "CreationDate": "stamp"})
        with mock.patch.object(wrapper.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload)):
            self.assertEqual(wrapper._cim_process_entry_from_system(42), ({
                "pid": 42, "parent_pid": 7, "name": "host.exe", "executable_path": "original.exe",
                "command_line": "host.exe --mcp", "creation_date": "stamp",
            }, True))


@WINDOWS_ONLY
class LazyProcessTableTests(unittest.TestCase):
    """The table must stay lazy for the hot path and honest for everything else."""

    def setUp(self):
        # Layout note: upstream keeps the lazy table in ``core.processes``; this
        # vendored copy predates that split and still keeps the process probes
        # in ``server_wrapper``. Resolve either, so the test keeps passing on
        # whichever side a future re-sync lands on.
        try:
            from core.processes import process_table_from_system
            import core.processes as processes
        except ImportError:
            import server_wrapper as processes

            process_table_from_system = processes._process_table_from_system

        self.table = process_table_from_system()
        self.spawns = 0

        self._processes = processes
        self._real_run = processes.subprocess.run

        def counting(*args, **kwargs):
            self.spawns += 1
            return self._real_run(*args, **kwargs)

        processes.subprocess.run = counting

    def tearDown(self):
        self._processes.subprocess.run = self._real_run

    def test_is_a_dict(self):
        # Callers annotate this as Dict[int, Dict[str, Any]] and pass it around.
        self.assertIsInstance(self.table, dict)

    def test_resolving_an_owned_pid_spawns_nothing(self):
        entry = self.table.get(os.getpid())
        self.assertIsNotNone(entry)
        self.assertEqual(entry["pid"], os.getpid())
        self.assertEqual(self.spawns, 0)

    def test_repeated_lookups_are_cached(self):
        for _ in range(200):
            self.table.get(os.getpid())
        self.assertEqual(self.spawns, 0)

    def test_absent_pid_spawns_nothing(self):
        self.assertIsNone(self.table.get(0x7FFFFFF0))
        self.assertEqual(self.spawns, 0)

    def test_absent_pid_is_not_requeried(self):
        for _ in range(50):
            self.table.get(0x7FFFFFF0)
        self.assertEqual(self.spawns, 0)

    def test_membership_matches_lookup(self):
        self.assertIn(os.getpid(), self.table)
        self.assertNotIn(0x7FFFFFF0, self.table)

    def test_getitem_raises_for_absent_pid(self):
        with self.assertRaises(KeyError):
            self.table[0x7FFFFFF0]

    def test_non_integer_key_is_absent_rather_than_raising(self):
        self.assertIsNone(self.table.get("not-a-pid"))
        self.assertIsNone(self.table.get(None))

    def test_iteration_materializes_the_full_snapshot(self):
        """Lazy, not lossy: anyone who really wants the whole table still gets it."""
        self.assertGreater(len(self.table), 100)
        self.assertIn(os.getpid(), set(self.table.keys()))


if __name__ == "__main__":
    unittest.main()
