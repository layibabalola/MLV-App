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
    def test_real_snapshot_collects_without_paths_or_command_lines(self) -> None:
        proc = self.run_snippet(
            "$s = Get-HostLoadSnapshot -TopProcessCount 6\n"
            "Write-Host \"COLLECTED=$($s.collected)\"\n"
            "Write-Host \"PROC_COUNT=$($s.processCount)\"\n"
            "Write-Host \"TOP_COUNT=$($s.topCpuConsumers.Count)\"\n"
            "Write-Host ($s.topCpuConsumers -join '|')\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("COLLECTED=True", proc.stdout)
        self.assertRegex(proc.stdout, r"PROC_COUNT=\d+")
        # Top consumers are recorded BY NAME ONLY: no path separators, no drive letters, no
        # command-line argument text -- this is host-load evidence, not process forensics.
        top_line = proc.stdout.splitlines()[-1]
        for name in [n for n in top_line.split("|") if n]:
            self.assertNotIn("\\", name)
            self.assertNotIn("/", name)
            self.assertNotIn(":", name)

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
        self.assertIn("$hostLoadDuringSamples.Add((Get-HostLoadSnapshot", source)
        self.assertIn("-During @($hostLoadDuringSamples)", source)
        self.assertIn("during = @($hostLoadDuringSamples)", source)


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

    def _record(self, baseline_provisional: bool, candidate_provisional: bool, fps_delta_pct: float = -40.0) -> str:
        baseline = "[pscustomobject]@{ hostLoadProvisional = " + ("$true" if baseline_provisional else "$false") + " }"
        candidate = "[pscustomobject]@{ hostLoadProvisional = " + ("$true" if candidate_provisional else "$false") + " }"
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
            "candidate = [pscustomobject]@{ hostLoadProvisional = $false }; candidateSpeed = $null; "
            "compare = [pscustomobject]@{ presentedFps = [pscustomobject]@{ deltaPercent = -40.0 } } }"
        )
        proc = self.run_snippet(
            f"$record = {record}\n"
            "$r = Get-PlaybackAbHostLoadRefusal -Record $record\n"
            "Write-Host \"PROVISIONAL=$($r.provisional) BASELINE=$($r.baselineProvisional) CANDIDATE=$($r.candidateProvisional)\"\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROVISIONAL=True BASELINE=True CANDIDATE=False", proc.stdout)


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

    def _clip(self, presented_fps: float, host_load_provisional) -> str:
        extra = ""
        if host_load_provisional is not None:
            extra = "; hostLoadProvisional = " + ("$true" if host_load_provisional else "$false")
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


if __name__ == "__main__":
    unittest.main()
