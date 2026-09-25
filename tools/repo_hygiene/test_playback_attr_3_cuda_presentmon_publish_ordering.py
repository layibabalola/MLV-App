"""CUDA-PERF-DISPLAY-IDENTITY-HARNESS-1/2: static assertions on the attribution generator's own
$template text -- never executed, since no real MLVApp/GPU/display is available here -- proving
the ordering and typed-terminal shape this round requires: smoke artifacts (result.json,
stdout/stderr, probe-timeline.csv, the log snapshot) are published BEFORE PresentMon is even
waited on (HARNESS-2: not just before its report is built -- see sol BLOCKER 1 below),
presentmon.csv is never read without a Test-Path guard ahead of it, and the old unguarded "no
positive MsBetweenDisplayChange samples" throw -- which used to fire AFTER a passed smoke run and
destroy every artifact already produced -- is gone.

HARNESS-2 closed a gap this file's own predecessor left open (sol BLOCKER 1): the previous
ordering test only pinned publish-before-REPORT, not publish-before-WAIT, so a PresentMon that
hung past its wait timeout or exited nonzero after a passed smoke run still threw before anything
was published, and this suite stayed green. Wait-PresentMonCapture's call site is now wrapped in
try/catch and routes to the same typed PRESENTMON_UNAVAILABLE terminal a parse failure already
used; see test_smoke_artifacts_are_published_before_presentmon_is_waited_on and
test_presentmon_wait_failure_is_a_typed_terminal_not_an_uncaught_throw below. HARNESS-2 also
closed sol BLOCKER 2 (the clock anchor is now a bracketed pre-spawn/post-spawn/OS-reported-start
triple with a stated uncertainty, not one guessed instant) and sol BLOCKER 3 (the required
PresentMon columns match the real pinned 2.5.1 header -- see the sibling display-report suite).

These are regression tripwires against reintroducing that ordering bug, not a soundness proof:
tools/repo_hygiene/test_playback_attr_3_cuda_presentmon_display_report.py is what actually EXECUTES
Get-AttrCudaPresentMonDisplayReport against real csv fixtures.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "tools" / "profiling" / "bachelor" / "AttrCudaArtifacts.psm1"
ATTRIBUTION_GENERATOR = ROOT / "tools" / "profiling" / "bachelor" / "playback-attr-3-cuda-job.ps1"


class TemplateOrderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        text = ATTRIBUTION_GENERATOR.read_text(encoding="utf-8")
        start = text.index("$template = @'")
        end = text.index("\n'@", start)
        cls.template = text[start:end]

    def test_smoke_artifacts_are_published_before_the_display_report_is_built(self) -> None:
        # The bare function NAME also appears earlier, in a comment explaining why
        # $presentMonCaptureStartUtc is recorded before PresentMon starts -- the actual CALL is
        # anchored by its assignment, so an early comment mention can never make this test pass
        # for the wrong reason.
        report_call = self.template.index("$displayReport = Get-AttrCudaPresentMonDisplayReport")
        publish_result_json = self.template.index("Publish-AttrCudaFileCopy -Source $resultPath")
        publish_stdout = self.template.index("'smoke-stdout.txt') -Destination")
        publish_log = self.template.index("logs\\smoke-run.log'")
        self.assertLess(publish_result_json, report_call)
        self.assertLess(publish_stdout, report_call)
        self.assertLess(publish_log, report_call)

    def test_presentmon_csv_is_never_read_without_a_test_path_guard(self) -> None:
        # The only Import-Csv over $presentMonPath left in the template is the one inside the
        # embedded Get-AttrCudaPresentMonDisplayReport function body -- which is not present as
        # literal text in the generator's own $template (only the __EMBEDDED_FUNCTIONS__
        # placeholder token is, until the generator splices it in at generation time) -- so there
        # is no bare `Import-Csv -LiteralPath $presentMonPath` left at the template's own top
        # level, guarded or not.
        self.assertNotIn("Import-Csv -LiteralPath $presentMonPath", self.template)
        report_call = self.template.index("$displayReport = Get-AttrCudaPresentMonDisplayReport")
        test_path_guard = self.template.index("Test-Path -LiteralPath $presentMonPath -PathType Leaf")
        self.assertLess(test_path_guard, report_call)

    def test_the_old_unguarded_no_positive_samples_throw_is_gone(self) -> None:
        self.assertNotIn("PresentMon sidecar had no positive MsBetweenDisplayChange samples", self.template)

    def test_the_exit_codes_route_on_the_typed_status(self) -> None:
        # The literal status strings ('PRESENTMON_UNAVAILABLE' / 'DISPLAY_ASLEEP') are defined
        # once, in Get-AttrCudaPresentMonDisplayReport's own return objects (asserted against
        # directly by test_playback_attr_3_cuda_presentmon_display_report.py) -- the template
        # only branches on $displayReport.status dynamically, so it is the branch and the
        # exit-code mapping that are checked here, not a re-spelling of the module's own string
        # literals.
        self.assertIn("if ($displayReport.status -ne 'OK')", self.template)
        self.assertIn("{ 24 } else { 23 }", self.template)
        self.assertIn("if ($displayReport.status -eq 'DISPLAY_ASLEEP')", self.template)

    def test_the_module_defines_both_typed_statuses(self) -> None:
        module_text = MODULE.read_text(encoding="utf-8")
        start = module_text.index("function Get-AttrCudaPresentMonDisplayReport {")
        end = module_text.index("\n}\n\nExport-ModuleMember", start)
        function_text = module_text[start:end]
        self.assertIn("'PRESENTMON_UNAVAILABLE'", function_text)
        self.assertIn("'DISPLAY_ASLEEP'", function_text)

    def test_the_display_report_function_is_embedded(self) -> None:
        embed_list_start = self.template.find("__EMBEDDED_FUNCTIONS__")
        self.assertNotEqual(embed_list_start, -1)
        text = ATTRIBUTION_GENERATOR.read_text(encoding="utf-8")
        embed_call_start = text.index("Get-AttrCudaEmbeddedFunctionSource -Name @(")
        embed_call_end = text.index("\n)", embed_call_start)
        self.assertIn("Get-AttrCudaPresentMonDisplayReport", text[embed_call_start:embed_call_end])

    def test_presentmon_capture_anchor_is_persisted_before_the_report_is_built(self) -> None:
        # hub (sol step-0 key): the join of app swap UTC timestamps against PresentMon TimeInMs needs the capture
        # origin on disk; step 0 mis-derived it. It must be saved before parsing and carried by both outcomes.
        save_anchor = self.template.index("'presentmon-capture.json')")
        report_call = self.template.index("$displayReport = Get-AttrCudaPresentMonDisplayReport")
        self.assertLess(save_anchor, report_call)
        self.assertIn("presentMonCaptureStartUtc=$presentMonCaptureStartUtc.ToString('o')", self.template)
        self.assertIn("presentMonCaptureStartUtc = $presentMonCaptureStartUtc.ToString('o')", self.template)

    def test_smoke_artifacts_are_published_before_presentmon_is_waited_on(self) -> None:
        # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-2 (sol BLOCKER 1 / fable HARDENING): a PresentMon
        # wait failure (a hang past TimeoutSeconds, a nonzero exit code) used to throw BEFORE any
        # smoke evidence was published, destroying a passed smoke run's evidence -- the
        # predecessor test above only pinned publish-before-REPORT, which a Wait failure never
        # reaches. The wait call must now come strictly after every smoke-evidence publish.
        wait_call = self.template.index("$presentMonDoneResult = Wait-PresentMonCapture $presentMonProc")
        publish_result_json = self.template.index("Publish-AttrCudaFileCopy -Source $resultPath")
        publish_stdout = self.template.index("'smoke-stdout.txt') -Destination")
        publish_probe_timeline = self.template.index("'probe-timeline.csv') -Destination")
        publish_log = self.template.index("logs\\smoke-run.log'")
        self.assertLess(publish_result_json, wait_call)
        self.assertLess(publish_stdout, wait_call)
        self.assertLess(publish_probe_timeline, wait_call)
        self.assertLess(publish_log, wait_call)

    def test_presentmon_wait_failure_is_a_typed_terminal_not_an_uncaught_throw(self) -> None:
        # The wait call is wrapped in try/catch; a caught failure routes to a typed
        # PRESENTMON_UNAVAILABLE summary and exit 23 -- the same terminal a parsing failure
        # already used -- never an uncaught throw that would propagate past the publish above.
        wait_call = self.template.index("$presentMonDoneResult = Wait-PresentMonCapture $presentMonProc")
        try_start = self.template.rindex("try {", 0, wait_call)
        catch_start = self.template.index("} catch {", wait_call)
        error_capture = self.template.index("$presentMonWaitError = $_.Exception.Message", catch_start)
        typed_check = self.template.index("if ($null -ne $presentMonWaitError) {", error_capture)
        typed_result = self.template.index("result='PRESENTMON_UNAVAILABLE'", typed_check)
        typed_exit = self.template.index("exit 23", typed_result)
        self.assertLess(try_start, wait_call)
        self.assertLess(wait_call, catch_start)
        self.assertLess(catch_start, error_capture)
        self.assertLess(error_capture, typed_check)
        self.assertLess(typed_check, typed_result)
        self.assertLess(typed_result, typed_exit)
        # PRESENTMON_TIMEOUT's own throw message (inside Wait-PresentMonCapture's body) is
        # unchanged -- only this call site's handling of it changed.
        self.assertIn("PRESENTMON_TIMEOUT: did not exit within", self.template)

    def test_presentmon_capture_anchor_bracket_and_uncertainty_are_persisted(self) -> None:
        # CUDA-PERF-DISPLAY-IDENTITY-HARNESS-2 (sol BLOCKER 2 / fable HARDENING): a single
        # guessed captureStartUtc had no stated error bar. The pre-spawn/post-spawn wall-clock
        # bracket, the OS-reported process start, and the uncertainty in ms must all be
        # persisted alongside it, before parsing.
        save_anchor = self.template.index("'presentmon-capture.json')")
        report_call = self.template.index("$displayReport = Get-AttrCudaPresentMonDisplayReport")
        self.assertLess(save_anchor, report_call)
        self.assertIn("preSpawnUtc=$presentMonPreSpawnUtc.ToString('o')", self.template)
        self.assertIn("postSpawnUtc=$presentMonPostSpawnUtc.ToString('o')", self.template)
        self.assertIn("captureStartUncertaintyMs=$presentMonCaptureStartUncertaintyMs", self.template)
        self.assertIn("$presentMonProc.StartTime.ToUniversalTime()", self.template)

    def test_the_required_columns_match_the_real_presentmon_2_5_1_legacy_header(self) -> None:
        # sol BLOCKER 3: DisplayedTime does not exist in the pinned tool's real legacy CSV schema
        # -- requiring it made every real capture PRESENTMON_UNAVAILABLE. Confirmed against real
        # Bachelor captures (presentmon.csv header), never a synthetic fixture's own columns.
        module_text = MODULE.read_text(encoding="utf-8")
        # Quoted, not a bare substring check: the module's own comments legitimately mention
        # DisplayedTime by name to explain why it was removed from the requirement.
        self.assertNotIn("'DisplayedTime'", module_text)
        self.assertIn(
            "$requiredColumns = @('Application', 'ProcessID', 'SwapChainAddress', 'PresentMode', "
            "'MsBetweenPresents', 'MsBetweenDisplayChange', 'MsUntilDisplayed', 'TimeInMs')",
            module_text,
        )


if __name__ == "__main__":
    unittest.main()
