"""CUDA-PERF-DISPLAY-IDENTITY-HARNESS-1: Get-AttrCudaPresentMonDisplayReport, EXECUTED against
real presentmon.csv fixtures -- the shared runtime function tools/profiling/bachelor/
AttrCudaArtifacts.psm1 exports and playback-attr-3-cuda-job.ps1 embeds verbatim, so a pass here is
a statement about the exact characters that run unattended on Bachelor.

WHY THIS FILE EXISTS. Three baseline defects, reproduced against the provisional bachelor
harness before this round: (1) 3 of 8 attempts ran a full smoke then died at an unguarded
Import-Csv of a missing out\\diagnostic\\presentmon.csv, publishing nothing; (2) "no positive
MsBetweenDisplayChange samples" was an uncaught throw AFTER the smoke run had already passed,
destroying every artifact already produced; (3) PresentMon runs --timed 55 against a --seconds 40
playback, so idle desktop/startup presents outside the measured window could inflate or deflate
whichever swap chain happened to look busiest. Get-AttrCudaPresentMonDisplayReport closes all
three: a missing/unreadable/columnless/empty csv, or a window with zero displayed samples for the
MLVApp chain, is a typed 'PRESENTMON_UNAVAILABLE' / 'DISPLAY_ASLEEP' return -- never a throw -- and
every row is grouped by (ProcessID, SwapChainAddress) and restricted to
[process.startedAtUtc, process.endedAtUtc] before any rate is computed.

The sibling test_playback_attr_3_cuda_presentmon_publish_ordering.py asserts, on the generator's
own template text, that the job-level ORDERING this round requires is also still there: smoke
artifacts published before the PresentMon report is ever built, and the old unguarded parse gone.

SKIPS. Everything is skipped cleanly when pwsh is absent, and on non-Windows platforms (the
module's own callers validate drive-letter paths); Windows CI runs it all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "tools" / "profiling" / "bachelor" / "AttrCudaArtifacts.psm1"

PWSH = shutil.which("pwsh")
requires_pwsh = unittest.skipIf(PWSH is None, "pwsh is not on PATH")


def _run_pwsh_file(script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        capture_output=True,
        text=True,
    )


# A fixed, deterministic window shared by every test below: PresentMon capture "starts" at this
# instant, and the MLVApp process is reported (by the stand-in result.json) to have run from +2s
# to +42s -- mirroring the real --timed 55 vs --seconds 40 gap the round's evidence describes,
# with idle time on both sides of the measured window.
CAPTURE_START_UTC = "2026-01-01T00:00:00.0000000Z"
WINDOW_START_UTC = "2026-01-01T00:00:02.0000000Z"
WINDOW_END_UTC = "2026-01-01T00:00:42.0000000Z"
TARGET_PID = 4242


def _result_json(pid: object = TARGET_PID, start: object = WINDOW_START_UTC, end: object = WINDOW_END_UTC) -> dict:
    process: dict[str, object] = {}
    if pid is not None:
        process["id"] = pid
    if start is not None:
        process["startedAtUtc"] = start
    if end is not None:
        process["endedAtUtc"] = end
    return {"process": process}


def _csv_row(
    *, application: str = "MLVApp.exe", process_id: object = TARGET_PID, swap_chain: str = "0xCCC",
    present_mode: str = "Hardware: Independent Flip", between_presents: str = "16.6",
    between_display_change: str = "16.6", displayed_time: str = "16.6", time_in_ms: object = 5000,
) -> dict[str, str]:
    return {
        "Application": application,
        "ProcessID": str(process_id),
        "SwapChainAddress": swap_chain,
        "PresentMode": present_mode,
        "MsBetweenPresents": between_presents,
        "MsBetweenDisplayChange": between_display_change,
        "DisplayedTime": displayed_time,
        "TimeInMs": str(time_in_ms),
    }


class _ReportCase(unittest.TestCase):
    def setUp(self) -> None:
        if PWSH is None:
            self.skipTest("pwsh is not on PATH")
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3-pmreport-")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write_csv(self, rows: list[dict[str, str]]) -> Path:
        path = self.tmp / "presentmon.csv"
        if not rows:
            path.write_text("", encoding="utf-8")
            return path
        header = list(rows[0].keys())
        lines = [",".join(header)]
        for row in rows:
            lines.append(",".join(row[key] for key in header))
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return path

    def call(
        self, csv_path: Path | None, result_json: dict | None, *,
        capture_start: str = CAPTURE_START_UTC,
    ) -> dict:
        out_path = self.tmp / "report-out.json"
        csv_literal = "$null" if csv_path is None else "'" + str(csv_path) + "'"
        result_json_expr = "$null"
        if result_json is not None:
            result_json_path = self.tmp / "result.json"
            result_json_path.write_text(json.dumps(result_json), encoding="utf-8")
            result_json_expr = (
                "(Get-Content -LiteralPath '" + str(result_json_path) + "' -Raw | ConvertFrom-Json)"
            )
        script = self.tmp / "probe.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{MODULE}' -Force\n"
            f"$captureStart = [datetime]::Parse('{capture_start}', $null, "
            "[Globalization.DateTimeStyles]::RoundtripKind)\n"
            f"$csvPath = {csv_literal}\n"
            f"$resultJson = {result_json_expr}\n"
            "$report = Get-AttrCudaPresentMonDisplayReport -CsvPath $csvPath "
            "-ResultJson $resultJson -CaptureStartUtc $captureStart\n"
            f"$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath '{out_path}' -Encoding UTF8\n"
            "Write-Output 'PROBE_DONE'\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(script)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROBE_DONE", proc.stdout, proc.stdout + proc.stderr)
        return json.loads(out_path.read_text(encoding="utf-8"))


@requires_pwsh
class MissingCsvFixtureTests(_ReportCase):
    """Defect 1: a missing/unreadable presentmon.csv is a typed refusal, never a throw."""

    def test_a_missing_csv_is_presentmon_unavailable_not_a_throw(self) -> None:
        missing = self.tmp / "does-not-exist.csv"
        report = self.call(missing, _result_json())
        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")
        self.assertIn("does not exist", report["reason"])
        self.assertEqual(report["selectedChainRows"], [])

    def test_an_empty_csv_is_presentmon_unavailable(self) -> None:
        empty = self._write_csv([])
        report = self.call(empty, _result_json())
        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")
        self.assertIn("no rows", report["reason"])

    def test_a_csv_missing_a_required_column_is_presentmon_unavailable(self) -> None:
        path = self.tmp / "presentmon.csv"
        # No TimeInMs column at all -- the exact shape a PresentMon build whose CSV schema
        # differs from what this job expects would produce; must fail closed with a clear
        # reason, never crash on an absent property.
        path.write_text(
            "Application,ProcessID,SwapChainAddress,PresentMode,MsBetweenPresents,"
            "MsBetweenDisplayChange,DisplayedTime\r\n"
            "MLVApp.exe,4242,0xCCC,Hardware: Independent Flip,16.6,16.6,16.6\r\n",
            encoding="utf-8",
        )
        report = self.call(path, _result_json())
        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")
        self.assertIn("TimeInMs", report["reason"])

    def test_missing_process_identity_is_presentmon_unavailable(self) -> None:
        path = self._write_csv([_csv_row()])
        report = self.call(path, _result_json(pid=None))
        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")
        self.assertIn("process.id", report["reason"])

    def test_malformed_window_timestamps_are_presentmon_unavailable(self) -> None:
        path = self._write_csv([_csv_row()])
        report = self.call(path, _result_json(start="not-a-timestamp"))
        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")


@requires_pwsh
class ZeroDisplayedFixtureTests(_ReportCase):
    """Defect 2: zero displayed samples for the MLVApp chain is DISPLAY_ASLEEP, never the old
    unguarded 'no positive MsBetweenDisplayChange samples' throw -- and the zero rows are not
    silently discarded: presentedCount must still reflect every one of them."""

    def test_all_zero_display_change_rows_in_window_is_display_asleep(self) -> None:
        rows = [
            _csv_row(between_display_change="0", displayed_time="0", time_in_ms=5000 + i * 1000)
            for i in range(5)
        ]
        path = self._write_csv(rows)
        report = self.call(path, _result_json())
        self.assertEqual(report["status"], "DISPLAY_ASLEEP")
        self.assertIn("displayed 0", report["reason"])
        self.assertEqual(len(report["chains"]), 1)
        chain = report["selectedChain"]
        self.assertEqual(chain["processId"], TARGET_PID)
        # The zero rows were kept, not discarded: presentedCount counts all 5, displayedCount none.
        self.assertEqual(chain["presentedCount"], 5)
        self.assertEqual(chain["displayedCount"], 0)
        self.assertEqual(report["selectedChainRows"], [])

    def test_no_rows_at_all_inside_the_window_is_presentmon_unavailable(self) -> None:
        # Every row sits before the window opens (process.startedAtUtc is +2s) -- idle
        # desktop presents from the --timed 55 vs --seconds 40 gap, never mistaken for a
        # display sample.
        rows = [_csv_row(time_in_ms=t) for t in (100, 500, 900)]
        path = self._write_csv(rows)
        report = self.call(path, _result_json())
        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")
        self.assertIn("playback window", report["reason"])


@requires_pwsh
class TwoSwapChainFixtureTests(_ReportCase):
    """Defect 3 (and the naming requirement): with two (ProcessID, SwapChainAddress) chains in
    the window, the MLVApp one -- matched by result.json's own process.id -- is selected, its
    rates are computed only from its own rows, and rows outside the playback window are excluded
    from every chain regardless of which process wrote them."""

    def test_the_mlvapp_chain_is_selected_over_a_busier_foreign_chain(self) -> None:
        rows = []
        # A foreign/desktop compositor chain: busier than MLVApp's, must NOT be selected.
        for i in range(20):
            rows.append(_csv_row(
                application="dwm.exe", process_id=999, swap_chain="0xBBB",
                present_mode="Composed: Flip", time_in_ms=3000 + i * 100,
            ))
        # The MLVApp chain: 10 rows in-window (2 with MsBetweenDisplayChange == 0), plus 2 rows
        # outside the window that must be excluded from both presentedCount and displayedCount.
        rows.append(_csv_row(time_in_ms=500))  # before the window opens
        for i in range(10):
            disp = "0" if i < 2 else "16.6"
            rows.append(_csv_row(between_display_change=disp, displayed_time=disp, time_in_ms=5000 + i * 1000))
        rows.append(_csv_row(time_in_ms=50000))  # after the window closes
        path = self._write_csv(rows)

        report = self.call(path, _result_json())

        self.assertEqual(report["status"], "OK")
        self.assertEqual(len(report["chains"]), 2)
        selected = report["selectedChain"]
        self.assertEqual(selected["processId"], TARGET_PID)
        self.assertEqual(selected["swapChainAddress"], "0xCCC")
        self.assertTrue(selected["isMlvAppChain"])
        # Only the 10 in-window MLVApp rows count -- the 2 outside the window are excluded.
        self.assertEqual(selected["presentedCount"], 10)
        self.assertEqual(selected["displayedCount"], 8)
        # windowSeconds is exactly 40 (WINDOW_END_UTC - WINDOW_START_UTC): rates are counts /
        # window duration, not per-row derived means.
        self.assertAlmostEqual(selected["presentedFps"], 10 / 40.0)
        self.assertAlmostEqual(selected["displayedFps"], 8 / 40.0)
        self.assertEqual(len(report["selectedChainRows"]), 8)

        foreign = next(c for c in report["chains"] if not c["isMlvAppChain"])
        self.assertEqual(foreign["processId"], 999)
        self.assertEqual(foreign["swapChainAddress"], "0xBBB")

    def test_a_second_mlvapp_swap_chain_prefers_the_busier_one(self) -> None:
        # A short-lived secondary swap chain for the SAME pid (e.g. a window resize tearing one
        # down and recreating it) must not be preferred over the dominant, longer-lived one.
        rows = [_csv_row(swap_chain="0xDDD", time_in_ms=5000 + i * 1000) for i in range(2)]
        rows += [_csv_row(swap_chain="0xCCC", time_in_ms=10000 + i * 1000) for i in range(8)]
        path = self._write_csv(rows)

        report = self.call(path, _result_json())

        self.assertEqual(report["status"], "OK")
        self.assertEqual(report["selectedChain"]["swapChainAddress"], "0xCCC")
        self.assertEqual(report["selectedChain"]["presentedCount"], 8)

    def test_no_mlvapp_rows_in_window_is_presentmon_unavailable_even_with_other_chains(self) -> None:
        rows = [_csv_row(application="dwm.exe", process_id=999, swap_chain="0xBBB", time_in_ms=5000 + i * 1000) for i in range(4)]
        path = self._write_csv(rows)

        report = self.call(path, _result_json())

        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")
        self.assertIn(str(TARGET_PID), report["reason"])
        self.assertEqual(len(report["chains"]), 1)


if __name__ == "__main__":
    unittest.main()
