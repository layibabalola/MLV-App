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
WORKSTREAM = ROOT / "tools" / "coordination" / "Invoke-Workstream.ps1"


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


def test_a_transient_fetch_failure_does_not_halt_the_cycle():
    """One blip must not cost a whole 45-minute cycle.

    Observed 2026-09-04T18:01:22Z: the loop wrote
    haltedReason="git fetch fork failed (exit 128); driver worktree may be stale" and
    dispatched nothing. The cause was lock contention with a concurrent git operation on the
    same repository -- the identical fetch succeeded by hand moments later, and the driver
    worktree was neither stale nor broken. The old code halted on the FIRST non-zero exit, so
    a blip and a genuinely broken remote were indistinguishable.

    Asserted on the source for the reason documented in
    test_cycle_receipt_stamp_has_exactly_one_assignment: running the loop syncs a real git
    worktree and writes into .claude-state, so it would mutate live board state.
    """
    text = LOOP.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert "function Invoke-GitFetchFork" in text, "no bounded retry helper"

    # the helper must attempt more than once
    attempts_line = [ln for ln in lines if "$Attempts" in ln and "param(" in ln]
    assert attempts_line, "Invoke-GitFetchFork must take a bounded $Attempts parameter"
    digits = "".join(ch for ch in attempts_line[0].split("$Attempts", 1)[1] if ch.isdigit())
    assert digits and int(digits[0]) > 1, "a single attempt is not a retry"

    # THE ANTI-PATTERN: a bare fetch whose very next line halts the cycle
    stale = "driver worktree may be stale"
    for i, ln in enumerate(lines):
        is_bare_fetch = ("git -C $RepoRoot fetch fork" in ln) and ("Invoke-GitFetchFork" not in ln)
        if not is_bare_fetch:
            continue
        following = lines[i + 1] if i + 1 < len(lines) else ""
        assert stale not in following, (
            "line %d halts the cycle on a single fetch attempt: %s" % (i + 1, ln.strip())
        )

    # the halt message reports the attempt count, so receipts stay diagnosable
    assert "attempts; " + stale in text


def test_landing_probe_knows_the_verbs_and_connectors_that_actually_get_used():
    """Two MEASURED misses on 2026-09-05, both from real merged PR bodies.

    PR #53: "Closes OWN-2 and delivers GATE-RESIDUALS-1(b)" -- OWN-2 was skipped,
    GATE-RESIDUALS-1 was not, and the loop spent a lane on it at 02:11Z.
    PR #52: "Closes queue item OWN-1-PRECEDENCE" -- only "card" was permitted between the verb
    and the id, so that missed too, and the near-miss diagnostic surfaced it on its first run.

    The vocabulary is a fixed list, and every writer who does not know it costs a dispatch.
    """
    text = WORKSTREAM.read_text(encoding="utf-8")
    assert "delivers" in text, "landing verb 'delivers' not accepted"
    assert "queue" in text, "'queue item' connector not accepted"


def test_a_bare_id_in_prose_still_does_NOT_count_as_landing():
    """The load-bearing asymmetry, from the probe's own comment: a false positive SKIPS
    genuinely open work, which is strictly worse than re-dispatching finished work. Widening
    the vocabulary must never reach a bare mention."""
    text = WORKSTREAM.read_text(encoding="utf-8")
    line = [l for l in text.splitlines() if "$rxBody =" in l]
    assert line, "landing-verb body regex not found"
    assert "(?:lands|closes|fixes|resolves|delivers)" in line[0], (
        "body match is no longer gated behind an explicit landing verb"
    )


def test_near_misses_are_summarised_not_printed_per_card():
    """The first draft printed one line per near-miss and produced TEN on a single run, mostly
    genuine prose mentions. A diagnostic that fires every run is one nobody reads -- which is
    exactly the failure it exists to prevent."""
    text = WORKSTREAM.read_text(encoding="utf-8")
    assert "$nearMisses" in text, "near-miss diagnostic absent"
    assert "NEAR-MISS ($($nearMisses.Count))" in text, "near-misses are not summarised into one line"
    assert "NOT skipped" in text, "the diagnostic must say it did not act on the near-miss"


# --- pre-dispatch hosted GitHub evidence ----------------------------------------------------
# MEASURED 2026-09-05: FACTORY-MATURITY-1-CLAUDE and FACTORY-MATURITY-1-OPUS, both priority 1,
# consumed two lane slots each and both returned the same wall -- `gh` answers "Access is denied"
# inside the read-only lane sandbox. The same gh, token and machine work from an interactive
# session; gh resolves its token through the Windows credential keyring and the sandbox denies
# that read. PR #55 had already TOLD lanes this might happen and that saying so is a FINDING, and
# both lanes did say so, correctly -- and were dispatched into the wall anyway, because the brief
# only taught them to report the limitation and never removed it.
#
# Asserted on the source, for the reason given in
# test_cycle_receipt_stamp_has_exactly_one_assignment: running Invoke-Workstream reads the live
# queue and writes a prompt and a run directory into real .claude-state, so executing it under
# test would mutate board state. Behavioural verification is a subject/falsifier pair run by hand
# (real gh -> "gh-evidence 5/5 export(s) ok"; a stub gh on PATH that exits 1 with "Access is
# denied" -> "gh-evidence 0/4", the section headed "THE EXPORT FAILED", and dispatch continues).


def test_hosted_evidence_is_collected_by_the_dispatcher_not_left_to_the_lane():
    """A warning in a prompt is not a fix; it is a nicer way to fail. The dispatcher runs in the
    venue where gh works and was already calling it for the landing probe, so the evidence was
    always one command away from the process doing the dispatching."""
    text = WORKSTREAM.read_text(encoding="utf-8")
    assert "$needsHostedEvidence" in text, "no hosted-evidence classification"
    assert "github-evidence" in text, "no export directory beside the run"
    # the export must actually shell out to gh from here, not merely describe it
    assert "& gh @ghArgs" in text, "the dispatcher does not itself invoke gh for the export"
    # ...and the brief must forbid the lane from retrying it. Doubled backticks: inside the
    # here-string a backtick is PowerShell's escape character, so ``gh`` is what renders as `gh`.
    assert "DO NOT RETRY ``gh`` YOURSELF" in text, "the brief does not stop the lane re-hitting the wall"


def test_the_run_directory_is_named_before_the_brief_that_cites_it():
    """The brief has to name files this script is about to write. $runDir was originally computed
    at dispatch time, AFTER the brief and only on the non-dry-run path -- so a section naming the
    export directory could not exist, and -DryRun could not be used to inspect one.

    Exactly one assignment, for the same reason $stamp has exactly one: a second one further down
    would silently point the lane at a directory that never receives the export.
    """
    text = WORKSTREAM.read_text(encoding="utf-8")
    for var in ("$runDir", "$stamp"):
        assignments = re.findall(r"(?m)^\s*" + re.escape(var) + r"\s*=", text)
        assert len(assignments) == 1, (
            "%s must have exactly one assignment; found %d" % (var, len(assignments))
        )
    assert text.index("$runDir = Join-Path") < text.index("$brief = @\""), (
        "$runDir is assigned after the brief is built, so the brief cannot name the export"
    )


def test_the_export_fails_open_and_says_why_rather_than_only_that_it_failed():
    """Fail-open, exactly like the landing probe: an unreadable signal must never silently shrink
    the board. And the REASON is kept, not just the exit code -- 'Access is denied' and a 404 send
    a reader to completely different places, and collapsing both to 'gh failed' is how a venue
    problem gets misfiled as an authorization problem for a second time."""
    text = WORKSTREAM.read_text(encoding="utf-8")
    assert "CANNOT-DETERMINE: gh not on PATH" in text, "a missing gh must not be fatal"
    assert "$why" in text and "no stderr" in text, "the failure reason is not preserved"

    # nothing between the export block and the dispatch may exit: a failed export must still
    # dispatch, with the lane told plainly that those facts are UNVERIFIED.
    start = text.index("$needsHostedEvidence =")
    end = text.index("if ($DryRun)")
    assert not re.search(r"(?m)^\s*exit\s+\d", text[start:end]), (
        "the hosted-evidence export can halt dispatch; it must fail open"
    )
    assert "UNVERIFIED" in text[start:end], "a failed export must tell the lane the facts are UNVERIFIED"


def test_dry_run_admits_the_export_already_happened():
    """-DryRun really does write the export -- that is the point, it is how you inspect what a
    lane would receive. Printing a bare 'nothing dispatched' would be a lie by omission about a
    directory this command just created."""
    text = WORKSTREAM.read_text(encoding="utf-8")
    dry = [ln for ln in text.splitlines() if "DRY RUN" in ln and "Write-Output" in ln]
    assert dry, "no dry-run notice"
    assert "on disk" in dry[0], "dry run does not disclose that the export is real"


# --- assert-script-currency.ps1 -------------------------------------------------------------
# The board root is the only checkout carrying .claude-state/, and it routinely sits on a peer
# branch. Running a tool from there by absolute path silently executes a stale copy: the script
# exits 0 and prints a wrong answer. These tests pin the guard that refuses that, and pin the
# fail-OPEN direction -- a guard that blocked on "I could not check" would be worse than the bug.

GUARD = ROOT / "tools" / "coordination" / "assert-script-currency.ps1"
GUARDED = [
    ROOT / "tools" / "coordination" / "queue-derive.ps1",
    ROOT / "tools" / "coordination" / "board-health-sweep.ps1",
]


def guard_status(script_path, ref="fork/master", env=None):
    """Run the guard with -PassThru and return its status, or 'THREW' when it refuses."""
    cmd = (
        "try { (& '%s' -ScriptPath '%s' -Ref '%s' -PassThru).status } "
        "catch { 'THREW' }" % (GUARD.as_posix(), Path(script_path).as_posix(), ref)
    )
    import os
    e = dict(os.environ)
    e.pop("MLV_ALLOW_STALE_TOOLS", None)
    if env:
        e.update(env)
    out = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", cmd], text=True, capture_output=True, env=e
    ).stdout.strip().splitlines()
    return out[-1].strip() if out else ""


def currency_repo(tmp_path):
    """A repo with a 'fork/master' ref and a tracked script, so the guard has something to compare."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    script = tmp_path / "tool.ps1"
    script.write_text("Write-Output 'original'\n")
    subprocess.run(["git", "add", "tool.ps1"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    # A local ref literally named refs/remotes/fork/master, so no network or remote is needed.
    subprocess.run(
        ["git", "update-ref", "refs/remotes/fork/master", git(tmp_path, "rev-parse", "HEAD")],
        cwd=tmp_path, check=True,
    )
    return script


def test_currency_guard_passes_when_the_file_matches_the_reference(tmp_path):
    script = currency_repo(tmp_path)
    assert guard_status(script) == "current"


def test_currency_guard_refuses_when_the_file_differs_from_the_reference(tmp_path):
    script = currency_repo(tmp_path)
    script.write_text("Write-Output 'a stale peer-branch copy'\n")
    assert guard_status(script) == "THREW"


def test_currency_guard_refusal_names_the_branch_the_checkout_is_on(tmp_path):
    # The message has to say WHY, or the reader treats it as noise and sets the hatch reflexively.
    script = currency_repo(tmp_path)
    subprocess.run(["git", "checkout", "-qb", "diag/some-peer-branch"], cwd=tmp_path, check=True)
    script.write_text("Write-Output 'drifted'\n")
    cmd = "try { & '%s' -ScriptPath '%s' } catch { $_.Exception.Message }" % (
        GUARD.as_posix(), script.as_posix(),
    )
    import os
    e = dict(os.environ)
    e.pop("MLV_ALLOW_STALE_TOOLS", None)  # an inherited hatch would make this pass vacuously
    out = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", cmd], text=True, capture_output=True, env=e
    )
    blob = out.stdout + out.stderr
    assert "diag/some-peer-branch" in blob
    assert "MLV_ALLOW_STALE_TOOLS" in blob


def test_currency_guard_honours_the_escape_hatch(tmp_path):
    script = currency_repo(tmp_path)
    script.write_text("Write-Output 'deliberately modified'\n")
    assert guard_status(script, env={"MLV_ALLOW_STALE_TOOLS": "1"}) == "skipped"


def test_currency_guard_allows_a_file_not_yet_present_on_the_reference(tmp_path):
    # A brand-new script is not stale. Blocking here would make it impossible to add one.
    script = currency_repo(tmp_path)
    fresh = script.parent / "brand-new.ps1"
    fresh.write_text("Write-Output 'new'\n")
    assert guard_status(fresh) == "untracked-on-ref"


def test_currency_guard_fails_open_when_the_reference_does_not_resolve(tmp_path):
    # A lane sandbox or a fresh clone has no 'fork' remote. That is missing infrastructure,
    # not proven drift, so the guard must stay silent rather than halt the caller.
    script = currency_repo(tmp_path)
    script.write_text("Write-Output 'differs'\n")
    assert guard_status(script, ref="nonexistent/ref") == "unknown"


def test_currency_guard_fails_open_outside_a_git_working_tree(tmp_path):
    loose = tmp_path / "loose.ps1"
    loose.write_text("Write-Output 'x'\n")
    assert guard_status(loose) == "unknown"


def test_the_read_only_board_diagnostics_actually_invoke_the_guard(tmp_path):
    # Without this, the guard could quietly stop being wired and nothing would notice.
    for script in GUARDED:
        body = script.read_text(encoding="utf-8")
        assert "assert-script-currency.ps1" in body, f"{script.name} no longer invokes the guard"
        assert "-ScriptPath $PSCommandPath" in body, f"{script.name} guards the wrong path"
