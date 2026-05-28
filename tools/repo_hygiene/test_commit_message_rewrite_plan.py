import unittest

from .commit_message_rewrite_plan import (
    classify_vague_subject,
    closeout_detail_for_subject,
    is_vague_subject,
    parse_log_rows,
    proposed_subject_for_commit,
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


if __name__ == "__main__":
    unittest.main()
