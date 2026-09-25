"""CUDA-PERF-DISPLAY-IDENTITY-HARNESS-1/2: Get-AttrCudaPresentMonDisplayReport, EXECUTED against
real presentmon.csv fixtures -- the shared runtime function tools/profiling/bachelor/
AttrCudaArtifacts.psm1 exports and playback-attr-3-cuda-job.ps1 embeds verbatim, so a pass here is
a statement about the exact characters that run unattended on Bachelor.

WHY THIS FILE EXISTS. Baseline defects, reproduced against the provisional bachelor harness
before HARNESS-1: (1) 3 of 8 attempts ran a full smoke then died at an unguarded Import-Csv of a
missing out\\diagnostic\\presentmon.csv, publishing nothing; (2) "no positive
MsBetweenDisplayChange samples" was an uncaught throw AFTER the smoke run had already passed,
destroying every artifact already produced; (3) PresentMon runs --timed 55 against a --seconds 40
playback, so idle desktop/startup presents outside the measured window could inflate or deflate
whichever swap chain happened to look busiest. Get-AttrCudaPresentMonDisplayReport closes all
three: a missing/unreadable/columnless/empty csv, or a window with zero displayed samples for the
MLVApp chain, is a typed 'PRESENTMON_UNAVAILABLE' / 'DISPLAY_ASLEEP' return -- never a throw -- and
every row is grouped by (ProcessID, SwapChainAddress) and restricted to
[process.startedAtUtc, process.endedAtUtc] before any rate is computed.

HARNESS-2 (sol BLOCKER 3) found that these fixtures were themselves a defect: they carried a
`DisplayedTime` column the pinned PresentMon 2.5.1 legacy launch never emits, which made the
required-column check pass here while failing on every real capture. Every fixture in this file
now uses only real columns (MsUntilDisplayed replaces DisplayedTime), and
RealPresentMon251HeaderFixtureTests below pins the exact 28-column real header, read directly
from a Bachelor capture, so a future column-set drift is caught here instead of on Bachelor.
HARNESS-2 (sol HARDENING) also changed chain selection: the MLVApp preview is now the sum of
every swap chain address the target PID used in the window, not just the busiest one -- see
TwoSwapChainFixtureTests.test_a_second_mlvapp_swap_chain_is_summed_into_the_logical_preview.

The sibling test_playback_attr_3_cuda_presentmon_publish_ordering.py asserts, on the generator's
own template text, that the job-level ORDERING this round requires is also still there: smoke
artifacts published before PresentMon is even waited on, and the old unguarded throws are gone.

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
ATTRIBUTION_GENERATOR = ROOT / "tools" / "profiling" / "bachelor" / "playback-attr-3-cuda-job.ps1"

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
    between_display_change: str = "16.6", until_displayed: str = "16.6", time_in_ms: object = 5000,
) -> dict[str, str]:
    # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-2 (sol BLOCKER 3): these are the columns the PINNED
    # PresentMon 2.5.1 legacy launch actually emits -- confirmed against real Bachelor captures.
    # There is no DisplayedTime column in that schema; MsUntilDisplayed is a real one instead.
    return {
        "Application": application,
        "ProcessID": str(process_id),
        "SwapChainAddress": swap_chain,
        "PresentMode": present_mode,
        "MsBetweenPresents": between_presents,
        "MsBetweenDisplayChange": between_display_change,
        "MsUntilDisplayed": until_displayed,
        "TimeInMs": str(time_in_ms),
    }


# CUDA-PERF-DISPLAY-IDENTITY-HARNESS-2 (sol BLOCKER 3): the EXACT 28-column header the pinned
# PresentMon 2.5.1 legacy launch emits, read directly from a real Bachelor capture -- never a
# synthetic column list. Module-level (not just RealPresentMon251HeaderFixtureTests's own copy)
# so IntervalStatsFilterFixtureTests below can build the same real-shaped fixture.
REAL_PRESENTMON_HEADER = [
    "Application", "ProcessID", "SwapChainAddress", "PresentRuntime", "SyncInterval",
    "PresentFlags", "AllowsTearing", "PresentMode", "TimeInMs", "MsBetweenSimulationStart",
    "MsBetweenPresents", "MsBetweenDisplayChange", "MsInPresentAPI", "MsRenderPresentLatency",
    "MsUntilDisplayed", "CPUStartTimeInMs", "MsBetweenAppStart", "MsCPUBusy", "MsCPUWait",
    "MsGPULatency", "MsGPUTime", "MsGPUBusy", "MsGPUWait", "MsAnimationError", "AnimationTime",
    "MsFlipDelay", "MsAllInputToPhotonLatency", "MsClickToPhotonLatency",
]


def _real_csv_row(
    *, process_id: object = TARGET_PID, swap_chain: str = "0xCCC",
    time_in_ms: object, between_display_change: str = "16.6", until_displayed: str = "8.3",
) -> dict[str, str]:
    values = {name: "NA" for name in REAL_PRESENTMON_HEADER}
    values.update({
        "Application": "MLVApp.exe",
        "ProcessID": str(process_id),
        "SwapChainAddress": swap_chain,
        "PresentRuntime": "DXGI",
        "SyncInterval": "0",
        "PresentFlags": "512",
        "AllowsTearing": "0",
        "PresentMode": "Composed: Flip",
        "TimeInMs": str(time_in_ms),
        "MsBetweenPresents": "16.6",
        "MsBetweenDisplayChange": between_display_change,
        "MsUntilDisplayed": until_displayed,
    })
    return values


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
        latest_capture_start: str | None = None,
    ) -> dict:
        # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-3: the function now windows under TWO bracket
        # endpoints. Every sibling test below that only cares about single-anchor behaviour
        # passes latest_capture_start=None, which degenerates the bracket to one point (both
        # endpoints equal) -- identical windowing to before this round, so none of those
        # assertions needed to change. Only the dedicated bracket tests further down pass two
        # distinct endpoints.
        out_path = self.tmp / "report-out.json"
        csv_literal = "$null" if csv_path is None else "'" + str(csv_path) + "'"
        result_json_expr = "$null"
        if result_json is not None:
            result_json_path = self.tmp / "result.json"
            result_json_path.write_text(json.dumps(result_json), encoding="utf-8")
            result_json_expr = (
                "(Get-Content -LiteralPath '" + str(result_json_path) + "' -Raw | ConvertFrom-Json)"
            )
        latest = latest_capture_start if latest_capture_start is not None else capture_start
        script = self.tmp / "probe.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{MODULE}' -Force\n"
            f"$earliestCaptureStart = [datetime]::Parse('{capture_start}', $null, "
            "[Globalization.DateTimeStyles]::RoundtripKind)\n"
            f"$latestCaptureStart = [datetime]::Parse('{latest}', $null, "
            "[Globalization.DateTimeStyles]::RoundtripKind)\n"
            f"$csvPath = {csv_literal}\n"
            f"$resultJson = {result_json_expr}\n"
            "$report = Get-AttrCudaPresentMonDisplayReport -CsvPath $csvPath "
            "-ResultJson $resultJson -EarliestCaptureStartUtc $earliestCaptureStart "
            "-LatestCaptureStartUtc $latestCaptureStart\n"
            f"$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath '{out_path}' -Encoding UTF8\n"
            "Write-Output 'PROBE_DONE'\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(script)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROBE_DONE", proc.stdout, proc.stdout + proc.stderr)
        return json.loads(out_path.read_text(encoding="utf-8"))

    def _write_real_csv(self, rows: list[dict[str, str]]) -> Path:
        path = self.tmp / "presentmon.csv"
        lines = [",".join(REAL_PRESENTMON_HEADER)]
        for row in rows:
            lines.append(",".join(row[name] for name in REAL_PRESENTMON_HEADER))
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return path


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
            "MsBetweenDisplayChange,MsUntilDisplayed\r\n"
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
            _csv_row(between_display_change="0", until_displayed="0", time_in_ms=5000 + i * 1000)
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
        # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-3: clockBracket is carried on this typed refusal
        # too, not only on OK/DISPLAY_ASLEEP -- a reader refused here still learns both endpoints
        # genuinely found nothing, not just one of them.
        self.assertIsNotNone(report["clockBracket"])
        self.assertEqual(report["clockBracket"]["earliest"]["presentedCount"], 0)


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
            rows.append(_csv_row(between_display_change=disp, until_displayed=disp, time_in_ms=5000 + i * 1000))
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

    def test_a_second_mlvapp_swap_chain_is_summed_into_the_logical_preview(self) -> None:
        # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-2 (sol HARDENING): a swap chain recreated mid-run
        # for the SAME pid (e.g. a window resize tearing one down and recreating it) is one
        # continuous logical MLVApp preview, not two competing chains -- the old "prefer the
        # busier one" behaviour silently dropped the shorter-lived chain's frames from both the
        # count and the rate.
        rows = [_csv_row(swap_chain="0xDDD", time_in_ms=5000 + i * 1000) for i in range(2)]
        rows += [_csv_row(swap_chain="0xCCC", time_in_ms=10000 + i * 1000) for i in range(8)]
        path = self._write_csv(rows)

        report = self.call(path, _result_json())

        self.assertEqual(report["status"], "OK")
        self.assertEqual(report["selectedChain"]["presentedCount"], 10)
        self.assertEqual(report["selectedChain"]["displayedCount"], 10)
        self.assertCountEqual(report["selectedChain"]["swapChainAddresses"], ["0xDDD", "0xCCC"])
        self.assertEqual(len(report["chains"]), 2)
        self.assertEqual(len(report["selectedChainRows"]), 10)
        self.assertAlmostEqual(report["selectedChain"]["presentedFps"], 10 / 40.0)

    def test_no_mlvapp_rows_in_window_is_presentmon_unavailable_even_with_other_chains(self) -> None:
        rows = [_csv_row(application="dwm.exe", process_id=999, swap_chain="0xBBB", time_in_ms=5000 + i * 1000) for i in range(4)]
        path = self._write_csv(rows)

        report = self.call(path, _result_json())

        self.assertEqual(report["status"], "PRESENTMON_UNAVAILABLE")
        self.assertIn(str(TARGET_PID), report["reason"])
        self.assertEqual(len(report["chains"]), 1)
        # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-3: clockBracket is carried here too -- both
        # endpoints agree MLVApp presented 0 rows in this fixture (only the foreign chain is in
        # the window), so both read 0, not just the headline one.
        self.assertIsNotNone(report["clockBracket"])
        self.assertEqual(report["clockBracket"]["earliest"]["presentedCount"], 0)
        self.assertEqual(report["clockBracket"]["latest"]["presentedCount"], 0)


@requires_pwsh
class RealPresentMon251HeaderFixtureTests(_ReportCase):
    """CUDA-PERF-DISPLAY-IDENTITY-HARNESS-2 (sol BLOCKER 3): the sibling tests above all use a
    convenient 8-column subset. A fixture carrying a column the pinned tool never emits (the old
    DisplayedTime requirement) hid exactly this defect, so this class instead uses the EXACT
    28-column header the pinned PresentMon 2.5.1 legacy launch emits, confirmed by directly
    reading a real presentmon.csv header from a Bachelor capture
    (tools/profiling/bachelor/playback-attr-3-cuda-*.artifacts/presentmon.csv) -- never a
    synthetic column list. Non-essential columns carry 'NA', exactly as PresentMon itself writes
    for a metric it could not compute (observed for MsBetweenDisplayChange/MsUntilDisplayed on
    the very first present of a real capture)."""

    def test_the_real_28_column_header_is_accepted_and_produces_a_report(self) -> None:
        rows = [
            _real_csv_row(
                time_in_ms=5000 + i * 1000,
                between_display_change=("NA" if i == 0 else "16.6"),
                until_displayed=("8.3" if i == 0 else "16.6"),
            )
            for i in range(10)
        ]
        path = self._write_real_csv(rows)

        report = self.call(path, _result_json())

        self.assertEqual(report["status"], "OK", report)
        self.assertEqual(report["selectedChain"]["processId"], TARGET_PID)
        # All 10 rows displayed: row 0 via MsUntilDisplayed (MsBetweenDisplayChange reads NA, as
        # PresentMon reports for the very first present of a capture, with no prior display
        # change to diff against), the rest via MsBetweenDisplayChange.
        self.assertEqual(report["selectedChain"]["displayedCount"], 10)
        self.assertEqual(len(report["selectedChainRows"]), 10)
        # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-3 (sol BLOCKER): row 0's interval is genuinely
        # absent, never a zero one -- msBetweenDisplayChange and displayFpsEquivalent are both
        # $null on the wire (Export-Csv later writes that as an empty cell, which
        # refresh_period_histogram.py already skips). See IntervalStatsFilterFixtureTests below
        # for the job-level Get-Stats consumer that must filter this out itself.
        self.assertIsNone(report["selectedChainRows"][0]["msBetweenDisplayChange"])
        self.assertIsNone(report["selectedChainRows"][0]["displayFpsEquivalent"])
        for row in report["selectedChainRows"][1:]:
            self.assertAlmostEqual(row["msBetweenDisplayChange"], 16.6)

    def test_a_row_with_na_display_metrics_is_presented_but_not_displayed(self) -> None:
        # PresentMon legitimately writes NA for metrics it cannot compute yet -- never mistaken
        # for a schema mismatch, and never counted as a display when both signals are absent.
        row = _real_csv_row(time_in_ms=5000, between_display_change="NA", until_displayed="NA")
        path = self._write_real_csv([row])

        report = self.call(path, _result_json())

        self.assertEqual(report["status"], "DISPLAY_ASLEEP")
        self.assertEqual(report["selectedChain"]["presentedCount"], 1)
        self.assertEqual(report["selectedChain"]["displayedCount"], 0)


@requires_pwsh
class BracketWindowingFixtureTests(_ReportCase):
    """CUDA-PERF-DISPLAY-IDENTITY-HARNESS-3 (sol+fable HARDENING): rows are now windowed under
    BOTH endpoints of the caller's capture-start bracket, never just the earlier one -- the
    earlier-only anchor's direction argument was inverted (see the corrected comment in the
    module and playback-attr-3-cuda-job.ps1). WINDOW_START_UTC is +2s of CAPTURE_START_UTC; a
    LatestCaptureStartUtc 500ms after EarliestCaptureStartUtc shifts windowStartMs 500ms earlier
    (windowSeconds -- the real playback duration -- is unaffected, since both window bounds shift
    together), so a row placed in that 500ms sliver is IN the window only under the later
    endpoint -- exactly the front-edge-exclusion failure direction the corrected comment
    describes."""

    _EARLIEST = "2026-01-01T00:00:00.0000000Z"
    _LATEST = "2026-01-01T00:00:00.5000000Z"

    def test_a_front_edge_row_admitted_only_under_the_later_endpoint_heads_the_report(self) -> None:
        rows = [
            _csv_row(time_in_ms=1600),  # in-window only if anchored on the later endpoint
            _csv_row(time_in_ms=10000),  # in-window under both endpoints
        ]
        path = self._write_csv(rows)

        report = self.call(path, _result_json(), capture_start=self._EARLIEST, latest_capture_start=self._LATEST)

        self.assertEqual(report["status"], "OK", report)
        bracket = report["clockBracket"]
        self.assertEqual(bracket["earliest"]["presentedCount"], 1)
        self.assertEqual(bracket["latest"]["presentedCount"], 2)
        # The later endpoint admits strictly more genuinely-in-window rows, so it heads the
        # report -- selectedChainRows/selectedChain reflect it, not the earlier endpoint.
        self.assertEqual(bracket["headline"], "latest")
        self.assertEqual(report["selectedChain"]["presentedCount"], 2)
        self.assertEqual(len(report["selectedChainRows"]), 2)
        # Exactly the one front-edge row disagrees on window membership between the endpoints.
        self.assertEqual(bracket["rowsDifferingInWindowMembership"], 1)

    def test_a_degenerate_bracket_has_no_disagreement_and_heads_earliest(self) -> None:
        rows = [_csv_row(time_in_ms=5000 + i * 1000) for i in range(3)]
        path = self._write_csv(rows)

        report = self.call(path, _result_json(), capture_start=self._EARLIEST)

        self.assertEqual(report["status"], "OK", report)
        bracket = report["clockBracket"]
        self.assertEqual(bracket["earliest"], bracket["latest"])
        self.assertEqual(bracket["rowsDifferingInWindowMembership"], 0)
        # Tie-break: equal endpoints keep the earlier one, deterministically.
        self.assertEqual(bracket["headline"], "earliest")

    def test_sol_repro_displayed_front_edge_row_beats_an_undisplayed_tail_row(self) -> None:
        # HARNESS-4 (sol BLOCKER on #163): earliest admits only the undisplayed tail row (41550 ms), latest admits only the
        # displayed front-edge row (1600 ms). Ranking by presented count alone headed the report with earliest -> a false
        # DISPLAY_ASLEEP. Displayed-first ranking heads it with latest -> OK.
        rows = [
            _csv_row(time_in_ms=1600),
            _csv_row(time_in_ms=41550, between_display_change="NA", until_displayed="NA"),
        ]
        path = self._write_csv(rows)

        report = self.call(path, _result_json(), capture_start=self._EARLIEST, latest_capture_start=self._LATEST)

        self.assertEqual(report["status"], "OK", report)
        bracket = report["clockBracket"]
        self.assertEqual(bracket["earliest"]["displayedCount"], 0)
        self.assertEqual(bracket["latest"]["displayedCount"], 1)
        self.assertEqual(bracket["headline"], "latest")

    def test_fable_repro_more_presented_but_undisplayed_endpoint_does_not_head(self) -> None:
        # HARNESS-4 (fable HARDENING on #163): earliest admits more PRESENTED rows (all undisplayed), latest admits fewer but
        # displayed ones -> not DISPLAY_ASLEEP; latest heads.
        rows = [
            # 41600/41700/41800 ms: inside the earliest window [2000, 42000] only (latest is [1500, 41500]).
            _csv_row(time_in_ms=41600, between_display_change="NA", until_displayed="NA"),
            _csv_row(time_in_ms=41700, between_display_change="NA", until_displayed="NA"),
            _csv_row(time_in_ms=41800, between_display_change="NA", until_displayed="NA"),
            _csv_row(time_in_ms=1600),
            _csv_row(time_in_ms=1800),
        ]
        path = self._write_csv(rows)

        report = self.call(path, _result_json(), capture_start=self._EARLIEST, latest_capture_start=self._LATEST)

        self.assertEqual(report["status"], "OK", report)
        bracket = report["clockBracket"]
        self.assertGreater(bracket["earliest"]["presentedCount"], bracket["latest"]["presentedCount"])
        self.assertEqual(bracket["earliest"]["displayedCount"], 0)
        self.assertEqual(bracket["headline"], "latest")

    def test_display_asleep_still_reports_the_bracket(self) -> None:
        rows = [_csv_row(between_display_change="0", until_displayed="0", time_in_ms=5000)]
        path = self._write_csv(rows)

        report = self.call(path, _result_json(), capture_start=self._EARLIEST, latest_capture_start=self._LATEST)

        self.assertEqual(report["status"], "DISPLAY_ASLEEP")
        self.assertIsNotNone(report["clockBracket"])
        self.assertEqual(report["clockBracket"]["earliest"]["presentedCount"], 1)


@requires_pwsh
class IntervalStatsFilterFixtureTests(_ReportCase):
    """CUDA-PERF-DISPLAY-IDENTITY-HARNESS-3 (sol BLOCKER): a row displayed only via
    MsUntilDisplayed (MsBetweenDisplayChange NA) carries msBetweenDisplayChange=$null in
    .selectedChainRows. [double]$null coerces to 0.0 in PowerShell, so feeding that array
    straight into the job's own Get-Stats turned a row with NO interval into a spuriously fast
    (0ms) one, inflating fpsEquivalentMean and counting a non-interval row as a positive sample
    (sol repro: [null,16.6] published meanMs=8.3/fpsEquivalentMean=120.48 instead of
    meanMs=16.6/fpsEquivalentMean=60.24). This class EXECUTES the job template's own
    Get-Mean/Get-SampleSd/Get-Percentile/Get-Stats source and its own $pmIntervalRows filter
    expression -- extracted verbatim from playback-attr-3-cuda-job.ps1, never hand-reimplemented
    -- against the module's real output for the exact real-28-column NA-first-row fixture, so a
    regression in either file's actual text fails this test."""

    @classmethod
    def setUpClass(cls) -> None:
        text = ATTRIBUTION_GENERATOR.read_text(encoding="utf-8")
        stats_start = text.index("function Get-Mean(")
        stats_end = text.index("function Start-PresentMonCapture(")
        cls.stats_source = text[stats_start:stats_end]
        filter_start = text.index("$pmIntervalRows = @(")
        filter_end = text.index("\n", filter_start)
        cls.filter_line = text[filter_start:filter_end]
        assert "function Get-Stats(" in cls.stats_source, cls.stats_source
        assert "$null -ne $_.msBetweenDisplayChange -and $_.msBetweenDisplayChange -gt 0" in cls.filter_line, cls.filter_line

    def _run_stats(self, csv_path: Path, result_json: dict) -> dict:
        out_path = self.tmp / "stats-out.json"
        result_json_path = self.tmp / "result.json"
        result_json_path.write_text(json.dumps(result_json), encoding="utf-8")
        script = self.tmp / "probe.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{MODULE}' -Force\n"
            f"{self.stats_source}\n"
            f"$captureStart = [datetime]::Parse('{CAPTURE_START_UTC}', $null, "
            "[Globalization.DateTimeStyles]::RoundtripKind)\n"
            f"$csvPath = '{csv_path}'\n"
            f"$resultJson = (Get-Content -LiteralPath '{result_json_path}' -Raw | ConvertFrom-Json)\n"
            "$report = Get-AttrCudaPresentMonDisplayReport -CsvPath $csvPath -ResultJson $resultJson "
            "-EarliestCaptureStartUtc $captureStart -LatestCaptureStartUtc $captureStart\n"
            "$pmRows = @($report.selectedChainRows)\n"
            f"{self.filter_line}\n"
            "$pmStats = Get-Stats @($pmIntervalRows | ForEach-Object { [double]$_.msBetweenDisplayChange })\n"
            "[pscustomobject]@{ pmRowsCount = $pmRows.Count; pmIntervalRowsCount = $pmIntervalRows.Count; "
            "pmStats = $pmStats } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath "
            f"'{out_path}' -Encoding UTF8\n"
            "Write-Output 'PROBE_DONE'\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(script)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PROBE_DONE", proc.stdout, proc.stdout + proc.stderr)
        return json.loads(out_path.read_text(encoding="utf-8"))

    def test_the_na_first_row_interval_is_excluded_before_get_stats(self) -> None:
        # sol's exact repro shape: two in-window displayed rows, row 0 NA/MsUntilDisplayed=8.3,
        # row 1 a real 16.6ms interval.
        rows = [
            _real_csv_row(time_in_ms=5000, between_display_change="NA", until_displayed="8.3"),
            _real_csv_row(time_in_ms=5017, between_display_change="16.6", until_displayed="16.6"),
        ]
        path = self._write_real_csv(rows)

        result = self._run_stats(path, _result_json())

        self.assertEqual(result["pmRowsCount"], 2, result)
        # The NA-first row is excluded from the interval population -- only the one real
        # interval remains, never coerced to a spurious 0.0.
        self.assertEqual(result["pmIntervalRowsCount"], 1, result)
        stats = result["pmStats"]
        self.assertEqual(stats["count"], 1)
        self.assertAlmostEqual(stats["meanMs"], 16.6)
        self.assertAlmostEqual(stats["fpsEquivalentMean"], 1000.0 / 16.6)
        # Pre-fix behaviour (regression guard): [double]$null coercing to 0.0 would have produced
        # meanMs=8.3 and fpsEquivalentMean~120.48 from count=2 -- assert those are NOT what a
        # future regression could silently reproduce.
        self.assertNotAlmostEqual(stats["meanMs"], 8.3)
        self.assertNotAlmostEqual(stats["fpsEquivalentMean"], 1000.0 / 8.3)


if __name__ == "__main__":
    unittest.main()
