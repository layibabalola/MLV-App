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


def run_gate(receipts_dir, board_root=None):
    args = [
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(SCRIPT),
        "-ReceiptsDir",
        str(receipts_dir),
    ]
    if board_root is not None:
        args += ["-BoardRoot", str(board_root)]
    result = subprocess.run(args, text=True, capture_output=True)
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


def test_board_root_is_genuinely_wired_to_the_default_receipts_path(tmp_path):
    """sol round 2, PR #83, MAJOR (restated): -BoardRoot's ONLY effect in the script is
    deriving the default -ReceiptsDir (Join-Path $BoardRoot
    '.claude-state\\coordination\\dual-lane\\receipts') when -ReceiptsDir is not passed.
    Every other test in this file always passes -ReceiptsDir explicitly, which bypasses that
    code path entirely -- so no test proved -BoardRoot is wired to anything real. This test
    is the one that does: it passes ONLY -BoardRoot (no -ReceiptsDir at all), places a valid
    receipt pair at the path -BoardRoot's own derivation formula predicts, and asserts the
    gate finds and validates them -- succeeding all the way to the happy-path stub output,
    which is only reachable if -BoardRoot genuinely routed to the right receipts directory.
    Measured to actually depend on -BoardRoot: renaming the script's -BoardRoot parameter
    (so it is silently unbound, per PowerShell's -File invocation behavior measured
    directly) makes this test fail, because the derived receipts directory would then be
    wrong and the receipts this test wrote would not be found."""
    board_root = tmp_path / "board"
    receipts_dir = board_root / ".claude-state" / "coordination" / "dual-lane" / "receipts"
    receipts_dir.mkdir(parents=True)
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, VALID_REQUIRED_CHECKS)

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), "-BoardRoot", str(board_root)],
        text=True, capture_output=True,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        "expected the gate to find receipts via -BoardRoot's own derivation and succeed;"
        " got exit=%r stdout=%r stderr=%r" % (result.returncode, result.stdout, result.stderr)
    )
    assert "both receipts validate" in combined, (
        "expected the happy-path validation message, reachable only if -BoardRoot routed"
        " correctly; stdout=%r stderr=%r" % (result.stdout, result.stderr)
    )


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
    array in some ConvertFrom-Json shapes; this pins that the shape/containment check still
    catches the bridge job when postContexts holds exactly one (blocked) entry."""
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


def test_refuses_when_required_checks_post_contexts_is_a_json_object(receipts_dir):
    """sol round 1, PR #83: an object-valued postContexts was being silently @()-coerced
    into a one-element array and accepted. Must now be refused as a malformed shape."""
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(
        receipts_dir,
        REQUIRED_CHECKS_NAME,
        '{"headSha": "%s", "preContexts": [], '
        '"postContexts": {"name": "Factory Bridge Regressions"}, '
        '"snapshotRowSha256": "%s"}' % ("a" * 40, "b" * 64),
    )
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "postContexts is not a JSON array")


def test_refuses_when_required_checks_post_contexts_is_an_empty_array(receipts_dir):
    """sol round 1, PR #83: an empty postContexts trivially 'does not contain' the bridge
    context and was silently accepted. An empty required-context list is itself malformed
    for a repo with any required checks and must be refused."""
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(
        receipts_dir,
        REQUIRED_CHECKS_NAME,
        {
            "headSha": "a" * 40,
            "preContexts": [],
            "postContexts": [],
            "snapshotRowSha256": "b" * 64,
        },
    )
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "postContexts is an empty array")


def test_refuses_when_required_checks_post_contexts_has_a_non_string_element(receipts_dir):
    """sol round 2, PR #83, MINOR: no test exercised the 'contains a non-string element'
    refusal branch -- the implementation refused a direct synthetic repro, but deleting that
    production branch would not have failed any committed test."""
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(
        receipts_dir,
        REQUIRED_CHECKS_NAME,
        '{"headSha": "%s", "preContexts": [], '
        '"postContexts": ["Windows GUI Pilot", 42], '
        '"snapshotRowSha256": "%s"}' % ("a" * 40, "b" * 64),
    )
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "non-string element")


def test_refuses_when_required_checks_post_contexts_has_a_whitespace_near_match(receipts_dir):
    """sol round 1, PR #83: a whitespace-padded near-match to the blocked context name
    ('Factory Bridge Regressions ') was not an exact match and was silently accepted. A
    receipt whose elements need trimming to match is itself a data-quality problem and must
    be refused, not auto-corrected."""
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, VALID_GUARDRAIL_MOVE)
    write_receipt(
        receipts_dir,
        REQUIRED_CHECKS_NAME,
        {
            "headSha": "a" * 40,
            "preContexts": [],
            "postContexts": ["Factory Bridge Regressions "],
            "snapshotRowSha256": "b" * 64,
        },
    )
    result = run_gate(receipts_dir)
    assert_refused(result, REQUIRED_CHECKS_NAME)
    assert_refused(result, "whitespace near-match")


# ---------------------------------------------------------------------------------------
# sol round 1, PR #83, MAJOR 2: the prior single test here supplied no receipts, created no
# sentinel file, and left -BoardRoot at its real-repository default, so it only exercised
# the FIRST refusal path and could not have detected a write bug anywhere else. This
# version sandboxes -BoardRoot at tmp_path, creates a real sentinel workflow file there
# BEFORE running the gate, and re-checks the sentinel's content after EACH of several
# distinct refusal paths -- not just the first one checked.
# ---------------------------------------------------------------------------------------

_SENTINEL_WORKFLOW_CONTENT = "name: Tests\n# sentinel content the gate must never touch\n"


@pytest.fixture
def sandboxed_board_root(tmp_path):
    board_root = tmp_path / "board"
    receipts_dir = board_root / ".claude-state" / "coordination" / "dual-lane" / "receipts"
    receipts_dir.mkdir(parents=True)
    workflows_dir = board_root / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    sentinel_path = workflows_dir / "tests.yml"
    sentinel_path.write_text(_SENTINEL_WORKFLOW_CONTENT, encoding="utf-8")
    return board_root, receipts_dir, sentinel_path


REFUSAL_CASES = [
    ("both_receipts_absent", None, None, GUARDRAIL_MOVE_NAME + " is absent"),
    ("only_guardrail_move_present", VALID_GUARDRAIL_MOVE, None, REQUIRED_CHECKS_NAME + " is absent"),
    ("only_required_checks_present", None, VALID_REQUIRED_CHECKS, GUARDRAIL_MOVE_NAME + " is absent"),
    (
        "guardrail_move_conclusion_not_success",
        {"conclusion": "failure"},
        VALID_REQUIRED_CHECKS,
        "conclusion=success",
    ),
    (
        "required_checks_still_lists_bridge",
        VALID_GUARDRAIL_MOVE,
        {**VALID_REQUIRED_CHECKS, "postContexts": VALID_REQUIRED_CHECKS["postContexts"] + [
            "Factory Bridge Regressions"
        ]},
        "still lists \'Factory Bridge Regressions\'",
    ),
]


@pytest.mark.parametrize(
    "case_name,guardrail_payload,required_payload,expected_substring", REFUSAL_CASES
)
def test_does_not_touch_any_workflow_file_on_refusal(
    sandboxed_board_root, case_name, guardrail_payload, required_payload, expected_substring
):
    # sol round 2, PR #83, MAJOR: the previous version of this test asserted only a
    # nonzero exit code, which cannot distinguish a genuine business-logic refusal from a
    # PowerShell PARAMETER-BINDING failure -- if -BoardRoot were removed from the script or
    # otherwise broken, invoking it with -BoardRoot still set would throw a binding error,
    # which is ALSO a nonzero exit, and this test would have kept passing without -BoardRoot
    # ever being exercised at all. Asserting the SPECIFIC refusal-reason substring (the same
    # discipline every other test in this file already uses via assert_refused) closes that
    # gap: a binding error's message never contains these business-logic strings.
    board_root, receipts_dir, sentinel_path = sandboxed_board_root
    write_receipt(receipts_dir, GUARDRAIL_MOVE_NAME, guardrail_payload)
    write_receipt(receipts_dir, REQUIRED_CHECKS_NAME, required_payload)

    result = run_gate(receipts_dir, board_root=board_root)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        "case %r: expected refusal, got exit=%r stdout=%r stderr=%r"
        % (case_name, result.returncode, result.stdout, result.stderr)
    )
    assert expected_substring in combined, (
        "case %r: expected %r in combined output (a generic nonzero exit is not enough --"
        " it must be THIS specific refusal, not a parameter-binding failure or some other"
        " error); stdout=%r stderr=%r"
        % (case_name, expected_substring, result.stdout, result.stderr)
    )
    actual_content = sentinel_path.read_text(encoding="utf-8")
    assert actual_content == _SENTINEL_WORKFLOW_CONTENT, (
        "case %r: sentinel workflow file was modified on refusal (expected %r, got %r)"
        % (case_name, _SENTINEL_WORKFLOW_CONTENT, actual_content)
    )
    # No file besides the sentinel and whichever receipts this case wrote may exist.
    all_files = sorted(p.relative_to(board_root).as_posix() for p in board_root.rglob("*") if p.is_file())
    expected_files = {".github/workflows/tests.yml"}
    if guardrail_payload is not None:
        expected_files.add(
            ".claude-state/coordination/dual-lane/receipts/" + GUARDRAIL_MOVE_NAME
        )
    if required_payload is not None:
        expected_files.add(
            ".claude-state/coordination/dual-lane/receipts/" + REQUIRED_CHECKS_NAME
        )
    assert set(all_files) == expected_files, (
        "case %r: unexpected files after refusal: found %r, expected %r"
        % (case_name, all_files, sorted(expected_files))
    )
