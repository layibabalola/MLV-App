"""Contract tests for durable dispatch intents and automatic queue repair."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "coordination" / "dispatch-queue.py"
WRITER = ROOT / "tools" / "coordination" / "write-verified-json.ps1"


def run_tool(*arguments):
    return subprocess.run(
        ["py", "-3", str(TOOL), *map(str, arguments)],
        text=True,
        capture_output=True,
    )


def make_fixture(root):
    coordination = Path(root) / "coordination"
    dual = coordination / "dual-lane"
    proposals = coordination / "proposals"
    proposals.mkdir(parents=True)
    dual.mkdir(parents=True)
    (dual / "queue.json").write_text(
        json.dumps(
            {
                "schema": "dual-lane-queue.v1",
                "note": "test queue",
                "updated": "2026-08-11T00:00:00Z",
                "updatedBySeq": 9,
                "items": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (dual / "fable.md").write_text(
        "## SEQ 9 | earlier\nno dispatch\n"
        "## SEQ 10 | dispatch\n"
        "**`FACTORY-MATURITY-1`** -- **DISPATCHED to opus.**\n",
        encoding="utf-8",
    )
    artifact = proposals / "factory.md"
    artifact.write_text("review packet\n", encoding="utf-8")
    (dual / "dispatch-intents.json").write_text(
        json.dumps(
            {
                "schema": "dual-lane-dispatch-intents.v1",
                "proseAuditFromSeq": 10,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    intent = {
        "schema": "dual-lane-dispatch-intent.v1",
        "intentId": "FACTORY-MATURITY-1-OPUS",
        "sourceDispatchId": "FACTORY-MATURITY-1",
        "createdAt": "2026-08-11T00:00:00Z",
        "source": {
            "ledger": "fable.md",
            "seq": 10,
            "artifact": "proposals/factory.md",
            "artifactSha256": hashlib.sha256(artifact.read_bytes()).hexdigest().upper(),
        },
        "card": {
            "id": "FACTORY-MATURITY-1-OPUS",
            "title": "Independently re-derive the factory maturity CI claims",
            "state": "dispatched",
            "owner": "opus",
            "priority": 1,
            "track": "factory",
            "dispatchedSeq": 10,
            "scope": "Re-derive from raw evidence; inherited packet numbers are not findings.",
        },
    }
    intent_file = Path(root) / "intent.json"
    intent_file.write_text(json.dumps(intent, indent=2), encoding="utf-8")
    return dual, intent_file, intent


class DispatchQueueTests(unittest.TestCase):

    def test_submit_persists_intent_then_materializes_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, intent_file, intent = make_fixture(temp)
            result = run_tool(
                "submit", "--dual-lane-dir", dual, "--writer", WRITER,
                "--intent-file", intent_file, "--apply", "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "HEALED")
            self.assertEqual(report["healed"], [intent["card"]["id"]])
            durable = dual / "dispatch-intents" / (intent["intentId"] + ".json")
            self.assertTrue(durable.is_file())
            queue = json.loads((dual / "queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["items"], [intent["card"]])
            self.assertEqual(queue["updatedBySeq"], 10)

    def test_reconcile_repairs_an_interrupted_submit_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, intent_file, intent = make_fixture(temp)
            intent_dir = dual / "dispatch-intents"
            intent_dir.mkdir()
            (intent_dir / (intent["intentId"] + ".json")).write_text(
                json.dumps(intent, indent=2), encoding="utf-8"
            )

            first = run_tool(
                "reconcile", "--dual-lane-dir", dual, "--writer", WRITER,
                "--apply", "--json",
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(json.loads(first.stdout)["status"], "HEALED")
            after_first = (dual / "queue.json").read_bytes()

            second = run_tool(
                "reconcile", "--dual-lane-dir", dual, "--writer", WRITER,
                "--apply", "--json",
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second.stdout)["status"], "OK")
            self.assertEqual((dual / "queue.json").read_bytes(), after_first)

    def test_reconcile_accepts_normal_card_lifecycle_without_reverting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, intent_file, intent = make_fixture(temp)
            submitted = run_tool(
                "submit", "--dual-lane-dir", dual, "--writer", WRITER,
                "--intent-file", intent_file, "--apply", "--json",
            )
            self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
            queue_path = dual / "queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            queue["items"][0].update(
                {
                    "state": "landed",
                    "owner": "fable",
                    "priority": 2,
                    "blocker": "none",
                }
            )
            queue_path.write_text(json.dumps(queue, indent=2), encoding="utf-8")
            before = queue_path.read_bytes()

            result = run_tool(
                "reconcile", "--dual-lane-dir", dual, "--writer", WRITER,
                "--apply", "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "OK")
            self.assertEqual(queue_path.read_bytes(), before)
            self.assertNotEqual(queue["items"][0], intent["card"])

            retried = run_tool(
                "submit", "--dual-lane-dir", dual, "--writer", WRITER,
                "--intent-file", intent_file, "--apply", "--json",
            )
            self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
            self.assertEqual(json.loads(retried.stdout)["status"], "OK")
            self.assertEqual(queue_path.read_bytes(), before)

    def test_different_dispatch_identity_fails_closed_without_mutating_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, intent_file, intent = make_fixture(temp)
            intent_dir = dual / "dispatch-intents"
            intent_dir.mkdir()
            (intent_dir / (intent["intentId"] + ".json")).write_text(
                json.dumps(intent, indent=2), encoding="utf-8"
            )
            queue_path = dual / "queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            conflicting = dict(intent["card"])
            conflicting["dispatchedSeq"] = 11
            queue["items"].append(conflicting)
            queue_path.write_text(json.dumps(queue, indent=2), encoding="utf-8")
            before = queue_path.read_bytes()

            result = run_tool(
                "reconcile", "--dual-lane-dir", dual, "--writer", WRITER,
                "--apply", "--json",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("different dispatch identity", result.stdout)
            self.assertEqual(queue_path.read_bytes(), before)

    def test_submit_refuses_preexisting_same_id_conflict_before_installing_intent(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, intent_file, intent = make_fixture(temp)
            queue_path = dual / "queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            conflicting = dict(intent["card"])
            conflicting["owner"] = "codex"
            queue["items"].append(conflicting)
            queue_path.write_text(json.dumps(queue, indent=2), encoding="utf-8")
            before = queue_path.read_bytes()

            result = run_tool(
                "submit", "--dual-lane-dir", dual, "--writer", WRITER,
                "--intent-file", intent_file, "--apply", "--json",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("conflicts with new durable intent", result.stdout)
            self.assertEqual(queue_path.read_bytes(), before)
            self.assertFalse((dual / "dispatch-intents").exists())

    def test_heal_never_regresses_updated_by_seq(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, _, intent = make_fixture(temp)
            intent_dir = dual / "dispatch-intents"
            intent_dir.mkdir()
            (intent_dir / (intent["intentId"] + ".json")).write_text(
                json.dumps(intent, indent=2), encoding="utf-8"
            )
            queue_path = dual / "queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            queue["updatedBySeq"] = 15
            queue_path.write_text(json.dumps(queue, indent=2), encoding="utf-8")

            result = run_tool(
                "reconcile", "--dual-lane-dir", dual, "--writer", WRITER,
                "--apply", "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "HEALED")
            healed = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(healed["updatedBySeq"], 15)

    def test_prose_only_dispatch_is_reported_and_never_invented(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, _, _ = make_fixture(temp)
            before = (dual / "queue.json").read_bytes()
            result = run_tool(
                "reconcile", "--dual-lane-dir", dual, "--writer", WRITER,
                "--apply", "--json",
            )
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "PROSE_ONLY")
            self.assertEqual(report["proseOnly"], ["FACTORY-MATURITY-1"])
            self.assertEqual((dual / "queue.json").read_bytes(), before)

    def test_source_artifact_hash_mismatch_blocks_intent(self):
        with tempfile.TemporaryDirectory() as temp:
            dual, intent_file, _ = make_fixture(temp)
            payload = json.loads(intent_file.read_text(encoding="utf-8"))
            payload["source"]["artifactSha256"] = "0" * 64
            intent_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            result = run_tool(
                "submit", "--dual-lane-dir", dual, "--writer", WRITER,
                "--intent-file", intent_file, "--apply", "--json",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("artifact hash mismatch", result.stdout)
            self.assertFalse((dual / "dispatch-intents").exists())


if __name__ == "__main__":
    unittest.main()
