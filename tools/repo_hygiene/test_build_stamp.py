import re, subprocess, sys, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
from tools.release import build_stamp

ROOT = Path(__file__).parents[2]
TOOL = ROOT / "tools/release/build_stamp.py"

def run(*args): return subprocess.run([sys.executable, str(TOOL), *args], text=True, capture_output=True)

class BuildStampTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.root = Path(self.t.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / "x").write_text("x"); subprocess.run(["git", "-C", str(self.root), "add", "x"], check=True); subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "init"], check=True)
        self.sha = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
    def tearDown(self): self.t.cleanup()
    def test_generate_and_verify(self):
        h = self.root / "build_buildinfo.h"; self.assertEqual(run("generate", "--repo-root", self.root, "--expected-sha", self.sha, "--output-header", h).returncode, 0)
        b = self.root / "bin"; b.write_bytes(b"prefix " + f"MLVAPP_BUILDSTAMP_v1|sha={self.sha}|dirty=0".encode() + b"\0 suffix")
        self.assertEqual(run("verify", "--binary", b, "--expected-sha", self.sha).returncode, 0)
    def test_dirty_and_ref_mismatch_fail(self):
        (self.root / "x").write_text("dirty"); self.assertNotEqual(run("generate", "--repo-root", self.root, "--expected-sha", self.sha, "--output-header", self.root / "h").returncode, 0)
        self.assertNotEqual(run("generate", "--repo-root", self.root, "--expected-sha", "0" * 40, "--output-header", self.root / "h").returncode, 0)
    def test_binary_failures(self):
        cases = [b"", b"MLVAPP_BUILDSTAMP_v1|sha=" + b"0" * 40 + b"|dirty=0", f"MLVAPP_BUILDSTAMP_v1|sha={self.sha}|dirty=1".encode(), (f"MLVAPP_BUILDSTAMP_v1|sha={self.sha}|dirty=0" * 2).encode(), b"MLVAPP_BUILDSTAMP_v1|sha=unknown|dirty=0"]
        for i, data in enumerate(cases):
            p = self.root / f"b{i}"; p.write_bytes(data); self.assertNotEqual(run("verify", "--binary", p, "--expected-sha", self.sha).returncode, 0)
    def test_failing_git(self):
        p = run("generate", "--repo-root", self.root / "missing", "--expected-sha", self.sha, "--output-header", self.root / "h"); self.assertNotEqual(p.returncode, 0)

    def test_valid_stamp_does_not_hide_malformed_or_extra_markers(self):
        good = f"MLVAPP_BUILDSTAMP_v1|sha={self.sha}|dirty=0".encode()
        for suffix in (b"MLVAPP_BUILDSTAMP_v1|sha=unknown|dirty=0", b"MLVAPP_BUILDSTAMP_v1|",
                       b"MLVAPP_BUILDSTAMP_v1|sha=" + b"x" * 40 + b"|dirty=0"):
            with self.subTest(suffix=suffix):
                binary = self.root / "mixed"
                binary.write_bytes(good + b"\0" + suffix)
                self.assertNotEqual(run("verify", "--binary", binary, "--expected-sha", self.sha).returncode, 0)
        binary.write_bytes(good + b"1\0")
        self.assertNotEqual(run("verify", "--binary", binary, "--expected-sha", self.sha).returncode, 0)

    def test_description_is_a_safe_c_literal(self):
        h = self.root / "header.h"
        # Quoted tags cannot be loose refs on Windows; exercise the metadata
        # seam directly while the other cases run the actual CLI against git.
        with patch.object(build_stamp, "git", side_effect=[self.sha, "", 'release"quote\\\U0001f600']):
            build_stamp.generate(self.root, self.sha, h)
        self.assertIn('#define MLVAPP_GIT_DESCRIBE "release\\042quote\\134\\360\\237\\230\\200"', h.read_text())

    def test_dirty_generation_preserves_existing_header(self):
        h = self.root / "header.h"
        h.write_text("previous header")
        result = run("generate", "--repo-root", self.root, "--expected-sha", self.sha, "--output-header", h)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(h.read_text(), "previous header")

    def test_installer_working_directory_keeps_logs_out_of_source(self):
        for filename, step_name in (("Windows.yml", "Install Qt 6.10.2 + MinGW 13.1"),
                                    ("Linux.yml", "Install Qt 6.10.2")):
            with self.subTest(workflow=filename), tempfile.TemporaryDirectory() as runner_temp:
                # Read the exact curated build-step surface without adding a
                # third-party YAML dependency to the stdlib hygiene suite.
                workflow = (ROOT / ".github/workflows" / filename).read_text()
                jobs = workflow.split("\njobs:\n"); self.assertEqual(len(jobs), 2)
                build = re.findall(r"(?ms)^  build:\n(.*?)(?=^  \S|\Z)", jobs[1])
                self.assertEqual(len(build), 1)
                steps = re.findall(r"(?ms)^    steps:\n(.*?)(?=^    [^\s-]|\Z)", build[0])
                self.assertEqual(len(steps), 1)
                selected = re.findall(r"(?ms)^    - name: " + re.escape(step_name) + r"\n(.*?)(?=^    -|\Z)", steps[0])
                self.assertEqual(len(selected), 1)
                directories = re.findall(r"(?m)^      working-directory:[ \t]*([^\n]+)$", selected[0])
                self.assertLessEqual(len(directories), 1)
                cwd = (directories[0].strip() if directories else str(self.root)).replace("${{ runner.temp }}", runner_temp)
                # AQT's file handler creates a relative aqtinstall.log. Exercise
                # the workflow-selected cwd with that behavior and the real gate.
                subprocess.run([sys.executable, "-c", "from pathlib import Path; Path('aqtinstall.log').write_text('installer log')"], cwd=cwd, check=True)
                h = Path(runner_temp) / "header.h"
                result = run("generate", "--repo-root", self.root, "--expected-sha", self.sha, "--output-header", h)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(h.exists())

if __name__ == "__main__": unittest.main()
