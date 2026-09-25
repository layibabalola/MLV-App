"""CUDA-PERF-DISPLAY-IDENTITY-HARNESS-1 (item 5): static assertions on the attribution
generator's own $template text -- never executed, since no real MLVApp/GPU/display is available
here -- proving the ordering and typed-terminal shape this round requires: smoke artifacts
(result.json, stdout/stderr, probe-timeline.csv, the log snapshot) are published BEFORE the
PresentMon report is ever built, presentmon.csv is never read without a Test-Path guard ahead of
it, and the old unguarded "no positive MsBetweenDisplayChange samples" throw -- which used to fire
AFTER a passed smoke run and destroy every artifact already produced -- is gone.

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


if __name__ == "__main__":
    unittest.main()
