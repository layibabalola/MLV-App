from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]

class BatchNoMainWindowIncludeTests(unittest.TestCase):
    def test_batch_sources_are_gui_header_free(self):
        offenders = []
        for path in sorted((ROOT / "src" / "batch").rglob("*")):
            if path.suffix not in {".h", ".hpp", ".cpp", ".cc"}:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"^\s*#\s*include\s*[<\"].*MainWindow\.h", text, re.MULTILINE):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_pipeline_links_export_and_runner_without_widgets(self):
        project = (ROOT / "tests/pipeline/pipeline_tests.pro").read_text(encoding="utf-8")
        qt_modules = re.findall(r"^\s*QT\s*\+?=\s*([^#\n]+)", project, re.MULTILINE)
        self.assertNotIn("widgets", " ".join(qt_modules).split())
        self.assertIn("$$REPO_ROOT/src/batch/CdngSequenceExport.cpp", project)
        self.assertIn("$$REPO_ROOT/src/batch/BatchRunner.cpp", project)

if __name__ == "__main__":
    unittest.main()
