import re
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "tools" / "coordination" / "validate_and_append_handoff.py"
WATCHDOG = ROOT / "tools" / "coordination" / "coordination_watchdog.py"
HEARTBEAT = ROOT / "tools" / "coordination" / "coordination-heartbeat.ps1"


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


def test_heartbeat_resolves_watchdog_beside_wrapper_not_under_repo_root(tmp_path):
    coord = tmp_path / ".claude-state" / "coordination"
    dual = coord / "dual-lane"
    dual.mkdir(parents=True)
    (coord / "gpu-lane-impl-review-sync.md").write_text("# gate\n")
    (dual / "claude.md").write_text("# claude\n")
    (dual / "codex.md").write_text("# codex\n")
    result = subprocess.run(
        [
            "pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(HEARTBEAT),
            "-RepoRoot", str(tmp_path), "-Once",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "IDLE"
    assert not (tmp_path / "tools" / "coordination" / "coordination_watchdog.py").exists()


def test_supplied_timestamp_must_be_a_real_instant(tmp_path):
    """STAMP-APPENDER-1: --timestamp was written into the entry verbatim, so NOT-A-CLOCK
    reached the ledger heading. The closeout gate BLOCKS on a heading it cannot parse
    (content_approval_unparsable_heading), so this appender could stop finalize by itself.
    A real instant must still pass through UNCHANGED: --timestamp exists for replay and
    fixtures, and silently rewriting it would surprise those callers."""
    start, feature = make_repo(tmp_path)

    # subject: a non-instant is refused before anything is appended
    command, ledgers = args(tmp_path, start, feature)
    result = run(VALIDATOR, *command, "--timestamp", "NOT-A-CLOCK", cwd=tmp_path)
    assert result.returncode != 0, result.stdout
    assert "ISO-8601" in (result.stderr + result.stdout)
    for ledger in ledgers:
        assert "NOT-A-CLOCK" not in ledger.read_text()

    # control: a real instant is accepted AND preserved byte-for-byte
    second = tmp_path / "second"
    second.mkdir()
    start2, feature2 = make_repo(second)
    command2, ledgers2 = args(second, start2, feature2)
    exact = "2000-01-02T03:04:05+00:00"
    ok = run(VALIDATOR, *command2, "--timestamp", exact, cwd=second)
    assert ok.returncode == 0, ok.stderr
    assert any(exact in ledger.read_text() for ledger in ledgers2)


LOOP = ROOT / "tools" / "coordination" / "Invoke-WorkstreamLoop.ps1"


def test_cycle_receipt_stamp_has_exactly_one_assignment():
    """The cycle-receipt filename is built from $stamp, which must stay a formatted STRING.

    On 2026-09-04 the daily-budget fix (PR #37) reused `$stamp` as a per-row [datetime]
    inside the counting loop -- same script scope, between the assignment and the use. The
    receipt path then interpolated with the current culture as
    'cycle-09\04\2026 09:05:02.json': "/" became directory separators and ":" is illegal
    in a Windows filename, so WriteAllText threw and the scheduled task exited 1 for ~4.5 h
    while writing no audit record.

    Asserted on the source rather than by running the loop: the loop syncs a git worktree,
    reads the live dispatch log and writes into .claude-state, so executing it under test
    would mutate real board state. The repo already asserts source shape this way in
    tools/repo_hygiene/test_repo_hygiene.py.
    """
    text = LOOP.read_text(encoding="utf-8")

    # the receipt is still built from $stamp
    assert 'cycle-$stamp.json' in text

    # ...and $stamp is assigned exactly once, at script scope
    assignments = re.findall(r"^\s*\$stamp\s*=", text, re.MULTILINE)
    assert len(assignments) == 1, (
        "$stamp must have exactly one assignment; a second one clobbers the receipt "
        "filename. Found %d." % len(assignments)
    )

    # ...and that one assignment produces a filename-safe format, not a culture default
    assert re.search(r"\$stamp\s*=\s*\$cycleStart\.ToString\('yyyyMMddTHHmmssZ'\)", text), (
        "$stamp must be formatted with an explicit invariant pattern; a culture-default "
        "ToString() yields '/' and ':' which are illegal in a Windows path."
    )
