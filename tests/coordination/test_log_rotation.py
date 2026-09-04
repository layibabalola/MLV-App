"""Contract tests for tools/coordination/rotate-logs.ps1.

COORDINATION-PRUNE-POLICY.md says, of logs: "rotate at 1 MB, keep 2 rotations, delete older",
naming gpu-lane-heartbeat.log, codex-delivery-watcher.log, health.log,
context-pressure-beacon.log and *.out/.err.

Measured 2026-09-04: NOTHING implemented that clause. No writer rotated, no .log.N existed on
disk, and codex-delivery-watcher.log had reached 30.2 MB with health.log at 8.3 MB -- 30x and 8x
the stated cap. A binding rule with no implementation is a wish; these are the tests it never had.

Written against BOTH SIDES of the threshold, per the standard set by test_board_health_sweep.py:
a test that only exercises the side it already passes proves nothing about where the boundary is.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROTATOR = os.path.join(ROOT, "tools", "coordination", "rotate-logs.ps1")
SWEEP = os.path.join(ROOT, "tools", "coordination", "board-health-sweep.ps1")


def run_rotator(root, extra=None):
    cmd = ["pwsh", "-NoProfile", "-File", ROTATOR, "-Root", root]
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, capture_output=True, text=True)


def write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


class LogRotationTests(unittest.TestCase):
    def test_a_file_UNDER_the_cap_is_left_alone(self):
        """The low side of the threshold. Rotating a small log would destroy history for nothing."""
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "health.log")
            write(log, 1024)
            res = run_rotator(d)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"), "rotated a file below the cap")

    def test_a_file_OVER_the_cap_is_rotated(self):
        """The high side. Without this the policy has no effect at all."""
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "health.log")
            write(log, 2 * 1024 * 1024)
            res = run_rotator(d)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(os.path.exists(log + ".1"), "did not rotate a file over the cap")
            self.assertFalse(os.path.exists(log), "original should have been moved, not copied")

    def test_rotations_beyond_the_keep_window_are_DELETED_not_archived(self):
        """The policy says DELETE for this class, not archive: they are regenerable."""
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "health.log")
            write(log, 2 * 1024 * 1024)
            write(log + ".1", 10)
            write(log + ".2", 10)
            write(log + ".3", 10)
            res = run_rotator(d)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(os.path.exists(log + ".3"), "kept a rotation beyond the window")
            self.assertTrue(os.path.exists(log + ".1"))
            self.assertTrue(os.path.exists(log + ".2"))

    def test_running_twice_changes_nothing_the_second_time(self):
        """Idempotence is what makes it safe to call on EVERY writer invocation."""
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "health.log")
            write(log, 2 * 1024 * 1024)
            run_rotator(d)
            before = sorted(os.listdir(d))
            res = run_rotator(d)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(sorted(os.listdir(d)), before)

    def test_an_unnamed_log_is_not_touched(self):
        """The policy enumerates its files. Rotating anything else is scope creep."""
        with tempfile.TemporaryDirectory() as d:
            other = os.path.join(d, "not-a-governed.log")
            write(other, 2 * 1024 * 1024)
            res = run_rotator(d)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(os.path.exists(other))
            self.assertFalse(os.path.exists(other + ".1"))

    def test_the_health_log_writer_actually_calls_the_rotator(self):
        """A mechanism nobody calls is the same unenforced policy in a new file."""
        text = open(SWEEP, encoding="utf-8").read()
        self.assertIn("rotate-logs.ps1", text)

    def test_the_rotator_is_tracked_not_gitignored(self):
        """It first lived under .claude-state/, where a TRACKED writer could not call it and no
        review could reach it -- the same defect board-health-sweep.ps1 was promoted to escape."""
        res = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", ROTATOR])
        self.assertNotEqual(res.returncode, 0, "rotate-logs.ps1 is gitignored")


if __name__ == "__main__":
    unittest.main()
