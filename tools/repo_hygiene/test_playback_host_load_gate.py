"""Behavioural tests for PLAYBACK-MEASURE-HOST-LOAD-GATE-1.

BACHELOR is also the owner's interactive workstation: an fps number measured while it is loaded
is not a property of the build (measured 2026-09-22 -- CPU_LOAD_PCT=96 turned a 4.8fps CPU
reference into 1.2fps with identical route counters; see
.claude-state/project-memory/bachelor-is-an-interactive-workstation-load-invalidates-fps-20260922.md).
These tests EXECUTE pwsh against the real functions, not a reimplementation of their logic --
tools/profiling/run-release-gui-smoke.ps1, tools/profiling/compare-release-gui-smoke-ab.ps1 and
tools/profiling/run-release-cuda-playback-ab.ps1 are procedural .ps1 scripts (not modules), so
each test extracts the target function's exact source text out of the real file by regex and
dot-sources it into a throwaway probe script -- the same "spliced verbatim" precedent
test_playback_attr_3_cuda_behaviour.py documents: a pass here is a statement about the code that
actually runs the gate, not a copy of it. What is NOT exercised: a full GUI-smoke launch (needs a
real MLVApp.exe and clip) and a full CUDA A/B run (needs a 4090) -- those are covered by the
fixture-driven unit level only. Everything is skipped cleanly when pwsh is not on PATH.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = ROOT / "tools" / "profiling" / "run-release-gui-smoke.ps1"
COMPARE_SCRIPT = ROOT / "tools" / "profiling" / "compare-release-gui-smoke-ab.ps1"
CUDA_AB_SCRIPT = ROOT / "tools" / "profiling" / "run-release-cuda-playback-ab.ps1"
COMPARE_MACHINE_PERF_SCRIPT = ROOT / "tools" / "profiling" / "compare-machine-perf.ps1"
P3_VALIDATION_SCRIPT = ROOT / "tools" / "profiling" / "run-ultramagnus-p3-validation.ps1"
CUDA_PROOF_SUMMARIZER_SCRIPT = ROOT / "tools" / "profiling" / "summarize-local-cuda-proof.ps1"

PWSH = shutil.which("pwsh")
requires_pwsh = unittest.skipIf(PWSH is None, "pwsh is not on PATH")


def _extract_functions(script: Path, names: list[str]) -> str:
    """Regex-extract the exact `function <name> { ... }` bodies out of a real .ps1 file."""
    source = script.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        pattern = re.compile(
            r"function " + re.escape(name) + r" \{.*?\n\}\r?\n", re.DOTALL
        )
        match = pattern.search(source)
        if not match:
            raise AssertionError(f"could not extract function {name} from {script}")
        chunks.append(match.group(0))
    return "\n".join(chunks)


def _extract_block(source: str, start_marker: str, end_marker: str, trailing_lines: int = 1) -> str:
    """Literal-text-search extraction of a script-body block that is NOT inside a named function
    (so _extract_functions above cannot find it) -- e.g. a guard clause living directly in a
    foreach loop at script scope. Finds `start_marker`, then `end_marker` after it, then includes
    `trailing_lines` more full lines past the line containing `end_marker` (to capture closing
    braces). round 6: used to make the P3-import and local-proof-summarizer host-load guards
    EXECUTABLE regression targets instead of only string-index-order checks -- a real operator/
    literal mutation in the live source changes what gets extracted and run, the same way it would
    change what actually runs in production."""
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    for _ in range(1 + trailing_lines):
        end = source.index("\n", end) + 1
    return source[start:end]


class _ProbeCase(unittest.TestCase):
    """Base: writes an extracted-function probe script into a temp dir and runs snippets against it."""

    script: Path
    functions: list[str]

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hostload-probe-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.probe = self.tmp / "probe.ps1"
        self.probe.write_text(_extract_functions(self.script, self.functions), encoding="utf-8")

    def run_snippet(self, body: str) -> subprocess.CompletedProcess:
        script = self.tmp / "run.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f". '{self.probe}'\n" + body,
            encoding="utf-8",
        )
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True,
        )


@requires_pwsh
class HostLoadSnapshotTests(_ProbeCase):
    """tools/profiling/run-release-gui-smoke.ps1's Get-HostLoadSnapshot."""

    script = SMOKE_SCRIPT
    functions = ["Get-HostLoadSnapshot"]

    @unittest.skipUnless(
        os.name == "nt",
        "Get-HostLoadSnapshot's Win32_Processor/Win32_OperatingSystem CIM collection is "
        "Windows-only; on other hosts it correctly reports collected=False (see "
        "HostLoadSnapshotUnknownWhereCollectionIsImpossibleTests below).",
    )
    def test_real_snapshot_collects_or_reports_uncollectable_never_paths_or_command_lines(self) -> None:
        # round 4 (sol major): a restricted Windows host (locked-down CIM/WMI, no admin rights, a
        # sandboxed runner) can legitimately fail to collect telemetry -- this is a valid third
        # outcome on Windows too, not something the test should assume away. This review lane
        # itself observed collected=false on every direct probe. Accept either outcome; only
        # assert the shape and privacy properties that must hold regardless of which one occurs.
        proc = self.run_snippet(
            "$s = Get-HostLoadSnapshot -TopProcessCount 6\n"
            "Write-Host \"COLLECTED=$($s.collected)\"\n"
            "Write-Host \"PROC_COUNT=$($s.processCount)\"\n"
            "Write-Host \"TOP_COUNT=$($s.topCpuConsumers.Count)\"\n"
            "Write-Host \"TOP=$($s.topCpuConsumers -join '|')\"\n"
            "Write-Host \"ERROR=$($s.error)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertRegex(proc.stdout, r"COLLECTED=(True|False)")
        top_line = next(l for l in proc.stdout.splitlines() if l.startswith("TOP="))
        # Top consumers are recorded BY NAME ONLY: no path separators, no drive letters, no
        # command-line argument text -- this is host-load evidence, not process forensics. This
        # holds whether or not collection actually succeeded (an uncollectable snapshot's
        # topCpuConsumers is simply empty).
        for name in [n for n in top_line[len("TOP="):].split("|") if n]:
            self.assertNotIn("\\", name)
            self.assertNotIn("/", name)
            self.assertNotIn(":", name)
        if "COLLECTED=True" in proc.stdout:
            self.assertRegex(proc.stdout, r"PROC_COUNT=\d+")
            self.assertIn("ERROR=", proc.stdout)
        else:
            self.assertIn("COLLECTED=False", proc.stdout)

    def test_snapshot_records_a_timestamp(self) -> None:
        proc = self.run_snippet(
            "$s = Get-HostLoadSnapshot -TopProcessCount 1\n"
            "Write-Host \"CAPTURED=$($s.capturedAtUtc)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertRegex(proc.stdout, r"CAPTURED=\d{4}-\d{2}-\d{2}T")


@requires_pwsh
@unittest.skipUnless(
    os.name != "nt",
    "exercises the real Get-HostLoadSnapshot on a host where its Windows-only CIM "
    "collection is expected to fail -- on Windows itself collection normally succeeds, "
    "so that path is covered by HostLoadSnapshotTests above instead.",
)
class HostLoadSnapshotUnknownWhereCollectionIsImpossibleTests(_ProbeCase):
    """A host that cannot collect telemetry must classify as UNKNOWN/PROVISIONAL, never
    'quiet' -- this is the round-1 card's own third-state rule, exercised end to end
    against the real Get-HostLoadSnapshot + Get-HostLoadVerdict functions rather than a
    fixture standing in for a collection failure."""

    script = SMOKE_SCRIPT
    functions = ["Get-HostLoadSnapshot", "Get-HostLoadVerdict"]

    def test_real_uncollectable_snapshot_is_classified_unknown_and_provisional(self) -> None:
        proc = self.run_snippet(
            "$before = Get-HostLoadSnapshot -TopProcessCount 1\n"
            "$after = Get-HostLoadSnapshot -TopProcessCount 1\n"
            "Write-Host \"BEFORE_COLLECTED=$($before.collected)\"\n"
            "Write-Host \"AFTER_COLLECTED=$($after.collected)\"\n"
            "$v = Get-HostLoadVerdict -Before $before -After $after -Bar 75\n"
            "Write-Host \"STATE=$($v.state) PROVISIONAL=$($v.provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("BEFORE_COLLECTED=False", proc.stdout)
        self.assertIn("AFTER_COLLECTED=False", proc.stdout)
        self.assertIn("STATE=unknown PROVISIONAL=True", proc.stdout)
        self.assertNotIn("STATE=quiet", proc.stdout)
        self.assertNotIn("STATE=exceeded", proc.stdout)

    def test_uncollectable_snapshot_error_is_sanitized_not_raw_exception_text(self) -> None:
        # round 4 (sol minor, fable minor): $_.Exception.Message can embed a path, server name,
        # or namespace. Record only the exception's TYPE -- this test exercises the real catch
        # path (CIM is unavailable outside Windows) and asserts the exact narrow shape, which by
        # construction cannot contain a path separator or drive letter.
        proc = self.run_snippet(
            "$s = Get-HostLoadSnapshot -TopProcessCount 1\n"
            "Write-Host \"ERROR=$($s.error)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        error_line = next(l for l in proc.stdout.splitlines() if l.startswith("ERROR="))
        error_text = error_line[len("ERROR="):]
        self.assertRegex(error_text, r"^collection failed: [A-Za-z0-9_.]+$")
        self.assertNotIn("\\", error_text)
        self.assertNotIn("/", error_text)


@requires_pwsh
class HostLoadSnapshotPrivacyTests(_ProbeCase):
    """round 4 (sol minor, fable minor): the privacy test previously inspected only
    topCpuConsumers; sol's finding is that the FULLY SERIALIZED snapshot (where an unsanitized
    exception message would actually surface in a receipt) was never checked."""

    script = SMOKE_SCRIPT
    functions = ["Get-HostLoadSnapshot"]

    def test_full_serialized_snapshot_carries_no_path_or_drive_text(self) -> None:
        proc = self.run_snippet(
            "$s = Get-HostLoadSnapshot -TopProcessCount 6\n"
            "$s | ConvertTo-Json -Depth 6 -Compress\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        serialized = json.dumps(payload)
        self.assertNotIn("\\", serialized)
        self.assertNotIn("C:", serialized)
        self.assertNotIn("/", serialized)


@requires_pwsh
class HostLoadVerdictTests(_ProbeCase):
    """tools/profiling/run-release-gui-smoke.ps1's Get-HostLoadVerdict -- the bar and the three
    outcomes (quiet / exceeded / unknown), never two."""

    script = SMOKE_SCRIPT
    functions = ["Get-HostLoadVerdict"]

    def _verdict(self, before: str, after: str, bar: float = 75) -> subprocess.CompletedProcess:
        return self.run_snippet(
            f"$before = {before}\n"
            f"$after = {after}\n"
            f"$v = Get-HostLoadVerdict -Before $before -After $after -Bar {bar}\n"
            "Write-Host \"STATE=$($v.state) PROVISIONAL=$($v.provisional) "
            "MAX=$($v.maxCpuLoadPercent) REASON=$($v.reason)\"\n"
        )

    def test_quiet_host_is_not_provisional(self) -> None:
        proc = self._verdict(
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }",
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 15.0 }",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=quiet PROVISIONAL=False", proc.stdout)

    def test_fixture_mirroring_the_measured_96_percent_sample_exceeds_the_bar(self) -> None:
        # 2026-09-22T17:55Z evidence: CPU_LOAD_PCT=96 on BACHELOR turned a 4.8fps reference into
        # 1.2fps. The declared bar must fail this sample -- that is the card's own acceptance test.
        proc = self._verdict(
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 40.0 }",
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0 }",
            bar=75,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=exceeded PROVISIONAL=True", proc.stdout)
        self.assertIn("MAX=96", proc.stdout)
        self.assertIn("is not a property of the build", proc.stdout)

    def test_unknown_telemetry_is_provisional_never_quiet(self) -> None:
        # RESUME.md STEP 4: "when you add a check, name its three outcomes." Telemetry that could
        # not be collected must never silently read as a passing "quiet" host.
        proc = self._verdict(
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }",
            "[pscustomobject]@{ collected = $false; cpuLoadPercent = $null }",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=unknown PROVISIONAL=True", proc.stdout)
        self.assertNotIn("STATE=quiet", proc.stdout)

    def test_both_snapshots_unknown_is_still_unknown_not_exceeded(self) -> None:
        proc = self._verdict(
            "[pscustomobject]@{ collected = $false; cpuLoadPercent = $null }",
            "[pscustomobject]@{ collected = $false; cpuLoadPercent = $null }",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=unknown PROVISIONAL=True", proc.stdout)

    def test_mid_leg_burst_is_missed_by_bracketing_alone_but_caught_by_sampling(self) -> None:
        # PLAYBACK-MEASURE-HOST-LOAD-GATE-1 round 3: this is the exact case in point. A burst
        # that starts after the before-snapshot and ends before the after-snapshot is invisible
        # to bracketing alone (round 2's behaviour -- calling Get-HostLoadVerdict with no -During
        # samples) but must be caught once interior samples are supplied (round 3's behaviour).
        # The rule is PEAK across all samples, not average or sustained-for-N: a single 96%
        # interior sample marks the whole leg exceeded/provisional even though before/after are
        # both quiet.
        before = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        after = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 14.0 }"
        burst_during = (
            "@("
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 11.0 }, "
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0 }, "
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 13.0 }"
            ")"
        )
        proc = self.run_snippet(
            f"$before = {before}\n"
            f"$after = {after}\n"
            f"$during = {burst_during}\n"
            "$bracketedOnly = Get-HostLoadVerdict -Before $before -After $after -Bar 75\n"
            "Write-Host \"BRACKETED_STATE=$($bracketedOnly.state) "
            "BRACKETED_PROVISIONAL=$($bracketedOnly.provisional)\"\n"
            "$sampled = Get-HostLoadVerdict -Before $before -After $after -During $during -Bar 75\n"
            "Write-Host \"SAMPLED_STATE=$($sampled.state) "
            "SAMPLED_PROVISIONAL=$($sampled.provisional) SAMPLED_MAX=$($sampled.maxCpuLoadPercent)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("BRACKETED_STATE=quiet BRACKETED_PROVISIONAL=False", proc.stdout)
        self.assertIn("SAMPLED_STATE=exceeded SAMPLED_PROVISIONAL=True", proc.stdout)
        self.assertIn("SAMPLED_MAX=96", proc.stdout)

    def test_an_uncollected_interior_sample_is_unknown_not_silently_dropped(self) -> None:
        before = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }"
        after = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }"
        during = "@([pscustomobject]@{ collected = $false; cpuLoadPercent = $null })"
        proc = self.run_snippet(
            f"$before = {before}\n$after = {after}\n$during = {during}\n"
            "$v = Get-HostLoadVerdict -Before $before -After $after -During $during -Bar 75\n"
            "Write-Host \"STATE=$($v.state) PROVISIONAL=$($v.provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=unknown PROVISIONAL=True", proc.stdout)

    def _verdict_with_sampling(
        self, before: str, after: str, during: str, sample_interval_ms, bar: float = 75
    ) -> subprocess.CompletedProcess:
        interval_literal = "$null" if sample_interval_ms is None else str(sample_interval_ms)
        return self.run_snippet(
            f"$before = {before}\n"
            f"$after = {after}\n"
            f"$during = {during}\n"
            f"$v = Get-HostLoadVerdict -Before $before -After $after -During $during "
            f"-Bar {bar} -SampleIntervalMs {interval_literal}\n"
            "Write-Host \"STATE=$($v.state) PROVISIONAL=$($v.provisional) REASON=$($v.reason)\"\n"
        )

    def test_sampling_disabled_forces_unknown_even_when_bracket_is_quiet(self) -> None:
        # round 4 (sol BLOCKER), repro case 1: "Run a leg with HostLoadSampleIntervalMs=0 ...
        # while before/after snapshots remain below 75%; ... Get-HostLoadVerdict returns
        # quiet/provisional=false." Declaring SampleIntervalMs=0 must now force unknown/provisional
        # even though bracketing alone reads quiet.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        proc = self._verdict_with_sampling(quiet, quiet, "@()", sample_interval_ms=0)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=unknown PROVISIONAL=True", proc.stdout)
        self.assertIn("sampling was disabled", proc.stdout)

    def test_sampling_enabled_but_zero_interior_samples_forces_unknown(self) -> None:
        # round 4 (sol BLOCKER), repro case 2: a leg short enough that not even one interior tick
        # occurred never rose above bracket-only coverage either, even though sampling was
        # nominally enabled.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        proc = self._verdict_with_sampling(quiet, quiet, "@()", sample_interval_ms=4000)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=unknown PROVISIONAL=True", proc.stdout)
        self.assertIn("collected zero samples", proc.stdout)

    def test_sampling_enabled_with_interior_samples_stays_quiet_when_actually_quiet(self) -> None:
        # Regression guard: once sampling is enabled AND it actually produced interior coverage,
        # a genuinely quiet leg must still read quiet -- the new coverage rule must not turn every
        # leg provisional.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        during = "@([pscustomobject]@{ collected = $true; cpuLoadPercent = 11.0 })"
        proc = self._verdict_with_sampling(quiet, quiet, during, sample_interval_ms=4000)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=quiet PROVISIONAL=False", proc.stdout)

    def test_sampling_enabled_still_catches_a_covered_burst(self) -> None:
        # Regression guard: the coverage rule must not weaken the round-3 peak-of-samples catch
        # for a burst that WAS covered by an interior tick.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        during = "@([pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0 })"
        proc = self._verdict_with_sampling(quiet, quiet, during, sample_interval_ms=4000)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=exceeded PROVISIONAL=True", proc.stdout)

    def test_callers_that_omit_sample_interval_ms_are_unaffected(self) -> None:
        # Backward compatibility: every caller/fixture that predates round 4 (and every other
        # round-3 test in this file) never passes -SampleIntervalMs at all; the new coverage rule
        # must stay inert for them, preserving bracket-only round-2/3 semantics exactly.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        proc = self._verdict_with_sampling(quiet, quiet, "@()", sample_interval_ms=None)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=quiet PROVISIONAL=False", proc.stdout)

    def _verdict_with_gap(
        self, before: str, after: str, during: str, sample_interval_ms, observed_gap_ms, bar: float = 75
    ) -> subprocess.CompletedProcess:
        interval_literal = "$null" if sample_interval_ms is None else str(sample_interval_ms)
        gap_literal = "$null" if observed_gap_ms is None else str(observed_gap_ms)
        return self.run_snippet(
            f"$before = {before}\n"
            f"$after = {after}\n"
            f"$during = {during}\n"
            f"$v = Get-HostLoadVerdict -Before $before -After $after -During $during "
            f"-Bar {bar} -SampleIntervalMs {interval_literal} -ObservedMaxSampleGapMs {gap_literal}\n"
            "Write-Host \"STATE=$($v.state) PROVISIONAL=$($v.provisional) REASON=$($v.reason)\"\n"
        )

    def test_observed_sample_gap_far_exceeding_declared_cadence_forces_unknown(self) -> None:
        # round 5 (sol MAJOR), exact repro: a 4000ms wait followed by a 3000ms OnSample callback
        # puts ~7000ms between snapshot starts while the declared cadence was 4000ms -- a 1.75x
        # overrun. Coverage cannot certify quiet when the caller's own disclosed bound was not
        # honored.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        during = "@([pscustomobject]@{ collected = $true; cpuLoadPercent = 11.0 })"
        proc = self._verdict_with_gap(quiet, quiet, during, sample_interval_ms=4000, observed_gap_ms=7000)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=unknown PROVISIONAL=True", proc.stdout)
        self.assertIn("did not actually meet the declared cadence", proc.stdout)

    def test_observed_sample_gap_within_tolerance_of_declared_cadence_stays_quiet(self) -> None:
        # Regression guard: ordinary scheduling jitter (well under the 1.5x tolerance) must not
        # turn every real leg provisional.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        during = "@([pscustomobject]@{ collected = $true; cpuLoadPercent = 11.0 })"
        proc = self._verdict_with_gap(quiet, quiet, during, sample_interval_ms=4000, observed_gap_ms=4200)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=quiet PROVISIONAL=False", proc.stdout)

    def test_missing_observed_sample_gap_is_backward_compatible(self) -> None:
        # Every test above this one, and every caller written before round 5, never passes
        # -ObservedMaxSampleGapMs at all -- the new spacing rule must stay inert for them.
        quiet = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 12.0 }"
        during = "@([pscustomobject]@{ collected = $true; cpuLoadPercent = 11.0 })"
        proc = self._verdict_with_gap(quiet, quiet, during, sample_interval_ms=4000, observed_gap_ms=None)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=quiet PROVISIONAL=False", proc.stdout)

    def test_cpu_heavy_subject_on_a_quiet_host_is_not_provisional(self) -> None:
        # round 4 (fable minor), required test 1: "a CPU-heavy SUBJECT on a quiet host must NOT
        # be provisional." A sample carrying a high raw cpuLoadPercent but a low
        # nonSubjectCpuLoadPercent (the rest of the host is quiet; the load is the measured
        # process's own legitimate decode work) must read quiet.
        before = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }"
        after = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }"
        during = (
            "@([pscustomobject]@{ collected = $true; cpuLoadPercent = 92.0; "
            "nonSubjectCpuLoadPercent = 8.0 })"
        )
        proc = self._verdict_with_sampling(before, after, during, sample_interval_ms=4000)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=quiet PROVISIONAL=False", proc.stdout)

    def test_busy_host_is_still_provisional_even_with_a_quiet_subject(self) -> None:
        # round 4 (fable minor), required test 2: "a busy host must be [provisional]." A sample
        # whose OTHER processes (not the subject) are driving the load must still exceed the bar.
        before = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }"
        after = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 10.0 }"
        during = (
            "@([pscustomobject]@{ collected = $true; cpuLoadPercent = 92.0; "
            "nonSubjectCpuLoadPercent = 90.0 })"
        )
        proc = self._verdict_with_sampling(before, after, during, sample_interval_ms=4000)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=exceeded PROVISIONAL=True", proc.stdout)


@requires_pwsh
class HostLoadNonSubjectCpuLoadPercentTests(_ProbeCase):
    """tools/profiling/run-release-gui-smoke.ps1's Get-HostLoadNonSubjectCpuLoadPercent."""

    script = SMOKE_SCRIPT
    # round 6: the function now depends on Get-HostLoadProcessCpuSecondsPairs to normalize
    # processCpuSecondsById -- both must be spliced into the probe.
    functions = ["Get-HostLoadProcessCpuSecondsPairs", "Get-HostLoadNonSubjectCpuLoadPercent"]

    def _percent(self, current: str, previous: str, processor_count: int = 4) -> subprocess.CompletedProcess:
        return self.run_snippet(
            f"$current = {current}\n"
            f"$previous = {previous}\n"
            f"$p = Get-HostLoadNonSubjectCpuLoadPercent -CurrentSnapshot $current "
            f"-PreviousSnapshot $previous -ProcessorCount {processor_count}\n"
            "Write-Host \"PERCENT=$p\"\n"
        )

    def test_subject_consuming_all_the_load_leaves_non_subject_near_zero(self) -> None:
        # Over 4 elapsed seconds on a 4-core host, the subject accrued 4*4=16 processor-seconds --
        # 100% of the machine's capacity for that window -- while raw cpuLoadPercent read 100%.
        # round 6: totalCpuSeconds/processCpuSecondsById now required (with unreadableProcessCount
        # = 0) for the matched-window path -- a single process (the subject, pid 1) accounts for
        # the entire load here, so its matched delta equals subjectCpuSeconds delta (16.0).
        current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 100.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 16.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 16.0 } }"
        )
        previous = (
            "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0 } }"
        )
        proc = self._percent(current, previous, processor_count=4)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=0", proc.stdout)

    def test_no_subject_tracking_falls_back_to_raw_load(self) -> None:
        current = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 55.0; capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = $null }"
        previous = "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = $null }"
        proc = self._percent(current, previous)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=55", proc.stdout)

    def test_missing_previous_sample_falls_back_to_raw_load(self) -> None:
        current = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 55.0; capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 2.0 }"
        proc = self._percent(current, "$null")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=55", proc.stdout)

    def test_non_positive_elapsed_time_falls_back_to_raw_load(self) -> None:
        current = "[pscustomobject]@{ collected = $true; cpuLoadPercent = 55.0; capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 2.0 }"
        previous = "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0 }"
        proc = self._percent(current, previous)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=55", proc.stdout)

    def test_missing_process_cpu_seconds_by_id_falls_back_to_raw_load_not_mismatched_subtraction(self) -> None:
        # round 5 (sol MAJOR): sol's exact repro, in the card's own units -- raw cpuLoadPercent=96,
        # four elapsed seconds, four processors, subjectCpuSeconds delta=12.8. Round 4's
        # subtraction (a POINT raw sample minus an INTERVAL-AVERAGE subject share) produced
        # subjectPercent=80 -> nonSubject=16, reading a 96% sample as quiet under a 75% bar. This
        # object has no processCpuSecondsById at all (exactly what round 4's fixtures, and any
        # pre-round-6 caller, look like) -- round 6 requires processCpuSecondsById on both sides
        # for ANY subtraction, so this now falls straight back to the RAW 96, which correctly
        # exceeds a 75% bar instead of being silently masked.
        current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 12.8 }"
        )
        previous = (
            "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0 }"
        )
        proc = self._percent(current, previous, processor_count=4)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=96", proc.stdout)

    def test_matched_window_subtracts_commensurate_cumulative_deltas(self) -> None:
        # round 5 (fable minor -- matched-window subtraction), updated round 6 for the
        # per-process-keyed shape. 4s tick, 8 processors (capacity 32 processor-seconds). The
        # subject (pid 1) burned 16 processor-seconds over the interval (50% of capacity); one
        # other process (pid 2) burned 8 processor-seconds (present in both snapshots, delta
        # 8 = 24-16... i.e. together subject+other = 24 processor-seconds, 75% of capacity, e.g. an
        # exogenous burst diluted across the same window). Both processes are present in BOTH
        # snapshots, so their matched deltas sum exactly like the old aggregate diff did for this
        # no-churn case: nonSubject = (24-16)/32*100 = 25%.
        current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 60.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 16.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 16.0; '2' = 8.0 } }"
        )
        previous = (
            "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0; '2' = 0.0 } }"
        )
        proc = self._percent(current, previous, processor_count=8)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=25", proc.stdout)

    def test_unreadable_process_on_either_bracket_falls_back_to_raw_not_an_undercount(self) -> None:
        # round 6 (astra MAJOR, exact repro): "a raw 96% interior sample, four seconds/four
        # processors, and only the subject's 0.16 CPU-seconds readable" -- every OTHER process's
        # TotalProcessorTime getter throws. The old code silently skipped those failures and
        # totalCpuSeconds ended up equal to subjectCpuSeconds, so the subtraction produced
        # effective=0 (quiet). unreadableProcessCount > 0 on the current snapshot must now refuse
        # the exclusion outright and fall back to RAW 96, which correctly exceeds a 75% bar.
        current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 0.16; "
            "unreadableProcessCount = 3; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.16 } }"
        )
        previous = (
            "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0 } }"
        )
        proc = self._percent(current, previous, processor_count=4)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=96", proc.stdout)

    def test_unreadable_process_on_the_previous_bracket_also_falls_back_to_raw(self) -> None:
        # round 6: incompleteness on EITHER side of the window is disqualifying -- a clean current
        # snapshot cannot rescue a previous snapshot that could not fully account for its processes.
        current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 90.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 1.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 1.0 } }"
        )
        previous = (
            "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 2; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0 } }"
        )
        proc = self._percent(current, previous, processor_count=4)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=90", proc.stdout)

    def test_a_process_that_exited_between_snapshots_cannot_cancel_a_survivors_usage(self) -> None:
        # round 6 (sol BLOCKER: "differencing two totals over a CHANGING process set lets
        # exited-process history cancel current usage"). Previous snapshot: pid 1 (subject, 0.0s)
        # and pid 999 (a short-lived process that had already burned 50.0 processor-seconds by the
        # previous snapshot, then exited before the current one was taken). Current snapshot: pid 1
        # (subject, 4.0s) and pid 2 (a NEW process that started after the previous snapshot, 12.0s)
        # -- pid 999 is simply absent, not present with a lower value. Under the OLD aggregate-diff
        # approach this would have been totalCurrent=16.0 minus totalPrevious=50.0 = -34.0, clamped
        # to 0 -- pid 999's past history completely hides pid 2's real, current 12.0s of usage. The
        # matched-BY-PID approach only sums pid 1 (delta 4.0, all subject) because pid 2 and pid 999
        # are each present in only one snapshot and contribute nothing: nonSubject = (4.0-4.0)/16
        # = 0%. This is deliberately NOT a claim that pid 2's usage is captured (it cannot be, from
        # only two snapshots) -- it is the claim that pid 999's exit no longer produces a NEGATIVE,
        # clamped-to-zero total that could mask unrelated genuine usage elsewhere. See the
        # complementary Get-HostLoadVerdict-level test below for why raw is what actually protects
        # this case end to end.
        current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 4.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 4.0; '2' = 12.0 } }"
        )
        previous = (
            "[pscustomobject]@{ capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0; '999' = 50.0 } }"
        )
        proc = self._percent(current, previous, processor_count=4)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PERCENT=0", proc.stdout)


@requires_pwsh
class HostLoadPartialCollectionEndToEndVerdictTests(_ProbeCase):
    """round 6 (sol BLOCKER, astra MAJOR): chains Get-HostLoadNonSubjectCpuLoadPercent's output
    into Get-HostLoadVerdict exactly the way run-release-gui-smoke.ps1's real $hostLoadOnSample
    scriptblock does (compute nonSubjectCpuLoadPercent from two chronological snapshots, Add-Member
    it onto the later sample, then judge the bar on that sample -- see run-release-gui-smoke.ps1
    around line 1450) -- proving BOTH keys' exact partial-collection repros no longer read quiet
    end to end, not just at the helper-function level tested above."""

    script = SMOKE_SCRIPT
    functions = [
        "Get-HostLoadProcessCpuSecondsPairs",
        "Get-HostLoadNonSubjectCpuLoadPercent",
        "Get-HostLoadVerdict",
    ]

    def _verdict_via_real_wiring(self, before: str, during_current: str, bar: float = 75) -> subprocess.CompletedProcess:
        return self.run_snippet(
            f"$before = {before}\n"
            f"$duringSample = {during_current}\n"
            "$nonSubject = Get-HostLoadNonSubjectCpuLoadPercent -CurrentSnapshot $duringSample "
            "-PreviousSnapshot $before -ProcessorCount 4\n"
            "$duringSample | Add-Member -MemberType NoteProperty -Name nonSubjectCpuLoadPercent "
            "-Value $nonSubject\n"
            f"$v = Get-HostLoadVerdict -Before $before -After $before -During @($duringSample) -Bar {bar}\n"
            "Write-Host \"STATE=$($v.state) PROVISIONAL=$($v.provisional) "
            "NONSUBJECT=$nonSubject MAX=$($v.maxCpuLoadPercent)\"\n"
        )

    def test_astra_exact_repro_unreadable_processes_no_longer_reads_quiet(self) -> None:
        # astra's exact repro: "a raw 96% interior sample, four seconds/four processors, and only
        # the subject's 0.16 CPU-seconds readable" -- three other processes' TotalProcessorTime
        # getters throw. Old behaviour: totalCpuSeconds silently ended up equal to subjectCpuSeconds
        # (the undercount), so nonSubject computed to 0 and the leg read quiet under a 75% bar.
        before = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 20.0; "
            "capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0 } }"
        )
        during_current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 0.16; "
            "unreadableProcessCount = 3; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.16 } }"
        )
        proc = self._verdict_via_real_wiring(before, during_current)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NONSUBJECT=96", proc.stdout)
        self.assertIn("STATE=exceeded PROVISIONAL=True", proc.stdout)
        self.assertNotIn("STATE=quiet", proc.stdout)

    def test_sols_exact_repro_undercounted_total_no_longer_reads_quiet(self) -> None:
        # sol's exact repro numbers, round 6 brief: "raw 96, subject delta 0, total delta 3.84, 4
        # procs -> read quiet." Constructed as an incomplete snapshot -- several CPU-consuming
        # processes unreadable, leaving only a small readable remainder (3.84 processor-seconds
        # worth from one other process) that made the old aggregate totalCpuSeconds look
        # deceptively low relative to the true 96% raw reading.
        before = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 20.0; "
            "capturedAtUtc = '2026-01-01T00:00:00Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 0; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0 } }"
        )
        during_current = (
            "[pscustomobject]@{ collected = $true; cpuLoadPercent = 96.0; "
            "capturedAtUtc = '2026-01-01T00:00:04Z'; subjectCpuSeconds = 0.0; "
            "unreadableProcessCount = 5; "
            "processCpuSecondsById = [pscustomobject]@{ '1' = 0.0; '2' = 3.84 } }"
        )
        proc = self._verdict_via_real_wiring(before, during_current)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NONSUBJECT=96", proc.stdout)
        self.assertIn("STATE=exceeded PROVISIONAL=True", proc.stdout)
        self.assertNotIn("STATE=quiet", proc.stdout)


@requires_pwsh
class GuiSmokeResultCarriesHostLoadTests(unittest.TestCase):
    """Static structural checks: the GUI-smoke JSON result must carry hostLoad end to end."""

    def test_result_object_declares_a_hostload_block_with_before_after_bar(self) -> None:
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("hostLoad = [pscustomobject]@{", source)
        self.assertIn("mlvapp-gui-smoke-host-load.v1", source)
        self.assertIn("before = $hostLoadBefore", source)
        self.assertIn("after = $hostLoadAfter", source)
        self.assertIn("provisional = [bool]$hostLoadVerdict.provisional", source)

    def test_hostload_capture_brackets_the_launched_process_not_just_the_script(self) -> None:
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
        before_capture = source.index("$hostLoadBefore = Get-HostLoadSnapshot")
        process_start = source.index("$process = [System.Diagnostics.Process]::Start($startInfo)")
        after_capture = source.index("$hostLoadAfter = Get-HostLoadSnapshot")
        self.assertLess(before_capture, process_start, "hostLoad 'before' must be captured before launch")
        self.assertLess(process_start, after_capture, "hostLoad 'after' must be captured after the process ends")

    def test_leg_wait_is_wired_to_sample_host_load_during_the_leg(self) -> None:
        # round 3: bracketing alone (before Process::Start, after WaitForExit) is not enough --
        # Wait-GuiSmokeProcessBounded must be given a sampling cadence and callback so it can
        # take interior snapshots too, and the verdict must actually consume them.
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("-SampleIntervalMs $HostLoadSampleIntervalMs", source)
        self.assertIn("-OnSample $hostLoadOnSample", source)
        # round 4: $OnSample now assigns to $newSample first (so Get-HostLoadNonSubjectCpuLoadPercent
        # can diff it against the previous sample) before adding it to the list.
        self.assertIn("$newSample = Get-HostLoadSnapshot", source)
        self.assertIn("$hostLoadDuringSamples.Add($newSample)", source)
        self.assertIn("-During @($hostLoadDuringSamples)", source)
        self.assertIn("during = @($hostLoadDuringSamples)", source)

    def test_verdict_call_is_wired_with_the_observed_max_sample_gap(self) -> None:
        # round 6 (astra MAJOR -- "enforcement tests check TOKENS, not behaviour"): astra's exact
        # repro against this class -- "Remove the production ObservedMaxSampleGapMs argument; all
        # three smoke wiring tests pass and the mutant parses." None of the three tests above ever
        # looked at the actual Get-HostLoadVerdict call site, so a caller that silently dropped
        # -ObservedMaxSampleGapMs (quietly reverting to round-4's declared-cadence-only coverage
        # rule) would sail through. Pin the real call site directly: the -Bar argument and the
        # -ObservedMaxSampleGapMs argument (fed from the real observed-gap computation, not the
        # declared cadence) must both be present on the SAME Get-HostLoadVerdict invocation.
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
        verdict_call_start = source.index("$hostLoadVerdict = Get-HostLoadVerdict -Before $hostLoadBefore")
        verdict_call_end = source.index("\n\n", verdict_call_start)
        verdict_call = source[verdict_call_start:verdict_call_end]
        self.assertIn("-Bar $HostLoadCpuPercentBar", verdict_call)
        self.assertIn("-SampleIntervalMs $HostLoadSampleIntervalMs", verdict_call)
        self.assertIn("-ObservedMaxSampleGapMs $(if ($null -ne $hostLoadObservedMaxSampleGapMs)", verdict_call)
        # And the value fed in must trace back to the REAL observed gap computation (the max
        # spacing actually measured between capturedAtUtc timestamps), not merely be present as a
        # parameter with some other, unbound source.
        observed_gap_computation = source.index("$hostLoadObservedMaxSampleGapMs = $null")
        self.assertLess(
            observed_gap_computation, verdict_call_start,
            "hostLoadObservedMaxSampleGapMs must be computed from real sample timestamps before "
            "the verdict call consumes it",
        )
        self.assertIn("$gapMs = ([datetime]$hostLoadSampleSequence[$i].capturedAtUtc -", source)


@requires_pwsh
class CompareGuiSmokeAbHostLoadRefusalTests(_ProbeCase):
    """tools/profiling/compare-release-gui-smoke-ab.ps1's Get-HostLoadComparisonEvidence."""

    script = COMPARE_SCRIPT
    functions = ["Get-NestedValue", "Get-HostLoadComparisonEvidence"]

    def _compare(self, before: str, after: str) -> subprocess.CompletedProcess:
        return self.run_snippet(
            f"$before = {before}\n"
            f"$after = {after}\n"
            "$r = Get-HostLoadComparisonEvidence -BeforeSmoke $before -AfterSmoke $after\n"
            "Write-Host \"FAILURES=$($r.failures.Count)\"\n"
            "Write-Host \"BEFORE_STATE=$($r.evidence.before.state)\"\n"
            "Write-Host \"AFTER_STATE=$($r.evidence.after.state)\"\n"
            "$r.failures | ForEach-Object { Write-Host \"FAILURE: $_\" }\n"
        )

    def test_two_quiet_legs_are_compared(self) -> None:
        quiet = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null } }"
        proc = self._compare(quiet, quiet)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("FAILURES=0", proc.stdout)

    def test_provisional_leg_vs_nonprovisional_leg_is_refused(self) -> None:
        exceeded = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $true; state = 'exceeded'; reason = 'CPU 96% on before' } }"
        quiet = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null } }"
        proc = self._compare(exceeded, quiet)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("FAILURES=0", proc.stdout)
        self.assertIn("before smoke host load is PROVISIONAL", proc.stdout)
        self.assertIn("refused", proc.stdout)

    def test_both_legs_provisional_is_also_refused(self) -> None:
        # Req: a provisional run is never usable as a regression signal -- full stop, regardless
        # of what it is being compared against, not only when paired with a non-provisional run.
        exceeded = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $true; state = 'exceeded'; reason = 'loaded' } }"
        proc = self._compare(exceeded, exceeded)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("FAILURES=2", proc.stdout)

    def test_missing_hostload_field_reads_as_unknown_not_quiet(self) -> None:
        # A run recorded before this gate existed (or an old schema) never measured load.
        legacy = "[pscustomobject]@{ schema = 'mlvapp-gui-smoke-result.v2' }"
        quiet = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null } }"
        proc = self._compare(legacy, quiet)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("BEFORE_STATE=unknown", proc.stdout)
        self.assertNotIn("FAILURES=0", proc.stdout)

    def test_hostload_block_present_but_missing_provisional_reads_as_provisional(self) -> None:
        # round 3 item 3: a hostLoad block that EXISTS but lacks the "provisional" property (a
        # degenerate/partial write, or a future schema drift) must not coerce [bool]$null to
        # $false and read as clean -- that is fail-toward-clean, backwards from this gate.
        degenerate = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ state = 'exceeded' } }"
        quiet = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null } }"
        proc = self._compare(degenerate, quiet)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("FAILURES=0", proc.stdout)
        self.assertIn("before smoke host load is PROVISIONAL", proc.stdout)

    def test_provisional_false_with_missing_state_is_still_refused_not_read_as_clean_unknown(self) -> None:
        # round 4 (sol major), exact repro: "Pass before.hostLoad={provisional:false} and a normal
        # quiet after leg ...; the observed result is before.state=unknown, before.provisional=
        # false, failures=0." "provisional" and "state" were derived independently, so an explicit
        # provisional=false paired with a missing state read as an inconsistent, clean-reading
        # combination. state=unknown must now always force provisional=true.
        inconsistent = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $false } }"
        quiet = "[pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null } }"
        proc = self._compare(inconsistent, quiet)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("BEFORE_STATE=unknown", proc.stdout)
        self.assertNotIn("FAILURES=0", proc.stdout)
        self.assertIn("before smoke host load is PROVISIONAL", proc.stdout)


@requires_pwsh
class SmokeSummaryHostLoadFieldsTests(_ProbeCase):
    """tools/profiling/run-release-cuda-playback-ab.ps1's Get-SmokeSummaryHostLoadFields --
    factored out of Read-SmokeSummary round 3 so the schema-drift-safety fix (item 3) is
    independently testable rather than only reachable through a full JSON-file read."""

    script = CUDA_AB_SCRIPT
    functions = ["Get-NestedValue", "Get-SmokeSummaryHostLoadFields"]

    def _fields(self, host_load: str) -> subprocess.CompletedProcess:
        return self.run_snippet(
            f"$hostLoad = {host_load}\n"
            "$f = Get-SmokeSummaryHostLoadFields -HostLoad $hostLoad\n"
            "Write-Host \"PROVISIONAL=$($f.provisional) STATE=$($f.state) REASON=$($f.reason)\"\n"
        )

    def test_missing_hostload_reads_as_unknown_provisional(self) -> None:
        proc = self._fields("$null")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True STATE=unknown", proc.stdout)

    def test_hostload_present_but_missing_provisional_property_reads_as_provisional(self) -> None:
        proc = self._fields("[pscustomobject]@{ state = 'exceeded' }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True", proc.stdout)

    def test_hostload_present_but_missing_state_property_reads_as_unknown(self) -> None:
        proc = self._fields("[pscustomobject]@{ provisional = $false }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STATE=unknown", proc.stdout)

    def test_well_formed_quiet_hostload_reads_through_untouched(self) -> None:
        proc = self._fields(
            "[pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null }"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=False STATE=quiet", proc.stdout)

    def test_provisional_false_with_missing_state_reads_as_provisional_not_clean_unknown(self) -> None:
        # round 4 (sol major): an explicit provisional=false paired with a missing state used to
        # read as the inconsistent, clean-reading STATE=unknown PROVISIONAL=False.
        proc = self._fields("[pscustomobject]@{ provisional = $false }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True STATE=unknown", proc.stdout)


@requires_pwsh
class CudaPlaybackAbHostLoadGateTests(_ProbeCase):
    """tools/profiling/run-release-cuda-playback-ab.ps1's Get-HostLoadProofFailures."""

    script = CUDA_AB_SCRIPT
    functions = ["Get-HostLoadProofFailures"]

    def _summary(self, provisional: bool, state: str, reason: str = "null") -> str:
        reason_literal = "$null" if reason == "null" else f"'{reason}'"
        provisional_literal = "$true" if provisional else "$false"
        return (
            "[pscustomobject]@{ hostLoadProvisional = " + provisional_literal +
            "; hostLoadState = '" + state + "'; hostLoadReason = " + reason_literal + " }"
        )

    def test_all_quiet_legs_pass_the_gate(self) -> None:
        quiet = self._summary(False, "quiet")
        proc = self.run_snippet(
            f"$baseline = {quiet}\n$candidate = {quiet}\n"
            "$f = Get-HostLoadProofFailures -BaselineSummary $baseline -CandidateSummary $candidate -CandidateSpeedSummary $null\n"
            "Write-Host \"FAILURES=$($f.Count)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("FAILURES=0", proc.stdout)

    def test_provisional_baseline_refuses_the_ab_proof(self) -> None:
        exceeded = self._summary(True, "exceeded", "CPU 96% on baseline")
        quiet = self._summary(False, "quiet")
        proc = self.run_snippet(
            f"$baseline = {exceeded}\n$candidate = {quiet}\n"
            "$f = Get-HostLoadProofFailures -BaselineSummary $baseline -CandidateSummary $candidate -CandidateSpeedSummary $null\n"
            "Write-Host \"FAILURES=$($f.Count)\"\n"
            "$f | ForEach-Object { Write-Host \"FAILURE: $_\" }\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("FAILURES=1", proc.stdout)
        self.assertIn("baseline-host-load-provisional", proc.stdout)

    def test_provisional_candidate_speed_leg_is_checked_only_when_requested(self) -> None:
        quiet = self._summary(False, "quiet")
        exceeded = self._summary(True, "exceeded", "loaded")
        proc = self.run_snippet(
            f"$baseline = {quiet}\n$candidate = {quiet}\n$speed = {exceeded}\n"
            "$fSkipped = Get-HostLoadProofFailures -BaselineSummary $baseline -CandidateSummary $candidate -CandidateSpeedSummary $speed\n"
            "Write-Host \"NOT_REQUESTED_FAILURES=$($fSkipped.Count)\"\n"
            "$fChecked = Get-HostLoadProofFailures -BaselineSummary $baseline -CandidateSummary $candidate -CandidateSpeedSummary $speed -SeparateCandidateSpeedRun\n"
            "Write-Host \"REQUESTED_FAILURES=$($fChecked.Count)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NOT_REQUESTED_FAILURES=0", proc.stdout)
        self.assertIn("REQUESTED_FAILURES=1", proc.stdout)


@requires_pwsh
class PlaybackAbAnalysisHostLoadRefusalTests(_ProbeCase):
    """tools/profiling/run-release-cuda-playback-ab.ps1's New-PlaybackAbAnalysis -- round 3: this
    is the AUTHORITATIVE analysis writer embedded in the summary JSON and trusted verbatim
    downstream (compare-machine-perf.ps1's Get-ProofSummarySuggestion reads
    .analysis.suggestedOptimization directly), so the refusal must live here too, not only in
    compare-machine-perf.ps1's own recompute path."""

    script = CUDA_AB_SCRIPT
    functions = [
        "Convert-ToNullableDouble",
        "Get-NestedValue",
        "Get-CompareDeltaPercent",
        "Test-DeltaAtLeast",
        "New-PlaybackAbAnalysis",
    ]

    def test_host_load_provisional_proof_failure_refuses_the_bottleneck_diagnosis(self) -> None:
        # A large, clearly-actionable-looking fps regression must still be refused when the
        # ProofFailures say the baseline leg was host-load provisional -- the diagnosis would
        # otherwise be derived from noise, not from the candidate build.
        compare = (
            "[pscustomobject]@{ "
            "presentedFps = [pscustomobject]@{ deltaPercent = -40.0 }; "
            "avgQueueWaitMs = [pscustomobject]@{ deltaPercent = 30.0; candidate = 20.0 }; "
            "avgDrawTotalMs = [pscustomobject]@{ deltaPercent = 20.0; candidate = 5.0 } "
            "}"
        )
        proc = self.run_snippet(
            f"$compare = {compare}\n"
            "$proofFailures = @('baseline-host-load-provisional state=exceeded reason=CPU 96%')\n"
            "$a = New-PlaybackAbAnalysis -Compare $compare -ProofFailures $proofFailures -ComparisonBasis 'candidate'\n"
            "Write-Host \"DOMINANT=$($a.dominantBottleneck) SUGGESTION=$($a.suggestedOptimization) "
            "CONFIDENCE=$($a.confidence)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("DOMINANT=host-load-provisional", proc.stdout)
        self.assertIn("SUGGESTION=rerun_playback_ab_with_quiet_host", proc.stdout)
        self.assertNotIn("DOMINANT=present-bound", proc.stdout)

    def test_non_host_load_proof_failure_does_not_trigger_the_host_load_refusal(self) -> None:
        # A different kind of proof failure (e.g. GL parity) must not be mislabeled as a
        # host-load problem -- the delta-based diagnosis should still run normally.
        compare = "[pscustomobject]@{ presentedFps = [pscustomobject]@{ deltaPercent = 20.0 } }"
        proc = self.run_snippet(
            f"$compare = {compare}\n"
            "$proofFailures = @('candidate-gl-parity-mismatches=3')\n"
            "$a = New-PlaybackAbAnalysis -Compare $compare -ProofFailures $proofFailures -ComparisonBasis 'candidate'\n"
            "Write-Host \"DOMINANT=$($a.dominantBottleneck)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("DOMINANT=host-load-provisional", proc.stdout)


class RunReleaseCudaPlaybackAbWiresTheGateInTests(unittest.TestCase):
    """Static check: the main script body must actually call the extracted gate function."""

    def test_proof_failures_are_extended_with_the_host_load_gate(self) -> None:
        source = CUDA_AB_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("$proofFailures += @(Get-HostLoadProofFailures", source)
        self.assertIn("-BaselineSummary $baselineSummary", source)
        self.assertIn("-CandidateSummary $candidateSummary", source)


@requires_pwsh
class CompareMachinePerfPlaybackAbHostLoadRefusalTests(_ProbeCase):
    """tools/profiling/compare-machine-perf.ps1's Get-PlaybackAbHostLoadRefusal and
    Get-PlaybackAbAnalysis -- round 3 item 2: this cross-machine/cross-run comparison tool must
    refuse a derived bottleneck diagnosis for a provisional leg, not just display the numbers
    next to a status flag nobody is required to read."""

    script = COMPARE_MACHINE_PERF_SCRIPT
    functions = [
        "Convert-ToNullableDouble",
        "Get-PlaybackAbLegHostLoadProvisional",
        "Get-PlaybackAbHostLoadRefusal",
        "Get-CompareDeltaPercent",
        "Test-DeltaAtLeast",
        "Get-PlaybackAbAnalysis",
    ]

    def _leg(self, provisional: bool, state: str = None) -> str:
        # round 5: legs are now built WITH hostLoadState by default (as every real round-4+
        # producer emits it alongside hostLoadProvisional) -- state defaults to the value implied
        # by provisional so callers that only care about the boolean keep working unchanged.
        resolved_state = state if state is not None else ("exceeded" if provisional else "quiet")
        provisional_literal = "$true" if provisional else "$false"
        return (
            "[pscustomobject]@{ hostLoadProvisional = " + provisional_literal +
            "; hostLoadState = '" + resolved_state + "' }"
        )

    def _record(self, baseline_provisional: bool, candidate_provisional: bool, fps_delta_pct: float = -40.0) -> str:
        baseline = self._leg(baseline_provisional)
        candidate = self._leg(candidate_provisional)
        return (
            "[pscustomobject]@{ "
            f"baseline = {baseline}; candidate = {candidate}; candidateSpeed = $null; "
            f"compare = [pscustomobject]@{{ presentedFps = [pscustomobject]@{{ deltaPercent = {fps_delta_pct} }} }} "
            "}"
        )

    def test_provisional_baseline_refuses_the_bottleneck_diagnosis(self) -> None:
        proc = self.run_snippet(
            f"$record = {self._record(True, False)}\n"
            "$a = Get-PlaybackAbAnalysis -Record $record\n"
            "Write-Host \"DOMINANT=$($a.dominantBottleneck) SUGGESTION=$($a.suggestedOptimization)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("DOMINANT=host-load-provisional", proc.stdout)
        self.assertIn("SUGGESTION=rerun_playback_ab_with_quiet_host", proc.stdout)

    def test_provisional_candidate_also_refuses(self) -> None:
        proc = self.run_snippet(
            f"$record = {self._record(False, True)}\n"
            "$a = Get-PlaybackAbAnalysis -Record $record\n"
            "Write-Host \"DOMINANT=$($a.dominantBottleneck)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("DOMINANT=host-load-provisional", proc.stdout)

    def test_quiet_legs_still_get_a_real_diagnosis(self) -> None:
        proc = self.run_snippet(
            f"$record = {self._record(False, False)}\n"
            "$a = Get-PlaybackAbAnalysis -Record $record\n"
            "Write-Host \"DOMINANT=$($a.dominantBottleneck)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("DOMINANT=host-load-provisional", proc.stdout)

    def test_leg_missing_hostloadprovisional_property_is_treated_as_provisional(self) -> None:
        # Same fail-toward-provisional stance as item 3: a leg object present but lacking the
        # property entirely (older/degenerate record) must not coerce to clean.
        record = (
            "[pscustomobject]@{ baseline = [pscustomobject]@{}; "
            f"candidate = {self._leg(False)}; candidateSpeed = $null; "
            "compare = [pscustomobject]@{ presentedFps = [pscustomobject]@{ deltaPercent = -40.0 } } }"
        )
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = Get-PlaybackAbHostLoadRefusal -Record $record\n"
            "Write-Host \"PROVISIONAL=$($r.provisional) BASELINE=$($r.baselineProvisional) CANDIDATE=$($r.candidateProvisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True BASELINE=True CANDIDATE=False", proc.stdout)

    def test_provisional_false_with_unknown_state_is_still_refused_not_read_as_clean(self) -> None:
        # round 5 (sol BLOCKER), exact repro: "a baseline leg with hostLoadProvisional=false and
        # hostLoadState='unknown' plus a quiet candidate gives Get-PlaybackAbHostLoadRefusal ->
        # refusal False." round 4 fixed this same inconsistency at the three PRODUCER
        # normalizers; this consumer read the flag alone, so a legacy/degenerate leg still entered
        # a cross-machine comparison as clean.
        record = (
            "[pscustomobject]@{ "
            "baseline = [pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'unknown' }; "
            f"candidate = {self._leg(False)}; candidateSpeed = $null; "
            "compare = [pscustomobject]@{ presentedFps = [pscustomobject]@{ deltaPercent = -40.0 } } }"
        )
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = Get-PlaybackAbHostLoadRefusal -Record $record\n"
            "Write-Host \"PROVISIONAL=$($r.provisional) BASELINE=$($r.baselineProvisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True BASELINE=True", proc.stdout)

    def test_provisional_false_with_missing_state_property_is_still_refused(self) -> None:
        # Same repro, but hostLoadState is entirely ABSENT (not the string 'unknown') -- the
        # legacy-record case round 4's producer-side fix and this consumer-side fix both name.
        record = (
            "[pscustomobject]@{ "
            "baseline = [pscustomobject]@{ hostLoadProvisional = $false }; "
            f"candidate = {self._leg(False)}; candidateSpeed = $null; "
            "compare = [pscustomobject]@{ presentedFps = [pscustomobject]@{ deltaPercent = -40.0 } } }"
        )
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = Get-PlaybackAbHostLoadRefusal -Record $record\n"
            "Write-Host \"PROVISIONAL=$($r.provisional) BASELINE=$($r.baselineProvisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True BASELINE=True", proc.stdout)


class CompareMachinePerfHostLoadWiringTests(unittest.TestCase):
    """Static structural checks: the human-facing table and JSON rows must actually carry
    host_load_provisional, not just the underlying analysis function."""

    def test_playback_ab_and_local_proof_rows_carry_host_load_provisional(self) -> None:
        source = COMPARE_MACHINE_PERF_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("host_load_provisional = $hostLoadRefusal.provisional", source)
        self.assertIn("host_load_provisional = $hostLoadProvisional", source)
        self.assertIn("host_load_provisional,", source)

    def test_proof_summary_suggestion_refuses_ahead_of_trusting_embedded_analysis(self) -> None:
        source = COMPARE_MACHINE_PERF_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("rerun_playback_ab_with_quiet_host", source)
        refusal_check = source.index("(Get-PlaybackAbHostLoadRefusal -Record $playbackAb).provisional")
        trusts_embedded_analysis = source.index("$playbackAb.analysis -and $playbackAb.analysis.suggestedOptimization")
        self.assertLess(
            refusal_check, trusts_embedded_analysis,
            "the host-load refusal must be checked before trusting the record's own cached analysis",
        )


class CompareMachinePerfRowHostLoadCensusTests(unittest.TestCase):
    """round 5 (sol MAJOR #4), fourth round of this finding: "stop enumerating and start
    enforcing." Prior rounds fixed each unbound New-*Row function individually as it was found
    (a nine-row sweep written up in prose in a fleet-run summary, per sol's round-4 review) --
    nothing stopped a TENTH row function from making the same mistake. This test scans every
    New-*Row function actually defined in compare-machine-perf.ps1 (not a hand-maintained list of
    names) and fails if any of them can emit a real (non-hardcoded-null) presented_fps without
    also carrying host_load_provisional in the same object literal, unless the function is named
    in TELEMETRY_ONLY_ROW_FUNCTIONS with a reason.

    Binds today: New-RemoteP3SummaryRow, New-ProfileRow, New-FieldLogRow, New-LocalProofSummaryRow,
    New-PlaybackAbSummaryRow (every New-*Row function that can emit a real presented_fps).
    Allowlisted: New-RemoteCdngSummaryRow (hardcodes presented_fps = $null -- CDNG export has no
    playback-fps signal to rank at all).

    Cannot bind: a brand-new STANDALONE script elsewhere under tools/profiling that independently
    parses smoke JSON and computes its own fps ranking outside this file entirely -- that would
    need a repo-wide PowerShell-aware scan this regex-based census does not attempt. fable's Q3
    named exactly this class of exception for run-local-cuda-playback-dng-smoke.ps1 and
    detect-playback-artifacts.ps1 (both project telemetry only, never rank or gate).

    round 6 (sol + astra MAJOR -- "enforcement tests check TOKENS, not behaviour"): astra's exact
    repro against the round-5 census -- "append a New-*Row with presented_fps=99 and
    host_load_provisional=$false, or only a comment containing host_load_provisional; the actual
    census passes both." A truly general executable test is not possible here (the whole point of
    this census is to catch an UNKNOWN future row function, so there is no fixed signature to call
    it with) -- instead the detector itself is hardened against exactly astra's two repro shapes:
    comment lines are stripped before any match (a mention inside a `#...` comment no longer counts
    as binding), and a LITERAL `host_load_provisional = $false` is treated as UNBOUND rather than
    bound (every real row in this file that hardcodes the flag hardcodes $true -- "always
    unrecorded", the safe direction -- never $false; see New-ProfileRow/New-FieldLogRow below).
    Both new detection rules are proven against synthetic fixtures below, the same way the
    round-5 tautology check already proved the base case."""

    # Extraction pattern deliberately mirrors _extract_functions above (same file, same
    # column-0-closing-brace convention this codebase already relies on for verbatim splicing).
    ROW_FUNCTION_PATTERN = re.compile(r"function (New-\w*Row) \{.*?\n\}\r?\n", re.DOTALL)
    REAL_FPS_PATTERN = re.compile(r"presented_fps\s*=(?!\s*\$null\b)")
    # round 6: a literal `= $false` (not a variable/expression) is the exact hardcoded-clean-flag
    # shape astra's repro used -- every legitimate hardcode in this file uses $true (fail-toward-
    # provisional), so a hardcoded $false is itself evidence of an unbound/fabricated row.
    HARDCODED_FALSE_PATTERN = re.compile(r"host_load_provisional\s*=\s*\$false\b")

    TELEMETRY_ONLY_ROW_FUNCTIONS = {"New-RemoteCdngSummaryRow"}

    @staticmethod
    def _strip_comments(text: str) -> str:
        # round 6: strips a `#...` trailing or whole-line comment from every line so a mention of
        # host_load_provisional that exists ONLY in a comment (astra's second repro shape) cannot
        # satisfy the census.
        return "\n".join(re.sub(r"#.*$", "", line) for line in text.splitlines())

    def _is_bound(self, body: str) -> bool:
        code_only = self._strip_comments(body)
        if "host_load_provisional" not in code_only:
            return False
        if self.HARDCODED_FALSE_PATTERN.search(code_only):
            return False
        return True

    def test_every_row_function_with_a_real_presented_fps_carries_host_load_provenance(self) -> None:
        source = COMPARE_MACHINE_PERF_SCRIPT.read_text(encoding="utf-8")
        matches = list(self.ROW_FUNCTION_PATTERN.finditer(source))
        self.assertGreater(len(matches), 0, "no New-*Row functions found -- the census pattern is stale")
        found_names = {match.group(1) for match in matches}
        bound = []
        unbound = []
        for match in matches:
            name = match.group(1)
            body = match.group(0)
            if name in self.TELEMETRY_ONLY_ROW_FUNCTIONS:
                self.assertIn(
                    "presented_fps = $null", body,
                    f"{name} is on TELEMETRY_ONLY_ROW_FUNCTIONS but its presented_fps is no "
                    "longer hardcoded null -- it now needs host_load_provisional, or removal "
                    "from the allowlist.",
                )
                continue
            if self.REAL_FPS_PATTERN.search(self._strip_comments(body)):
                (bound if self._is_bound(body) else unbound).append(name)
        self.assertEqual(
            unbound, [],
            "New-*Row function(s) in compare-machine-perf.ps1 emit a real presented_fps without "
            f"genuine host_load_provisional provenance: {unbound}. Either add host_load_provisional "
            "to the row (not a hardcoded $false, and not just a comment), or add the function name "
            "to TELEMETRY_ONLY_ROW_FUNCTIONS with a reason if it genuinely never carries an fps "
            "signal to gate.",
        )
        # Round-5 fix regression guard: New-ProfileRow and New-FieldLogRow were the two unbound
        # consumers sol's finding named -- if the census pattern stops matching them, this test
        # would silently stop covering the exact defect it exists to catch.
        self.assertIn("New-ProfileRow", found_names)
        self.assertIn("New-FieldLogRow", found_names)
        self.assertIn("New-ProfileRow", bound)
        self.assertIn("New-FieldLogRow", bound)

    def test_a_hypothetical_unbound_consumer_would_fail_this_census(self) -> None:
        # Proves the census is a real detector, not a tautology: a synthetic row function shaped
        # exactly like the round-5 defect (real presented_fps, no host_load_provisional) must be
        # caught by the same regex this test class applies to the real file.
        fixture = (
            "function New-HypotheticalRow {\n"
            "    param([object]$Record)\n"
            "    [pscustomobject]@{\n"
            "        presented_fps = if ($null -ne $Record.fps) { $Record.fps } else { $null }\n"
            "        source = 'test'\n"
            "    }\n"
            "}\n"
        )
        matches = list(self.ROW_FUNCTION_PATTERN.finditer(fixture))
        self.assertEqual(len(matches), 1)
        body = matches[0].group(0)
        self.assertTrue(self.REAL_FPS_PATTERN.search(body))
        self.assertFalse(self._is_bound(body))

    def test_a_hardcoded_false_provisional_flag_would_fail_this_census(self) -> None:
        # round 6 (astra's exact repro): "append a New-*Row with presented_fps=99 and
        # host_load_provisional=$false" -- the OLD census (bare substring presence) would have
        # classified this as bound purely because the token appears; it must now be caught as
        # UNBOUND because the flag is a hardcoded clean claim, not derived provenance.
        fixture = (
            "function New-BypassRow {\n"
            "    param([object]$Record)\n"
            "    [pscustomobject]@{\n"
            "        presented_fps = 99.0\n"
            "        host_load_provisional = $false\n"
            "    }\n"
            "}\n"
        )
        matches = list(self.ROW_FUNCTION_PATTERN.finditer(fixture))
        self.assertEqual(len(matches), 1)
        body = matches[0].group(0)
        self.assertTrue(self.REAL_FPS_PATTERN.search(body))
        self.assertIn("host_load_provisional", body)  # the OLD substring-presence check would pass
        self.assertFalse(self._is_bound(body))  # the round-6 detector must still refuse it

    def test_a_comment_only_mention_would_fail_this_census(self) -> None:
        # round 6 (astra's exact repro): "...or only a comment containing host_load_provisional."
        fixture = (
            "function New-CommentOnlyRow {\n"
            "    param([object]$Record)\n"
            "    # host_load_provisional is intentionally not tracked for this row yet\n"
            "    [pscustomobject]@{\n"
            "        presented_fps = 99.0\n"
            "    }\n"
            "}\n"
        )
        matches = list(self.ROW_FUNCTION_PATTERN.finditer(fixture))
        self.assertEqual(len(matches), 1)
        body = matches[0].group(0)
        self.assertTrue(self.REAL_FPS_PATTERN.search(self._strip_comments(body)))
        self.assertIn("host_load_provisional", body)  # the OLD substring-presence check would pass
        self.assertFalse(self._is_bound(body))  # the round-6 detector must still refuse it


@requires_pwsh
class CompareMachinePerfProfileAndFieldLogHostLoadTests(_ProbeCase):
    """round 5 (sol MAJOR #4): New-ProfileRow (mlvapp.playback_profile.v1) and New-FieldLogRow
    (mlvapp.perf-field-log.v1) used to emit presented_fps with NO host-load provenance at all --
    these two schemas have never carried host-load telemetry, so their fps was never checked
    against it. Fail toward provisional/unrecorded, same stance as every other reader."""

    script = COMPARE_MACHINE_PERF_SCRIPT
    functions = [
        "Convert-ToNullableInt64",
        "Get-TotalPipelineFrames",
        "Assert-MachineFingerprint",
        "Get-MachineLabel",
        "New-ProfileRow",
        "New-FieldLogRow",
    ]

    def _profile_record(self, cadence_ms: float) -> str:
        return (
            "[pscustomobject]@{ schema = 'mlvapp.playback_profile.v1'; "
            "machineFingerprint = [pscustomobject]@{ schema = 'machine-fingerprint.v1'; "
            "hostname = 'H'; cpu = 'x'; gpu = 'y'; "
            "os = 'z'; build_sha = 'abc1234' }; "
            "metadata = [pscustomobject]@{ average_cadence_ms = " + str(cadence_ms) + " }; "
            "summary = [pscustomobject]@{ schema = 'mlvapp.playback-profile-summary.v1'; "
            "pipeline_counts = [pscustomobject]@{ gpu_texture_no_readback = 5 }; "
            "fallback_count = 0; "
            "bottleneck = [pscustomobject]@{ limiting_stage = 'decode'; suggested_optimization = 'x' } } }"
        )

    def test_profile_row_reports_host_load_provisional(self) -> None:
        record = self._profile_record(20.0)
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = New-ProfileRow -Record $record -Source 'test'\n"
            "Write-Host \"FPS=$($r.presented_fps) HOST_LOAD_PROVISIONAL=$($r.host_load_provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("HOST_LOAD_PROVISIONAL=True", proc.stdout)
        # A real (non-null) fps must be present -- this is the exact repro shape (a real ranking
        # number with unrecorded host-load provenance), not a degenerate always-null row.
        self.assertNotIn("FPS= ", proc.stdout)
        self.assertNotIn("FPS=\n", proc.stdout)

    def _field_log_record(self, kind: str, presented_fps) -> str:
        fps_literal = "$null" if presented_fps is None else str(presented_fps)
        return (
            "[pscustomobject]@{ schema = 'mlvapp.perf-field-log.v1'; kind = '" + kind + "'; "
            "machineFingerprint = [pscustomobject]@{ schema = 'machine-fingerprint.v1'; "
            "hostname = 'H'; cpu = 'x'; gpu = 'y'; "
            "os = 'z'; build_sha = 'abc1234' }; "
            "pipeline_counts = [pscustomobject]@{ gpu_texture_no_readback = 5 }; "
            "fallback_count = 0; "
            f"presented_fps = {fps_literal}; no_readback_percent = 1.0; "
            "frame_count = 100; "
            "bottleneck = [pscustomobject]@{ limiting_stage = 'decode' }; "
            "suggested_optimization = 'x' }"
        )

    def test_playback_field_log_row_reports_host_load_provisional(self) -> None:
        record = self._field_log_record("playback", 24.0)
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = New-FieldLogRow -Record $record -Source 'test'\n"
            "Write-Host \"FPS=$($r.presented_fps) HOST_LOAD_PROVISIONAL=$($r.host_load_provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("FPS=24", proc.stdout)
        self.assertIn("HOST_LOAD_PROVISIONAL=True", proc.stdout)

    def test_export_field_log_row_has_no_fps_signal_to_gate(self) -> None:
        record = self._field_log_record("export", None)
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = New-FieldLogRow -Record $record -Source 'test'\n"
            "Write-Host \"FPS=$($r.presented_fps) HOST_LOAD_PROVISIONAL=$($r.host_load_provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("FPS= HOST_LOAD_PROVISIONAL=", proc.stdout)


@requires_pwsh
class CompareMachinePerfP3HostLoadRowTests(_ProbeCase):
    """tools/profiling/compare-machine-perf.ps1's New-RemoteP3SummaryRow -- round 3 folds in
    sol's finding: p3 rows previously carried presented_fps with no host-load provenance at all
    (fable's finding #2 also named this gap). Get-PlaybackAbLegHostLoadProvisional is reused here
    even though it is named for playback-ab legs, because its absent-property-safe check on a
    bare "hostLoadProvisional" boolean applies to any leg-shaped object, p3 clips included."""

    script = COMPARE_MACHINE_PERF_SCRIPT
    functions = [
        "Convert-ToNullableDouble",
        "Convert-ToNullableInt64",
        "Get-AverageNullableDouble",
        "Get-PlaybackAbLegHostLoadProvisional",
        "Get-FirstPropertyValue",
        "Convert-NvidiaSmiMemoryToMb",
        "Get-ImportedP3RunMetadata",
        "New-MachineFingerprintObject",
        "New-RemoteP3MachineFingerprint",
        "Assert-MachineFingerprint",
        "Get-MachineLabel",
        "Get-ClipPresentedFrames",
        "Get-TotalPipelineFrames",
        "New-RemoteP3SummaryRow",
    ]

    def _clip(self, presented_fps: float, host_load_provisional, host_load_state: str = None) -> str:
        extra = ""
        if host_load_provisional is not None:
            extra = "; hostLoadProvisional = " + ("$true" if host_load_provisional else "$false")
            # round 5: default hostLoadState to what the boolean implies, unless the caller wants
            # to test a specific (e.g. inconsistent) state explicitly.
            resolved_state = host_load_state if host_load_state is not None else (
                "exceeded" if host_load_provisional else "quiet")
            extra += f"; hostLoadState = '{resolved_state}'"
        return (
            "[pscustomobject]@{ "
            f"presentedFps = {presented_fps}; gpuTextureNoReadbackFrames = 10; "
            f"fallbackFrameCount = 0; validationOk = $true{extra} }}"
        )

    def _record(self, clips: list) -> str:
        clips_literal = ", ".join(clips)
        return (
            "[pscustomobject]@{ schema = 'mlvapp-ultramagnus-p3-validation.v1'; status = 'success'; "
            "machineFingerprint = [pscustomobject]@{ hostname = 'ULTRAMAGNUS'; cpu = 'x'; gpu = 'y'; "
            "os = 'z'; build_sha = 'abc1234' }; "
            f"clipResults = @({clips_literal}); "
            "proof = [pscustomobject]@{ correctnessValidated = $true } }"
        )

    def test_all_quiet_clips_report_host_load_provisional_false(self) -> None:
        record = self._record([self._clip(24.0, False), self._clip(25.0, False)])
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = New-RemoteP3SummaryRow -Record $record -Source 'test'\n"
            "Write-Host \"HOST_LOAD_PROVISIONAL=$($r.host_load_provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("HOST_LOAD_PROVISIONAL=False", proc.stdout)

    def test_one_provisional_clip_marks_the_whole_row_provisional(self) -> None:
        record = self._record([self._clip(24.0, False), self._clip(4.0, True)])
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = New-RemoteP3SummaryRow -Record $record -Source 'test'\n"
            "Write-Host \"HOST_LOAD_PROVISIONAL=$($r.host_load_provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("HOST_LOAD_PROVISIONAL=True", proc.stdout)

    def test_clip_missing_hostloadprovisional_property_is_treated_as_provisional(self) -> None:
        # A p3 record from before this fix never carried the property at all -- "cannot know" is
        # the same disposition as "provisional" under this card's fail-toward-provisional stance.
        record = self._record([self._clip(24.0, None)])
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = New-RemoteP3SummaryRow -Record $record -Source 'test'\n"
            "Write-Host \"HOST_LOAD_PROVISIONAL=$($r.host_load_provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("HOST_LOAD_PROVISIONAL=True", proc.stdout)

    def test_clip_with_provisional_false_and_unknown_state_is_still_provisional(self) -> None:
        # round 5 (sol BLOCKER): Get-PlaybackAbLegHostLoadProvisional is reused for p3 clips
        # (docstring above); this is the same "state=unknown, provisional=false" repro as
        # CompareMachinePerfPlaybackAbHostLoadRefusalTests, exercised through the p3 row path.
        record = self._record([self._clip(24.0, False, host_load_state="unknown")])
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = New-RemoteP3SummaryRow -Record $record -Source 'test'\n"
            "Write-Host \"HOST_LOAD_PROVISIONAL=$($r.host_load_provisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("HOST_LOAD_PROVISIONAL=True", proc.stdout)


@requires_pwsh
class P3ValidationSmokeSummaryHostLoadFieldsTests(_ProbeCase):
    """tools/profiling/run-ultramagnus-p3-validation.ps1's Get-SmokeSummaryHostLoadFields --
    duplicated (this script has no shared module with the CUDA A/B script) but same behaviour:
    fail toward provisional/unknown on a missing or degenerate hostLoad block."""

    script = P3_VALIDATION_SCRIPT
    functions = ["Get-SmokeSummaryHostLoadFields"]

    def _fields(self, host_load: str) -> subprocess.CompletedProcess:
        return self.run_snippet(
            f"$hostLoad = {host_load}\n"
            "$f = Get-SmokeSummaryHostLoadFields -HostLoad $hostLoad\n"
            "Write-Host \"PROVISIONAL=$($f.provisional) STATE=$($f.state)\"\n"
        )

    def test_missing_hostload_reads_as_unknown_provisional(self) -> None:
        proc = self._fields("$null")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True STATE=unknown", proc.stdout)

    def test_hostload_present_but_missing_provisional_property_reads_as_provisional(self) -> None:
        proc = self._fields("[pscustomobject]@{ state = 'exceeded' }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True", proc.stdout)

    def test_well_formed_quiet_hostload_reads_through_untouched(self) -> None:
        proc = self._fields(
            "[pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null }"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=False STATE=quiet", proc.stdout)

    def test_provisional_false_with_missing_state_reads_as_provisional_not_clean_unknown(self) -> None:
        # round 4 (sol major): an explicit provisional=false paired with a missing state used to
        # read as the inconsistent, clean-reading STATE=unknown PROVISIONAL=False.
        proc = self._fields("[pscustomobject]@{ provisional = $false }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True STATE=unknown", proc.stdout)


class P3ValidationSpeedValidatedWiringTests(unittest.TestCase):
    """Static structural check: speedValidated (the card's own "acceptance signal" language,
    applied to the speed leg's fps floor) must consult per-clip host-load provenance, and each
    clip result must carry it -- otherwise the mark computed above is never reachable."""

    def test_clip_results_carry_host_load_fields(self) -> None:
        source = P3_VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("hostLoadProvisional = $hostLoadFields.provisional", source)
        self.assertIn("$hostLoadFields = Get-SmokeSummaryHostLoadFields -HostLoad", source)

    def test_speed_validated_excludes_host_load_provisional_clips(self) -> None:
        source = P3_VALIDATION_SCRIPT.read_text(encoding="utf-8")
        speed_validated_start = source.index("$speedValidated =")
        speed_validated_block = source[speed_validated_start:speed_validated_start + 1600]
        self.assertIn("[bool]$_.hostLoadProvisional", speed_validated_block)

    def test_speed_floor_check_computes_host_load_fields_before_it_runs(self) -> None:
        # round 4 (sol major): the per-clip fps floor used to run BEFORE $hostLoadFields existed,
        # so a loaded/uncollectable host that dragged presented_fps under the floor was recorded
        # as a genuine clipFailures entry -- conflating "we couldn't get a clean signal" with "the
        # build is broken". $hostLoadFields must now be computed ahead of the floor check.
        source = P3_VALIDATION_SCRIPT.read_text(encoding="utf-8")
        host_load_fields_index = source.index(
            "$hostLoadFields = Get-SmokeSummaryHostLoadFields -HostLoad "
            # round 6 (astra MAJOR / production crash): the bare "(if ...)" form below was being
            # executed by PowerShell as an attempt to invoke a command literally named "if"
            # (CommandNotFoundException) -- the fix wraps it as a subexpression, "$(if ...)". See
            # P3ValidationHostLoadFieldsAssignmentExecutesTests below for the executable regression.
            "$(if ($result) { $result.hostLoad } else { $null })"
        )
        floor_check_index = source.index('was below hard floor {1:N3} fps."')
        self.assertLess(
            host_load_fields_index, floor_check_index,
            "$hostLoadFields must be computed before the speed-leg fps floor check consumes it",
        )

    def test_speed_floor_breach_is_a_warning_not_a_failure_when_host_load_is_provisional(self) -> None:
        source = P3_VALIDATION_SCRIPT.read_text(encoding="utf-8")
        speed_leg_start = source.index("$presentedFpsValue = if ($result)")
        speed_leg_block = source[speed_leg_start:speed_leg_start + 1000]
        self.assertIn("if ($hostLoadFields.provisional)", speed_leg_block)
        self.assertIn("$warnings.Add(", speed_leg_block)
        # the raw Add-Failure floor breach must only fire in the non-provisional branch
        provisional_branch_index = speed_leg_block.index("if ($hostLoadFields.provisional)")
        else_index = speed_leg_block.index("else {", provisional_branch_index)
        add_failure_index = speed_leg_block.index(
            'Add-Failure $clipFailures ("Speed leg presented_fps'
        )
        self.assertGreater(
            add_failure_index, else_index,
            "the floor-breach Add-Failure must live in the non-provisional else branch",
        )


class P3ValidationImportIndependentlyChecksHostLoadTests(unittest.TestCase):
    """Static structural check: packet import for a speed proof must independently verify each
    clip's host-load provenance rather than trusting a possibly-stale summary.proof.speedValidated
    alone -- round 4 (sol major), closing the last of three PARTIAL findings this round."""

    def test_import_speed_floor_block_independently_checks_host_load_provisional(self) -> None:
        source = P3_VALIDATION_SCRIPT.read_text(encoding="utf-8")
        import_speed_start = source.index('if ($isSpeedProof) {\n                $minSpeedFps')
        # round 5: widened from 1800 to fit the state-check addition below the flag-only read.
        import_speed_block = source[import_speed_start:import_speed_start + 3200]
        self.assertIn('$clip.PSObject.Properties["hostLoadProvisional"]', import_speed_block)
        self.assertIn("host load was PROVISIONAL or unrecorded", import_speed_block)
        # round 5 (sol BLOCKER): the flag alone is not enough -- hostLoadState must also gate.
        self.assertIn('$clip.PSObject.Properties["hostLoadState"]', import_speed_block)
        # the independent host-load Add-Failure must not be gated behind the floor comparison
        host_load_check_index = import_speed_block.index("$clipHostLoadProvisionalProperty")
        floor_check_index = import_speed_block.index("[double]$clip.presentedFps -lt $minSpeedFps")
        self.assertLess(host_load_check_index, floor_check_index)


@requires_pwsh
class P3ValidationImportSpeedFloorGuardExecutesTests(_ProbeCase):
    """round 6 (sol + astra MAJOR -- "enforcement tests check TOKENS, not behaviour"):
    P3ValidationImportIndependentlyChecksHostLoadTests above only asserts that certain substrings
    appear, in the right relative order, in the source text -- astra's exact repro: "Change the
    P3/summarizer state/flag guards from -or to -and; their respective tests remain green." This
    class extracts the SAME guard block by literal text search (the boundaries the static test
    above already locates via source.index) and EXECUTES it against a mocked $clip/$importFailures/
    Add-Failure, so a real -or/-and or $true/$false mutation in the live source changes what this
    test actually observes, not just what substrings are present."""

    script = P3_VALIDATION_SCRIPT
    functions: list[str] = []

    START_MARKER = '$clipHostLoadProvisionalProperty = $clip.PSObject.Properties["hostLoadProvisional"]'
    END_MARKER = 'an fps number measured under provisional host load is not proof of speed, regardless of the floor."'

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hostload-p3-import-guard-probe-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        source = self.script.read_text(encoding="utf-8")
        self.guard_block = _extract_block(source, self.START_MARKER, self.END_MARKER, trailing_lines=1)
        self.probe = self.tmp / "probe.ps1"
        self.probe.write_text(
            "function Add-Failure {\n"
            "    param($FailureList, [string]$Message)\n"
            "    [void]$FailureList.Add($Message)\n"
            "}\n",
            encoding="utf-8",
        )

    def _run(self, clip_snippet: str) -> subprocess.CompletedProcess:
        script = self.tmp / "run.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f". '{self.probe}'\n"
            f"$clip = {clip_snippet}\n"
            "$clipName = 'probe-clip'\n"
            "$importFailures = [System.Collections.Generic.List[string]]::new()\n"
            f"{self.guard_block}\n"
            "Write-Host \"REFUSED=$($importFailures.Count -gt 0)\"\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True,
        )

    def test_provisional_false_with_unknown_state_is_refused(self) -> None:
        # sol's exact round-5 repro, executed against the real guard block instead of searched
        # for as source text.
        proc = self._run("[pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'unknown' }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("REFUSED=True", proc.stdout)

    def test_provisional_false_with_missing_state_property_is_refused(self) -> None:
        proc = self._run("[pscustomobject]@{ hostLoadProvisional = $false }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("REFUSED=True", proc.stdout)

    def test_provisional_true_is_refused_regardless_of_state(self) -> None:
        proc = self._run("[pscustomobject]@{ hostLoadProvisional = $true; hostLoadState = 'quiet' }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("REFUSED=True", proc.stdout)

    def test_provisional_false_with_quiet_state_is_accepted(self) -> None:
        # Regression guard: the fix must not turn every clean clip provisional.
        proc = self._run("[pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'quiet' }")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("REFUSED=False", proc.stdout)


@requires_pwsh
class P3ValidationHostLoadFieldsAssignmentExecutesTests(_ProbeCase):
    """round 6 (astra MAJOR, production crash): the $hostLoadFields assignment above lives in
    run-ultramagnus-p3-validation.ps1's top-level script body (not inside a named function), so it
    cannot be regex-extracted via _extract_functions like the rest of this file's probes. The old
    form -- `-HostLoad (if ($result) { $result.hostLoad } else { $null })` -- PARSES cleanly
    (single-line if/else blocks are valid PowerShell), which is exactly why the round-5 static
    token/regex checks above stayed green while every real P3 validation run threw
    CommandNotFoundException at runtime: PowerShell parses a bare parenthesized `(if ...)` passed
    as a command argument as an attempt to *invoke* a command literally named `if`. This class
    extracts the exact production assignment line out of the live file by literal text search (the
    same technique the structural tests above already use to locate it) and actually EXECUTES it
    under pwsh against a mocked Get-SmokeSummaryHostLoadFields and both a populated and a null
    $result -- it fails against the pre-round-6 source (CommandNotFoundException, non-zero exit)
    and passes against the fix."""

    script = P3_VALIDATION_SCRIPT
    functions: list[str] = []

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hostload-p3-assign-probe-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        source = self.script.read_text(encoding="utf-8")
        marker = "$hostLoadFields = Get-SmokeSummaryHostLoadFields -HostLoad "
        start = source.index(marker)
        end = source.index("\n", start)
        self.production_line = source[start:end]
        self.probe = self.tmp / "probe.ps1"
        self.probe.write_text(
            "function Get-SmokeSummaryHostLoadFields {\n"
            "    param($HostLoad)\n"
            "    [pscustomobject]@{ provisional = $false; state = 'quiet'; reason = $null; "
            "hostLoadWasNull = ($null -eq $HostLoad) }\n"
            "}\n",
            encoding="utf-8",
        )

    def _run(self, result_snippet: str) -> subprocess.CompletedProcess:
        script = self.tmp / "run.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f". '{self.probe}'\n"
            f"{result_snippet}\n"
            f"{self.production_line}\n"
            "$hostLoadFields | ConvertTo-Json -Compress\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True,
        )

    def test_production_assignment_executes_with_a_populated_result(self) -> None:
        proc = self._run(
            "$result = [pscustomobject]@{ hostLoad = [pscustomobject]@{ provisional = $false; "
            "state = 'quiet' } }"
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["hostLoadWasNull"])

    def test_production_assignment_executes_when_result_is_null(self) -> None:
        proc = self._run("$result = $null")
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["hostLoadWasNull"])


class RequireCandidateImprovesPresentedFpsHostLoadGuardTests(unittest.TestCase):
    """Static structural check: tools/profiling/run-release-cuda-playback-ab.ps1's optional
    -RequireCandidateImprovesPresentedFps proof must refuse the fps comparison itself when either
    leg is host-load provisional, rather than computing a "not improved" claim from noisy data --
    round 4 (sol major)."""

    def test_fps_improvement_requirement_refuses_before_comparing_when_host_load_is_provisional(self) -> None:
        source = CUDA_AB_SCRIPT.read_text(encoding="utf-8")
        block_start = source.index("if ($RequireCandidateImprovesPresentedFps) {")
        block_end = source.index("\n}\n", block_start)
        block = source[block_start:block_end]
        self.assertIn("hostLoadProvisional", block)
        self.assertIn("candidate-presented-fps-improvement-not-evaluated", block)
        guard_index = block.index(
            "if ($baselineSummary.hostLoadProvisional -or $candidateForSpeed.hostLoadProvisional)"
        )
        comparison_index = block.index("$candidateFps -le $baselineFps")
        self.assertLess(
            guard_index, comparison_index,
            "the host-load-provisional guard must be checked before the raw fps comparison",
        )


class LocalCudaProofSummarizerHostLoadGuardTests(unittest.TestCase):
    """Static structural check: tools/profiling/summarize-local-cuda-proof.ps1 must independently
    verify playback A/B host-load provenance rather than relying only on $playbackAb.proofFailures
    (which a legacy/degenerate packet lacking the field would never populate) -- round 4 (sol
    major)."""

    def test_summarizer_checks_host_load_provisional_independently_of_proof_failures(self) -> None:
        source = CUDA_PROOF_SUMMARIZER_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('Get-Field $legEntry.Leg "hostLoadProvisional"', source)
        self.assertIn("PLAYBACK_AB_HOST_LOAD_PROVISIONAL", source)
        host_load_guard_index = source.index("$playbackAbHostLoadLegs = @(")
        # "PLAYBACK_PRESENTED_FPS_NOT_IMPROVED" also appears earlier in the file as a reason-code
        # string in an unrelated aggregation helper -- search from the guard onward for the
        # diagnostic emission this test actually cares about.
        fps_not_improved_index = source.index("PLAYBACK_PRESENTED_FPS_NOT_IMPROVED", host_load_guard_index)
        self.assertLess(
            host_load_guard_index, fps_not_improved_index,
            "the host-load-provisional guard must be checked before the fps-not-improved diagnostic",
        )

    def test_summarizer_also_checks_host_load_state_not_just_the_flag(self) -> None:
        # round 5 (sol BLOCKER): reading hostLoadProvisional alone repeats the exact inconsistency
        # round 4 closed at the producer -- a leg with hostLoadProvisional=false and
        # hostLoadState='unknown' (or missing) must still be treated as provisional here.
        source = CUDA_PROOF_SUMMARIZER_SCRIPT.read_text(encoding="utf-8")
        host_load_guard_index = source.index("$playbackAbHostLoadLegs = @(")
        guard_block = source[host_load_guard_index:host_load_guard_index + 1600]
        self.assertIn('Get-Field $legEntry.Leg "hostLoadState"', guard_block)
        self.assertIn('$legState -eq "unknown"', guard_block)

    def test_fps_not_improved_diagnostic_is_skipped_when_host_load_is_provisional(self) -> None:
        source = CUDA_PROOF_SUMMARIZER_SCRIPT.read_text(encoding="utf-8")
        guard_start = source.index("if (-not $playbackAbHostLoadProvisional -and")
        guard_block = source[guard_start:guard_start + 500]
        self.assertIn("PLAYBACK_PRESENTED_FPS_NOT_IMPROVED", guard_block)


@requires_pwsh
class LocalCudaProofSummarizerGuardExecutesTests(_ProbeCase):
    """round 6 (sol + astra MAJOR -- "enforcement tests check TOKENS, not behaviour"): the
    LocalCudaProofSummarizerHostLoadGuardTests class above only searches source text for
    substrings/index ordering -- astra's exact repro: changing the summarizer's -or to -and leaves
    its test green. This class extracts the same per-leg guard block (the foreach loop deciding
    $playbackAbHostLoadProvisional) by literal text search and EXECUTES it, spliced together with
    the real Get-Field/New-Diagnostic/Add-Diagnostic functions it actually calls, against a mocked
    leg list -- so a real -or/-and or $true/$false mutation changes what this test observes."""

    script = CUDA_PROOF_SUMMARIZER_SCRIPT
    functions = ["Get-Field", "New-Diagnostic", "Add-Diagnostic"]

    START_MARKER = "$playbackAbCandidateLeg = if ($playbackAbComparisonBasis -eq \"candidateSpeed\")"
    END_MARKER = (
        "an fps number measured under provisional or unrecorded host load is not a property "
        "of the build."
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hostload-summarizer-guard-probe-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        source = self.script.read_text(encoding="utf-8")
        guard_block = _extract_block(source, self.START_MARKER, self.END_MARKER, trailing_lines=2)
        probe_source = _extract_functions(self.script, self.functions)
        self.probe = self.tmp / "probe.ps1"
        self.probe.write_text(probe_source, encoding="utf-8")
        self.guard_block = guard_block

    def _run(self, baseline_snippet: str, candidate_snippet: str) -> subprocess.CompletedProcess:
        script = self.tmp / "run.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f". '{self.probe}'\n"
            "$playbackAbComparisonBasis = 'candidate'\n"
            f"$playbackAb = [pscustomobject]@{{ baseline = {baseline_snippet}; "
            f"candidate = {candidate_snippet} }}\n"
            "$playbackAbBlockers = [System.Collections.Generic.List[string]]::new()\n"
            "$diagnostics = [System.Collections.Generic.List[object]]::new()\n"
            f"{self.guard_block}\n"
            "Write-Host \"REFUSED=$playbackAbHostLoadProvisional BLOCKERS=$($playbackAbBlockers.Count)\"\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True,
        )

    def test_baseline_provisional_false_with_unknown_state_is_refused(self) -> None:
        # sol's exact round-5 repro, executed against the real guard block instead of searched
        # for as source text.
        proc = self._run(
            "[pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'unknown' }",
            "[pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'quiet' }",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("REFUSED=True BLOCKERS=1", proc.stdout)

    def test_both_legs_quiet_and_non_provisional_are_accepted(self) -> None:
        proc = self._run(
            "[pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'quiet' }",
            "[pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'quiet' }",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("REFUSED=False BLOCKERS=0", proc.stdout)

    def test_missing_state_property_on_either_leg_is_refused(self) -> None:
        proc = self._run(
            "[pscustomobject]@{ hostLoadProvisional = $false }",
            "[pscustomobject]@{ hostLoadProvisional = $false; hostLoadState = 'quiet' }",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("REFUSED=True BLOCKERS=1", proc.stdout)


GUI_SMOKE_PROCESS_BOUNDARY_MODULE = ROOT / "tools" / "profiling" / "gui-smoke-process-boundary.psm1"


@requires_pwsh
class WholeFileParseSafetyNetTests(unittest.TestCase):
    """round 5: fable's round-3 finding was that the splice-based extraction technique this whole
    test module relies on (_extract_functions) is BLIND to a syntax error outside any spliced
    function -- a top-level break in run-ultramagnus-p3-validation.ps1 or any sibling script could
    pass all 63 tests here silently, because every test only ever compiles the ONE named function
    it spliced out, never the whole file. The round-4 producer explicitly DEFERRED this ("no parse
    safety net exists" -- fable round-4 minor); the round-4 HUB ADDENDUM then wrongly told both
    review keys it HAD been added (filed as HUB-ADDENDUM-CLAIMED-A-FIX-THAT-WAS-DEFERRED-1). This
    closes it for real, using the same technique as test_candidate_acceptance.py:1417
    (System.Management.Automation.Language.Parser]::ParseFile), swept over every .ps1/.psm1 file
    this card touches."""

    FILES = (
        SMOKE_SCRIPT,
        COMPARE_SCRIPT,
        CUDA_AB_SCRIPT,
        COMPARE_MACHINE_PERF_SCRIPT,
        P3_VALIDATION_SCRIPT,
        CUDA_PROOF_SUMMARIZER_SCRIPT,
        GUI_SMOKE_PROCESS_BOUNDARY_MODULE,
    )

    @staticmethod
    def _parse_check_command(path: Path) -> str:
        literal = str(path).replace("'", "''")
        return (
            "$errors=$null; $tokens=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{literal}',[ref]$tokens,[ref]$errors) | Out-Null; "
            "if($errors.Count){$errors | ForEach-Object {$_.Message}; exit 1}"
        )

    @requires_pwsh
    def test_every_host_load_gate_script_parses_cleanly(self) -> None:
        self.assertEqual(len(self.FILES), 7, "the file list drifted -- update it alongside the card's file set")
        for path in self.FILES:
            self.assertTrue(path.is_file(), f"missing file: {path}")
            proc = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", self._parse_check_command(path)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, f"{path} failed to parse:\n{proc.stdout}{proc.stderr}")

    @requires_pwsh
    def test_a_top_level_syntax_error_outside_any_function_is_actually_caught(self) -> None:
        # Proves this is a real detector, not a tautology: a top-level syntax error OUTSIDE any
        # function definition (exactly the class of break the splice-based tests above cannot
        # see, since they only ever compile one named function body at a time) must fail here.
        with tempfile.TemporaryDirectory(prefix="parse-sweep-negative-") as tmp:
            broken = Path(tmp) / "broken.ps1"
            broken.write_text(
                "function Foo {\n    Write-Host 'ok'\n}\n"
                "$leftoverTopLevelBreak = 1 +\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", self._parse_check_command(broken)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
