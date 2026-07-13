import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "tools" / "coordination" / "validate_and_append_handoff.py"
WATCHDOG = ROOT / "tools" / "coordination" / "coordination_watchdog.py"


def run(*args, cwd):
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, text=True, capture_output=True)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def make_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    start = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "seed.txt").write_text("feature\n")
    subprocess.run(["git", "commit", "-qam", "feature"], cwd=tmp_path, check=True)
    feature = git(tmp_path, "rev-parse", "HEAD")
    return start, feature


def args(repo, start, feature, *extra):
    coord = repo / ".claude-state" / "coordination"
    coord.mkdir(parents=True)
    ledgers = [coord / "gpu.md", coord / "claude.md", coord / "codex.md"]
    for ledger in ledgers:
        ledger.write_text("# ledger\n")
    return [
        "--repo-root", repo, "--start", start, "--feature", feature,
        "--work-block", "wb-test", "--summary", "test handoff",
        "--changes", "coordination only", "--validation", "focused test pass",
        "--proof-boundary", "no product claim", "--request", "review exact range",
        "--ledger", ledgers[0], "--ledger", ledgers[1], "--codex-ledger", ledgers[2], *extra,
    ], ledgers


def test_validator_rejects_phantom_feature(tmp_path):
    start, feature = make_repo(tmp_path)
    command, _ = args(tmp_path, start, "e7311126f0f638f01eb06808f99a5e62908fc7b8")
    result = run(VALIDATOR, *command, cwd=tmp_path)
    assert result.returncode == 2
    assert "HANDOFF_BLOCKED" in result.stderr


def test_validator_mirrors_exact_payload(tmp_path):
    start, feature = make_repo(tmp_path)
    command, ledgers = args(tmp_path, start, feature)
    result = run(VALIDATOR, *command, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    range_token = f"{start}..{feature}"
    for ledger in ledgers:
        text = ledger.read_text()
        assert text.endswith("Reviewer: Claude\n")
        assert range_token in text
        assert "WorkBlock: wb-test" in text


def test_watchdog_escalates_after_two_missed_reviews(tmp_path):
    start, feature = make_repo(tmp_path)
    command, ledgers = args(tmp_path, start, feature)
    assert run(VALIDATOR, *command, cwd=tmp_path).returncode == 0
    state_file = tmp_path / ".claude-state" / "coordination" / "state.json"
    watch = ["--repo-root", tmp_path, "--state-file", state_file, "--missed-heartbeats", "2", "--ledger", ledgers[0], "--ledger", ledgers[1], "--ledger", ledgers[2]]
    result = run(WATCHDOG, *watch, cwd=tmp_path)
    assert result.returncode == 3
    assert json.loads(result.stdout)["state"] == "STALL"


def test_watchdog_repairs_missing_mirror_but_requires_ack(tmp_path):
    start, feature = make_repo(tmp_path)
    command, ledgers = args(tmp_path, start, feature)
    assert run(VALIDATOR, *command, cwd=tmp_path).returncode == 0
    codex_ledger = ledgers[2]
    codex_ledger.write_text("# ledger\n")
    state_file = tmp_path / ".claude-state" / "coordination" / "state.json"
    watch = ["--repo-root", tmp_path, "--state-file", state_file, "--repair-mirrors", "--ledger", ledgers[0], "--ledger", ledgers[1], "--ledger", ledgers[2]]
    result = run(WATCHDOG, *watch, cwd=tmp_path)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["state"] == "ACK_REQUIRED"
    assert payload["repairedMirrors"] == [str(codex_ledger)]
