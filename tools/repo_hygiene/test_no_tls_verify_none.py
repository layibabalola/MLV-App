"""Protect pixel-map TLS verification and the downloader's safety wiring."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PixelMapDownloadSafetyTests(unittest.TestCase):
    def test_no_disabled_tls_verification_in_product_code(self):
        offenders = []
        for directory in ("platform", "src"):
            for path in (ROOT / directory).rglob("*"):
                if path.is_file() and path.suffix.lower() in {".c", ".cpp", ".cc", ".h", ".hpp", ".mm"}:
                    if "VerifyNone" in path.read_text(encoding="utf-8", errors="replace"):
                        offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders, "TLS verification disabled in product sources")

    def test_download_manager_uses_validated_atomic_writes(self):
        source = (ROOT / "platform/qt/DownloadManager.cpp").read_text(encoding="utf-8")
        self.assertIn("allowedDownloadBasenameForUrl(", source)
        self.assertIn("writeAtomically(", source)
        self.assertNotIn("file.write(data->readAll())", source)

    def test_real_caller_urls_are_covered_by_the_validator_tests(self):
        test_source = (ROOT / "tests/console/test_fpm_name_validator.cpp").read_text(encoding="utf-8")
        catalog_source = (ROOT / "platform/qt/FocusPixelMapManager.cpp").read_text(encoding="utf-8")
        mainwindow_source = (ROOT / "platform/qt/MainWindow.cpp").read_text(encoding="utf-8")

        self.assertIn("https://api.github.com/repos/ilia3101/MLV-App/contents/pixel_maps", catalog_source)
        self.assertIn("https://api.github.com/repos/ilia3101/MLV-App/releases", mainwindow_source)

        self.assertIn("https://api.github.com/repos/ilia3101/MLV-App/contents/pixel_maps", test_source)
        self.assertIn("https://api.github.com/repos/ilia3101/MLV-App/releases", test_source)


if __name__ == "__main__":
    unittest.main()
