"""Contract tests for the promoted board health sweep (SWEEP-CLASS-1).

tools/coordination/board-health-sweep.ps1 writes the health.log line that every liveness
claim on this board reads. It was promoted out of an untracked .claude-state path, where it
could not be committed, gated, or tested. These are the tests that path never had.

They are written against BOTH SIDES of every boundary. A threshold test that only checks
the side it already passes proves nothing about where the boundary actually sits.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / 'tools' / 'coordination' / 'board-health-sweep.ps1'
CLASSES = ROOT / 'tools' / 'coordination' / 'lane-health-classes.psd1'

SESSIONS = {
    'fable': '11111111-1111-1111-1111-111111111111',
    'codex': '22222222-2222-2222-2222-222222222222',
    'sol': '33333333-3333-3333-3333-333333333333',
    'opus': '44444444-4444-4444-4444-444444444444',
    'claude': '55555555-5555-5555-5555-555555555555',
}
# lane -> registry SEAT KEY (not session). The lane id and the seat key are the same for
# every lane except 'claude-review', which is seated in the registry as 'claude'.
SEAT_KEY = {name: name for name in SESSIONS}
SEAT_KEY['claude-review'] = 'claude'


def run_sweep(health):
    return subprocess.run(
        ['pwsh.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
         '-File', str(SWEEP), '-HealthDir', str(health)],
        text=True, capture_output=True)


def make_tree(root):
    health = Path(root) / 'coordination' / 'dual-lane' / 'health'
    (health / 'leases').mkdir(parents=True)
    dual = health.parent
    for name in ('fable.md', 'codex.md', 'sol.md', 'opus.md'):
        (dual / name).write_text('journal\n', encoding='utf-8')
    (dual.parent / 'gpu-lane-impl-review-sync.md').write_text('gate\n', encoding='utf-8')
    seats = {k: {'session': v} for k, v in SESSIONS.items()}
    (dual / 'seat-registry.json').write_text(json.dumps({'seats': seats}), encoding='utf-8')
    return health


def write_lease(health, lane, age_minutes, cadence, session=None, note=None):
    if session is None:
        session = SESSIONS[SEAT_KEY[lane]]
    renewed = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    payload = {
        'lane': lane,
        'session': session,
        'leaseMinutes': cadence,
        'renewed': renewed.isoformat().replace('+00:00', 'Z'),
    }
    if note:
        payload['note'] = note
    (health / 'leases' / f'{lane}.json').write_text(json.dumps(payload), encoding='utf-8')


def all_fresh(health, cadence=30):
    for lane in ('fable', 'codex', 'sol', 'opus', 'claude-review'):
        write_lease(health, lane, 1, cadence)


def lane_state(line, lane):
    m = re.search(r'(?:^|\s)%s=([A-Z-]+)\(' % re.escape(lane), line)
    return m.group(1) if m else None


class BoardHealthSweepTests(unittest.TestCase):

    # ---- (a) ONE boundary, and EXPIRED is gone -----------------------------

    def test_dark_boundary_is_min_cadence_thirty_plus_twenty_on_both_sides(self):
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            # cadence 30 -> threshold 50. The OLD rule was 2*30 = 60, which called DARK
            # ten minutes LATE, board-wide, for the whole history of the 30m default.
            write_lease(health, 'sol', 49, 30)
            self.assertEqual(lane_state(run_sweep(health).stdout, 'sol'), 'LIVE')
            write_lease(health, 'sol', 51, 30)
            self.assertEqual(lane_state(run_sweep(health).stdout, 'sol'), 'DARK')

    def test_short_cadence_is_not_dark_before_the_board_threshold(self):
        """sol declares cadence 5. The old 2*d rule called it DARK at 10m against a 25m
        board threshold, so sol spent its turns reporting its own false alarm."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            write_lease(health, 'sol', 12, 5)
            line = run_sweep(health).stdout
            self.assertEqual(lane_state(line, 'sol'), 'LIVE')
            write_lease(health, 'sol', 26, 5)
            self.assertEqual(lane_state(run_sweep(health).stdout, 'sol'), 'DARK')

    def test_long_cadence_is_capped_at_thirty(self):
        """A long declaration must not buy extra undetected silence: 60 -> 50m, not 120m."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            write_lease(health, 'sol', 49, 60)
            self.assertEqual(lane_state(run_sweep(health).stdout, 'sol'), 'LIVE')
            write_lease(health, 'sol', 51, 60)
            self.assertEqual(lane_state(run_sweep(health).stdout, 'sol'), 'DARK')

    def test_expired_class_is_never_emitted(self):
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            # Sweep the whole region the old EXPIRED band occupied for cadence 30 (30..60).
            for age in (31, 40, 49, 51, 59):
                write_lease(health, 'sol', age, 30)
                self.assertNotIn('EXPIRED', run_sweep(health).stdout,
                                 'EXPIRED must not be emitted at age %d' % age)
            self.assertNotIn('EXPIRED', CLASSES.read_text(encoding='utf-8').split('@{')[1])

    # ---- (b) cadence-keyed inter-renewal gap -------------------------------

    def test_cadence_gap_is_emitted_while_still_live_and_is_not_a_liveness_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            write_lease(health, 'sol', 12, 5)          # past its own cadence, under 25m
            line = run_sweep(health).stdout
            self.assertIn('gap:sol=OVER(12', line)
            self.assertEqual(lane_state(line, 'sol'), 'LIVE')   # cadence != liveness
            self.assertIn('OVERALL=ATTENTION', line)

    def test_no_cadence_gap_when_within_cadence(self):
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            write_lease(health, 'sol', 3, 5)
            line = run_sweep(health).stdout
            self.assertNotIn('gap:sol', line)
            self.assertIn('OVERALL=HEALTHY', line)

    # ---- dispatch intent self-healing --------------------------------------

    def test_missing_queue_card_is_healed_from_durable_intent(self):
        """The crash window is intent-present/card-missing. A sweep must close it without a
        hub turn, and a second sweep must be idempotent rather than append a duplicate."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            dual = health.parent
            coordination = dual.parent
            artifact = coordination / 'packet.md'
            artifact.write_text('packet\n', encoding='utf-8')
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest().upper()
            (dual / 'fable.md').write_text(
                '## SEQ 10 | dispatch\n**`TEST-DISPATCH-1`** -- **DISPATCHED.**\n',
                encoding='utf-8')
            (dual / 'queue.json').write_text(json.dumps({
                'schema': 'dual-lane-queue.v1', 'updated': '2026-08-11T00:00:00Z',
                'updatedBySeq': 9, 'items': []}), encoding='utf-8')
            (dual / 'dispatch-intents.json').write_text(json.dumps({
                'schema': 'dual-lane-dispatch-intents.v1', 'proseAuditFromSeq': 10}),
                encoding='utf-8')
            intents = dual / 'dispatch-intents'
            intents.mkdir()
            intent = {
                'schema': 'dual-lane-dispatch-intent.v1',
                'intentId': 'TEST-DISPATCH-1-OPUS',
                'sourceDispatchId': 'TEST-DISPATCH-1',
                'source': {'ledger': 'fable.md', 'seq': 10, 'artifact': 'packet.md',
                           'artifactSha256': digest},
                'card': {'id': 'TEST-DISPATCH-1-OPUS', 'title': 'Test dispatch',
                         'state': 'dispatched', 'owner': 'opus', 'priority': 1,
                         'track': 'factory', 'dispatchedSeq': 10,
                         'scope': 'Independent test review.'},
            }
            (intents / 'TEST-DISPATCH-1-OPUS.json').write_text(
                json.dumps(intent, indent=2), encoding='utf-8')

            first = run_sweep(health)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn('dispatch-intents=HEALED(1/1)', first.stdout)
            queue = json.loads((dual / 'queue.json').read_text(encoding='utf-8'))
            self.assertEqual([item['id'] for item in queue['items']],
                             ['TEST-DISPATCH-1-OPUS'])

            second = run_sweep(health)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn('dispatch-intents=OK(1)', second.stdout)
            queue = json.loads((dual / 'queue.json').read_text(encoding='utf-8'))
            self.assertEqual(len(queue['items']), 1)

    def test_prose_only_dispatch_is_loud_attention_and_not_auto_invented(self):
        """Prose lacks owner/scope/priority, so guessing a card would invent authority. The
        sweep must be loud while leaving the queue byte-identical."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            dual = health.parent
            (dual / 'fable.md').write_text(
                '## SEQ 10 | dispatch\n**`PROSE-ONLY-1`** -- **DISPATCHED.**\n',
                encoding='utf-8')
            (dual / 'queue.json').write_text(json.dumps({
                'schema': 'dual-lane-queue.v1', 'updated': '2026-08-11T00:00:00Z',
                'updatedBySeq': 9, 'items': []}), encoding='utf-8')
            (dual / 'dispatch-intents.json').write_text(json.dumps({
                'schema': 'dual-lane-dispatch-intents.v1', 'proseAuditFromSeq': 10}),
                encoding='utf-8')
            before = (dual / 'queue.json').read_bytes()

            result = run_sweep(health)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('dispatch-intents=PROSE-ONLY(1)', result.stdout)
            self.assertIn('OVERALL=ATTENTION', result.stdout)
            self.assertEqual((dual / 'queue.json').read_bytes(), before)

    # ---- (c) checkpoint identity -------------------------------------------

    def test_checkpoint_identity_reports_ok_drift_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            dual = health.parent

            self.assertIn('ckid:fable=MISSING', run_sweep(health).stdout)

            (dual / 'fable-resume-CURRENT.md').write_text(
                'Seat: %s' % SESSIONS['fable'], encoding='utf-8')
            (dual / 'codex-resume-CURRENT.md').write_text(
                'Seat: 99999999-9999-9999-9999-999999999999', encoding='utf-8')
            line = run_sweep(health).stdout
            self.assertRegex(line, r'ckid:fable=OK\(')
            self.assertRegex(line, r'ckid:codex=DRIFT\(')

    def test_checkpoint_naming_other_ids_before_the_seat_is_not_drift(self):
        """Live false positive on the detector's first day, and it was mine.

        These checkpoints are prose and legitimately name OTHER identifiers first -- codex's
        names a Codex TASK id before its session credential. Taking the first GUID in the
        document reported DRIFT on a correctly-attributed checkpoint. DRIFT must mean the
        registered seat is NOWHERE in the file, not merely 'not mentioned first'."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            (health.parent / 'codex-resume-CURRENT.md').write_text(
                'rotated to successor `019fe03e-8d7f-74b2-9b73-631ff1fee2d0` while\n'
                'preserving session credential `%s`.\n' % SESSIONS['codex'],
                encoding='utf-8')
            self.assertRegex(run_sweep(health).stdout, r'ckid:codex=OK\(')

    def test_checkpoint_naming_only_foreign_ids_is_drift(self):
        """The other direction, so the relaxation above cannot be vacuous: a checkpoint that
        names identities but NOT the registered seat is exactly the condition worth waking
        someone for -- a successor would resume from another seat's state."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            (health.parent / 'codex-resume-CURRENT.md').write_text(
                'task `019fe03e-8d7f-74b2-9b73-631ff1fee2d0` and session\n'
                '`99999999-9999-9999-9999-999999999999` only.\n', encoding='utf-8')
            self.assertRegex(run_sweep(health).stdout, r'ckid:codex=DRIFT\(')

    def test_checkpoint_without_any_session_is_unattributed_not_ok(self):
        """A checkpoint that names no session must not read OK -- 'could not tell' is not
        the same as 'matches', and a green token that means both is worth less than none."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            (health.parent / 'fable-resume-CURRENT.md').write_text(
                'no session id here', encoding='utf-8')
            self.assertRegex(run_sweep(health).stdout, r'ckid:fable=UNATTRIBUTED\(')

    # ---- (d) watchhb and lease age read as a PAIR ---------------------------

    def test_stale_watch_is_dead_seat_beside_dark_lease_and_watch_only_beside_live(self):
        """The same STALE watch means opposite things depending on the lease beside it.
        Reading either token alone cannot tell those apart."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            write_lease(health, 'codex', 90, 30)   # DARK
            write_lease(health, 'sol', 1, 30)      # LIVE
            stale = datetime.now(timezone.utc) - timedelta(minutes=40)
            for lane in ('codex', 'sol'):
                hb = health / ('.%s-inbound-watch-heartbeat' % lane)
                hb.write_text('session=%s\n' % SESSIONS[lane], encoding='utf-8')
                os.utime(hb, (stale.timestamp(), stale.timestamp()))
            line = run_sweep(health).stdout
            self.assertIn('pair=DEAD-SEAT', line)
            self.assertIn('pair=WATCH-ONLY', line)
            self.assertRegex(line, r'watchhb:codex=STALE\([\d.]+m,pair=DEAD-SEAT\)')
            self.assertRegex(line, r'watchhb:sol=STALE\([\d.]+m,pair=WATCH-ONLY\)')

    def test_fresh_watch_beside_dark_lease_is_unrenewed(self):
        """Armed but not renewing: the seat must TAKE A TURN, not re-arm its watch."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            write_lease(health, 'codex', 90, 30)
            hb = health / '.codex-inbound-watch-heartbeat'
            hb.write_text('session=%s\n' % SESSIONS['codex'], encoding='utf-8')
            self.assertIn('pair=UNRENEWED', run_sweep(health).stdout)

    # ---- [B3] dormancy must be DECLARED, never inferred from prose ----------

    def test_negating_notes_do_not_suppress_dark(self):
        """[B3]. The old predicate had a 100-character proximity arm with no polarity, so
        a note asserting the lane is NOT dormant matched and converted a genuinely dark
        seat into SEATED-UNLEASED. That direction silences a true alarm rather than
        raising a false one, which is the expensive way to be wrong."""
        negating = [
            'lane declared leaseMinutes 20; it is not dormant and is actively working',
            'declared 20m cadence. Not dormant, not parked, actively reviewing the range',
            'heartbeat intake complete; declared cadence 5; no dormancy claimed by this lane',
            'the lane declared its cadence and dormancy does not apply here',
        ]
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            for note in negating:
                all_fresh(health)
                write_lease(health, 'sol', 90, 30, note=note)   # well past the 50m threshold
                line = run_sweep(health).stdout
                self.assertEqual(
                    lane_state(line, 'sol'), 'DARK',
                    'a note that DENIES dormancy must not suppress DARK: %r' % note)

    def test_declared_dormancy_tokens_still_suppress_dark(self):
        """The two explicit tokens remain the supported way to declare dormancy -- the
        fix removes inference, not the feature."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            for note in ('DORMANT-BY-BLOCKER waiting on hosted CI',
                         'SEATED-UNLEASED pending hub registration'):
                all_fresh(health)
                write_lease(health, 'sol', 90, 30, note=note)
                self.assertEqual(lane_state(run_sweep(health).stdout, 'sol'), 'SEATED-UNLEASED',
                                 'explicit token must still declare dormancy: %r' % note)

    def test_dormancy_is_never_inferred_from_free_text(self):
        """Prose that talks about dormancy without using a token is not a declaration."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            for note in ('going dormant for a while',
                         'declared dormant by the hub earlier today',
                         'dormancy expected overnight'):
                all_fresh(health)
                write_lease(health, 'sol', 90, 30, note=note)
                self.assertEqual(lane_state(run_sweep(health).stdout, 'sol'), 'DARK',
                                 'dormancy must be DECLARED with a token, not inferred: %r' % note)

    # ---- SWEEP-REG-1: the instrument must fail LOUD, never invent thresholds ----

    def _run_isolated_copy(self, health, classes_text=None):
        """Run a copy of the sweep from a directory that has no class file (or a bad one).

        This is a real deployment shape, not a contrivance: the sweep was first exercised
        live by extracting the single script out of the tree, which is exactly how the
        regression was found."""
        with tempfile.TemporaryDirectory() as scriptdir:
            copy = Path(scriptdir) / 'board-health-sweep.ps1'
            copy.write_bytes(SWEEP.read_bytes())
            if classes_text is not None:
                (Path(scriptdir) / 'lane-health-classes.psd1').write_text(
                    classes_text, encoding='utf-8')
            return subprocess.run(
                ['pwsh.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                 'Bypass', '-File', str(copy), '-HealthDir', str(health)],
                text=True, capture_output=True)

    def test_missing_class_file_fails_loud_and_classifies_nothing(self):
        """SWEEP-REG-1. With the class file unreadable the thresholds cast to 0, the
        boundary became min(cadence,0)+0 = 0, and EVERY lane with a positive lease age was
        called DARK -- observed live on all five lanes, including a 0.4-minute-old lease.

        A board-wide false DARK is what triggers reseats and succession, so the instrument
        must refuse to classify rather than report a confident wrong answer."""
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            result = self._run_isolated_copy(health)

            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn('OVERALL=INSTRUMENT-FAILED', result.stdout)
            # The load-bearing assertion: NO lane verdict of any kind is emitted.
            for lane in ('fable', 'codex', 'sol', 'opus', 'claude-review'):
                self.assertIsNone(lane_state(result.stdout, lane),
                                  'no lane may be classified when the thresholds are unknown')
            self.assertNotIn('DARK', result.stdout)

    def test_class_file_that_parses_but_carries_a_zero_threshold_also_fails_loud(self):
        """Validating that the FILE parsed is not enough -- a psd1 that loads with a zero or
        missing threshold produces the identical board-wide false DARK. The values are what
        matter, so the values are what is checked."""
        for bad in ('@{ DarkThresholdCapMinutes = 0; DarkThresholdGraceMinutes = 20 }',
                    '@{ DarkThresholdCapMinutes = 30; DarkThresholdGraceMinutes = 0 }',
                    '@{ LaneStateClasses = @("LIVE") }'):
            with tempfile.TemporaryDirectory() as d:
                health = make_tree(d)
                all_fresh(health)
                result = self._run_isolated_copy(health, classes_text=bad)
                self.assertEqual(result.returncode, 3,
                                 'must refuse to classify for %r' % bad)
                self.assertIn('OVERALL=INSTRUMENT-FAILED', result.stdout)
                self.assertIsNone(lane_state(result.stdout, 'sol'))

    def test_a_valid_class_file_beside_an_isolated_copy_still_works(self):
        """The failure must be attributable to the missing thresholds and nothing else --
        the same isolated copy, given a good class file, classifies normally."""
        good = CLASSES.read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            result = self._run_isolated_copy(health, classes_text=good)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(lane_state(result.stdout, 'sol'), 'LIVE')
            self.assertNotIn('INSTRUMENT-FAILED', result.stdout)

    # ---- the class file must actually GOVERN, not merely be read ------------

    def test_watch_heartbeat_freshness_is_governed_by_the_class_file(self):
        """LANE-4 residual on 1f1e2264..5b3d9d2c. `$hbFresh` was read from
        WatchHeartbeatFreshMinutes and never used -- the comparison hardcoded 5 -- so that
        value had two authorities that happened to agree, and editing the psd1 changed
        nothing SILENTLY.

        The test is the discriminating one: it changes the class file and requires the
        BEHAVIOUR to follow. A test that only asserted the field is read would have passed
        against the broken code."""
        good = CLASSES.read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            hb = health / '.codex-inbound-watch-heartbeat'
            hb.write_text('session=%s\n' % SESSIONS['codex'], encoding='utf-8')
            stale = datetime.now(timezone.utc) - timedelta(minutes=8)
            os.utime(hb, (stale.timestamp(), stale.timestamp()))

            # Default is 5 minutes, so an 8-minute-old heartbeat is STALE.
            r = self._run_isolated_copy(health, classes_text=good)
            self.assertRegex(r.stdout, r'watchhb:codex=STALE\(')

            # Raise the threshold past 8 in the CLASS FILE ONLY. If the value governs, the
            # same heartbeat must now read OK; against the hardcoded 5 it stayed STALE.
            widened = good.replace('WatchHeartbeatFreshMinutes = 5',
                                   'WatchHeartbeatFreshMinutes = 20')
            self.assertNotEqual(widened, good, 'fixture edit did not apply')
            r2 = self._run_isolated_copy(health, classes_text=widened)
            self.assertRegex(r2.stdout, r'watchhb:codex=OK\(')

    def test_missing_heartbeat_freshness_also_fails_loud(self):
        """Same fail-open shape as the DARK regression, one field over: without the key
        $hbFresh would be 0 and every heartbeat would read STALE."""
        good = CLASSES.read_text(encoding='utf-8')
        without = '\n'.join(l for l in good.splitlines()
                            if 'WatchHeartbeatFreshMinutes' not in l)
        with tempfile.TemporaryDirectory() as d:
            health = make_tree(d)
            all_fresh(health)
            r = self._run_isolated_copy(health, classes_text=without)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn('OVERALL=INSTRUMENT-FAILED', r.stdout)

    # ---- the shared class list is genuinely shared --------------------------

    def test_both_sweeps_read_the_same_class_file(self):
        text_board = SWEEP.read_text(encoding='utf-8')
        text_json = (ROOT / 'tools' / 'coordination' / 'sweep-lane-health.ps1').read_text(encoding='utf-8')
        for text in (text_board, text_json):
            self.assertIn('lane-health-classes.psd1', text)
        self.assertTrue(CLASSES.is_file())

    def test_promoted_sweep_is_ascii_only(self):
        """The script declares ASCII only, and PowerShell on this board has been bitten by
        non-ASCII before."""
        raw = SWEEP.read_bytes()
        self.assertEqual([b for b in raw if b > 127], [])


if __name__ == '__main__':
    unittest.main()
