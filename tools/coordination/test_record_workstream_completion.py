import hashlib
import json
import subprocess
import sys
import importlib.util
import shutil
import os
from pathlib import Path

import pytest
import jsonschema


ROOT = Path(__file__).parent
ADAPTER = ROOT / "record_workstream_completion.py"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell wrapper requires pwsh")
def test_wrapper_preserves_array_paths_spaces_and_exit_status(tmp_path):
    wrapper = tmp_path / "Record-WorkstreamCompletion.ps1"
    wrapper.write_bytes((ROOT / wrapper.name).read_bytes())
    captured = tmp_path / "captured argv.json"
    fake = tmp_path / "record_workstream_completion.py"
    fake.write_text("import json,sys\nfrom pathlib import Path\n"
                    f"Path({str(captured)!r}).write_text(json.dumps(sys.argv))\nsys.exit(23)\n")
    values = {"CardId": "CARD WITH SPACES", "LaneReceipt": "run dir/lane.json",
              "ReviewVerdictPath": "review dir/verdict.json", "Worktree": "work tree",
              "OutputReceipt": "run dir/completion.json", "PythonExecutable": sys.executable,
              "AllowedPath": ["src/one file.cpp", "src/two file.cpp"],
              "TestReceiptPath": ["test dir/first.json", "test dir/second.json"],
              "ArtifactPath": ["artifact dir/first.bin", "artifact dir/second.bin"]}
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    caller = tmp_path / "invoke wrapper.ps1"
    argv = ["&", quote(wrapper)]
    for key, value in values.items():
        argv += ["-" + key, "@(" + ",".join(map(quote, value)) + ")"
                 if isinstance(value, list) else quote(value)]
    caller.write_text(" ".join(argv) + "\nexit $LASTEXITCODE\n", encoding="utf-8")
    result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(caller)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 23, (result.stdout, result.stderr)
    expected = [str(fake), "--card-id", values["CardId"], "--lane-receipt", values["LaneReceipt"],
                "--review-verdict", values["ReviewVerdictPath"], "--worktree", values["Worktree"],
                "--output-receipt", values["OutputReceipt"]]
    for key, flag in [("AllowedPath", "--allowed-path"), ("TestReceiptPath", "--test-receipt"),
                      ("ArtifactPath", "--artifact")]:
        for value in values[key]:
            expected += [flag, value]
    assert json.loads(captured.read_text()) == expected


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          text=True, capture_output=True)


@pytest.fixture
def case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Completion Test")
    (repo / ".gitignore").write_text(".claude-state/\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/a").write_text("one\n", encoding="utf-8")
    (repo / "src/delete").write_text("remove\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "src/a").write_text("two\n", encoding="utf-8")
    git(repo, "add", "src/a")
    git(repo, "commit", "-qm", "change")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    run = tmp_path / "run-dir"
    run.mkdir()
    output = run / "provider-output.json"
    output.write_text('{"verdict":"APPROVE"}\n', encoding="utf-8")
    command = run / "test-command.sh"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    test_output = run / "test-output.txt"
    test_output.write_text("pytest: 12 passed\n", encoding="utf-8")
    lane = run / "lane.json"
    write_json(lane, {"schema": "mlv-app/fleet-lane-receipt/v1", "state": "complete",
        "complete": True, "card": "CARD-1", "workDir": str(repo), "allowEdits": True,
        "baseSha": base, "exitCode": 0, "failure": None, "providerRefusal": None,
        "timedOut": False, "outputPath": str(output), "outputBytes": output.stat().st_size,
        "outputSha256": sha(output)})
    review = run / "review.json"
    write_json(review, {"schema": "mlv-app/workstream-review/v1", "verdict": "APPROVE",
        "cardId": "CARD-1", "subject_sha": head, "laneOutputSha256": sha(output)})
    test = run / "test.json"
    write_json(test, {"schema": "mlv-app/workstream-test/v1", "cardId": "CARD-1",
        "subject_sha": head, "exitCode": 0, "commandPath": str(command),
        "commandSha256": sha(command), "outputPath": str(test_output),
        "outputSha256": sha(test_output), "outputBytes": test_output.stat().st_size})
    return locals()


def command(c, **overrides):
    receipt = c["repo"] / ".claude-state" / "completion.receipt.json"
    receipt.parent.mkdir(exist_ok=True)
    args = ["--card-id", "CARD-1", "--lane-receipt", str(c["lane"]),
            "--review-verdict", str(c["review"]), "--worktree", str(c["repo"]),
            "--allowed-path", "src/a", "--test-receipt", str(c["test"]),
            "--output-receipt", str(overrides.pop("output", receipt))]
    if "artifact" in overrides:
        args += ["--artifact", str(overrides.pop("artifact"))]
    for key, value in overrides.items():
        args[args.index("--" + key.replace("_", "-")) + 1] = str(value)
    return [sys.executable, str(ADAPTER), *args]


def invoke(c, **overrides):
    return subprocess.run(command(c, **overrides), text=True, capture_output=True)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="Dispatcher requires pwsh")
@pytest.mark.parametrize("scenario,expected", [("valid", 0), ("failed-test", 2),
                                             ("dispatch-flag", 1), ("missing-review", 1)])
def test_dispatcher_completion_mode_never_dispatches_or_mutates_board(case, scenario, expected):
    board = case["run"] / "board"
    dual = board / ".claude-state/coordination/dual-lane"
    (dual / "receipts").mkdir(parents=True)
    for name in ("queue.json", "workstream-dispatch-log.jsonl", "receipts/dispatch-reservations.jsonl",
                 "WORKSTREAM-LOOP-DISABLED"):
        (dual / name).write_text("preserve " + name)
    before = {str(p.relative_to(board)): p.read_bytes() for p in board.rglob("*") if p.is_file()}
    if scenario == "failed-test":
        test = json.loads(case["test"].read_text())
        test["exitCode"] = 9
        write_json(case["test"], test)
    output = case["run"] / "dispatcher-completion.json"
    flags = {"CardId": "CARD-1", "CompletionLaneReceipt": str(case["lane"]),
             "CompletionReviewVerdictPath": str(case["review"]), "CompletionWorktree": str(case["repo"]),
             "CompletionAllowedPath": ["src/a"], "CompletionTestReceiptPath": [str(case["test"])],
             "CompletionOutputReceipt": str(output)}
    if scenario == "missing-review":
        del flags["CompletionReviewVerdictPath"]
    if scenario == "dispatch-flag":
        flags["Lane"] = "luna"
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    args = ["&", quote(ROOT / "Invoke-Workstream.ps1"), "-RecordCompletion"]
    for key, value in flags.items():
        args += ["-" + key, "@(" + ",".join(map(quote, value)) + ")"
                 if isinstance(value, list) else quote(value)]
    caller = case["run"] / "completion-call.ps1"
    caller.write_text("$ErrorActionPreference='Stop'\n" + " ".join(args) + "\nexit $LASTEXITCODE\n", encoding="utf-8")
    env = dict(os.environ, MLV_BOARD_ROOT=str(board))
    result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(caller)],
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == expected, (result.stdout, result.stderr)
    assert {str(p.relative_to(board)): p.read_bytes() for p in board.rglob("*") if p.is_file()} == before
    if expected == 0:
        value = json.loads(result.stdout)
        assert value["decision"] == "reviewed-ready" and value["subjectSha"] == case["head"]
        assert json.loads(output.read_text()) == value
    else:
        assert not output.exists()
        if scenario == "failed-test":
            assert json.loads(result.stderr)["error"] == "test-failed"


def test_existing_receipt_rejects_boolean_replacing_zero_bytes(case):
    artifact = case["run"] / "empty.bin"
    artifact.write_bytes(b"")
    first = invoke(case, artifact=artifact)
    assert first.returncode == 0, first.stderr
    receipt = case["repo"] / ".claude-state/completion.receipt.json"
    value = json.loads(receipt.read_text())
    value["artifacts"][0]["bytes"] = False
    body = {key: item for key, item in value.items() if key not in {"semanticDigest", "recordedUtc"}}
    value["semanticDigest"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    write_json(receipt, value)
    before = receipt.read_bytes()
    result = invoke(case, artifact=artifact)
    assert result.returncode == 2, result.stdout
    assert json.loads(result.stderr)["error"] == "conflicting-existing-receipt"
    assert receipt.read_bytes() == before


def test_valid_reviewed_ready_and_replay_is_byte_identical(case):
    inside = case["repo"] / ".claude-state" / "completion.receipt.json"
    first = invoke(case, output=inside)
    assert first.returncode == 0, first.stderr
    receipt = inside
    before = receipt.read_bytes()
    body = json.loads(before)
    assert body["schema"] == "mlv-app/workstream-completion/v1"
    assert body["decision"] == "reviewed-ready"
    assert body["subjectSha"] == case["head"]
    assert body["changedPaths"] == ["src/a"]
    assert body["semanticDigest"]
    second = invoke(case, output=inside)
    assert second.returncode == 0, second.stderr
    assert receipt.read_bytes() == before


def test_valid_receipt_matches_repository_schema(case):
    result = invoke(case)
    assert result.returncode == 0, result.stderr
    body = json.loads((case["repo"] / ".claude-state" / "completion.receipt.json").read_text())
    schema = json.loads((ROOT / "workstream-completion.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(body, schema)


@pytest.mark.parametrize("mutation,expected", [
    ("no_commit", "base-equals-head"),
    ("dirty", "dirty-worktree"),
    ("out_of_scope", "changed-paths-not-allowed"),
    ("deleted_path", "changed-paths-not-allowed"),
    ("card_mismatch", "lane-card-mismatch"),
    ("head_mismatch", "review-not-bound"),
    ("output_mismatch", "review-not-bound"),
    ("missing_test", "missing-file"),
    ("failed_test", "test-failed"),
    ("wrong_head_test", "test-not-bound"),
    ("bool_exit", "lane-not-successful"),
    ("missing_artifact", "missing-artifact"),
    ("changed_command", "test-command-mismatch"),
    ("changed_output", "lane-output-mismatch"),
])
def test_invalid_completion_inputs_refuse_without_receipt(case, mutation, expected):
    if mutation == "no_commit":
        case["lane"].write_text(case["lane"].read_text().replace(case["base"], case["head"]))
    elif mutation == "dirty":
        (case["repo"] / "src/a").write_text("dirty\n", encoding="utf-8")
    elif mutation == "out_of_scope":
        (case["repo"] / "src/b").write_text("out\n", encoding="utf-8")
        git(case["repo"], "add", "src/b")
        git(case["repo"], "commit", "-qm", "out of scope")
        case["head"] = git(case["repo"], "rev-parse", "HEAD").stdout.strip()
        case["review"].write_text(case["review"].read_text().replace(case["review"].read_text().split('"subject_sha":"')[1].split('"')[0], case["head"]), encoding="utf-8")
        case["test"].write_text(case["test"].read_text().replace(case["test"].read_text().split('"subject_sha":"')[1].split('"')[0], case["head"]), encoding="utf-8")
    elif mutation == "deleted_path":
        (case["repo"] / "src/delete").unlink()
        git(case["repo"], "add", "-u", "src/delete")
        git(case["repo"], "commit", "-qm", "delete path")
        case["head"] = git(case["repo"], "rev-parse", "HEAD").stdout.strip()
        for path in (case["review"], case["test"]):
            text = path.read_text()
            old = text.split('"subject_sha":"')[1].split('"')[0]
            path.write_text(text.replace(old, case["head"]), encoding="utf-8")
    elif mutation == "card_mismatch":
        case["lane"].write_text(case["lane"].read_text().replace("CARD-1", "OTHER"))
    elif mutation == "head_mismatch":
        case["review"].write_text(case["review"].read_text().replace(case["head"], "0" * 40))
    elif mutation == "output_mismatch":
        case["review"].write_text(case["review"].read_text().replace(sha(case["output"]), "0" * 64))
    elif mutation == "missing_test":
        case["test"].unlink()
    elif mutation == "failed_test":
        case["test"].write_text(case["test"].read_text().replace('"exitCode":0', '"exitCode":1'))
    elif mutation == "wrong_head_test":
        case["test"].write_text(case["test"].read_text().replace(case["head"], "0" * 40))
    elif mutation == "bool_exit":
        case["lane"].write_text(case["lane"].read_text().replace('"exitCode":0', '"exitCode":false'))
    elif mutation == "missing_artifact":
        case["artifact"] = case["run"] / "missing.bin"
    elif mutation == "changed_command":
        case["test"].write_text(case["test"].read_text().replace(case["test"].read_text().split('"commandSha256":"')[1].split('"')[0], "0" * 64), encoding="utf-8")
    elif mutation == "changed_output":
        case["output"].write_text("changed\n", encoding="utf-8")
    result = invoke(case, **({"artifact": case["artifact"]} if "artifact" in case else {}))
    assert result.returncode != 0
    assert json.loads(result.stderr)["error"] == expected
    assert not (case["repo"] / ".claude-state" / "completion.receipt.json").exists()


def test_output_receipt_may_be_in_explicit_run_dir_outside_worktree(case):
    result = invoke(case, output=case["run"] / "completion.receipt.json")
    assert result.returncode == 0, result.stderr


def test_malformed_duplicate_json_and_conflict_preserve_existing(case):
    case["review"].write_text('{"schema":"x","schema":"y"}\n', encoding="utf-8")
    result = invoke(case)
    assert result.returncode != 0
    receipt = case["repo"] / ".claude-state" / "completion.receipt.json"
    assert not receipt.exists()
    case["review"].write_text('{bad\n', encoding="utf-8")
    result = invoke(case)
    assert result.returncode != 0
    assert json.loads(result.stderr)["error"] == "malformed-review-verdict"


def test_nonobject_json_is_rejected_and_conflicting_existing_receipt_is_preserved(case):
    case["review"].write_text("null\n", encoding="utf-8")
    result = invoke(case)
    assert result.returncode != 0
    assert json.loads(result.stderr)["error"] == "malformed-review-verdict"
    case["review"].write_text('{"schema":"mlv-app/workstream-review/v1","verdict":"APPROVE",'
        '"cardId":"CARD-1","subject_sha":"%s","laneOutputSha256":"%s"}\n' %
        (case["head"], sha(case["output"])), encoding="utf-8")
    first = invoke(case)
    assert first.returncode == 0
    receipt = case["repo"] / ".claude-state" / "completion.receipt.json"
    before = receipt.read_bytes()
    case["review"].write_text(case["review"].read_text().replace('"APPROVE"', '"APPROVE" '), encoding="utf-8")
    conflict = invoke(case)
    assert conflict.returncode != 0
    assert json.loads(conflict.stderr)["error"] == "conflicting-existing-receipt"
    assert receipt.read_bytes() == before


def test_malformed_existing_receipt_is_preserved(case):
    first = invoke(case)
    assert first.returncode == 0
    receipt = case["repo"] / ".claude-state" / "completion.receipt.json"
    receipt.write_text("[]\n", encoding="utf-8")
    before = receipt.read_bytes()
    result = invoke(case)
    assert result.returncode != 0
    assert json.loads(result.stderr)["error"] == "malformed-existing-receipt"
    assert receipt.read_bytes() == before


def test_boolean_true_lane_exit_is_not_numeric_success(case):
    case["lane"].write_text(case["lane"].read_text().replace('"exitCode":0', '"exitCode":true'))
    result = invoke(case)
    assert result.returncode != 0
    assert json.loads(result.stderr)["error"] == "lane-not-successful"


def test_empty_commit_is_not_implementation_delivery(case):
    lane = json.loads(case["lane"].read_text())
    lane["baseSha"] = case["head"]
    write_json(case["lane"], lane)
    git(case["repo"], "commit", "--allow-empty", "-qm", "empty")
    new_head = git(case["repo"], "rev-parse", "HEAD").stdout.strip()
    for path in (case["review"], case["test"]):
        value = json.loads(path.read_text()); value["subject_sha"] = new_head; write_json(path, value)
    result = invoke(case)
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "no-source-changes"


def load_adapter():
    spec = importlib.util.spec_from_file_location("completion_contract_adapter", ADAPTER)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_changed_input_after_publication_preserves_receipt_and_reports_stale(case, monkeypatch, capsys):
    module = load_adapter(); real_link = module.os.link
    def race(src, dst):
        real_link(src, dst)
        case["output"].write_text("concurrently changed")
    monkeypatch.setattr(module.os, "link", race)
    monkeypatch.setattr(sys, "argv", command(case)[1:])
    with pytest.raises(SystemExit) as exc: module.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().err)["error"] == "stale-after-publication"
    assert (case["repo"] / ".claude-state/completion.receipt.json").exists()
    assert case["output"].read_text() == "concurrently changed"


def test_changed_receipt_during_replay_is_detected(case, monkeypatch, capsys):
    assert invoke(case).returncode == 0
    module = load_adapter(); real_verify = module.Registry.verify
    receipt = case["repo"] / ".claude-state/completion.receipt.json"
    def race(registry, code):
        real_verify(registry, code)
        if code == "inputs-changed-during-replay": receipt.write_text("concurrently changed")
    monkeypatch.setattr(module.Registry, "verify", race)
    monkeypatch.setattr(sys, "argv", command(case)[1:])
    with pytest.raises(SystemExit) as exc: module.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().err)["error"] == "receipt-changed-during-replay"
    assert receipt.read_text() == "concurrently changed"


def test_schema_repository_path_examples():
    schema = json.loads((ROOT / "workstream-completion.schema.json").read_text())
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/repoPath", "$defs": schema["$defs"]})
    for path in ("src/a", "src/gpu/foo.c", "platform/qt/BuildInfo.cpp"):
        assert validator.is_valid(path), path
    for path in ("../outside", "/absolute", "src/../outside", "src\\a", "src/*"):
        assert not validator.is_valid(path), path


def test_changed_receipt_after_publication_is_detected(case, monkeypatch, capsys):
    module = load_adapter(); real_verify = module.Registry.verify
    receipt = case["repo"] / ".claude-state/completion.receipt.json"
    def race(registry, code):
        real_verify(registry, code)
        if code == "stale-after-publication": receipt.write_text("concurrently changed")
    monkeypatch.setattr(module.Registry, "verify", race)
    monkeypatch.setattr(sys, "argv", command(case)[1:])
    with pytest.raises(SystemExit) as exc: module.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().err)["error"] == "receipt-changed-after-publication"
    assert receipt.read_text() == "concurrently changed"
