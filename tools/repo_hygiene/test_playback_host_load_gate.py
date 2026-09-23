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


class RunReleaseCudaPlaybackAbWiresTheGateInTests(unittest.TestCase):
    """Static check: the main script body must actually call the extracted gate function."""

    def test_proof_failures_are_extended_with_the_host_load_gate(self) -> None:
        source = CUDA_AB_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("$proofFailures += @(Get-HostLoadProofFailures", source)
        self.assertIn("-BaselineSummary $baselineSummary", source)
        self.assertIn("-CandidateSummary $candidateSummary", source)


if __name__ == "__main__":
    unittest.main()
