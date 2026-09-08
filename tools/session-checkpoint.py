#!/usr/bin/env python3
"""session-checkpoint v2. SessionStart: detect an account rotation, list dirty
or ahead worktrees, print the newest checkpoints, re-seed the runbook, snapshot
current dirt. Stop: write a pointer-only checkpoint for THIS session to
Home/.claude/session-checkpoints/<repo-slug>/ (machine-local, outside the repo,
survives an account rotation). Never blocks, never exits non-zero, never runs a
git write. Owned dirt = files dirty now that were not dirty at SessionStart."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

RUNBOOK_CANDIDATES = ["docs/ops/ROTATION.md", "knowledge/ROTATION.md",
                      "roadmap/handoff/ROTATION.md", "docs/ROTATION.md"]


def git(cwd, *args):
    try:
        p = subprocess.run(["git", "-C", str(cwd), "--no-optional-locks", *args],
                           capture_output=True, text=True, timeout=60)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def account_id():
    try:
        d = json.load(open(Path.home() / ".claude.json", encoding="utf-8"))
        return str((d.get("oauthAccount") or {}).get("accountUuid") or "")[:8]
    except Exception:
        return ""


def worktree_scan(repo):
    out = []
    paths = [l[9:] for l in git(repo, "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]
    if not paths:
        return out
    main = git(paths[0], "symbolic-ref", "-q", "--short", "HEAD") or "HEAD"
    for p in paths:
        dirty = len(git(p, "diff", "--name-only").splitlines()) + len(git(p, "ls-files", "--others", "--exclude-standard").splitlines())
        ahead = git(p, "rev-list", "--count", f"{main}..HEAD") if p != paths[0] else "0"
        if dirty or (ahead not in ("", "0")):
            out.append(f"  {p}: dirty={dirty} ahead-of-{main}={ahead or '?'}")
    return out


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    cwd = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    sid = str(payload.get("session_id") or "unknown")[:8]
    event = payload.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "")
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return 0
    repo = Path(common).parent
    home = Path.home() / ".claude"
    base = home / "session-checkpoints" / repo.name.replace(" ", "-")
    base.mkdir(parents=True, exist_ok=True)
    snap = base / f".snap-{sid}.json"
    acct = account_id()
    dirty = set(git(cwd, "diff", "--name-only").splitlines()) | \
        set(git(cwd, "ls-files", "--others", "--exclude-standard").splitlines())
    if event == "SessionStart":
        snap.write_text(json.dumps(sorted(dirty)), encoding="utf-8")
        seen = base / ".account"
        prev = seen.read_text(encoding="utf-8").strip() if seen.exists() else ""
        if acct:
            seen.write_text(acct, encoding="utf-8")
        if not (home / "ROTATION.md").exists():
            for c in RUNBOOK_CANDIDATES:
                if (repo / c).exists():
                    shutil.copyfile(repo / c, home / "ROTATION.md")
                    break
        if prev and acct and prev != acct:
            print(f"ACCOUNT ROTATION DETECTED ({prev} -> {acct}): read {home / 'ROTATION.md'} section 4 before deriving anything.")
        scan = worktree_scan(repo)
        if scan:
            print(f"Worktrees of {repo.name} with uncommitted or unintegrated work:")
            print("\n".join(scan))
        files = sorted(base.glob("SESSION-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        if files:
            print(f"Session checkpoints for {repo.name} (newest first, pointer-only, machine-local):")
            for f in files:
                print(f"  {f}")
        return 0
    if event != "Stop":
        return 0
    before = set(json.loads(snap.read_text(encoding="utf-8"))) if snap.exists() else set()
    owned = sorted(dirty - before)
    branch = git(cwd, "symbolic-ref", "-q", "--short", "HEAD") or "(detached)"
    head = git(cwd, "rev-parse", "--short", "HEAD")
    subject = git(cwd, "log", "-1", "--format=%s")[:120]
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = base / f"SESSION-{sid}.md"
    lines = [f"# SESSION-{sid} checkpoint (pointer-only, written mechanically at every turn end)", "",
             f"- written: {ts}", f"- account: {acct or 'unknown'}", f"- repo: {repo}", f"- cwd: {cwd}",
             f"- branch: {branch} @ {head}", f"- last commit: {subject}",
             f"- files this session dirtied and has NOT committed ({len(owned)}):"]
    lines += [f"  - {p}" for p in owned] or ["  - none"]
    lines += ["", "Resume: read the repo entry file, then `git log -5 <branch>`; the list above is what a rotation would lose."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if owned:
        print(f"checkpoint: {len(owned)} uncommitted file(s) from this session recorded at {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
