"""Fail-closed tests for get_doctrine_brief.py (fixture + REFUSED path)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve().parent / "get_doctrine_brief.py"
CANDIDATE_ZERO = "agent-bridge-sot-suspend-mlv-in-tree-20260909.md"


def _run(*args: str, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
    )


def _write_fixture(root: Path, *, with_candidate_zero: bool = True) -> Path:
    (root / "ruling-candidates").mkdir(parents=True)
    (root / "specs").mkdir(parents=True)
    (root / "RULINGS.md").write_text(
        "# Rulings\n\n- Pull is the only direction.\n- Doctrine repo is the bus.\n",
        encoding="utf-8",
    )
    (root / "specs" / "mlv-app.md").write_text(
        "# MLV-App factory spec\n\nLanes are PROCESSES, not seats.\n",
        encoding="utf-8",
    )
    (root / "ruling-candidates" / "unrelated-other-project-r1.md").write_text(
        "# Other project candidate\n\nNo MLV content here.\n",
        encoding="utf-8",
    )
    (root / "ruling-candidates" / "mlv-fable-opus-model-failback-r1.md").write_text(
        "# MLV fable failback\n\nCANDIDATE for MLV-App model failback.\n",
        encoding="utf-8",
    )
    if with_candidate_zero:
        (root / "ruling-candidates" / CANDIDATE_ZERO).write_text(
            "# Agent Bridge SoT: suspend MLV in-tree package (2026-09-09)\n\n"
            "**CANDIDATE_ZERO_AUTHORITY**: no runtime activation.\n\n"
            "- Source of truth: layibabalola/agent-bridge\n"
            "- Suspend tools/agent-bridge/ in MLV-App\n",
            encoding="utf-8",
        )
    return root


class GetDoctrineBriefTests(unittest.TestCase):
    def test_fixture_brief_includes_candidate_zero_and_sot_strings(self):
        with tempfile.TemporaryDirectory(prefix="mlv-doctrine-") as tmp:
            root = _write_fixture(Path(tmp))
            result = _run("--fixture-root", str(root))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            out = result.stdout
            self.assertIn("CANDIDATE_ZERO_AUTHORITY", out)
            self.assertIn(CANDIDATE_ZERO, out)
            self.assertIn("layibabalola/agent-bridge", out)
            self.assertIn("tools/agent-bridge", out)
            self.assertIn("rulingsSha256:", out)
            self.assertIn("mlvSpecSha256:", out)
            self.assertIn("busHead:", out)
            self.assertIn("candidateZeroPresent: true", out)
            # Unrelated non-MLV candidate should not be forced in by name alone
            # (mlv-fable should appear; unrelated-other should not).
            self.assertIn("mlv-fable-opus-model-failback-r1.md", out)
            self.assertNotIn("unrelated-other-project-r1.md", out)

    def test_fixture_missing_required_file_refuses(self):
        with tempfile.TemporaryDirectory(prefix="mlv-doctrine-") as tmp:
            root = Path(tmp)
            (root / "ruling-candidates").mkdir()
            # Missing RULINGS.md and specs/mlv-app.md
            result = _run("--fixture-root", str(root))
            self.assertEqual(result.returncode, 2)
            self.assertTrue(
                result.stdout.strip().startswith("REFUSED:"),
                result.stdout,
            )

    def test_gh_failure_refuses_when_no_fixture(self):
        # Force gh to fail by pointing at a nonsense repo; no fixture.
        env = os.environ.copy()
        env.pop("MLV_DOCTRINE_FIXTURE_ROOT", None)
        result = _run(
            "--repo",
            "layibabalola/this-repo-does-not-exist-doctrine-brief-test",
            "--ref",
            "master",
            "--no-candidate-fallback",
            env=env,
        )
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stdout.strip().startswith("REFUSED:"), result.stdout)

    def test_candidate_zero_label_even_when_only_mlv_relevant_filter(self):
        with tempfile.TemporaryDirectory(prefix="mlv-doctrine-") as tmp:
            root = _write_fixture(Path(tmp), with_candidate_zero=True)
            result = _run("--fixture-root", str(root))
            self.assertEqual(result.returncode, 0, result.stdout)
            # Machine field + human label both present.
            self.assertIn("**CANDIDATE_ZERO_AUTHORITY**", result.stdout)
            self.assertIn("SoT pointer present: `layibabalola/agent-bridge`", result.stdout)


if __name__ == "__main__":
    unittest.main()
