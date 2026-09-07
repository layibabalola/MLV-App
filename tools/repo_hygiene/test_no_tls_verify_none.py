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
        self.assertIn("isValidFpmName(", source)
        self.assertIn("writeAtomically(", source)
        self.assertNotIn("file.write(data->readAll())", source)


if __name__ == "__main__":
    unittest.main()
