"""Refusal-path unit tests for demote-factory-bridge.ps1 (plan step 0.4c-i,
CI-GUARDRAIL-MOVE-1).

The script's job is to refuse -- before touching any workflow file -- unless BOTH
`0.4c-guardrail-move.json` and `0.4b-required-checks.json` are present, well-formed, and say
the demotion is safe. `0.4b-required-checks.json` genuinely does not exist yet at this point
in the plan's fixed execution order (0.4a-i -> 0.4a-ii -> 0.4c-i -> 0.4b-i -> 0.4b-ii ->
0.4c-ii), so every test here uses SYNTHETIC fixture receipts written to a tmp_path directory
via `-ReceiptsDir`, never the real board paths. The script's happy path (reached only once
both receipts validate) is deliberately NOT exercised here -- see the script's own comments.
"""

import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "demote-factory-bridge.ps1"

GUARDRAIL_MOVE_NAME = "0.4c-guardrail-move.json"
REQUIRED_CHECKS_NAME = "0.4b-required-checks.json"

VALID_GUARDRAIL_MOVE = {"conclusion": "success"}
VALID_REQUIRED_CHECKS = {
    "headSha": "a" * 40,
    "preContexts": ["Factory Bridge Regressions"],
    "postContexts": [
        "Repo Hygiene Python (windows-latest)",
        "Repo Hygiene Python (ubuntu-latest)",
        "Windows GUI Pilot",
        "Windows Product Oracles",
        "Batch Compile",
    ],
    "snapshotRowSha256": "b" * 64,
}


def write_receipt(receipts_dir, name, payload):
    """Writes `payload` as JSON to receipts_dir/name. `payload=None` writes nothing (absent).
    A string `payload` is written verbatim, so a caller can inject malformed JSON."""
    if payload is None:
        return
    path = receipts_dir / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def run_gate(receipts_dir):
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-ReceiptsDir",
            str(receipts_dir),
        ],
        text=True,
        capture_output=True,
    )
    return result


def assert_refused(result, expected_substring):
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        "expected a non-zero (refused) exit, got %r; stdout=%r stderr=%r"
        % (result.returncode, result.stdout, result.stderr)
    )
    assert expected_substring in combined, (
        "expected %r in combined output; stdout=%r stderr=%r"
        % (expected_substring, result.stdout, result.stderr)
    )


@pytest.fixture
def receipts_dir(tmp_path):
    d = tmp_path / "receipts"
    d.mkdir()
    return d


def test_refuses_when_both_receipts_are_absent(receipts_dir):
    result = run_gate(receipts_dir)
    assert_refused(result, GUARDRAIL_MOVE_NAME)
    assert_refused(result, "is absent")


def test_refuses_when_only_guardrail_move_receipt_is_absent(receipts_dir):
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, VALID_REQUIRED_CHECKS)
    result = run_gate(receipts_dir)
    assert_refused(result, GUARDRAIL_MOVE_NAME)
    assert_refused(result, "is absent")


def test_refuses_when_guardrail_move_receipt_is_malformed_json(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, "{not valid json")
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, VALID_REQUIRED_CHECKS)
    result = run_gate(receipts_dir)
    assert_refused(result, GUARDRAIL_MOVE_NAME)
    assert_refused(result, "does not parse as JSON")


def test_refuses_when_guardrail_move_receipt_is_not_a_json_object(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, ["not", "an", "object"])
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, VALID_REQUIRED_CHECKS)
    result = run_gate(receipts_dir)
    assert_refused(result, GUARDRAIL_MOVE_NAME)
    assert_refused(result, "is not a JSON object")


def test_refuses_when_guardrail_move_receipt_conclusion_is_missing(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, {"someOtherKey": "value"})
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, VALID_REQUIRED_CHECKS)
    result = run_gate(receipts_dir)
    assert_refused(result, GUARDRAIL_MOVE_NAME)
    assert_refused(result, "conclusion=success")


def test_refuses_when_guardrail_move_receipt_conclusion_is_not_success(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, {"conclusion": "failure"})
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, VALID_REQUIRED_CHECKS)
    result = run_gate(receipts_dir)
    assert_refused(result, GUARDRAIL_MOVE_NAME)
    assert_refused(result, "conclusion=success")


def test_refuses_when_only_required_checks_receipt_is_absent(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "is absent")


def test_refuses_when_required_checks_receipt_is_malformed_json(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, "{also not valid")
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "does not parse as JSON")


def test_refuses_when_required_checks_receipt_is_not_a_json_object(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, "42")
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "is not a JSON object")


def test_refuses_when_required_checks_receipt_is_missing_post_contexts(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(
        receipts_dir,
        REQUIRED_CHECKS_NAME,
        {"headSha": "a" * 40, "preContexts": [], "snapshotRowSha256": "b" * 64},
    )
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "missing postContexts")


def test_refuses_when_required_checks_receipt_still_lists_the_bridge_job(receipts_dir):
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    still_required = dict(VALID_REQUIRED_CHECKS)
    still_required["postContexts"] = list(VALID_REQUIRED_CHECKS["postContexts"]) + [
        "Factory Bridge Regressions"
    ]
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, still_required)
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "still lists 'Factory Bridge Regressions'")


def test_refuses_when_required_checks_receipt_post_contexts_is_a_single_bridge_string(
    receipts_dir,
):
    """A single-element JSON array can deserialize to a PowerShell scalar rather than an
    array in some ConvertFrom-Json shapes; this pins that the -contains check still catches
    the bridge job when postContexts holds exactly one (blocked) entry."""
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(
        receipts_dir,
        REQUIRED_CHECKS_NAME,
        {
            "headSha": "a" * 40,
            "preContexts": [],
            "postContexts": ["Factory Bridge Regressions"],
            "snapshotRowSha256": "b" * 64,
        },
    )
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "still lists 'Factory Bridge Regressions'")


def test_does_not_touch_any_workflow_file_on_refusal(receipts_dir, tmp_path):
    """Every refusal path fires before any file write; the script never even opens the
    workflow file, but this asserts the observable contract: no files besides the two
    synthetic receipts exist in the tmp tree after a refused run."""
    result = run_gate(receipts_dir)
    assert result.returncode != 0
    all_files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert all_files == [], "refusal must not create or modify any file: found %r" % all_files
