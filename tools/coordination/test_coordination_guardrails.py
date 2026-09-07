import glob
import hashlib
import os
import re
import json
import shlex
import shutil
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


def init_repo(tmp_path):
    r"""`git init` a throwaway repo that survives a long TMPDIR.

    MEASURED 2026-09-05. With TMPDIR under a deep path, eleven tests in this file failed with
    `git add` reporting `Filename too long`. pytest's tmp_path adds
    `pytest-of-<user>/pytest-N/<testname>0/` on top of TMPDIR -- 220 chars here -- and Windows
    MAX_PATH bites at 260. The suite passed from `C:\mlvtmp\pt` and failed from the agent
    scratchpad, on the same commit and interpreter, so the result depended on WHERE it ran.
    A test that passes or fails by its temp directory is not testing what it claims to.

    THE ORDER MATTERS AND WAS FALSIFIED, because the obvious fix does not work:
      `git init` then `git config core.longpaths true`  -> init ITSELF dies, stat'ing
          .git/hooks/fsmonitor-watchman.sample, before any config can be set.
      `git -c core.longpaths=true init` alone           -> init succeeds, but -c is transient,
          so the very next bare `git add` fails exactly as before.
    Only -c ON THE INIT, followed by persisting it into the new repo, makes every later bare
    git call in these helpers safe.

    The repo already reaches for this mitigation elsewhere -- brokered_closeout.py's
    run_git_longpaths() and Invoke-WorkstreamLoop.ps1's worktree add both pass
    `-c core.longpaths=true`. These helpers were simply never given it.

    CI never sees this: hosted runners put RUNNER_TEMP at a short path like D:\_temp, which
    is why the trap survived to bite local runs only.
    """
    subprocess.run(
        ["git", "-c", "core.longpaths=true", "init", "-q"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "core.longpaths", "true"], cwd=tmp_path, check=True
    )


def make_repo(tmp_path):
    init_repo(tmp_path)
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
# The landing probe was extracted out of Invoke-Workstream.ps1 so queue-derive.ps1 could consult
# the SAME rules instead of growing a second copy that would be widened once and left stale. The
# three probe tests below follow it: their subject never changed, only which file owns it.
PROBE = ROOT / "tools" / "coordination" / "landing-probe.ps1"
DERIVE = ROOT / "tools" / "coordination" / "queue-derive.ps1"


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
    text = PROBE.read_text(encoding="utf-8")
    assert "delivers" in text, "landing verb 'delivers' not accepted"
    assert "queue" in text, "'queue item' connector not accepted"


def test_a_bare_id_in_prose_still_does_NOT_count_as_landing():
    """The load-bearing asymmetry, from the probe's own comment: a false positive SKIPS
    genuinely open work, which is strictly worse than re-dispatching finished work. Widening
    the vocabulary must never reach a bare mention."""
    text = PROBE.read_text(encoding="utf-8")
    line = [l for l in text.splitlines() if "$script:LandingVerbs" in l and "=" in l]
    assert line, "landing verb list not found"
    assert "lands|closes|fixes|resolves|delivers" in line[0], (
        "the match is no longer gated behind an explicit landing verb"
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

    # nothing between the export block and the READ-ONLY dispatch's own -DryRun check may exit:
    # a failed export must still dispatch, with the lane told plainly that those facts are
    # UNVERIFIED. Scoped to AFTER "the brief" marker (rather than the first "if ($DryRun)" in the
    # file) because TOOL-LOOP-PLUMBING-1 added a second, earlier dispatch path (-AllowEdits) with
    # its own unrelated -DryRun check.
    start = text.index("$needsHostedEvidence =")
    brief = text.index("# ------------------------------------------------------------------ the brief")
    end = text.index("if ($DryRun)", brief)
    assert not re.search(r"(?m)^\s*exit\s+\d", text[start:end]), (
        "the hosted-evidence export can halt dispatch; it must fail open"
    )
    assert "UNVERIFIED" in text[start:end], "a failed export must tell the lane the facts are UNVERIFIED"


def test_dry_run_admits_the_export_already_happened():
    """-DryRun really does write the export -- that is the point, it is how you inspect what a
    lane would receive. Printing a bare 'nothing dispatched' would be a lie by omission about a
    directory this command just created. TOOL-LOOP-PLUMBING-1 added a second dry-run notice for
    the -AllowEdits path (about its own real-then-removed worktree) -- checking ANY line here,
    not just the first, keeps this test about the read-only path's own disclosure."""
    text = WORKSTREAM.read_text(encoding="utf-8")
    dry = [ln for ln in text.splitlines() if "DRY RUN" in ln and "Write-Output" in ln]
    assert dry, "no dry-run notice"
    assert any("on disk" in ln for ln in dry), "dry run does not disclose that the export is real"



def test_the_title_match_is_verb_gated_exactly_like_the_body_match():
    """MEASURED 2026-09-05. The title match used to be unconditional, on the theory that a card id
    in a merge subject implies a landing. PR #41 falsifies it: titled "(addresses
    STAMP-APPENDER-1)", body saying "this PR says addresses, not lands: a false landing signal
    would mark the card done and skip remaining work". The probe skipped the open card anyway.

    Gating costs nothing: across all 51 merged PRs and 117 queue ids there are exactly three title
    matches, and both real landings (#23, #38) read "lands".
    """
    text = PROBE.read_text(encoding="utf-8")
    body = text[text.index("function Get-CardLandingEvidence"):]
    # Only the LANDING decision is under test. The near-miss detector below it legitimately
    # matches a bare mention against the title -- that is its whole job, and it reports rather
    # than acting. The line that assigns $hit is the one that decides "landed".
    decisions = [l for l in body.splitlines() if "$hit = " in l and "-match" in l]
    assert len(decisions) == 2, "expected exactly a title and a body landing decision, got %d" % len(decisions)
    for line in decisions:
        assert "$rxLanding" in line and "$rxMention" not in line, (
            "a landing decision is made on something other than the verb-gated regex: %s" % line.strip()
        )
    assert any("$_.title" in l for l in decisions), "the title is no longer consulted at all"


def test_the_near_miss_matcher_has_a_word_boundary_on_BOTH_sides():
    """MEASURED 2026-09-05. The landing regex always had a leading `(?<![A-Za-z0-9-])`; the
    near-miss regex did not, and nobody noticed because it only ever over-reports. PR #42's body
    names the PROMPT FILE `ws-GATE-ID-3`, and the unanchored matcher found the card id inside that
    filename -- a near-miss for a card that PR never mentioned. Every generated prompt on this
    board is named `ws-<CARD-ID>-<stamp>.md`, so this misfires structurally, not by bad luck."""
    text = PROBE.read_text(encoding="utf-8")
    fn = text[text.index("function Get-MentionRegex"):text.index("function Get-CardLandingEvidence")]
    assert "(?<![A-Za-z0-9-])" in fn, "the mention regex can match inside a longer token (e.g. ws-<ID>)"
    assert "(?![A-Za-z0-9-])" in fn, "the mention regex can match a longer id (C2-SUBMIT-2 vs -22)"


def test_the_probe_is_shared_and_not_copied_into_its_callers():
    """Two tools ask 'did this card land'. A second copy of the vocabulary would be widened once
    and left stale -- this board's most frequently paid failure. Both must DOT-SOURCE the probe,
    and neither may carry its own matcher."""
    for caller in (WORKSTREAM, DERIVE):
        text = caller.read_text(encoding="utf-8")
        assert "landing-probe.ps1" in text, "%s does not consult the shared probe" % caller.name
        assert "Get-CardLandingEvidence" in text, "%s does not call the shared probe" % caller.name
        assert "gh pr list" not in text, (
            "%s re-implements the probe instead of consulting it" % caller.name
        )


def test_the_shared_probe_sets_no_strictmode_on_its_callers():
    """It is DOT-SOURCED, so anything it sets lands in the CALLER's scope. An earlier draft set
    `-Version Latest` and immediately broke queue-derive.ps1, which reads optional queue fields
    that are legitimately absent -- a shared helper silently changing an unrelated script's
    semantics just by being consulted."""
    text = PROBE.read_text(encoding="utf-8")
    active = [l for l in text.splitlines() if "Set-StrictMode" in l and not l.strip().startswith("#")]
    assert not active, "the dot-sourced probe imposes strictness on its callers: %s" % active


def test_queue_derive_fails_open_when_the_landing_probe_cannot_run():
    """An unreadable signal must never silently shrink the board. The probe's status is printed on
    EVERY run, including when it could not run: a check whose failure looks identical to a pass is
    the defect queue-derive.ps1 exists to refuse."""
    text = DERIVE.read_text(encoding="utf-8")
    assert "queue-derive: landing-probe {0}" in text, "probe status is not reported unconditionally"
    assert "NoLandingProbe" in text, "no way to run the ancestry axis without the network"
    probe = PROBE.read_text(encoding="utf-8")
    assert probe.count("cannot-determine:") >= 3, "the probe does not fail open on every failure path"


def test_an_open_card_naming_no_sha_is_reported_not_silently_passed():
    """THE BLIND SPOT THIS EXISTS TO CLOSE. On 2026-09-05 six landed cards named no sha and the
    tool printed NO MISMATCHES. An item the sha axis cannot judge must be VISIBLE, and if a merged
    PR claims it behind a landing verb it must be a MISMATCH -- not folded into either."""
    text = DERIVE.read_text(encoding="utf-8")
    assert "NO-SHA-EVIDENCE" in text, "items the sha axis cannot judge are not tracked"
    assert "STALE-LANDED-IN-PR" in text, "the PR axis produces no finding"
    assert "NOT a mismatch, nothing to check them against" in text, (
        "an unjudgeable item must not be reported as a disagreement"
    )

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
    init_repo(tmp_path)
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

# --- Invoke-Workstream: a malformed priority must not stop dispatch -------------------------
# Get-Rank used a bare [int] cast. On a non-numeric string that THROWS, and the throw lands
# inside Sort-Object -Property { Get-Rank $_ }, killing selection -- while the script still
# exits 0, so the loop records a normal cycle that dispatched nothing. Silent starvation is
# worse than a halt because nothing reports it. queue.json demonstrably carries prose in this
# field (DISPATCH-CDX-14, DISPATCH-CDX-15), so the authoring habit is real, not hypothetical.



def workstream_dry_run(tmp_path, items, track="factory"):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"schema": "test", "items": items}), encoding="ascii")
    return subprocess.run(
        [
            "pwsh", "-NoProfile", "-File", str(WORKSTREAM),
            "-Track", track, "-DryRun", "-NoLandingProbe", "-QueuePath", str(queue),
        ],
        text=True, capture_output=True,
    )


# 'queued', not 'booked': PR #63 made the dispatcher's admission an ALLOWLIST derived from
# queue.json's own note, where booked means "named trigger, not schedulable". These fixtures
# exist to reach Get-Rank, so they must carry a state a lane actually owns. The rule is right;
# the fixture was relying on booked being dispatchable, which it never should have been.
NUMERIC_CARD = {"id": "NUMERIC-1", "state": "queued", "track": "factory", "priority": 1, "title": "normal"}
PROSE_CARD = {
    "id": "PROSE-1", "state": "queued", "track": "factory",
    "priority": "CRITICAL PATH - the cap is now two blocks away, not one",
    "title": "prose in the priority field",
}


def test_a_prose_priority_does_not_stop_the_dispatcher_selecting(tmp_path):
    out = workstream_dry_run(tmp_path, [NUMERIC_CARD, PROSE_CARD])
    assert "card=NUMERIC-1" in out.stdout, out.stdout + out.stderr
    assert "Sort-Object" not in out.stderr, "selection still dies on the cast"


def test_a_prose_priority_is_reported_loudly_rather_than_swallowed(tmp_path):
    # Ranking it 999 silently would hide a card that someone deliberately marked urgent.
    out = workstream_dry_run(tmp_path, [NUMERIC_CARD, PROSE_CARD])
    assert "NON-NUMERIC PRIORITY" in out.stderr
    assert "PROSE-1" in out.stderr


def test_the_priority_warning_stays_off_machine_readable_stdout(tmp_path):
    # The [WORKSTREAM] stdout lines are parsed, and emitting inside a Sort-Object property
    # block would make the sort key an array rather than an int.
    out = workstream_dry_run(tmp_path, [NUMERIC_CARD, PROSE_CARD])
    assert "NON-NUMERIC PRIORITY" not in out.stdout


def test_the_priority_warning_is_emitted_once_per_card(tmp_path):
    # Sort-Object may evaluate the property block more than once per item.
    out = workstream_dry_run(tmp_path, [NUMERIC_CARD, PROSE_CARD])
    assert out.stderr.count("NON-NUMERIC PRIORITY on card 'PROSE-1'") == 1


def test_a_card_with_a_prose_priority_is_still_dispatchable(tmp_path):
    # Ranked last, not excluded -- a malformed field must not make work unreachable.
    out = workstream_dry_run(tmp_path, [PROSE_CARD])
    assert "card=PROSE-1" in out.stdout, out.stdout + out.stderr


def test_a_wholly_numeric_queue_produces_no_priority_warning(tmp_path):
    out = workstream_dry_run(tmp_path, [NUMERIC_CARD])
    assert "NON-NUMERIC PRIORITY" not in out.stderr
    assert "card=NUMERIC-1" in out.stdout


# --- Invoke-Lane: the exit code must be the truth ------------------------------------------
# The script's own header says "it is alive because it is running and dead when it exits, and the
# exit code is the truth" -- and it did not honour that at its own boundary. It computed the
# lane's exit code, wrote it into the receipt, printed it, then fell off the end, which exits 0.
# Invoke-Workstream captured that 0 and logged laneExitCode=0, so a failed lane was recorded as a
# success. Measured 2026-09-05: two of twelve dispatches hit HTTP 429 ("You've hit your session
# limit"), produced nothing, carried exitCode 1 in their receipts and laneExitCode 0 in the log.

LANE_RUNNER = ROOT / "tools" / "coordination" / "Invoke-Lane.ps1"


def test_invoke_lane_propagates_its_exit_code_instead_of_falling_off_the_end():
    body = LANE_RUNNER.read_text(encoding="utf-8")
    assert "exit $propagated" in body, (
        "Invoke-Lane must exit with the lane's outcome; falling off the end exits 0 and makes "
        "every failed lane look successful to Invoke-Workstream's laneExitCode"
    )


def test_invoke_lane_maps_the_negative_sentinels_rather_than_passing_them_through():
    # -1 would surface as 255 through a process exit code, and -999 does not survive at all.
    body = LANE_RUNNER.read_text(encoding="utf-8")
    assert "-1      { 124 }" in body, "timeout sentinel is not mapped"
    assert "-999    { 127 }" in body, "never-completed sentinel is not mapped"


def test_the_timeout_code_matches_the_taxonomy_the_repo_already_uses():
    # boundedRunnerExitCodes already fixes timeout=124; a second private meaning for the same
    # condition is how two tools end up disagreeing about one event.
    config = json.loads((ROOT / "closeout.config.json").read_text(encoding="utf-8"))
    # The taxonomy lives under `locking`, not at the top level. Found by searching the config
    # rather than assuming: the first version of this test guessed the top level, and a test that
    # skips is a test that never fired.
    codes = config["locking"]["boundedRunnerExitCodes"]
    assert int(codes["timeout"]) == 124
    assert "-1      { 124 }" in LANE_RUNNER.read_text(encoding="utf-8")

DISPATCHER = ROOT / "tools" / "coordination" / "Invoke-Workstream.ps1"


def run_dispatcher(tmp_path, items, *extra):
    """Run the real dispatcher against a synthetic queue fixture, -DryRun and
    -NoLandingProbe so it never calls gh or Invoke-Lane.ps1 for real. Uses -QueuePath,
    which exists precisely so a fixture queue can be substituted (the script refuses to
    ever mutate the canonical one). A successful (non-empty-candidates) run still writes
    a real prompt file under the live repo's .claude-state\\fleet-runs\\prompts\\ -- that
    is the dispatcher's normal side effect even under -DryRun -- so the caller must clean
    up any path reported on a "WORKSTREAM: prompt=" line.
    """
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps({"schema": "dual-lane-queue.v1", "note": "test fixture", "items": items}))
    result = subprocess.run(
        [
            "pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(DISPATCHER), "-QueuePath", str(queue_path), "-NoLandingProbe", "-DryRun", *extra,
        ],
        text=True, capture_output=True,
    )
    prompt_path = None
    for line in result.stdout.splitlines():
        if line.startswith("WORKSTREAM: prompt="):
            prompt_path = Path(line.split("=", 1)[1].strip())
    if prompt_path and prompt_path.exists():
        prompt_path.unlink()
    return result


def test_booked_card_is_reported_not_dispatched():
    """B2-TOOLING-BASELINE (state=booked) burned a dispatch slot on 2026-09-05: the old
    $Terminal blocklist did not name 'booked', so it was treated as ordinary live work.
    queue.json's own note defines 'booked' as "named trigger, not schedulable". A card in
    that state must be reported via a NOT-SCHEDULABLE line and must never be selected,
    even when it is the only candidate on its track."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        result = run_dispatcher(
            tmp_path,
            [{"id": "TEST-BOOKED-1", "state": "booked", "track": "factory", "priority": 1}],
            "-Track", "factory",
        )
        assert "NOT-SCHEDULABLE card=TEST-BOOKED-1 state=booked" in result.stdout
        assert "WORKSTREAM: track=factory card=TEST-BOOKED-1" not in result.stdout
        assert result.returncode != 0


def test_queued_card_on_a_track_is_still_dispatched():
    """Control for the rule above: a card in a genuinely schedulable state (queued) with
    a real track must still be picked and dry-run dispatched, so the allowlist does not
    over-exclude ordinary live work."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        result = run_dispatcher(
            tmp_path,
            [{"id": "TEST-QUEUED-1", "state": "queued", "track": "factory", "priority": 1}],
            "-Track", "factory",
        )
        assert "WORKSTREAM: NOT-SCHEDULABLE" not in result.stdout
        assert "WORKSTREAM: track=factory card=TEST-QUEUED-1" in result.stdout
        assert result.returncode == 0


def test_untracked_card_is_not_auto_selected():
    """Root cause of the same defect: Get-Track returns 'UNSET' for a card with no track,
    and the default -Track auto pool never filtered by track at all, so an untracked card
    was always a candidate once the tracked pools ran dry. A schedulable-state card with
    no track (queue field) must be reported and skipped under the default auto selection."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        result = run_dispatcher(
            tmp_path,
            [{"id": "TEST-UNTRACKED-1", "state": "queued", "priority": 1}],
        )
        assert "NOT-SCHEDULABLE card=TEST-UNTRACKED-1" in result.stdout
        assert "no-board-track-set" in result.stdout
        assert "WORKSTREAM: track=" not in result.stdout
        assert result.returncode != 0


def test_dispatched_untracked_target_state_is_still_schedulable():
    """'dispatched-untracked-target' is a REAL state already live in the board's queue
    (SIDECAR-COVERAGE-1, track=factory, priority=7, owner=codex) - its own stateReason
    explains that "untracked" means the deliverable is gitignored and has no gate, which
    is unrelated to whether this script's `track` field is set. Excluding this state from
    the allowlist would silently strand a card a lane genuinely still owns, which is the
    exact class of defect this fix exists to remove, just aimed at a different card."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        result = run_dispatcher(
            tmp_path,
            [{"id": "TEST-DUT-1", "state": "dispatched-untracked-target", "track": "factory", "priority": 7}],
            "-Track", "factory",
        )
        assert "WORKSTREAM: NOT-SCHEDULABLE" not in result.stdout
        assert "WORKSTREAM: track=factory card=TEST-DUT-1" in result.stdout
        assert result.returncode == 0


def test_explicit_track_unset_still_dispatches_an_untracked_card():
    """Control for the rule above: Invoke-WorkstreamLoop.ps1 rotates through '-Track UNSET'
    on purpose as its own named track, so an explicit request for the UNSET pool must keep
    working even though the default AUTO pool now excludes it."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        result = run_dispatcher(
            tmp_path,
            [{"id": "TEST-UNTRACKED-2", "state": "queued", "priority": 1}],
            "-Track", "UNSET",
        )
        assert "WORKSTREAM: NOT-SCHEDULABLE" not in result.stdout
        assert "WORKSTREAM: track=UNSET card=TEST-UNTRACKED-2" in result.stdout
        assert result.returncode == 0


# --- queue-derive.ps1: the ref it MEASURES AGAINST must not itself be stale -----------------
# Every finding is "is <sha> an ancestor of -MasterRef". A -MasterRef behind its own upstream does
# not make the tool fail, it makes it QUIETLY WRONG in both directions. Measured 2026-09-05: the
# board's local `master` was 5 behind `fork/master`, so c043f6fc -- the commit that landed
# B2-TOOLING-BASELINE -- was not an ancestor of `master` while being one of `fork/master`. It was
# fast-forwarded by hand at 07:05Z and was behind AGAIN by 13:41Z. A hand fix is not a fix.

def stale_ref_repo(tmp_path):
    """A clone whose local branch is strictly BEHIND its upstream -- the exact live defect."""
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=origin, check=True)
    (origin / "f.txt").write_text("one")
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=origin, check=True)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=clone, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=clone, check=True)

    # origin moves on; the clone fetches, so origin/main ends up ahead of the clone's own main.
    for n in ("two", "three"):
        (origin / "f.txt").write_text(n)
        subprocess.run(["git", "commit", "-qam", n], cwd=origin, check=True)
    subprocess.run(["git", "fetch", "-q"], cwd=clone, check=True)

    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"schema": "dual-lane-queue.v1", "items": []}))
    return clone, queue


def run_derive(clone, queue, ref, extra_env=None):
    e = dict(os.environ)
    # We run the branch's own copy of the script, which by construction differs from fork/master
    # while the PR is open; that is the currency guard's business, not this test's.
    e["MLV_ALLOW_STALE_TOOLS"] = "1"
    e.pop("MLV_ALLOW_STALE_MASTER_REF", None)
    if extra_env:
        e.update(extra_env)
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(DERIVE), "-RepoRoot", str(clone), "-QueueFile", str(queue),
         "-MasterRef", ref, "-NoLandingProbe"],
        text=True, capture_output=True, env=e,
    )


def test_queue_derive_REFUSES_a_master_ref_that_is_behind_its_upstream(tmp_path):
    clone, queue = stale_ref_repo(tmp_path)
    r = run_derive(clone, queue, "main")
    assert r.returncode == 14, "expected refusal exit 14, got %d: %s%s" % (
        r.returncode, r.stdout, r.stderr)
    assert "STALE MEASURING REF REFUSED" in r.stderr
    assert "2 commit(s) behind" in r.stderr, r.stderr
    # The refusal must be ACTIONABLE, not merely correct: it names the fix and the escape hatch.
    assert "git -C" in r.stderr and "fetch" in r.stderr
    assert "MLV_ALLOW_STALE_MASTER_REF=1" in r.stderr


def test_the_stale_ref_refusal_has_a_working_escape_hatch(tmp_path):
    clone, queue = stale_ref_repo(tmp_path)
    r = run_derive(clone, queue, "main", {"MLV_ALLOW_STALE_MASTER_REF": "1"})
    assert r.returncode == 0, "escape hatch did not let the audit run: %s%s" % (r.stdout, r.stderr)
    assert "NO MISMATCHES" in r.stdout


def test_measuring_against_the_upstream_directly_is_never_refused(tmp_path):
    """The refusal must not fire on the one ref that is current by definition."""
    clone, queue = stale_ref_repo(tmp_path)
    r = run_derive(clone, queue, "origin/main")
    assert r.returncode == 0, "%s%s" % (r.stdout, r.stderr)


def test_a_branch_that_is_AHEAD_of_its_upstream_is_not_refused(tmp_path):
    """Unpushed local work is normal development, NOT the staleness defect. Refusing on it would
    make the tool unusable exactly when someone is building a change to the board."""
    clone, queue = stale_ref_repo(tmp_path)
    subprocess.run(["git", "merge", "-q", "origin/main"], cwd=clone, check=True)
    (clone / "f.txt").write_text("local")
    subprocess.run(["git", "commit", "-qam", "unpushed"], cwd=clone, check=True)
    r = run_derive(clone, queue, "main")
    assert r.returncode == 0, "a branch AHEAD of its upstream was refused: %s%s" % (
        r.stdout, r.stderr)


def test_the_stale_ref_guard_fails_OPEN_when_there_is_no_upstream(tmp_path):
    """Polarity copied from assert-script-currency.ps1: stop only on PROVEN drift. A repo with no
    remote cannot prove anything, and a guard that blocked on 'I could not check' would be worse
    than the bug it prevents."""
    repo = tmp_path / "solo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)
    queue = tmp_path / "q.json"
    queue.write_text(json.dumps({"schema": "dual-lane-queue.v1", "items": []}))
    r = run_derive(repo, queue, "main")
    assert r.returncode == 0, "guard blocked a repo with no upstream to compare: %s" % r.stderr
    assert "STALE MEASURING REF" not in r.stderr


def test_the_refusal_suggests_a_fix_that_actually_WORKS_when_the_ref_is_checked_out(tmp_path):
    """MEASURED while using this guard on 2026-09-05, minutes after writing it.

    It printed `git fetch fork master:master`. Git REFUSES that with "refusing to fetch into
    branch 'refs/heads/master' checked out at ..." -- and the board root had moved onto master
    hours earlier, so the single command the tool suggested was the one command that could not
    work. A refusal that names an impossible fix is a refusal nobody can act on, which is most of
    the way back to being silent.
    """
    clone, queue = stale_ref_repo(tmp_path)   # 'main' IS checked out in this clone
    r = run_derive(clone, queue, "main")
    assert r.returncode == 14
    assert "merge --ff-only origin/main" in r.stderr, r.stderr
    assert "is CHECKED OUT at that path" in r.stderr, r.stderr
    # The impossible form must not be OFFERED AS A COMMAND. It may still be NAMED in the prose
    # that explains why it is not offered -- an earlier draft of this test failed on exactly that
    # sentence, which is the test being wrong rather than the message.
    commands = [l for l in r.stderr.splitlines() if l.strip().startswith("git -C")]
    assert commands, "the refusal offered no command at all"
    assert not [l for l in commands if "main:main" in l], (
        "still OFFERING the refspec fetch that git refuses for a checked-out branch: %s" % commands
    )


def test_the_refusal_suggests_the_refspec_fetch_when_the_ref_is_NOT_checked_out(tmp_path):
    """The other half of the same decision. With the ref not checked out anywhere, the one-line
    refspec fetch is correct and is the better advice -- it does not touch any working tree."""
    clone, queue = stale_ref_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, text=True,
                          capture_output=True, check=True).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "--detach", head], cwd=clone, check=True)
    r = run_derive(clone, queue, "main")
    assert r.returncode == 14
    assert "fetch origin main:main" in r.stderr, r.stderr
    assert "is CHECKED OUT at that path" not in r.stderr, r.stderr


# =============================================================================================
# TOOL-LOOP-PLUMBING-1: install-arg forwarding, kind-based lane resolution, the editing dispatch
# (worktree + composer + reservations + kill-switch recheck), and the pre-dispatch PR-review
# evidence exporter.
# =============================================================================================

LOOP_INSTALL_ARGS = ROOT / "tools" / "coordination" / "loop-install-args.ps1"
COMPOSE_CLI = ROOT / "tools" / "coordination" / "Compose-LanePrompt.ps1"
EXPORT_PR_EVIDENCE = ROOT / "tools" / "coordination" / "Export-PrReviewEvidence.ps1"
TEMPLATE_TEXT = (ROOT / "docs" / "lane-prompts" / "v2" / "product-card-TEMPLATE.md").read_text(encoding="utf-8")


def run_pwsh_command(cmd):
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        text=True, capture_output=True,
    )


# --- loop-install-args.ps1: -Install must persist EVERY schedulable parameter --------------
# UNTIL THIS CARD -Install persisted only four of the loop's own parameters (DailyBudget,
# MaxDispatchesPerCycle, TimeoutSec, StaleHours) and silently dropped -Tracks/-Lane/-AllowEdits,
# so a reinstall reset every one of them to its default -- the same class of defect that made the
# 2026-09-03 budget-flag omission an emergency. Get-InstallArgLine/Resolve-Tracks are pure and
# dot-sourced precisely so they can be tested without running the loop itself.

def call_get_install_arg_line(tracks, lane="", allow_edits=False):
    tracks_literal = "@(" + ",".join("'%s'" % t for t in tracks) + ")"
    cmd = (
        ". '%s'; Get-InstallArgLine -ScriptPath 'X:\\loop.ps1' -DailyBudget 12 "
        "-MaxDispatchesPerCycle 2 -TimeoutSec 1500 -StaleHours 12 -Tracks %s%s%s"
        % (
            LOOP_INSTALL_ARGS.as_posix(),
            tracks_literal,
            " -Lane %s" % lane if lane else "",
            " -AllowEdits" if allow_edits else "",
        )
    )
    return run_pwsh_command(cmd)


def test_install_arg_line_forwards_tracks_lane_and_allowedits():
    result = call_get_install_arg_line(["product", "playback"], lane="sonnet", allow_edits=True)
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert '-Tracks "product,playback"' in line, line
    assert "-Lane sonnet" in line, line
    assert "-AllowEdits" in line, line


def test_install_arg_line_omits_lane_and_allowedits_when_unset():
    result = call_get_install_arg_line(["playback"])
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert '-Tracks "playback"' in line, line
    assert "-Lane" not in line, line
    assert "-AllowEdits" not in line, line


def test_resolve_tracks_splits_a_comma_joined_single_string():
    """A pwsh -File scheduled-task action hands -Tracks back as ONE literal string; Resolve-Tracks
    is what the loop calls immediately after binding $Tracks to undo that."""
    cmd = ". '%s'; (Resolve-Tracks -Tracks @('product,playback')) -join '|'" % LOOP_INSTALL_ARGS.as_posix()
    result = run_pwsh_command(cmd)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "product|playback"


def test_resolve_tracks_leaves_a_genuine_multi_element_array_alone():
    cmd = ". '%s'; (Resolve-Tracks -Tracks @('product','playback')) -join '|'" % LOOP_INSTALL_ARGS.as_posix()
    result = run_pwsh_command(cmd)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "product|playback"


def test_resolve_tracks_does_not_split_a_single_track_that_has_no_comma():
    cmd = ". '%s'; (Resolve-Tracks -Tracks @('UNSET')) -join '|'" % LOOP_INSTALL_ARGS.as_posix()
    result = run_pwsh_command(cmd)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "UNSET"


def test_a_dry_run_cycle_started_from_the_persisted_arg_line_reports_the_resolved_tracks():
    """Proves the round trip through REAL pwsh -File argument binding, not just the pure
    Resolve-Tracks function in isolation: -Tracks "product,playback" arrives at the loop as a
    single-element array and must still resolve and print as two comma-separated tracks.
    -MaxDispatchesPerCycle 0 means no dispatch is attempted regardless of the real board's queue
    state, and the kill switch (armed or not) is checked only AFTER this line is printed.

    MINOR 4 (sol round-1 review): this used to hand-reconstruct the scheduled-task argument list
    directly as a Python literal (-Tracks "product,playback" -Lane sonnet -AllowEdits), never
    actually calling Get-InstallArgLine -- so a regression in the arg-line BUILDER itself (a
    dropped flag, wrong quoting, wrong flag spelling) would not show up here even though this
    test's whole point is the exact-line install/reinstall contract. Now it calls
    Get-InstallArgLine for real, takes its ACTUAL returned string, and parses THAT string into an
    argv list -- exactly what a `pwsh -File` scheduled-task action does with a persisted
    Arguments string -- before starting the -DryRun cycle from it."""
    # -MaxDispatchesPerCycle 0 goes INTO the persisted line itself (not appended afterwards): the
    # loop's own param binder throws "specified more than once" if the same flag appears twice in
    # argv, and 0 makes the per-track dispatch loop break before attempting a single real
    # dispatch against the live board, which -DryRun alone would not prevent (it would still
    # forward -DryRun to up to MaxDispatchesPerCycle real dispatcher invocations).
    get_line_cmd = (
        ". '%s'; Get-InstallArgLine -ScriptPath '%s' -DailyBudget 12 -MaxDispatchesPerCycle 0 "
        "-TimeoutSec 1500 -StaleHours 12 -Tracks @('product','playback') -Lane sonnet -AllowEdits"
        % (LOOP_INSTALL_ARGS.as_posix(), LOOP.as_posix())
    )
    line_result = run_pwsh_command(get_line_cmd)
    assert line_result.returncode == 0, line_result.stdout + line_result.stderr
    arg_line = line_result.stdout.strip()
    assert '-Tracks "product,playback"' in arg_line, arg_line
    assert '-Lane sonnet' in arg_line, arg_line
    assert '-AllowEdits' in arg_line, arg_line

    # shlex with posix=False keeps Windows-style backslash paths intact (no backslash-escape
    # processing) while still respecting quotes for word-splitting; the quote characters
    # themselves are left attached and must be stripped before use as a real argv element,
    # since subprocess.run's list form passes each element through literally (no shell requoting).
    raw_tokens = shlex.split(arg_line, posix=False)
    argv = [t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t for t in raw_tokens]

    result = subprocess.run(
        ["pwsh.exe", *argv, "-DryRun"],
        text=True, capture_output=True, timeout=120,
    )
    assert "LOOP: tracks=product, playback" in result.stdout, (
        "argLine=%r argv=%r\n%s%s" % (arg_line, argv, result.stdout, result.stderr)
    )


# --- kind/owner/scope-based lane resolution (deliverable 2) ---------------------------------

def test_kind_and_owner_resolve_the_lane_to_sonnet():
    """0.18 seeds every product/playback card with kind and owner=sonnet; the dispatcher must
    route it to the sonnet lane with no explicit -Lane, replacing the old needsShell-only
    heuristic for any card that names both fields."""
    with_tmp_result = run_dispatcher_lane_test(
        {"id": "TEST-KIND-1", "state": "queued", "track": "product", "kind": "product",
         "owner": "sonnet", "priority": 1},
        "-Track", "product",
    )
    assert "lane=sonnet" in with_tmp_result.stdout, with_tmp_result.stdout + with_tmp_result.stderr


def test_a_kind_only_card_is_not_selected_by_track_filtering():
    """S82: track SELECTION stays keyed on the queue's own `track` field, never on `kind` -- a
    card that carries kind=product but no track field is UNSET on track (Get-Track only reads
    `track`) and must not be picked by an explicit -Track product, even though its kind matches.
    An explicit non-auto -Track that matches nothing reports NO-LIVE-CARDS on that track (a
    distinct, pre-existing diagnostic from the auto-pool's own no-board-track-set) -- what this
    test actually pins is that the card is never selected, whichever diagnostic explains why."""
    result = run_dispatcher_lane_test(
        {"id": "TEST-KIND-ONLY-1", "state": "queued", "kind": "product", "owner": "sonnet", "priority": 1},
        "-Track", "product",
    )
    assert "WORKSTREAM: track=product card=TEST-KIND-ONLY-1" not in result.stdout, result.stdout + result.stderr
    assert result.returncode != 0


def test_a_recon_scope_prefix_routes_to_luna():
    result = run_dispatcher_lane_test(
        {"id": "TEST-RECON-1", "state": "queued", "track": "factory", "priority": 1,
         "scope": "RECON: survey the CI queue"},
        "-Track", "factory",
    )
    assert "lane=luna" in result.stdout, result.stdout + result.stderr


def test_a_review_scope_prefix_routes_to_fable():
    result = run_dispatcher_lane_test(
        {"id": "TEST-REVIEW-1", "state": "queued", "track": "factory", "priority": 1,
         "scope": "REVIEW: read the plan"},
        "-Track", "factory",
    )
    assert "lane=fable" in result.stdout, result.stdout + result.stderr


def test_explicit_lane_wins_over_kind_based_resolution():
    result = run_dispatcher_lane_test(
        {"id": "TEST-EXPLICIT-1", "state": "queued", "track": "product", "kind": "product",
         "owner": "sonnet", "priority": 1},
        "-Track", "product", "-Lane", "fable",
    )
    assert "lane=fable" in result.stdout, result.stdout + result.stderr


def run_dispatcher_lane_test(item, *extra):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        return run_dispatcher(Path(td), [item], *extra)


# --- deliverable 9 wiring: the dispatcher itself must call the pre-dispatch PR-review evidence
# exporter before a review-lane starts (MAJOR 1, sol round-1 review). The exporter script has its
# own standalone tests further down; these three exercise the DISPATCHER's wiring specifically,
# with a fake exporter shim standing in for the real one -- matching the existing fake-gh shim
# pattern rather than re-testing the exporter in isolation, which is exactly what sol's finding
# says the previous suite was missing (the only reference to the exporter outside its own test
# was in this file, never in a production caller).

FAKE_EXPORTER_SHIM = (
    "param([int]$PrNumber,[string]$RunDir,[string]$RepoRoot,[string]$GhExe)\n"
    "if (-not (Test-Path -LiteralPath $RunDir)) { New-Item -ItemType Directory -Path $RunDir -Force | Out-Null }\n"
    "Set-Content -LiteralPath (Join-Path $RunDir 'exporter-called.txt') "
    "-Value ($PrNumber.ToString() + '|' + $RunDir)\n"
    "exit 0\n"
)

FAKE_EXPORTER_SHIM_REFUSES = (
    "param([int]$PrNumber,[string]$RunDir,[string]$RepoRoot,[string]$GhExe)\n"
    "Write-Output \"REFUSED: pr-head-drift before=a after=b pr=$PrNumber\"\n"
    "exit 3\n"
)


def test_a_review_lane_dispatch_calls_the_pr_evidence_exporter_before_the_lane_starts(tmp_path):
    """The dispatcher must call Export-PrReviewEvidence.ps1 itself before a review-lane (routed
    here by the REVIEW: scope rule to fable) starts, rather than leaving the lane to call `gh`
    and hit the read-only sandbox's Access-is-denied wall. Run under -DryRun (via
    run_dispatcher_lane_test), so the exporter call must happen BEFORE the dry-run exit -- exactly
    like the generic hosted-evidence export it sits beside."""
    fake_exporter = tmp_path / "fake-exporter.ps1"
    fake_exporter.write_text(FAKE_EXPORTER_SHIM, encoding="ascii")
    result = run_dispatcher_lane_test(
        {"id": "TEST-REVIEW-EVIDENCE-1", "state": "queued", "track": "factory", "priority": 1,
         "scope": "REVIEW: read the plan", "prNumber": 81},
        "-Track", "factory", "-ExporterPath", str(fake_exporter),
    )
    assert "lane=fable" in result.stdout, result.stdout + result.stderr
    # NOT \S+: the real board root is "C:\!Layi Wkspc\MLV-App", which contains a space, and
    # runDir is the last field on its line -- capture to end-of-line, not to the first whitespace.
    export_line = next(
        (l for l in result.stdout.splitlines() if "pre-dispatch review-evidence export" in l), None
    )
    assert export_line, "no export line reported: %s" % result.stdout
    run_dir = Path(export_line.split("runDir=", 1)[1].strip())
    marker = run_dir / "exporter-called.txt"
    try:
        assert marker.exists(), "the dispatcher never invoked the exporter: %s" % result.stdout
        assert marker.read_text(encoding="utf-8").startswith("81|"), marker.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_a_review_lane_dispatch_is_refused_when_the_exporter_refuses(tmp_path):
    """Fail CLOSED, the opposite polarity of the generic hosted-evidence export: unverified PR
    evidence is worse than no review at all, so an exporter refusal must stop the dispatch."""
    fake_exporter = tmp_path / "fake-exporter-fail.ps1"
    fake_exporter.write_text(FAKE_EXPORTER_SHIM_REFUSES, encoding="ascii")
    result = run_dispatcher_lane_test(
        {"id": "TEST-REVIEW-EVIDENCE-2", "state": "queued", "track": "factory", "priority": 1,
         "scope": "REVIEW: read the plan", "prNumber": 81},
        "-Track", "factory", "-ExporterPath", str(fake_exporter),
    )
    assert "REFUSED review-evidence-export-failed" in result.stdout, result.stdout + result.stderr
    assert result.returncode != 0
    assert "WORKSTREAM: track=factory card=TEST-REVIEW-EVIDENCE-2" not in result.stdout


def test_a_review_lane_dispatch_with_no_pr_number_does_not_call_the_exporter(tmp_path):
    """Scoped to cards that actually name a PR: a review-lane card with nothing to bind the
    exporter's mandatory -PrNumber to must not attempt the call at all."""
    fake_exporter = tmp_path / "fake-exporter-unused.ps1"
    fake_exporter.write_text(FAKE_EXPORTER_SHIM, encoding="ascii")
    result = run_dispatcher_lane_test(
        {"id": "TEST-REVIEW-EVIDENCE-3", "state": "queued", "track": "factory", "priority": 1,
         "scope": "REVIEW: read the plan"},
        "-Track", "factory", "-ExporterPath", str(fake_exporter),
    )
    assert "lane=fable" in result.stdout, result.stdout + result.stderr
    assert "pre-dispatch review-evidence export" not in result.stdout, result.stdout


# --- editing dispatch: procedure/worktree/composition/reservations (deliverables 2,3,5,6,7) -

def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_fields_text(card_id, extra_lines=""):
    return (
        "# FIELDS for %s\n"
        "CARD_ID: %s\n"
        "PRIORITY: 1\n"
        "CLIP_OR_NONE: none\n"
        "ALLOWED_PATHS: some/path.cpp\n"
        "DELIVERABLE: do the thing\n"
        "ACCEPTANCE: run the test\n"
        "VERIFY_FIRST: check first\n"
        "%s" % (card_id, card_id, extra_lines)
    )


def write_fields_card(dir_path, card_id, extra_lines=""):
    path = Path(dir_path) / ("fields-%s.md" % card_id)
    path.write_text(write_fields_text(card_id, extra_lines), encoding="ascii")
    return path


def editing_board(tmp_path, with_lane_shim=True):
    """A throwaway git repo playing the board root: a 'fork/master' ref for baseSha resolution
    and worktree-add, and the dual-lane coordination tree an editing dispatch reads from (the
    composer template, and by default a fake Start-EditingLane.ps1 shim so no real lane is ever
    started by a test)."""
    init_repo(tmp_path)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    head = git(tmp_path, "rev-parse", "HEAD")
    subprocess.run(["git", "update-ref", "refs/remotes/fork/master", head], cwd=tmp_path, check=True)

    dual = tmp_path / ".claude-state" / "coordination" / "dual-lane"
    (dual / "prompts" / "v2").mkdir(parents=True)
    (dual / "prompts" / "v2" / "product-card-TEMPLATE.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
    (dual / "receipts").mkdir(parents=True, exist_ok=True)

    if with_lane_shim:
        shim = dual / "Start-EditingLane.ps1"
        shim.write_text(
            "param([string]$Lane,[string]$PromptFile,[string]$WorkDir,[string]$Card,"
            "[string]$RunDir,[string]$ExtraReadDir,[int]$TimeoutSec)\n"
            "Write-Output ('SHIM: lane=' + $Lane + ' card=' + $Card + ' workDir=' + $WorkDir)\n"
            "exit 0\n",
            encoding="ascii",
        )
    return dual, head


def editing_dispatch_env(tmp_path):
    e = dict(os.environ)
    e["MLV_BOARD_ROOT"] = str(tmp_path)
    return e


def cleanup_lane_worktree(board_root, card_id):
    """Best-effort cleanup: a real -DryRun (or shimmed real) editing dispatch creates then
    removes its own C:\\mlvtmp\\lane-<card>-<ts> worktree, but a failed assertion must not leak
    one if a bug ever leaves it behind."""
    for p in glob.glob(str(Path("C:/mlvtmp") / ("lane-%s-*" % card_id))):
        subprocess.run(["git", "-C", str(board_root), "worktree", "remove", p, "--force"],
                        capture_output=True)
        shutil.rmtree(p, ignore_errors=True)
    subprocess.run(["git", "-C", str(board_root), "worktree", "prune"], capture_output=True)


def run_editing_dispatch(tmp_path, queue_items, card_id, extra=(), dry_run=True):
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps({"schema": "test", "items": queue_items}))
    args = [
        "pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(WORKSTREAM), "-QueuePath", str(queue_path), "-CardId", card_id,
        "-AllowEdits", "-NoLandingProbe", *extra,
    ]
    if dry_run:
        args.append("-DryRun")
    return subprocess.run(args, text=True, capture_output=True, env=editing_dispatch_env(tmp_path))


def test_editing_dispatch_prints_workdir_lane_and_a_full_basesha(tmp_path):
    """Acceptance: a -DryRun dispatch of an eligible product card prints workDir=, lane=sonnet,
    and baseSha=<40 hex> -- baseSha is fork/master resolved at dispatch time in the board repo."""
    dual, head = editing_board(tmp_path)
    proc = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-A")
    item = {"id": "TEST-EDIT-A", "state": "queued", "track": "product", "kind": "product",
            "owner": "sonnet", "priority": 1,
            "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-A.md",
            "procedureSha256": sha256_of(proc)}
    try:
        result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-A")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "lane=sonnet" in result.stdout, result.stdout
        m = re.search(r"WORKSTREAM: workDir=(\S+)", result.stdout)
        assert m, result.stdout
        m2 = re.search(r"WORKSTREAM: baseSha=([0-9a-f]{40})\b", result.stdout)
        assert m2, result.stdout
        assert m2.group(1) == head
    finally:
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-A")


def test_two_editing_dispatches_get_two_distinct_worktree_paths(tmp_path):
    dual, head = editing_board(tmp_path)
    proc_a = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-B1")
    proc_b = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-B2")
    items = [
        {"id": "TEST-EDIT-B1", "state": "queued", "track": "product", "kind": "product",
         "owner": "sonnet", "priority": 1,
         "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-B1.md",
         "procedureSha256": sha256_of(proc_a)},
        {"id": "TEST-EDIT-B2", "state": "queued", "track": "product", "kind": "product",
         "owner": "sonnet", "priority": 1,
         "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-B2.md",
         "procedureSha256": sha256_of(proc_b)},
    ]
    workdirs = []
    try:
        for card_id in ("TEST-EDIT-B1", "TEST-EDIT-B2"):
            result = run_editing_dispatch(tmp_path, items, card_id)
            assert result.returncode == 0, result.stdout + result.stderr
            m = re.search(r"WORKSTREAM: workDir=(\S+)", result.stdout)
            assert m, result.stdout
            workdirs.append(m.group(1))
        assert workdirs[0] != workdirs[1]
    finally:
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-B1")
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-B2")


def test_editing_dispatch_refuses_a_codex_lane(tmp_path):
    editing_board(tmp_path)
    item = {"id": "TEST-EDIT-CODEX-1", "state": "queued", "track": "product", "priority": 1}
    result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-CODEX-1", extra=("-Lane", "luna"))
    assert result.returncode == 6, result.stdout + result.stderr
    assert "REFUSED codex-lane-never-edits" in result.stdout


def test_editing_dispatch_refuses_a_card_with_no_procedure(tmp_path):
    editing_board(tmp_path)
    item = {"id": "TEST-EDIT-NOPROC-1", "state": "queued", "track": "product", "priority": 1}
    result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-NOPROC-1")
    assert result.returncode == 6, result.stdout + result.stderr
    assert "REFUSED procedure-missing-or-drifted" in result.stdout


def test_editing_dispatch_refuses_a_drifted_procedure_sha(tmp_path):
    dual, head = editing_board(tmp_path)
    proc = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-DRIFT-1")
    item = {"id": "TEST-EDIT-DRIFT-1", "state": "queued", "track": "product", "priority": 1,
            "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-DRIFT-1.md",
            "procedureSha256": "0" * 64}
    result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-DRIFT-1")
    assert result.returncode == 6, result.stdout + result.stderr
    assert "REFUSED procedure-missing-or-drifted" in result.stdout
    assert sha256_of(proc) != "0" * 64


def test_editing_dispatch_refuses_an_unknown_field(tmp_path):
    dual, head = editing_board(tmp_path)
    proc = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-UNK-1", extra_lines="FOO: a stray field\n")
    item = {"id": "TEST-EDIT-UNK-1", "state": "queued", "track": "product", "priority": 1,
            "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-UNK-1.md",
            "procedureSha256": sha256_of(proc)}
    result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-UNK-1")
    assert result.returncode == 6, result.stdout + result.stderr
    assert "REFUSED unknown-field" in result.stdout


def read_reservation_rows(dual):
    path = dual / "receipts" / "dispatch-reservations.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_a_real_editing_dispatch_reserves_before_it_starts_and_charges_after(tmp_path):
    """S76: a 'reserved' row is written BEFORE the process launches and a 'charged' row after,
    sharing one reservationId -- the loop counts only 'reserved' rows for today's spend."""
    dual, head = editing_board(tmp_path)
    proc = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-RES-1")
    item = {"id": "TEST-EDIT-RES-1", "state": "queued", "track": "product", "kind": "product",
            "owner": "sonnet", "priority": 1,
            "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-RES-1.md",
            "procedureSha256": sha256_of(proc)}
    try:
        result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-RES-1", dry_run=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SHIM: lane=sonnet card=TEST-EDIT-RES-1" in result.stdout, result.stdout
        rows = [r for r in read_reservation_rows(dual) if r["card"] == "TEST-EDIT-RES-1"]
        assert len(rows) == 2, rows
        assert rows[0]["state"] == "reserved", rows
        assert rows[1]["state"] == "charged", rows
        assert rows[0]["reservationId"] == rows[1]["reservationId"]
    finally:
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-RES-1")


def test_a_real_dispatch_refuses_when_the_kill_switch_is_armed_immediately_before_start(tmp_path):
    """The kill switch is re-checked immediately before this lane starts, not only once at the
    top of the loop's own cycle -- a long cycle can dispatch several lanes, and the switch may be
    armed between the cycle's check and this particular start."""
    dual, head = editing_board(tmp_path)
    proc = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-KILL-1")
    (dual / "WORKSTREAM-LOOP-DISABLED").write_text("armed for test\n", encoding="utf-8")
    item = {"id": "TEST-EDIT-KILL-1", "state": "queued", "track": "product", "priority": 1,
            "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-KILL-1.md",
            "procedureSha256": sha256_of(proc)}
    try:
        result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-KILL-1", dry_run=False)
        assert result.returncode == 6, result.stdout + result.stderr
        assert "REFUSED kill-switch-armed" in result.stdout
        assert "SHIM:" not in result.stdout
        assert not [r for r in read_reservation_rows(dual) if r["card"] == "TEST-EDIT-KILL-1"]
    finally:
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-KILL-1")


def test_kill_switch_armed_mid_run_by_the_lane_itself_blocks_the_next_dispatch(tmp_path):
    """MINOR 3 (sol round-1 review): the test above pre-arms the switch from Python BEFORE either
    dispatch runs, which an early one-time check (e.g. only at the top of a cycle, not
    immediately before each lane's own start) could also satisfy. Here the fake lane shim arms
    the switch itself, from inside what a real lane's own process would be, so only a check that
    re-reads the file at THIS dispatch's own start -- not a cached or once-per-cycle value -- can
    catch it before the second, separate dispatch."""
    dual, head = editing_board(tmp_path, with_lane_shim=False)
    proc_a = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-MIDRUN-A")
    proc_b = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-MIDRUN-B")
    kill_switch = dual / "WORKSTREAM-LOOP-DISABLED"
    shim = dual / "Start-EditingLane.ps1"
    shim.write_text(
        "param([string]$Lane,[string]$PromptFile,[string]$WorkDir,[string]$Card,"
        "[string]$RunDir,[string]$ExtraReadDir,[int]$TimeoutSec)\n"
        "Set-Content -LiteralPath '%s' -Value 'armed mid-run by the lane'\n"
        "Write-Output ('SHIM: lane=' + $Lane + ' card=' + $Card + ' workDir=' + $WorkDir)\n"
        "exit 0\n" % kill_switch.as_posix(),
        encoding="ascii",
    )
    items = [
        {"id": "TEST-EDIT-MIDRUN-A", "state": "queued", "track": "product", "kind": "product",
         "owner": "sonnet", "priority": 1,
         "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-MIDRUN-A.md",
         "procedureSha256": sha256_of(proc_a)},
        {"id": "TEST-EDIT-MIDRUN-B", "state": "queued", "track": "product", "kind": "product",
         "owner": "sonnet", "priority": 1,
         "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-MIDRUN-B.md",
         "procedureSha256": sha256_of(proc_b)},
    ]
    try:
        first = run_editing_dispatch(tmp_path, items, "TEST-EDIT-MIDRUN-A", dry_run=False)
        assert first.returncode == 0, first.stdout + first.stderr
        assert "SHIM: lane=sonnet card=TEST-EDIT-MIDRUN-A" in first.stdout, first.stdout
        assert kill_switch.exists(), "the shim did not arm the switch it was given"

        second = run_editing_dispatch(tmp_path, items, "TEST-EDIT-MIDRUN-B", dry_run=False)
        assert second.returncode == 6, second.stdout + second.stderr
        assert "REFUSED kill-switch-armed" in second.stdout
        assert "SHIM: lane=sonnet card=TEST-EDIT-MIDRUN-B" not in second.stdout
    finally:
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-MIDRUN-A")
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-MIDRUN-B")


def test_the_reservation_row_exists_before_the_lane_itself_finishes(tmp_path):
    """MINOR 3 (sol round-1 review): appending a 'reserved' row before start and a 'charged' row
    after would also pass a check that only inspects the file once the whole dispatcher process
    has already returned. Here the fake lane shim reads the reservation file FROM INSIDE its own
    run and asserts its 'reserved' row for THIS card is already on disk -- proving the row lands
    before the lane's own process even finishes, not merely before the dispatcher's return."""
    dual, head = editing_board(tmp_path, with_lane_shim=False)
    proc = write_fields_card(dual / "prompts" / "v2", "TEST-EDIT-RESORDER-1")
    reservations = dual / "receipts" / "dispatch-reservations.jsonl"
    shim = dual / "Start-EditingLane.ps1"
    shim.write_text(
        "param([string]$Lane,[string]$PromptFile,[string]$WorkDir,[string]$Card,"
        "[string]$RunDir,[string]$ExtraReadDir,[int]$TimeoutSec)\n"
        "$rows = @()\n"
        "if (Test-Path -LiteralPath '%s') {\n"
        "    $rows = @(Get-Content -LiteralPath '%s' | ForEach-Object { $_ | ConvertFrom-Json })\n"
        "}\n"
        "$mine = @($rows | Where-Object { $_.card -eq $Card -and $_.state -eq 'reserved' })\n"
        "if ($mine.Count -eq 0) { Write-Output 'SHIM: NO-RESERVED-ROW-YET'; exit 1 }\n"
        "Write-Output ('SHIM: lane=' + $Lane + ' card=' + $Card + ' sawReservedRow=' + $mine[0].reservationId)\n"
        "exit 0\n" % (reservations.as_posix(), reservations.as_posix()),
        encoding="ascii",
    )
    item = {"id": "TEST-EDIT-RESORDER-1", "state": "queued", "track": "product", "kind": "product",
            "owner": "sonnet", "priority": 1,
            "procedure": ".claude-state/coordination/dual-lane/prompts/v2/fields-TEST-EDIT-RESORDER-1.md",
            "procedureSha256": sha256_of(proc)}
    try:
        result = run_editing_dispatch(tmp_path, [item], "TEST-EDIT-RESORDER-1", dry_run=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "sawReservedRow=" in result.stdout, result.stdout
        assert "NO-RESERVED-ROW-YET" not in result.stdout
    finally:
        cleanup_lane_worktree(tmp_path, "TEST-EDIT-RESORDER-1")


# --- Invoke-Lane.ps1's own allowlist-required refusal, reachable only if something calls it
# directly instead of going through Start-EditingLane.ps1 -- which is exactly why deliverable 3
# requires EVERY editing dispatch to go through the wrapper.

def test_invoke_lane_refuses_allowedtools_all(tmp_path):
    cmd = (
        "try { & '%s' -Lane sonnet -Prompt 'x' -WorkDir '%s' -AllowEdits -AllowedTools 'ALL' } "
        "catch { $_.Exception.Message }" % (LANE_RUNNER.as_posix(), tmp_path.as_posix())
    )
    result = run_pwsh_command(cmd)
    assert "allowlist-required" in (result.stdout + result.stderr)


def test_invoke_lane_refuses_editing_with_allowedtools_entirely_absent(tmp_path):
    """MINOR 2 (sol round-1 review): the existing test above only covers -AllowedTools 'ALL'.
    The implementation's guard is `IsNullOrWhiteSpace($AllowedTools) -or $AllowedTools -eq 'ALL'`
    -- an `-or` with two independently reachable branches -- and the absent-argument branch
    (which binds $AllowedTools to its default '') had no test of its own until now."""
    cmd = (
        "try { & '%s' -Lane sonnet -Prompt 'x' -WorkDir '%s' -AllowEdits } "
        "catch { $_.Exception.Message }" % (LANE_RUNNER.as_posix(), tmp_path.as_posix())
    )
    result = run_pwsh_command(cmd)
    assert "allowlist-required" in (result.stdout + result.stderr)


# --- Compose-LanePrompt.ps1 / compose-lane-prompt-core.ps1: determinism, PR_STEP literals, ---
# field echo, unknown-field refusal (deliverable 6)

PR_STEP_LANE_CAN_OPEN_PR = (
    'gh pr create -R layibabalola/MLV-App --head {branch} --title "<card id>: <subject>" '
    '--body "<what, why, red run, green run>"; then print PR-OPENED: <number> as your last line.'
)
PR_STEP_OTHERWISE = (
    'Do NOT call gh. Print PUSHED: {branch} <head sha> as your last line; the dispatcher opens the PR.'
)


def compose(procedure_path, gh_capability, work_dir="C:\\mlvtmp\\lane-x", base_sha="a" * 40,
            run_dir=None, ts="20260101T000000Z"):
    run_dir = run_dir or (Path(procedure_path).parent / "run")
    args = [
        "pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(COMPOSE_CLI), "-ProcedurePath", str(procedure_path),
        "-WorkDir", work_dir, "-BaseSha", base_sha, "-RunDir", str(run_dir), "-Ts", ts,
        "-GhCapability", gh_capability,
    ]
    return subprocess.run(args, text=True, capture_output=True)


def test_composition_is_deterministic_byte_for_byte(tmp_path):
    proc = write_fields_card(tmp_path, "DET-1")
    r1 = compose(proc, "lane-can-open-pr")
    r2 = compose(proc, "lane-can-open-pr")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert r1.stdout == r2.stdout


def test_composed_fields_prompt_carries_the_lane_can_open_pr_literal_exactly(tmp_path):
    proc = write_fields_card(tmp_path, "PR-STEP-A")
    result = compose(proc, "lane-can-open-pr")
    assert result.returncode == 0, result.stdout + result.stderr
    assert PR_STEP_LANE_CAN_OPEN_PR.format(branch="product/PR-STEP-A") in result.stdout


def test_composed_fields_prompt_carries_the_otherwise_literal_exactly(tmp_path):
    proc = write_fields_card(tmp_path, "PR-STEP-B")
    result = compose(proc, "no-pr-capability")
    assert result.returncode == 0, result.stdout + result.stderr
    assert PR_STEP_OTHERWISE.format(branch="product/PR-STEP-B") in result.stdout


def test_composed_full_card_prompt_carries_the_lane_can_open_pr_literal_exactly(tmp_path):
    card = tmp_path / "card-PR-STEP-C.md"
    card.write_text("# CARD: PR-STEP-C\nSomething.\n{{PR_STEP}}\n", encoding="utf-8")
    result = compose(card, "lane-can-open-pr")
    assert result.returncode == 0, result.stdout + result.stderr
    assert PR_STEP_LANE_CAN_OPEN_PR.format(branch="product/PR-STEP-C") in result.stdout


def test_composed_full_card_prompt_carries_the_otherwise_literal_exactly(tmp_path):
    card = tmp_path / "card-PR-STEP-D.md"
    card.write_text("# CARD: PR-STEP-D\nSomething.\n{{PR_STEP}}\n", encoding="utf-8")
    result = compose(card, "no-pr-capability")
    assert result.returncode == 0, result.stdout + result.stderr
    assert PR_STEP_OTHERWISE.format(branch="product/PR-STEP-D") in result.stdout


def test_every_parsed_field_appears_byte_for_byte_in_the_composed_prompt(tmp_path):
    proc = write_fields_card(tmp_path, "FIELD-ECHO-1")
    text = proc.read_text(encoding="ascii")
    fields = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*):\s?(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2)
    result = compose(proc, "lane-can-open-pr")
    assert result.returncode == 0, result.stdout + result.stderr
    for label, value in fields.items():
        if label == "CARD_ID":
            continue  # substituted in multiple places; covered implicitly by the branch/title
        assert value in result.stdout, "field %s=%r missing byte-for-byte from composed prompt" % (label, value)


def test_compose_cli_refuses_an_unknown_field(tmp_path):
    proc = write_fields_card(tmp_path, "UNK-CLI-1", extra_lines="ZORP: not a real field\n")
    result = compose(proc, "lane-can-open-pr")
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("REFUSED: unknown-field")


# --- Export-PrReviewEvidence.ps1: pinned repo, byte-exact exports, drift refusal, missing --
# required context reported as a failure (deliverable 9)

FAKE_GH_REPO_STRING = "layibabalola/MLV-App"

FAKE_GH_SCRIPT = (
    "$stateDir = $env:FAKE_GH_STATE_DIR\n"
    "Add-Content -LiteralPath (Join-Path $stateDir 'call_log.txt') -Value ($args -join '|')\n"
    "function Get-NextLine([string]$Path, [string]$CounterPath) {\n"
    "    $n = 0\n"
    "    if (Test-Path -LiteralPath $CounterPath) { $n = [int](Get-Content -LiteralPath $CounterPath -Raw) }\n"
    "    $n = $n + 1\n"
    "    Set-Content -LiteralPath $CounterPath -Value $n\n"
    "    $lines = @(Get-Content -LiteralPath $Path)\n"
    "    return $lines[$n - 1]\n"
    "}\n"
    "if ($args.Count -ge 2 -and $args[0] -eq 'pr' -and $args[1] -eq 'view') {\n"
    "    Write-Output (Get-NextLine (Join-Path $stateDir 'pr_view.jsonl') (Join-Path $stateDir 'pr_view.count'))\n"
    "    exit 0\n"
    "}\n"
    "if ($args.Count -ge 1 -and $args[0] -eq 'api' -and (($args -join ' ') -match 'branches/master/protection')) {\n"
    "    Write-Output (Get-NextLine (Join-Path $stateDir 'protection.jsonl') (Join-Path $stateDir 'protection.count'))\n"
    "    exit 0\n"
    "}\n"
    "if ($args.Count -ge 2 -and $args[0] -eq 'pr' -and $args[1] -eq 'checks') {\n"
    "    Get-Content -LiteralPath (Join-Path $stateDir 'checks.json') -Raw\n"
    "    exit 0\n"
    "}\n"
    "Write-Error ('fake-gh: unrecognized args: ' + ($args -join ' '))\n"
    "exit 1\n"
)


def make_fake_gh(dir_path, pr_view_sequence, protection_sequence, checks_payload):
    state = dir_path / "fake-gh-state"
    state.mkdir()
    (state / "pr_view.jsonl").write_text("\n".join(json.dumps(x) for x in pr_view_sequence), encoding="utf-8")
    (state / "protection.jsonl").write_text("\n".join(json.dumps(x) for x in protection_sequence), encoding="utf-8")
    (state / "checks.json").write_text(json.dumps(checks_payload), encoding="utf-8")
    (state / "call_log.txt").write_text("", encoding="utf-8")
    shim = dir_path / "fake-gh.ps1"
    shim.write_text(FAKE_GH_SCRIPT, encoding="ascii")
    return shim, state


def pr_evidence_repo(tmp_path, with_fork_remote=True):
    """A repo with base/head commits and (by default) a REAL `fork` remote pinned at the base
    commit, so a genuine `git fetch fork` (MAJOR 2, sol round-1 review) succeeds instead of
    silently no-op'ing against a remote that was never more than a manually poked ref.

    The remote is a bare snapshot taken right after the base commit, not an alias to `repo`
    itself: `repo`'s own branch keeps moving (the head commit below), and if `fork` pointed at
    that same path, fetching AFTER the head commit would walk the live branch forward and
    silently repoint `fork/master` at head_sha instead of base_sha.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    # Named explicitly, not left to `init.defaultBranch`: the exporter hardcodes `fork/master`,
    # so the fork remote's default branch must actually be called `master` for a real fetch to
    # populate `refs/remotes/fork/master`.
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "f.txt").write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base_sha = git(repo, "rev-parse", "HEAD")

    if with_fork_remote:
        fork_remote = tmp_path / "fork_remote.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(fork_remote)], check=True)
        subprocess.run(["git", "remote", "add", "fork", str(fork_remote)], cwd=repo, check=True)
    # else: no remote named 'fork' at all -- `git fetch fork` fails deterministically (exit 128,
    # "'fork' does not appear to be a git repository"), the fetch-failure case MAJOR 2 covers.

    (repo / "f.txt").write_text("head\n")
    subprocess.run(["git", "commit", "-qam", "head commit"], cwd=repo, check=True)
    head_sha = git(repo, "rev-parse", "HEAD")
    return repo, base_sha, head_sha


def run_exporter(repo, run_dir, gh_shim, state_dir, pr_number=99):
    env = dict(os.environ)
    env["FAKE_GH_STATE_DIR"] = str(state_dir)
    args = [
        "pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(EXPORT_PR_EVIDENCE), "-PrNumber", str(pr_number), "-RunDir", str(run_dir),
        "-RepoRoot", str(repo), "-GhExe", str(gh_shim),
    ]
    return subprocess.run(args, text=True, capture_output=True, env=env)


def test_exporter_pins_the_repository_on_every_gh_call(tmp_path):
    """MAJOR 3 (sol round-1 review): the old assertion only checked that the repository STRING
    occurred SOMEWHERE in the joined argv line, which a repo name appearing in the wrong place
    (or coincidentally inside the API endpoint's path) would also satisfy -- it could false-green
    a `-R` flag that carried the wrong value, or a `gh api` call with no repo pin at all. This
    checks the actual argv TOKEN immediately after `-R` on every `pr view`/`pr checks` call, and
    the exact `repos/<repo>/branches/master/protection` endpoint string on the `gh api` call --
    not a substring hit against the whole line."""
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path)
    run_dir = tmp_path / "run"
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[
            {"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"},
            {"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"},
        ],
        protection_sequence=[["build"], ["build"]],
        checks_payload=[{"name": "build", "state": "SUCCESS", "link": "x"}],
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (state / "call_log.txt").read_text(encoding="utf-8").splitlines()
    assert calls, "gh was never invoked"
    saw_pr_call = False
    saw_api_call = False
    for line in calls:
        tokens = line.split("|")
        if tokens[:2] in (["pr", "view"], ["pr", "checks"]):
            saw_pr_call = True
            assert "-R" in tokens, "no -R flag on a pr view/checks call: %s" % line
            idx = tokens.index("-R")
            assert tokens[idx + 1] == FAKE_GH_REPO_STRING, (
                "the -R flag does not carry the exact pinned repo (got %r): %s" % (tokens[idx + 1], line)
            )
        if tokens[0] == "api":
            saw_api_call = True
            endpoint = tokens[1]
            assert endpoint == "repos/%s/branches/master/protection" % FAKE_GH_REPO_STRING, (
                "the gh api endpoint is not the exact pinned repo path: %s" % endpoint
            )
    assert saw_pr_call, "no pr view/checks call was observed"
    assert saw_api_call, "no gh api call was observed"


def test_exporter_repo_is_a_hardcoded_pin_not_a_caller_supplied_parameter(tmp_path):
    """MAJOR 3 (sol round-1 review): '-Repo is caller-overridable' was the actual defect behind
    the weak test above -- a parameter with a safe-looking default is still an argv path that can
    carry a different value in. The fix removes the parameter entirely rather than merely
    validating it, so this asserts on the source that no such parameter exists."""
    text = EXPORT_PR_EVIDENCE.read_text(encoding="utf-8")
    # Scoped to the SCRIPT's own param() block, not the internal Get-PrView/Get-RequiredContexts
    # helper functions further down, which legitimately take a $Repo parameter fed from the one
    # pinned script-scope value below -- that is an implementation detail, not a caller-facing
    # argv path. \b so this also does not false-positive on the unrelated [string]$RepoRoot.
    script_param_block = text[text.index("[CmdletBinding()]"):text.index("$ErrorActionPreference")]
    assert not re.search(r"\[string\]\$Repo\b", script_param_block), (
        "the repository is still a caller-settable parameter: %r" % script_param_block
    )
    assert "'layibabalola/MLV-App'" in text, "the pinned repo is no longer a literal in the source"


def extract_json_array_bytes(raw, key):
    """Slice out the raw bytes of a top-level JSON array value for `key` (e.g. b'"checks":
    [...]'), by bracket-balancing rather than reparsing -- so the comparison below is a real
    byte comparison of what was WRITTEN, not a reparse-and-recompare of what was MEANT."""
    marker = ('"%s":' % key).encode("ascii")
    idx = raw.index(marker)
    start = raw.index(b"[", idx)
    depth = 0
    i = start
    while i < len(raw):
        c = raw[i:i + 1]
        if c == b"[":
            depth += 1
        elif c == b"]":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
        i += 1
    raise AssertionError("unbalanced [ ] while extracting %r from JSON" % key)


def test_exporter_writes_both_exports_byte_exact(tmp_path):
    """MINOR 1 (sol round-1 review): a test named 'byte exact' that only reparses both files with
    json.loads and compares fields never actually compares a single byte -- BOM, whitespace,
    key ordering or any other byte-level change would still pass. The exporter writes the SAME
    `checks` array into both pr-99-checks.json and pr-99-review.json from the same $checks value,
    so their serialized `checks` bytes must be IDENTICAL; that is asserted here as a real
    raw-bytes comparison, on top of (not instead of) the existing semantic checks."""
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path)
    run_dir = tmp_path / "run"
    checks_payload = [{"name": "build", "state": "SUCCESS", "link": "x"}]
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[
            {"number": 99, "headRefOid": head_sha, "body": "the body", "state": "OPEN"},
            {"number": 99, "headRefOid": head_sha, "body": "the body", "state": "OPEN"},
        ],
        protection_sequence=[["build"], ["build"]],
        checks_payload=checks_payload,
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode == 0, result.stdout + result.stderr
    checks_raw = (run_dir / "pr-99-checks.json").read_bytes()
    review_raw = (run_dir / "pr-99-review.json").read_bytes()

    # the actual byte comparison the test's name claims
    checks_bytes = extract_json_array_bytes(checks_raw, "checks")
    review_checks_bytes = extract_json_array_bytes(review_raw, "checks")
    assert checks_bytes == review_checks_bytes, (
        "the 'checks' array is not byte-identical between pr-99-checks.json and "
        "pr-99-review.json: %r != %r" % (checks_bytes, review_checks_bytes)
    )

    checks_doc = json.loads(checks_raw.decode("utf-8"))
    review_doc = json.loads(review_raw.decode("utf-8"))
    assert checks_doc["checks"] == checks_payload
    assert review_doc["headRefOidBefore"] == head_sha
    assert review_doc["headRefOidAfter"] == head_sha
    assert review_doc["requiredContextsBefore"] == ["build"]
    assert review_doc["requiredContextsAfter"] == ["build"]
    assert review_doc["body"] == "the body"
    assert review_doc["checks"] == checks_payload
    assert review_doc["missingRequiredContexts"] == []


def test_exporter_refuses_when_git_fetch_fails(tmp_path):
    """MAJOR 2 (sol round-1 review): `git fetch fork` used to be piped to Out-Null with its exit
    code never checked, so a fetch failure was indistinguishable from success and the exporter
    proceeded to review whatever objects happened to already be local. `with_fork_remote=False`
    means no remote named 'fork' exists at all, so the fetch fails deterministically."""
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path, with_fork_remote=False)
    run_dir = tmp_path / "run"
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[{"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"}],
        protection_sequence=[["build"]],
        checks_payload=[{"name": "build", "state": "SUCCESS", "link": "x"}],
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode != 0
    assert "REFUSED: git-fetch-failed" in result.stdout, result.stdout + result.stderr
    assert not (state / "call_log.txt").read_text(encoding="utf-8").strip(), (
        "gh must never be called after a failed fetch"
    )
    assert not run_dir.exists() or not any(run_dir.iterdir())


def test_exporter_refuses_on_an_empty_head_sha(tmp_path):
    """MAJOR 2 (sol round-1 review): the old `if ($sha) { cat-file -e ... }` SKIPPED the
    commit-existence check entirely for a falsy value, so an empty headRefOid silently passed
    with no object ever verified. An empty or short sha must be a REFUSAL, not a skipped check."""
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path)
    run_dir = tmp_path / "run"
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[{"number": 99, "headRefOid": "", "body": "b", "state": "OPEN"}],
        protection_sequence=[["build"]],
        checks_payload=[{"name": "build", "state": "SUCCESS", "link": "x"}],
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode != 0
    assert "REFUSED: pr-sha-invalid field=head" in result.stdout, result.stdout + result.stderr
    assert not (run_dir / "pr-99-checks.json").exists()
    assert not (run_dir / "pr-99-review.json").exists()


def test_exporter_refuses_on_a_non_40_hex_head_sha(tmp_path):
    """MAJOR 2 (sol round-1 review): a short or otherwise malformed value is just as unbindable
    as an empty one -- both must fail the same explicit shape check before any cat-file call."""
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path)
    run_dir = tmp_path / "run"
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[{"number": 99, "headRefOid": "not-a-sha", "body": "b", "state": "OPEN"}],
        protection_sequence=[["build"]],
        checks_payload=[{"name": "build", "state": "SUCCESS", "link": "x"}],
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode != 0
    assert "REFUSED: pr-sha-invalid field=head value=not-a-sha" in result.stdout, result.stdout + result.stderr


def test_exporter_refuses_on_head_drift(tmp_path):
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path)
    run_dir = tmp_path / "run"
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[
            {"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"},
            {"number": 99, "headRefOid": "f" * 40, "body": "b", "state": "OPEN"},
        ],
        protection_sequence=[["build"], ["build"]],
        checks_payload=[{"name": "build", "state": "SUCCESS", "link": "x"}],
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode != 0
    assert "REFUSED: pr-head-drift" in result.stdout
    assert not (run_dir / "pr-99-checks.json").exists()
    assert not (run_dir / "pr-99-review.json").exists()


def test_exporter_refuses_on_required_context_drift(tmp_path):
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path)
    run_dir = tmp_path / "run"
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[
            {"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"},
            {"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"},
        ],
        protection_sequence=[["build"], ["build", "extra-check"]],
        checks_payload=[{"name": "build", "state": "SUCCESS", "link": "x"}],
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode != 0
    assert "REFUSED: required-context-drift" in result.stdout


def test_exporter_reports_a_missing_required_context_as_a_failure(tmp_path):
    """'lint' is required but never ran (absent from checks entirely, not merely non-SUCCESS)
    -- it must be reported, never silently folded into either verdict."""
    repo, base_sha, head_sha = pr_evidence_repo(tmp_path)
    run_dir = tmp_path / "run"
    shim, state = make_fake_gh(
        tmp_path,
        pr_view_sequence=[
            {"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"},
            {"number": 99, "headRefOid": head_sha, "body": "b", "state": "OPEN"},
        ],
        protection_sequence=[["build", "lint"], ["build", "lint"]],
        checks_payload=[{"name": "build", "state": "SUCCESS", "link": "x"}],
    )
    result = run_exporter(repo, run_dir, shim, state)
    assert result.returncode == 0, result.stdout + result.stderr
    review_doc = json.loads((run_dir / "pr-99-review.json").read_text(encoding="utf-8"))
    assert review_doc["missingRequiredContexts"] == ["lint"]
    assert "EXPORT: missing-required-context lint" in result.stdout
