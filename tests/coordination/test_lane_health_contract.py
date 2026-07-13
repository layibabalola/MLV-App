import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / 'tools' / 'coordination' / 'sweep-lane-health.ps1'
TARGET = ROOT / 'tools' / 'coordination' / 'validate-heartbeat-target.ps1'


def ps(script, *args):
    return subprocess.run(['pwsh.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), *map(str, args)], text=True, capture_output=True)


class LaneHealthContractTests(unittest.TestCase):
    def test_target_validator_accepts_exact_uuid_and_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            automation = Path(d) / 'automation.toml'
            target = '019f5a22-dd1d-7a32-a29a-492406563d29'
            automation.write_text(f'target_thread_id = "{target}"\n', encoding='utf-8')
            ok = ps(TARGET, '-AutomationPath', automation, '-ExpectedThreadId', target)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertTrue(json.loads(ok.stdout)['valid'])
            bad = ps(TARGET, '-AutomationPath', automation, '-ExpectedThreadId', '019f5a2b-6070-7483-8de9-981e8607157c')
            self.assertNotEqual(bad.returncode, 0)

    def test_sweep_reports_pending_adoption_without_degrading(self):
        with tempfile.TemporaryDirectory() as d:
            health = Path(d) / 'health'
            (health / 'leases').mkdir(parents=True)
            lane = health.parent
            for name in ('fable.md', 'codex.md', 'opus.md'):
                (lane / name).write_text('journal\n', encoding='utf-8')
            (lane.parent / 'gpu-lane-impl-review-sync.md').write_text('gate\n', encoding='utf-8')
            result = ps(SWEEP, '-HealthDir', health, '-Quiet')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((health / 'health.log').exists())

    def test_invalid_target_is_degraded_even_when_journals_are_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            health = root / 'health'
            (health / 'leases').mkdir(parents=True)
            lane = health.parent
            for name in ('fable.md', 'codex.md', 'opus.md'):
                (lane / name).write_text('journal\n', encoding='utf-8')
            (lane.parent / 'gpu-lane-impl-review-sync.md').write_text('gate\n', encoding='utf-8')
            automation = root / 'automation.toml'
            automation.write_text('target_thread_id = "019f5a2b-6070-7483-8de9-981e8607157c"\n', encoding='utf-8')
            result = ps(SWEEP, '-HealthDir', health, '-AutomationPath', automation, '-ExpectedThreadId', '019f5a22-dd1d-7a32-a29a-492406563d29')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['overall'], 'DEGRADED')


if __name__ == '__main__':
    unittest.main()
