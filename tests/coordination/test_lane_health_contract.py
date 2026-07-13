import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / 'tools' / 'coordination' / 'sweep-lane-health.ps1'
TARGET = ROOT / 'tools' / 'coordination' / 'validate-heartbeat-target.ps1'
RUN_HEARTBEAT = ROOT / 'tools' / 'coordination' / 'run-lane-heartbeat.ps1'


def ps(script, *args):
    return subprocess.run(['pwsh.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), *map(str, args)], text=True, capture_output=True)


def make_health_tree(root):
    health = Path(root) / 'coordination' / 'dual-lane' / 'health'
    (health / 'leases').mkdir(parents=True)
    lane = health.parent
    for name in ('fable.md', 'codex.md', 'sol.md', 'opus.md'):
        (lane / name).write_text('journal\n', encoding='utf-8')
    (lane.parent / 'gpu-lane-impl-review-sync.md').write_text('gate\n', encoding='utf-8')
    return health


def write_lease(health, lane, minutes=30):
    payload = {
        'lane': lane,
        'renewed': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'leaseMinutes': minutes,
    }
    (health / 'leases' / f'{lane}.json').write_text(json.dumps(payload), encoding='utf-8')


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

    def test_missing_adopted_sol_lease_is_degraded(self):
        with tempfile.TemporaryDirectory() as d:
            health = make_health_tree(d)
            write_lease(health, 'codex')
            write_lease(health, 'claude-review', 60)
            result = ps(SWEEP, '-HealthDir', health)
            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['overall'], 'DEGRADED')
            sol = next(item for item in payload['lanes'] if item['lane'] == 'sol')
            self.assertEqual(sol['leaseState'], 'MISSING')
            self.assertTrue((health / 'health.log').exists())

    def test_fresh_adopted_leases_are_healthy_while_unadopted_lanes_are_advisory(self):
        with tempfile.TemporaryDirectory() as d:
            health = make_health_tree(d)
            for lane, minutes in (('codex', 30), ('sol', 30), ('claude-review', 60)):
                write_lease(health, lane, minutes)
            result = ps(SWEEP, '-HealthDir', health)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['schema'], 'lane-health.v3')
            self.assertEqual(payload['overall'], 'HEALTHY')
            self.assertEqual(payload['policy']['adoptedLeaseRequiredFor'], ['codex', 'sol', 'claude-review'])

    def test_invalid_target_is_degraded_even_when_journals_are_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            health = make_health_tree(root)
            for lane, minutes in (('codex', 30), ('sol', 30), ('claude-review', 60)):
                write_lease(health, lane, minutes)
            automation = root / 'automation.toml'
            automation.write_text('target_thread_id = "019f5a2b-6070-7483-8de9-981e8607157c"\n', encoding='utf-8')
            result = ps(SWEEP, '-HealthDir', health, '-AutomationPath', automation, '-ExpectedThreadId', '019f5a22-dd1d-7a32-a29a-492406563d29')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['overall'], 'DEGRADED')

    def test_sol_heartbeat_atomically_renews_lease_and_validates_target(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            health = make_health_tree(root / '.claude-state')
            write_lease(health, 'codex')
            write_lease(health, 'claude-review', 60)
            target = '019f5a5b-f375-73e3-b4e4-22cd0d33f77b'
            automation = root / 'sol-automation.toml'
            automation.write_text(f'target_thread_id = "{target}"\n', encoding='utf-8')
            result = ps(
                RUN_HEARTBEAT,
                '-RepoRoot', root,
                '-Lane', 'sol',
                '-LeaseMinutes', '30',
                '-AutomationPath', automation,
                '-ExpectedThreadId', target,
                '-Once',
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['schema'], 'lane-heartbeat.v1')
            self.assertEqual(payload['lane'], 'sol')
            self.assertEqual(payload['health']['overall'], 'HEALTHY')
            lease = json.loads((health / 'leases' / 'sol.json').read_text(encoding='utf-8'))
            self.assertEqual(lease['lane'], 'sol')

    def test_lane_heartbeat_preserves_degraded_exit_status(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            health = make_health_tree(root / '.claude-state')
            write_lease(health, 'codex')
            target = '019f5a5b-f375-73e3-b4e4-22cd0d33f77b'
            automation = root / 'sol-automation.toml'
            automation.write_text(f'target_thread_id = "{target}"\n', encoding='utf-8')

            result = ps(
                RUN_HEARTBEAT,
                '-RepoRoot', root,
                '-Lane', 'sol',
                '-AutomationPath', automation,
                '-ExpectedThreadId', target,
                '-Once',
            )

            self.assertEqual(result.returncode, 2, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['health']['overall'], 'DEGRADED')
            self.assertEqual(payload['exit']['sweep'], 2)


if __name__ == '__main__':
    unittest.main()
