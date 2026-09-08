# ROTATION - account rotation runbook (pointer-only; tracked in every project, seeded to ~/.claude)

**Trigger:** "we rotated", "rotate accounts", "usage is almost gone", or simply "resume our work"
after a login change: the session-checkpoint hook detects the account change itself and prints
the pointer to section 4 before the first prompt. Read this file, then follow section 3 or 4.

**Rule that makes everything below work: the disk is the unit of work, the session is not.**
A session is a view onto worktrees, branches, transcripts and entry files that all belong to the
OS user. Nothing that matters is allowed to live only in a chat context. This file carries
procedures and pointers only; every value is derived by the command next to it.

## 1. What survives a rotation on this machine, and what dies

Survives (owned by the OS user, not the account):
- every worktree under each project's `.claude/worktrees/` and its branch: `git worktree list`
- every transcript under `~/.claude/projects/<slug>/*.jsonl`
- machine-local memory under `~/.claude/projects/<slug>/memory/` and this file
- the session checkpoints under `~/.claude/session-checkpoints/<repo>/` (section 2)
- scheduled tasks, dead-man floors, ingress state under `.claude-state/`: they run with no session
- the project entry files and the tracked copy of this runbook, which survive a clone too

Dies with the account:
- the app's session list and every chip
- any Monitor, artifact watch or loop armed inside a chat session
- any edit that exists only in a session's working memory and not in its worktree
- the standalone Claude binary's login, so both Claude dead-man floors in Conjugal go dark until
  it re-authenticates (section 4, step 1)

## 2. The standing mechanism: a mechanical checkpoint at every turn end

Measured on DropBox Vault (its `tools/continuity-beat.py` docstring): every rotation there was
unplanned, and at the moment a handoff was needed the model was the thing that had stopped. So a
handoff the model must write is absent exactly when it is needed. The fix is a hook, installed
once per project by pasting the install prompt (section 7) into a Haiku or Sonnet session in that
project's canonical checkout. The prompt is self-seeding: on a new machine it also writes this
runbook to `~/.claude/` and the trigger to `~/.claude/CLAUDE.md`.

- **Stop hook**: writes `~/.claude/session-checkpoints/<repo>/SESSION-<id>.md` at every turn end:
  session id, worktree path, branch, HEAD, last commit subject, account id, and the files this
  session dirtied and has not committed. Pointer-only, no prose. Never blocks, always exits 0.
- **SessionStart hook**: prints, before the first prompt, (1) ACCOUNT ROTATION DETECTED if the
  signed-in account differs from the last one seen, with a pointer to section 4; (2) every
  worktree of the repo that carries content dirt or commits ahead of the main branch; (3) the
  newest five session checkpoints. It re-seeds `~/.claude/ROTATION.md` from the tracked copy when
  the user-level file is missing (fresh machine).

Verify it is installed in a project: `grep -n session-checkpoint .claude/settings.json`

## 3. Before a planned rotation

1. Count running sessions in the sidebar. Above eight, the box saturates and the factory watch
   tasks hang; finish or close the idle watchers first.
2. In each session with real work (not scheduled routines such as warden ticks), say:
   "Commit your work in progress to your branch with `git add` on exact paths and a plain commit,
   even as a WIP commit. Then append a pointer-only handoff to this project's handoff surface:
   next step, the derivation commands, no status narrative." No session can do this for another
   one (hub-only channel), so it is the one manual step. The hook already records the session id,
   worktree, branch and uncommitted files; this step adds the next step.
3. Any watcher you still need goes into a scheduled task under the OS user, never a chat window.
4. Land findings into the tree before rotating; chat is not a bus.
5. Rotate. The reset time the app shows has been wrong before; the probe in step 4.1 is the proof.

## 4. After a rotation

1. Re-authenticate the standalone binary and prove capacity per project, before deriving anything:
   - Conjugal: `python coordination/tools/check-cli-auth.py --floor-models --allow-live-probe`
   - DropBox Vault: `python tools/check-cli-auth.py`
   Paste a non-zero result verbatim into chat; a limit is never fixed by re-authing.
2. In each project, open one session in the canonical checkout and say "resume our work". The
   SessionStart hook prints the rotation notice, the dirty worktrees and the checkpoints first;
   the entry file does the rest and needs no old session.
3. If the hook did not print a worktree scan, derive it by content and not by `git status`
   (autocrlf reports phantom dirt):
   ```bash
   git worktree list
   for w in .claude/worktrees/*/; do echo "$w"; git -C "$w" --no-optional-locks diff --stat | tail -1; git -C "$w" --no-optional-locks status --short | grep -c '^??'; done
   ```
4. For any worktree with real dirt or a branch ahead of the main branch, open a session in that
   directory and have it finish, commit, or hand off. Integrate by fast-forward or cherry-pick of
   the exact commit from the canonical checkout, never by rebase of shared history.
5. Re-arm the watchers you moved to scheduled tasks; confirm from their own logs, not the
   scheduler's State field.
6. Memory is expected to be empty on a brand-new account and is not a problem to fix; the entry
   files are designed to work with no memory at all.

## 5. Reduce how often this hurts

- Six to eight concurrent sessions, not twenty. Idle sessions cost nothing but are the ones lost.
- Prefer chips that finish in one sitting over sessions that idle and watch.
- Every long-lived watcher is a scheduled task under the OS user.
- Every project entry file stays pointer-only; a rotation then costs one sentence per project.
- The binding limit is the all-models weekly cap, so switching models on the same account buys
  nothing once it is near 100%.

## 6. Not yet verified, test before relying on it

- `claude --resume <session-id>` from a terminal under a different org. Transcripts are local, so
  it is plausible; try one throwaway session first.
- Whether the app's session list can be re-pointed at the local transcripts at all.

## 7. Project table

| Project | Trigger phrase | Entry file | Tracked copy of this runbook | Checkpoint dir slug |
|---|---|---|---|---|
| `C:\code\Conjugal` | "resume our work" | `coordination/RESUME.md` | `docs/ops/ROTATION.md` | `Conjugal` |
| `C:\code\DropBox Vault` (Cloudvore) | "resume our work" | `review/RESUME.md` | `knowledge/ROTATION.md` | `DropBox-Vault` |
| `C:\code\magic-lantern_dannephoto` | "resume our work" | `roadmap/HANDOFF.md` | `roadmap/handoff/ROTATION.md` | `magic-lantern_dannephoto` |
| any other repo | "resume our work" | its entry file | `docs/ROTATION.md` | repo folder name |
| this laptop | "resume our laptop troubleshooting" | `~/.claude/MACHINE.md` | none | none |
| `C:\code\DngAutoProcessor - Claude` | none: frozen archive, read-only | none | none | none |

The install prompt is `~/.claude/ROTATION-install-prompt.md` on a seeded machine and, on a fresh
one, the same file beside the tracked copy of this runbook (`ROTATION-install-prompt.md` in the
same directory).

Sources synthesised 2026-09-05: the Conjugal maintenance session (mechanical checkpoint, CRLF
and pinned-literal traps), a Cloudvore session's rotation strategy (what survives and dies,
post-rotation sequence, concurrency ceiling, watchers as scheduled tasks), and DropBox Vault's
own measured continuity failure.
