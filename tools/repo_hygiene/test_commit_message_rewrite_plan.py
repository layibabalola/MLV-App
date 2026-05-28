import unittest
from pathlib import Path

from .commit_message_rewrite_plan import (
    classify_vague_subject,
    closeout_detail_for_subject,
    harmonize_work_block_subjects,
    is_vague_subject,
    parse_log_rows,
    proposed_subject_for_commit,
    subject_from_delta,
)


class CommitMessageRewritePlanTests(unittest.TestCase):
    def test_classifies_generated_closeout_subjects(self) -> None:
        classified = classify_vague_subject(
            "chore(closeout): repair metrics.json, handoff.json, session.json, and 1 more for wb-demo before final push"
        )

        self.assertIsNotNone(classified)
        self.assertEqual(classified["family"], "work-block evidence repair")
        self.assertEqual(classified["workBlockId"], "wb-demo")
        self.assertEqual(classified["reason"], "final push")

    def test_rejects_human_subjects_as_not_vague(self) -> None:
        self.assertFalse(is_vague_subject("closeout: require human commit subjects"))

    def test_builds_closeout_body_detail_from_generated_subject(self) -> None:
        detail = closeout_detail_for_subject(
            "chore(closeout): checkpoint MainWindow.cpp, MainWindow.h, and dng.c for wb-demo"
        )

        self.assertEqual(detail, "Closeout: checkpoint MainWindow.cpp, MainWindow.h, and dng.c.")

    def test_proposal_uses_single_human_side_branch_subject(self) -> None:
        row = parse_log_rows(
            "abc\x1fparent\x1f2026-05-28T00:00:00-05:00\x1f"
            "chore(closeout): checkpoint MainWindow.cpp for wb-demo\n"
        )[0]
        proposal = proposed_subject_for_commit(
            row,
            {
                "wb-demo": {
                    "humanSubjects": [
                        {"hash": "def", "subject": "export: apply Auto Look Assist raw defaults"},
                    ]
                }
            },
        )

        self.assertEqual(proposal["status"], "candidate")
        self.assertEqual(proposal["subject"], "export: apply Auto Look Assist raw defaults")

    def test_proposal_requires_review_without_human_subject(self) -> None:
        row = parse_log_rows(
            "abc\x1fparent\x1f2026-05-28T00:00:00-05:00\x1f"
            "chore(closeout): checkpoint MainWindow.cpp for wb-demo\n"
        )[0]
        proposal = proposed_subject_for_commit(row, {})

        self.assertEqual(proposal["status"], "needs_subject")
        self.assertIsNone(proposal["subject"])

    def test_delta_subject_summarizes_qt_paths(self) -> None:
        row = parse_log_rows(
            "abc\x1fparent\x1f2026-05-28T00:00:00-05:00\x1f"
            "brokered closeout checkpoint\n"
        )[0]

        proposal = subject_from_delta(Path("."), row, ["platform/qt/MainWindow.cpp", "platform/qt/MainWindow.h"])

        self.assertEqual(proposal["status"], "delta_generated")
        self.assertEqual(proposal["subject"], "qt: update Qt playback controls")

    def test_multiple_human_side_branch_subjects_are_drafted(self) -> None:
        row = parse_log_rows(
            "abc\x1fparent\x1f2026-05-28T00:00:00-05:00\x1f"
            "merge(closeout): integrate wb-demo closeout hardening into master\n"
        )[0]
        proposal = proposed_subject_for_commit(
            row,
            {
                "wb-demo": {
                    "humanSubjects": [
                        {"hash": "def", "subject": "playback: update scale handling"},
                        {"hash": "fed", "subject": "closeout: stabilize validation budgets"},
                    ]
                }
            },
        )

        self.assertEqual(proposal["status"], "multi_subject_draft")
        self.assertEqual(proposal["subject"], "closeout: stabilize validation budgets")

    def test_harmonizes_evidence_subject_with_checkpoint_delta(self) -> None:
        entries = [
            {
                "hash": "abc",
                "family": "work-block checkpoint",
                "workBlockId": "wb-demo",
                "proposal": {"subject": "qt: update Qt playback controls", "confidence": "medium"},
            },
            {
                "hash": "def",
                "family": "work-block evidence repair",
                "workBlockId": "wb-demo",
                "proposal": {"subject": "closeout: record closeout evidence", "confidence": "low"},
            },
        ]

        harmonize_work_block_subjects(entries)

        self.assertEqual(entries[1]["proposal"]["subject"], "qt: update Qt playback controls")
        self.assertEqual(entries[1]["proposal"]["sourceCommit"], "abc")


if __name__ == "__main__":
    unittest.main()
