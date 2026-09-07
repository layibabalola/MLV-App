import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "coordination",
    "freeze-factory-cards.py",
)

# Import the module under test directly by path so this test carries zero
# dependency on tools/coordination being an importable package (its filename
# has a hyphen, so `import` cannot reach it any other way).
import importlib.util

_spec = importlib.util.spec_from_file_location("freeze_factory_cards", SCRIPT)
ffc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ffc)

FROZEN_STATE = ffc.FROZEN_STATE

# The 15 seeded current sonnet-owned product/playback cards, deep-identical,
# never derived, never re-typed. Kept intentionally small/representative but
# real field shapes (state/track present, some with extra history fields).
SEEDED_SONNET_CARDS = [
    {"id": "USECASE-1", "kind": "playback", "owner": "sonnet", "state": "booked", "track": "playback", "title": "A"},
    {"id": "MEASURE-STRATEGY-1", "kind": "playback", "owner": "sonnet", "state": "open", "track": "playback"},
    {"id": "VENUE-NOISE-1", "kind": "playback", "owner": "sonnet", "state": "open-risk", "track": "playback"},
    {"id": "C2-SUBMIT-2", "kind": "playback", "owner": "sonnet", "state": "landed", "track": "playback"},
    {"id": "C2-PROV-1", "kind": "playback", "owner": "sonnet", "state": "closed-fixed", "track": "playback"},
    {"id": "C2-TELEM-2", "kind": "playback", "owner": "sonnet", "state": "consult-open", "track": "playback"},
    {"id": "P-1", "kind": "product", "owner": "sonnet", "state": "booked", "track": "product", "history": [1, 2, 3]},
    {"id": "P-2", "kind": "product", "owner": "sonnet", "state": "open", "track": "product"},
    {"id": "P-3", "kind": "product", "owner": "sonnet", "state": "answered-folded", "track": "product"},
    {"id": "P-4", "kind": "product", "owner": "sonnet", "state": "surfaced-awaiting-ordering", "track": "product"},
    {"id": "P-5", "kind": "playback", "owner": "sonnet", "state": "booked", "track": "playback"},
    {"id": "P-6", "kind": "playback", "owner": "sonnet", "state": "changes-requested-awaiting-acceptance-evidence", "track": "playback"},
    {"id": "P-7", "kind": "factory", "owner": "sonnet", "state": "open", "track": "factory"},
    {"id": "P-8", "kind": "product", "owner": "sonnet", "state": "withdrawn", "track": "product"},
    {"id": "P-9", "kind": "playback", "owner": "sonnet", "state": "open", "track": "playback", "priority": 3},
]


def _write_queue(path, items, extra_top_level=None):
    doc = {"items": items}
    if extra_top_level:
        doc.update(extra_top_level)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _run(args):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        text=True,
    )


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def qpath(self, name="queue.json"):
        return os.path.join(self.tmp.name, name)

    def rpath(self, name="receipt.json"):
        return os.path.join(self.tmp.name, name)


class TestValidation(TmpCase):
    def test_malformed_not_object_zero_writes(self):
        q = self.qpath()
        with open(q, "w", encoding="utf-8") as fh:
            fh.write("[1,2,3]")
        before = open(q, "rb").read()
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(open(q, "rb").read(), before)
        self.assertFalse(os.path.exists(r))

    def test_duplicate_ids_zero_writes(self):
        q = self.qpath()
        _write_queue(q, [{"id": "X-1", "state": "open"}, {"id": "X-1", "state": "open"}])
        before = open(q, "rb").read()
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(open(q, "rb").read(), before)
        self.assertFalse(os.path.exists(r))

    def test_missing_items_key_zero_writes(self):
        q = self.qpath()
        with open(q, "w", encoding="utf-8") as fh:
            json.dump({"note": "no items here"}, fh)
        before = open(q, "rb").read()
        res = _run(["--queue", q, "--dry-run"])
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(open(q, "rb").read(), before)

    def test_empty_id_zero_writes(self):
        q = self.qpath()
        _write_queue(q, [{"id": "", "state": "open"}])
        res = _run(["--queue", q, "--dry-run"])
        self.assertNotEqual(res.returncode, 0)


class TestSeededSonnetCards(TmpCase):
    def test_all_15_deep_identical_after_apply(self):
        q = self.qpath()
        _write_queue(q, copy.deepcopy(SEEDED_SONNET_CARDS))
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        self.assertEqual(len(result["items"]), 15)
        for original, after in zip(SEEDED_SONNET_CARDS, result["items"]):
            self.assertEqual(original, after, f"card {original['id']} mutated")


class TestExactKeepLiveSet(TmpCase):
    def test_exact_set_and_scopeless(self):
        ids = [
            "USECASE-1", "MEASURE-STRATEGY-1", "VENUE-NOISE-1",
            "C2-SUBMIT-2", "C2-PROV-1", "C2-TELEM-2",
        ]
        cards = [{"id": i, "state": "open"} for i in ids]
        # A control card with no kind and no exemption -> factory + scopeless.
        cards.append({"id": "NOPE-1", "state": "open"})
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        by_id = {c["id"]: c for c in result["items"]}
        for i in ids:
            self.assertEqual(by_id[i]["kind"], "playback", i)
            self.assertEqual(by_id[i]["state"], "open")  # never frozen
        self.assertEqual(by_id["NOPE-1"]["kind"], "factory")
        self.assertEqual(by_id["NOPE-1"]["state"], FROZEN_STATE)

        receipt = json.load(open(r, encoding="utf-8"))
        self.assertEqual(sorted(receipt["scopelessIds"]), sorted(ids + ["NOPE-1"]))


class TestPathClassification(TmpCase):
    def test_mixed_unknown_windows_glob(self):
        cards = [
            {"id": "PROD-1", "state": "open", "scope": "src/foo/bar.cpp"},
            {"id": "PROD-2", "state": "open", "scope": "tests\\gui\\panel_test.cpp:88"},
            {"id": "PLAY-1", "state": "open", "scope": "src/gpu/decode.cu"},
            {"id": "PLAY-2", "state": "open", "scope": "platform/qt/RenderThread.cpp"},
            # platform/qt/ is product but the GpuDisplay* prefix is narrower playback
            {"id": "PLAY-3", "state": "open", "scope": "platform/qt/GpuDisplayWidget.cpp"},
            {"id": "FAC-1", "state": "open", "scope": "tools/coordination/x.py"},
            {"id": "FAC-2", "state": "open", "scope": "docs/plan.md"},
            {"id": "FAC-3", "state": "open", "scope": ".github/workflows/ci.yml"},
            {"id": "FAC-4", "state": "open", "scope": ".claude-state/foo.md"},
            # factory wins over product when both present in one scope list
            {"id": "MIX-1", "state": "open", "scope": ["src/foo.cpp", "tools/bar.py"]},
            # playback wins over product when both present
            {"id": "MIX-2", "state": "open", "scope": ["src/foo.cpp", "src/gpu/x.cu"]},
            # bare filename / prose only -> no path token -> scopeless -> factory
            {"id": "PROSE-1", "state": "open", "scope": "fix the flicker bug, see notes.txt"},
            # glob-ish token still classifies via prefix match
            {"id": "GLOB-1", "state": "open", "scope": "tests/fixtures/*.cdng"},
        ]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        by_id = {c["id"]: c for c in result["items"]}

        self.assertEqual(by_id["PROD-1"]["kind"], "product")
        self.assertEqual(by_id["PROD-2"]["kind"], "product")
        self.assertEqual(by_id["PLAY-1"]["kind"], "playback")
        self.assertEqual(by_id["PLAY-2"]["kind"], "playback")
        self.assertEqual(by_id["PLAY-3"]["kind"], "playback")
        for fid in ("FAC-1", "FAC-2", "FAC-3", "FAC-4"):
            self.assertEqual(by_id[fid]["kind"], "factory", fid)
            self.assertEqual(by_id[fid]["state"], FROZEN_STATE, fid)
        self.assertEqual(by_id["MIX-1"]["kind"], "factory")
        self.assertEqual(by_id["MIX-2"]["kind"], "playback")
        self.assertEqual(by_id["PROSE-1"]["kind"], "factory")

        receipt = json.load(open(r, encoding="utf-8"))
        self.assertIn("PROSE-1", receipt["scopelessIds"])
        self.assertNotIn("PROD-1", receipt["scopelessIds"])


class TestTerminalPreservation(TmpCase):
    def test_terminal_factory_card_not_frozen(self):
        cards = [
            {"id": "T-1", "state": "closed-fixed", "scope": "tools/x.py"},
            {"id": "T-2", "state": "RETIRED", "scope": "docs/x.md"},
            {"id": "L-1", "state": "open", "scope": "tools/x.py"},
        ]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        by_id = {c["id"]: c for c in result["items"]}
        self.assertEqual(by_id["T-1"]["state"], "closed-fixed")
        self.assertEqual(by_id["T-2"]["state"], "RETIRED")
        self.assertEqual(by_id["L-1"]["state"], FROZEN_STATE)

    def test_already_frozen_stays_frozen(self):
        cards = [{"id": "AF-1", "state": FROZEN_STATE, "kind": "factory", "owner": "codex"}]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        self.assertEqual(result["items"][0]["state"], FROZEN_STATE)
        self.assertNotIn("freezeProvenance", result["items"][0])


class TestFieldAndHistoryPreservation(TmpCase):
    def test_unknown_fields_and_history_and_ordering_preserved(self):
        cards = [
            {"id": "Z-2", "state": "open", "scope": "tools/x.py", "weird": {"nested": [1, 2]}, "history": ["a", "b"]},
            {"id": "Z-1", "state": "open", "scope": "src/x.cpp", "custom_field": True},
        ]
        q = self.qpath()
        _write_queue(q, cards, extra_top_level={"note": "board note", "version": 3})
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        self.assertEqual(result["note"], "board note")
        self.assertEqual(result["version"], 3)
        self.assertEqual([c["id"] for c in result["items"]], ["Z-2", "Z-1"])  # order preserved
        self.assertEqual(result["items"][0]["weird"], {"nested": [1, 2]})
        self.assertEqual(result["items"][0]["history"], ["a", "b"])
        self.assertEqual(result["items"][1]["custom_field"], True)

    def test_existing_factory_other_owner_freezes_if_nonterminal(self):
        cards = [{"id": "OO-1", "kind": "factory", "owner": "codex", "state": "open"}]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        self.assertEqual(result["items"][0]["state"], FROZEN_STATE)
        self.assertEqual(result["items"][0]["freezeProvenance"]["previousState"], "open")

    def test_unknown_existing_kind_retained_conservatively(self):
        cards = [{"id": "UK-1", "kind": "experimental", "owner": "codex", "state": "open"}]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        self.assertEqual(result["items"][0]["kind"], "experimental")
        self.assertEqual(result["items"][0]["state"], "open")  # never frozen: not literally 'factory'
        self.assertIn("UK-1", res.stdout)


class TestNoDispatchableFactoryAfterApply(TmpCase):
    def test_no_live_factory_cards_remain(self):
        cards = [
            {"id": "D-1", "state": "open", "scope": "tools/a.py"},
            {"id": "D-2", "state": "open-risk", "scope": "docs/b.md"},
            {"id": "D-3", "state": "closed-fixed", "scope": "tools/c.py"},
        ]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        result = json.load(open(q, encoding="utf-8"))
        for c in result["items"]:
            if c["kind"] == "factory" and c["state"] not in ffc.TERMINAL_STATES:
                self.assertEqual(c["state"], FROZEN_STATE)


class TestDryRun(TmpCase):
    def test_dry_run_no_writes(self):
        cards = [{"id": "DR-1", "state": "open", "scope": "tools/a.py"}]
        q = self.qpath()
        _write_queue(q, cards)
        before = open(q, "rb").read()
        res = _run(["--queue", q, "--dry-run"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(open(q, "rb").read(), before)
        self.assertIn("dryRunDiffSha256", res.stdout)

    def test_default_is_dry_run(self):
        cards = [{"id": "DR-2", "state": "open", "scope": "tools/a.py"}]
        q = self.qpath()
        _write_queue(q, cards)
        before = open(q, "rb").read()
        res = _run(["--queue", q])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(open(q, "rb").read(), before)


class TestByteNonShrinking(TmpCase):
    def test_apply_output_not_shorter_than_input(self):
        # A long-winded original with lots of whitespace/comments-as-fields so
        # the computed compact-ish result would otherwise be shorter.
        cards = [{"id": "BN-1", "state": "open", "scope": "tools/a.py",
                  "title": "x" * 200}]
        q = self.qpath()
        _write_queue(q, cards)
        original_len = os.path.getsize(q)
        r = self.rpath()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertGreaterEqual(os.path.getsize(q), original_len)


class TestIdempotency(TmpCase):
    def test_second_apply_is_noop_and_receipt_unchanged(self):
        cards = [{"id": "ID-1", "state": "open", "scope": "tools/a.py"}]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        res1 = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res1.returncode, 0, res1.stderr)
        queue_after_1 = open(q, "rb").read()
        receipt_after_1 = open(r, "rb").read()

        res2 = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertEqual(res2.returncode, 0, res2.stderr)
        self.assertEqual(open(q, "rb").read(), queue_after_1)
        self.assertEqual(open(r, "rb").read(), receipt_after_1)


class TestConflictingReceipt(TmpCase):
    def test_conflicting_receipt_zero_writes(self):
        cards = [{"id": "CR-1", "state": "open", "scope": "tools/a.py"}]
        q = self.qpath()
        _write_queue(q, cards)
        r = self.rpath()
        with open(r, "w", encoding="utf-8") as fh:
            json.dump({
                "recordedUtc": "2000-01-01T00:00:00Z",
                "queueSha256": "0" * 64,
                "frozenCount": 999,
                "dryRunDiffSha256": "0" * 64,
                "scopelessIds": [],
            }, fh)
        before = open(q, "rb").read()
        res = _run(["--queue", q, "--receipt", r, "--apply"])
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(open(q, "rb").read(), before)


class TestConcurrentModification(TmpCase):
    def test_queue_changed_after_load_refuses(self):
        # Simulate the race by pre-seeding a queue whose on-disk bytes will
        # differ from what compute_plan saw, using the library function
        # directly rather than trying to win an actual filesystem race.
        cards = [{"id": "CM-1", "state": "open", "scope": "tools/a.py"}]
        q = self.qpath()
        _write_queue(q, cards)
        original_bytes = ffc.load_queue_bytes(q)
        data = ffc.parse_queue(original_bytes)
        plan = ffc.compute_plan(data)
        # Mutate the file on disk after the "load" above, before any write.
        with open(q, "a", encoding="utf-8") as fh:
            fh.write(" ")
            fh.write("\n")
        # A trailing-whitespace-only change is still a byte difference.
        current_bytes = ffc.load_queue_bytes(q)
        self.assertNotEqual(current_bytes, original_bytes)


class TestRequiredArgs(TmpCase):
    def test_apply_without_receipt_fails(self):
        cards = [{"id": "RA-1", "state": "open"}]
        q = self.qpath()
        _write_queue(q, cards)
        res = _run(["--queue", q, "--apply"])
        self.assertNotEqual(res.returncode, 0)

    def test_missing_queue_file_fails(self):
        res = _run(["--queue", os.path.join(self.tmp.name, "nope.json"), "--dry-run"])
        self.assertNotEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
