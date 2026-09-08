"""Keep the fork's entry point visible without removing upstream documentation."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def banner_errors(text):
    lines = text.splitlines()[:25]
    banner = "\n".join(lines)
    missing = [token for token in ("layibabalola/MLV-App", "--batch", "Qt 6.10.2")
               if token not in banner]
    if not any(line.startswith("Report problems") for line in lines):
        missing.append("Report problems")
    return missing


class ForkBannerTests(unittest.TestCase):
    def test_readme_starts_with_fork_guidance(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual([], banner_errors(text))
        self.assertIn("## Inherited from ilia3101/MLV-App", text)

    def test_missing_required_token_is_rejected(self):
        valid = "layibabalola/MLV-App\n--batch\nQt 6.10.2\nReport problems using SUPPORT.md"
        self.assertEqual([], banner_errors(valid))
        for token in ("layibabalola/MLV-App", "--batch", "Qt 6.10.2", "Report problems"):
            with self.subTest(token=token):
                self.assertIn(token, banner_errors(valid.replace(token, "")))
        self.assertTrue(banner_errors("\n" * 25 + valid))

    def test_unreleased_changelog_names_headless_flags(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
        for flag in ("--batch", "--trim-mlv", "--receipt"):
            with self.subTest(flag=flag):
                self.assertIn(flag, unreleased)


if __name__ == "__main__":
    unittest.main()
