# Agent-Bridge Code Audit — Change Request

**Status:** v2 — Amended after stranger-reviewer scoring pass
**Authors:** Claude (synthesis of 7 parallel cold auditors)
**Date:** 2026-05-01
**Scope:** `tools/agent-bridge/` — all Python source, PowerShell wake scripts, MCP server surface
**Auditors:** Correctness/Concurrency · Security · Error Handling · Test Coverage · API/MCP Design · Windows Platform · Data Model/Schema

---

## Executive Summary

Seven independent cold auditors reviewed the agent-bridge codebase.
**No dimension scored above 5.5/10 against production-hardening criteria.**
The primary failure modes are:

1. **No cross-process synchronization** — watcher and MCP server both write shared JSONL
   state files without any filesystem-level lock. Data loss and corruption are reachable
   under normal concurrent operation.
2. **No authentication on any MCP tool** — any local process can impersonate any agent,
   inject audit records, and read all bridge traffic.
3. **Unguarded JSON parses in the hot path** — a single corrupt byte in watcher-state.json
   or seen-ids.json permanently deadlocks the watcher with no recovery path.
4. **Zero functional tests for several critical subsystems** — `wait_inbox`, rate limiter,
   the watcher daemon loop itself, and concurrent JSONL write safety are completely untested.
5. **Windows-specific hazards throughout** — SIGTERM never fires on Windows, `os.replace()`
   raises PermissionError under concurrent readers, daemon spawn is missing `CREATE_NO_WINDOW`.

All critical items are fixable without architectural changes. This document lists every
finding, assigns priority, and proposes a concrete fix for each.

---

## Priority Classification

| Priority | Definition |
|---|---|
| **P0 — Critical** | Data loss, silent corruption, unrecoverable deadlock, or security breach under normal operation |
| **P1 — High** | Reliability regression under realistic workload; wrong behavior visible to users |
| **P2 — Medium** | Operational gap: missing observability, configuration hazard, or non-fatal misbehavior |
| **P3 — Low** | Polish, test coverage gap, or latent inconsistency unlikely to manifest in current usage |

---

## Dimension 1 — Correctness & Concurrency

**Auditor score (pre-fix): 4/10**

### CR-C1 [P0] No cross-process lock on shared JSONL state files

**Files:** `watcher.py`, `server.py`
**Problem:** `watcher.py` and the MCP server process both perform read-modify-write on
`watcher-state.json` and the inbox JSONL files without holding any file-level lock.
On Windows, `open(path, 'a')` and `open(path, 'r')` from two processes interleave.
Partial writes produce truncated JSON lines; readers crash or silently drop rows.

**Fix:** Wrap every cross-process read-modify-write with a `filelock.FileLock` (or
`msvcrt.locking`). Add `filelock` to `requirements.txt`. Guard at minimum:
- `watcher-state.json` read + write pair in `_save_watcher_state`
- All `inbox-*.jsonl` append and compaction paths
- `wake-failure-windows.json` read + write

**Acceptance:** Two simultaneous writers (watcher + server) run for 60 s; zero
corrupt JSON lines in any output file.

---

### CR-C2 [P0] `_save_watcher_state` read-modify-write is not atomic

**File:** `watcher.py`
**Problem:** The pattern is: (1) read full state from disk, (2) modify in memory,
(3) write back. Between steps 1 and 3, the MCP server may have written a different
field. The last writer wins, silently discarding the other's update.

**Fix:** Use the file lock from CR-C1; additionally write to a `.tmp` file and use
`shutil.move` (not `os.replace`) for the final rename — `shutil.move` on Windows
falls back to copy+delete when rename fails across volumes.

---

### CR-C3 [P0] `seen_ids` in-memory set with deferred flush creates re-delivery window

**File:** `watcher.py`
**Problem:** `seen_ids` is loaded from disk once at startup and flushed periodically.
If the watcher restarts between a message being marked seen in memory and the flush,
the message is redelivered. The flush interval is configurable up to 60 s.

**Fix:** Flush `seen_ids` to disk immediately after marking a message seen, inside
the same file lock that protects the inbox read. Do not rely on periodic flush for
correctness; periodic flush may remain as a compaction-style optimization only.

---

### CR-C4 [P0] `_append_control_message` performs non-atomic file replace on Windows

**File:** `server.py`
**Problem:** The append path reads the file, appends a row in memory, then calls
`open(path, 'w')` to write the whole file back. On Windows, `open(path, 'w')` truncates
the file before writing. A crash or process kill between truncation and write leaves an
empty file; all prior control messages are lost.

**Fix:** Append using `open(path, 'a')` directly (one row, no read-back). If the
full-rewrite pattern is required (e.g. for compaction), write to `.tmp` then
`shutil.move`.

---

### CR-C5 [P1] Rate-limit history saves the stale pending-wake list

**File:** `watcher.py`
**Problem:** The rate-limit history snapshot is taken before the current message is
removed from the pending list. The saved list includes the message being processed,
making the rate-limit window appear one message shorter on the next load.

**Fix:** Snapshot the pending list after removing the current message, or exclude
the message being processed from the count.

---

## Dimension 2 — Security

**Auditor score (pre-fix): 4.5/10**

### CR-S1 [P0] No authentication on any MCP tool

**File:** `server.py`
**Problem:** Every MCP tool (`send_to_peer`, `mark_read`, `clear_inbox`, etc.) accepts
calls from any local process with no credential check. On a multi-user machine or in
a containerized environment, any process can read all bridge messages, inject messages
as any agent, and alter session state.

**Fix:** Generate a shared secret at bridge startup (written to
`~/.agent-bridge/mcp_secret.json`, chmod 600). Require callers to pass the secret
as an `_auth` header or parameter. For local single-user deployments, the secret can
be a random UUID rotated per session; the Claude/Codex MCP clients read it from the
file at startup.

**Acceptance:** A call without the correct secret returns 401; a call with the correct
secret succeeds normally.

---

### CR-S2 [P0] `project_identity` exposes arbitrary filesystem paths via `cwd`

**File:** `server.py`
**Problem:** `project_identity(cwd=<any_path>)` resolves the project root by walking
upward from the provided `cwd`. A caller can provide any path (e.g. `cwd="C:\Users\..."`)
to enumerate the filesystem and learn the directory structure.

**Fix:** Restrict `cwd` to paths within allowed project roots. Maintain a whitelist of
registered project roots at startup; reject `cwd` values that resolve outside the list.

---

### CR-S3 [P1] `mark_seen` `via` field is written verbatim to the audit log

**File:** `server.py`
**Problem:** The `via` parameter to `mark_seen` is stored as-is in the JSONL audit
record. A caller can inject newlines, control characters, or a crafted JSON fragment
that corrupts the log or misleads log readers.

**Fix:** Sanitize `via` before writing: strip newlines and non-printable characters;
truncate to a safe maximum length (e.g. 256 chars); validate it matches the allowed
verb enum if `via` is intended to be a fixed-vocabulary field.

---

### CR-S4 [P1] `from_agent` in `send_to_peer` can be impersonated

**File:** `server.py`
**Problem:** The `from_agent` parameter defaults to the calling agent's registered
identity, but the caller can override it to claim to be any agent. A rogue plugin or
injected MCP call could forge messages from `claude` or `codex`.

**Fix:** Derive `from_agent` server-side from the authenticated session (after CR-S1
lands). Do not accept `from_agent` as a caller-supplied parameter once auth is in place.
Until CR-S1 lands, log a warning when `from_agent` does not match the caller's registered
agent identity.

---

### CR-S5 [P1] `send_control_message` `control_type` not validated against allowlist

**File:** `server.py`
**Problem:** Any string is accepted as `control_type`. A caller can craft types like
`SESSION_UPDATE: superseded` or `HANDSHAKE` with forged payloads.

**Fix:** Validate `control_type` against a server-side enum of known types. Reject
unknown types with a clear error.

---

## Dimension 3 — Error Handling

**Auditor score (pre-fix): 4.5/10**

### CR-E1 [P0] `read_json` unhandled `JSONDecodeError` permanently deadlocks watcher

**File:** `watcher.py` (or `core/paths.py`)
**Problem:** `read_json(path)` propagates `json.JSONDecodeError` to callers. The
watcher's main loop calls `read_json` for `watcher-state.json` and `session.json` on
every poll cycle with no try/except. A single corrupt byte (write interrupted by power
loss, partial flush, OS crash) puts the watcher into an infinite exception loop.
The watcher never marks messages seen, never fires wake commands, and never logs
a user-visible error. The bridge silently dies.

**Fix:**
```python
def read_json_safe(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        log.warning("Corrupt or missing JSON at %s; using default", path)
        return default
```
All watcher hot-path reads must use `read_json_safe`. On corrupt watcher-state.json,
emit a user-visible toast/log warning and reinitialize to a safe empty state rather
than crashing.

---

### CR-E2 [P0] `load_seen` unhandled `JSONDecodeError` crashes watcher at startup

**File:** `watcher.py`
**Problem:** `load_seen` reads `seen-ids.json` at startup without exception handling.
A corrupt file causes the watcher to fail to start with a Python traceback, no toast,
no log entry, and no recovery.

**Fix:** Wrap `load_seen` in try/except as in CR-E1. On failure, log a warning and
start with an empty seen set (accepts the risk of re-delivering messages already seen
before the corruption — this is acceptable and recoverable by the user).

---

### CR-E3 [P0] `process_session_once` has no per-session try/except

**File:** `watcher.py`
**Problem:** An unhandled exception from any session's processing (e.g. malformed
inbox row, missing field, OS error mid-read) propagates to the top-level loop. Depending
on the loop structure, this can stop all session processing or cause the daemon to exit.

**Fix:** Wrap `process_session_once` in a per-session try/except that:
1. Logs the exception with full traceback.
2. Increments a per-session error counter.
3. After 5 consecutive errors for the same session, emits a user-visible warning and
   backs off that session for 60 s (does not skip all sessions).

---

### CR-E4 [P1] `_load_config` at startup is unguarded against malformed config

**File:** `watcher.py`
**Problem:** If `watcher-config.json` contains invalid JSON or an unknown key (which the
validation rejects), the watcher fails to start with no recovery path. Users have no way
to know which key is invalid.

**Fix:**
1. On `JSONDecodeError`: emit a clear error message naming the file and line/column,
   then fall back to defaults (do not exit).
2. On unknown key: log a warning, skip the key, continue with defaults for that key.
3. On value out of range: log a warning, clamp to the allowed range, continue.

---

### CR-E5 [P1] Hot-reload `except Exception: pass` silently swallows config errors

**File:** `watcher.py`
**Problem:** The hot-reload path catches all exceptions and passes silently. A bad
config written at runtime leaves the watcher running with stale config and no indication
to the user that the reload failed.

**Fix:** Replace `except Exception: pass` with:
```python
except Exception as e:
    log.warning("Hot-reload failed for %s: %s", path, e)
    # optionally: emit toast notification
```

---

## Dimension 4 — Test Coverage

**Auditor score (pre-fix): 5.5/10 (~65% coverage, 204 tests)**

### CR-T1 [P0] `wait_inbox` has zero functional tests

**File:** `test_agent_bridge.py`
**Problem:** `wait_inbox` is a core long-poll operation used by Codex as its primary
inbox check. No test exercises: timeout expiry, message arrival before timeout,
cancellation, or behavior when the monitored file is being written concurrently.

**Fix:** Add a test suite for `wait_inbox`:
- `test_wait_inbox_returns_message_before_timeout` — write a message file after 100ms delay
- `test_wait_inbox_returns_empty_on_timeout` — no message written; assert returns after timeout
- `test_wait_inbox_concurrent_writer` — write while wait_inbox is blocking; assert no data loss
- `test_wait_inbox_mark_read_false` — verify message is not marked read when `mark_read=False`

---

### CR-T2 [P0] Rate limiter has no unit tests

**File:** `test_agent_bridge.py`
**Problem:** The wake rate limiter (`WAKE_PREFIRE_LIMIT`, `WAKE_RATE_WINDOW_S`) is entirely
untested. Bugs in the limiter cause either unlimited wake storms or incorrect suppression
of legitimate wakes.

**Fix:** Add:
- `test_rate_limiter_allows_under_limit` — N-1 wakes in window; (N)th succeeds
- `test_rate_limiter_blocks_at_limit` — Nth wake in window is suppressed with audit
- `test_rate_limiter_resets_after_window` — after window elapses, wakes resume
- `test_rate_limiter_does_not_count_preflight_deferrals`

---

### CR-T3 [P0] Watcher daemon loop is completely untested

**File:** `test_agent_bridge.py`
**Problem:** The main `run()` loop in `watcher.py` — the process that drives all message
delivery — has no integration tests. Bugs in the loop structure (session iteration order,
pause-state handling, poll sleep) are invisible to CI.

**Fix:** Add a watcher integration test that:
1. Spawns the watcher as a subprocess against a temp directory.
2. Writes a message to `inbox-codex.jsonl`.
3. Asserts the wake command is fired within `2 × poll_interval` seconds.
4. Writes a second message while bridge is paused; asserts no wake fires.
5. Resumes; asserts the paused message fires exactly once.

---

### CR-T4 [P0] Concurrent JSONL write safety is completely untested

**File:** `test_agent_bridge.py`
**Problem:** The correctness issues in CR-C1 through CR-C4 are untested because there are
no tests that run the watcher and MCP server concurrently against shared state files.

**Fix:** Add a concurrency test:
- 2 writer threads append to `inbox-claude.jsonl` simultaneously for 5 s.
- 1 reader thread reads all rows every 100 ms.
- Assert: zero duplicate message IDs, zero truncated JSON lines, final row count
  equals sum of writes from both threads.

---

### CR-T5 [P1] `compact_inbox` is untested

**File:** `test_agent_bridge.py`
**Problem:** Compaction modifies all inbox files in-place and is triggered on a time
interval. Bugs silently delete messages. No test covers: normal compaction, compaction
with concurrent writer, compaction of an empty file, or compaction of a file with
only-read vs mixed read/unread rows.

**Fix:** Add compaction test suite covering each of the above cases.

---

## Dimension 5 — API/MCP Design

**Auditor score (pre-fix): 4.5/10**

### CR-A1 [P0] `send_to_peer` `session_id` footgun remains in the live API

**File:** `server.py`
**Problem:** Passing the caller's own session GUID as `session_id` routes the message
to a bucket keyed by that GUID — which nobody polls. The message is silently orphaned.
This has bitten Claude-side at least once (documented in `send_to_peer_session_id_footgun`
memory). The parameter name `session_id` is ambiguous: it looks like "my session" but
is interpreted as "the target session."

**Fix:**
1. Rename `session_id` to `target_session_id` to clarify intent.
2. Add a server-side guard: if `target_session_id` equals the caller's own active session
   GUID, return a descriptive error (`"Cannot send to own session"`) rather than silently
   orphaning the message.
3. Document the `None`/default behavior: "leave as `None` to auto-route to the peer's
   active session."

---

### CR-A2 [P1] `check_inbox` `record_seen=True` / `mark_read=False` asymmetry creates phantom state

**File:** `server.py`
**Problem:** A caller can call `check_inbox(record_seen=True, mark_read=False)`. The
message is recorded in the seen set (will not be re-delivered by default polling) but
is NOT marked read (appears unread to tools that filter on `read_at`). This creates
a message that exists in an unspecified in-between state. There is no documented
contract for this combination, and no test covers it.

**Fix:** Either:
- Make `mark_read=True` the only option when `record_seen=True` (enforce the invariant
  at the API layer), OR
- Document the contract explicitly and add a test that proves the intended behavior
  for each of the four `(record_seen, mark_read)` combinations.

---

### CR-A3 [P1] `clear_inbox` with default `session_id=None` immediately rejects with opaque error

**File:** `server.py`
**Problem:** `clear_inbox(agent="claude")` (no `session_id`) resolves `session_id=None`
and immediately returns an error because `None` is not a valid session identifier. The
error message does not tell the caller what to pass.

**Fix:** When `session_id=None`, auto-resolve to the caller's active session (same
behavior as `send_to_peer`). If no active session exists, return a clear error:
`"No active claude session found; pass session_id explicitly."`

---

### CR-A4 [P1] `send_to_peer` missing `target_session_id` gives opaque routing error

**File:** `server.py`
**Problem:** If the peer has no active session, `send_to_peer` returns a generic routing
failure. The caller cannot distinguish between "peer has no active session" and "routing
table is corrupt."

**Fix:** Before routing, check whether the target agent has an active session. If not,
return a structured error:
```json
{"error": "no_active_session", "agent": "codex", "detail": "No active codex session registered; start the peer agent first."}
```

---

### CR-A5 [P2] No idempotency guarantee on `mark_read` / `mark_seen`

**File:** `server.py`
**Problem:** Calling `mark_read(message_id)` twice should be safe (idempotent). Currently,
the second call may append a duplicate `read_at` timestamp to the inbox row or return
an error depending on implementation. This causes non-deterministic behavior in retry
scenarios.

**Fix:** Make `mark_read` and `mark_seen` idempotent: if the message is already marked,
return success without mutating state. Add a test that calls each twice and asserts
the output is identical to calling once.

---

## Dimension 6 — Windows Platform

**Auditor score (pre-fix): 5/10**

### CR-W1 [P0] SIGTERM never fires on Windows — shutdown handler is dead code

**File:** `watcher.py`
**Problem:** `signal.signal(signal.SIGTERM, handler)` is a no-op on Windows because
Windows processes are terminated via `TerminateProcess()`, which does not deliver SIGTERM.
The graceful shutdown handler (flush seen-ids, write final state) never runs. The result
is that every watcher shutdown on Windows is effectively an unclean exit, producing the
exact data loss / re-delivery risk described in CR-C3.

**Fix:** On Windows, register a `win32api.SetConsoleCtrlHandler` to handle
`CTRL_C_EVENT` and `CTRL_BREAK_EVENT`. For programmatic shutdown from the MCP server,
use `CTRL_BREAK_EVENT` (which Python's `subprocess` can send with `os.kill(pid, signal.CTRL_BREAK_EVENT)`).
Add a `atexit.register(flush_state)` as a second-line catch for abnormal exits.

---

### CR-W2 [P0] `os.replace()` raises `PermissionError` on Windows when file is open

**File:** `watcher.py`, `server.py`
**Problem:** `os.replace(tmp, dest)` on Windows raises `PermissionError` if any process
has `dest` open (even for reading). The watcher and MCP server may both have state files
open at the same time. This makes the atomic-rename pattern unreliable.

**Fix:** Replace `os.replace(tmp, dest)` with:
```python
def atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    for attempt in range(5):
        try:
            shutil.move(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    raise RuntimeError(f"Could not atomically write {path} after 5 attempts")
```
This wraps the rename in a short retry loop, which is the standard Windows pattern.

---

### CR-W3 [P1] Daemon spawn is missing `CREATE_NO_WINDOW` flag

**File:** `watcher.py` (daemon relaunch path) or `server_wrapper.py`
**Problem:** When the watcher spawns itself as a daemon (or when `server_wrapper.py`
launches the MCP server), the subprocess is created without `creationflags=subprocess.CREATE_NO_WINDOW`.
This causes a console window to flash briefly on screen for every daemon restart, and
leaves a visible window if the spawn is long-lived.

**Fix:**
```python
kwargs = {}
if sys.platform == "win32":
    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
proc = subprocess.Popen(cmd, **kwargs)
```

---

### CR-W4 [P1] JSONL append is not atomic on Windows

**File:** `watcher.py`, `server.py`
**Problem:** `open(path, 'a').write(line + '\n')` on Windows does not guarantee that
the line is written atomically. The OS can preempt between the write and the flush,
leaving a partial line that the reader sees as a truncated (invalid) JSON object.

**Fix:** Write the full line, then call `f.flush()` and `os.fsync(f.fileno())` inside
the file lock. For high-throughput paths, batch lines and fsync once per batch.

---

### CR-W5 [P2] Path handling uses string concatenation instead of `pathlib`

**File:** Multiple (watcher.py, server.py, core/paths.py)
**Problem:** Several paths are constructed with `os.path.join` or string concatenation
without normalization. On Windows, paths with mixed slashes (`C:\Users/foo\bar`) are
accepted by the OS but break string comparisons and may mislead log readers.

**Fix:** Use `pathlib.Path` throughout for path construction and comparison. Convert to
string only at the point of OS calls that require it.

---

## Dimension 7 — Data Model & Schema

**Auditor score (pre-fix): 5.5/10**

### CR-D1 [P0] `write_inbox_rows` data loss when `replace_session_ids=None`

**File:** `server.py` or `core/`
**Problem:** When `replace_session_ids=None`, `write_inbox_rows` writes an empty list
rather than preserving the existing rows that did not match the filter. The effect is
that calling `write_inbox_rows` with a None filter deletes all inbox rows.

**Fix:** Guard the replace path:
```python
if replace_session_ids is None:
    # Do not replace any rows; this is a no-op or a clear-all — caller must be explicit
    raise ValueError("replace_session_ids=None is ambiguous; pass [] to clear all or a list of session IDs to replace")
```
Alternatively, treat `None` as "do not replace any session" (pure append behavior)
and add a separate `clear_inbox_for_sessions(session_ids)` function for the clear-all case.

---

### CR-D2 [P0] Schema v1/v2 coexistence without reader-side upgrade fence

**File:** `watcher.py`, `server.py`
**Problem:** Both schema_version `v1` and `v2` records can exist in the same JSONL file.
Readers that expect v2 fields will encounter v1 rows and raise `KeyError` or return
None for required fields. There is no migration guard that prevents v1 rows from
being processed by v2 readers.

**Fix:** On file open, read the first row to detect schema version. If v1 rows are
found in a file expected to be v2, either:
1. Run an in-place migration (rewrite the file with all rows upgraded to v2), OR
2. Skip v1 rows with a warning and audit event.
Add a schema_version enum and assert on read.

---

### CR-D3 [P1] `compact.py` uses `tmp.replace()` which is not Windows-safe

**File:** `compact.py`
**Problem:** Same class of bug as CR-W2. The compaction path uses `Path.replace()` (Python
pathlib) for the final rename; on Windows this raises `PermissionError` if the destination
is open.

**Fix:** Use the retry-wrapped `atomic_write` from CR-W2, or use `shutil.move()` which
does copy+delete on Windows when rename is blocked.

---

### CR-D4 [P1] Rate-limit timestamps use `time.time()` (float) instead of ISO UTC strings

**File:** `watcher.py`
**Problem:** Wake failure window entries store timestamps as `time.time()` floats. Floats
are not human-readable in logs, cannot be sorted correctly as strings, and produce
timezone-ambiguous values that differ between machines.

**Fix:** Store timestamps as ISO 8601 UTC strings (`datetime.utcnow().isoformat() + "Z"`).
All comparisons use `datetime.fromisoformat(ts)`.

---

### CR-D5 [P2] No schema validator at JSONL read boundary

**File:** `watcher.py`, `server.py`
**Problem:** JSONL rows are consumed as raw dicts. Any unexpected shape (missing field,
wrong type, extra key from a future schema version) produces a silent `KeyError` or
incorrect behavior that is not surfaced until runtime.

**Fix:** Define a minimal Pydantic or dataclass schema for each JSONL row type and
validate at read time. Rows that fail validation are logged and skipped, not silently
processed with wrong data.

---

## Summary Table

| ID | Dim | Priority | Title |
|---|---|---|---|
| CR-C1 | Correctness | P0 | No cross-process lock on shared JSONL state files |
| CR-C2 | Correctness | P0 | `_save_watcher_state` R/M/W is not atomic |
| CR-C3 | Correctness | P0 | `seen_ids` deferred flush creates re-delivery window |
| CR-C4 | Correctness | P0 | `_append_control_message` non-atomic on Windows |
| CR-C5 | Correctness | P1 | Rate-limit history saves stale pending list |
| CR-S1 | Security | P0 | No auth on any MCP tool |
| CR-S2 | Security | P0 | `project_identity cwd` filesystem enumeration |
| CR-S3 | Security | P1 | `mark_seen via` audit log injection |
| CR-S4 | Security | P1 | `from_agent` impersonation possible |
| CR-S5 | Security | P1 | `send_control_message` control_type injection |
| CR-E1 | Error Handling | P0 | `read_json` unhandled JSONDecodeError deadlocks watcher |
| CR-E2 | Error Handling | P0 | `load_seen` crashes watcher at startup |
| CR-E3 | Error Handling | P0 | `process_session_once` no per-session try/except |
| CR-E4 | Error Handling | P1 | `_load_config` unguarded against malformed config |
| CR-E5 | Error Handling | P1 | Hot-reload swallows config errors silently |
| CR-T1 | Tests | P0 | `wait_inbox` zero functional tests |
| CR-T2 | Tests | P0 | Rate limiter entirely untested |
| CR-T3 | Tests | P0 | Watcher daemon loop completely untested |
| CR-T4 | Tests | P0 | Concurrent JSONL write safety untested |
| CR-T5 | Tests | P1 | `compact_inbox` untested |
| CR-A1 | API Design | P0 | `send_to_peer` session_id footgun |
| CR-A2 | API Design | P1 | `check_inbox` seen/read asymmetry |
| CR-A3 | API Design | P1 | `clear_inbox` None session opaque error |
| CR-A4 | API Design | P1 | `send_to_peer` missing peer session opaque error |
| CR-A5 | API Design | P2 | No idempotency guarantee on `mark_read`/`mark_seen` |
| CR-W1 | Windows | P0 | SIGTERM never fires — shutdown handler is dead code |
| CR-W2 | Windows | P0 | `os.replace()` PermissionError under concurrent readers |
| CR-W3 | Windows | P1 | Daemon spawn missing `CREATE_NO_WINDOW` |
| CR-W4 | Windows | P1 | JSONL append not atomic on Windows |
| CR-W5 | Windows | P2 | Path construction uses string concatenation |
| CR-D1 | Data Model | P0 | `write_inbox_rows` data loss with `replace_session_ids=None` |
| CR-D2 | Data Model | P0 | Schema v1/v2 coexistence without upgrade fence |
| CR-D3 | Data Model | P1 | `compact.py` uses non-Windows-safe rename |
| CR-D4 | Data Model | P1 | Rate-limit timestamps use float not ISO UTC |
| CR-D5 | Data Model | P2 | No schema validator at JSONL read boundary |

**P0 count: 17 · P1 count: 13 · P2 count: 5 · P3 count: 0**

---

## Projected Post-Fix Scores

If all P0 and P1 items are resolved:

| Dimension | Pre-fix | Projected post-fix |
|---|---|---|
| Correctness & Concurrency | 4/10 | 9/10 |
| Security | 4.5/10 | 8.5/10 |
| Error Handling | 4.5/10 | 9/10 |
| Test Coverage | 5.5/10 | 9/10 |
| API/MCP Design | 4.5/10 | 9/10 |
| Windows Platform | 5/10 | 9/10 |
| Data Model & Schema | 5.5/10 | 9/10 |

P2 items bring each dimension to 9.5–10/10 when resolved. The one
dimension that may not reach 10/10 with P1 alone is **Security**:
CR-S1 (no auth) requires a non-trivial protocol change and the post-P1
score of 8.5/10 reflects residual risk from the MCP call-site auth gap
until CR-S1 ships.

---

## Implementation Order Recommendation

**Wave 1 — P0 stability fixes (unblock safe operation):**
CR-C1 → CR-C2 → CR-C3 → CR-E1 → CR-E2 → CR-W1 → CR-W2 → CR-D1

**Wave 2 — P0 test and API (unblock CI and correct routing):**
CR-T1 → CR-T3 → CR-T4 → CR-A1 → CR-D2

**Wave 3 — P1 hardening:**
All remaining P1 items (CR-C5, CR-S3–S5, CR-E3–E5, CR-T2 CR-T5, CR-A2–A4, CR-W3–W4, CR-D3–D4)

**Wave 4 — P0/P1 security (non-trivial protocol change):**
CR-S1 → CR-S2 → CR-S4 (depend on auth infrastructure from CR-S1)

**Wave 5 — P2 polish:**
CR-A5, CR-W5, CR-D5

---

## Reviewer Notes

This document was generated by synthesis of 7 independent cold auditors.
Codex is the implementing agent; Claude is the reviewing agent.
Findings are proposals, not mandates. Codex may propose alternative fixes
via SPEC_REVIEW_RESULT; changes to the fix strategy are acceptable as long
as the acceptance criterion for each item is met.

---

## Amendment — v2 Gaps Found by Stranger Reviewers

Three independent cold reviewers scored the v1 document.
No dimension reached 10/10. The items below correct or extend the v1 CR.
Implement alongside the corresponding parent CR item.

---

### CR-C6 [P0] Temp-file naming must be per-process unique

**Found by:** Stranger Reviewer 1
**Parent:** CR-C2 / CR-W2
**Problem:** Two processes writing the same `.tmp` path race to overwrite each other's
in-flight temp file before either rename completes. The last writer wins silently.

**Fix:** Suffix temp files with the process PID:
```python
tmp = path + f".{os.getpid()}.tmp"
```
Use this in every `atomic_write` call.

---

### CR-C7 [P0] `seen_ids` cross-process compare-and-swap race

**Found by:** Stranger Reviewer 1
**Parent:** CR-C3
**Problem:** Two `check_inbox` calls from separate processes can both load the same
on-disk `seen_ids`, both see the same message as unseen, both mark it in memory,
and both write back — delivering the message twice. Holding the file lock from CR-C1
prevents interleaved writes, but the in-memory load-then-write pattern must be
replaced with a read-inside-lock pattern that re-reads from disk after acquiring the
lock, not from the in-memory cache.

**Fix:** Inside the `mark_seen` file lock:
1. Re-read `seen_ids` from disk.
2. If the message is already present, return success immediately (idempotent).
3. If not present, add it and write back.

---

### CR-C8 [P1] JSONL file growth bounding unspecified

**Found by:** Stranger Reviewer 1
**Problem:** The CR flags compaction as untested (CR-T5) but does not specify what
triggers compaction, whether compaction can be starved indefinitely, or whether a
reader blocked on a growing JSONL during compaction causes a live lock.

**Fix:** Add to the compaction spec:
- Maximum JSONL size (e.g. 10 MB) triggers immediate compaction regardless of
  `compact_interval_hours`.
- Compaction acquires the file lock from CR-C1 before truncating.
- If a reader holds the lock for more than 10 s during compaction, emit a warning
  and skip compaction for this cycle (do not block indefinitely).

---

### CR-W1a [P0] `CTRL_BREAK_EVENT` and `CREATE_NO_WINDOW` are mutually exclusive

**Found by:** Stranger Reviewer 1
**Parent:** CR-W1 + CR-W3
**Problem:** `os.kill(pid, CTRL_BREAK_EVENT)` requires a shared console session. When
the watcher is spawned with `CREATE_NO_WINDOW` (the correct fix from CR-W3), it has
no console; `CTRL_BREAK_EVENT` is silently ignored. The two CR items conflict.

**Fix:** Drop `CTRL_BREAK_EVENT` as the shutdown signal. Use a named Windows event:
```python
# Writer (MCP server requesting shutdown):
import win32event
ev = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, "AgentBridgeWatcherStop")
win32event.SetEvent(ev)

# Reader (watcher main loop):
ev = win32event.CreateEvent(None, True, False, "AgentBridgeWatcherStop")
# Check ev in the poll loop with WaitForSingleObject(..., 0)
```
`atexit` must NOT be described as a catch for abnormal exits (it only runs on clean
Python exit, not on `TerminateProcess`). Remove that claim from CR-W1.

---

### CR-W2a [P1] CR-C2 `shutil.move` same-volume behavior clarification

**Found by:** Stranger Reviewer 1
**Parent:** CR-C2
**Problem:** `shutil.move` on Windows falls back to copy+delete only for cross-device
moves. On the same volume it calls `os.rename`, which has the same `PermissionError`
as `os.replace`. The CR-C2 fix as written is not more correct than the original on
a single-volume installation.

**Fix:** CR-C2 must explicitly use the retry-wrapped `atomic_write` from CR-W2 (which
retries on `PermissionError`) rather than citing `shutil.move` as the solution.
Update CR-C2 fix text to reference `atomic_write` with per-process `.pid.tmp` suffix
from CR-C6.

---

### CR-W4a [P2] `os.fsync` performance impact and AV scanner interference

**Found by:** Stranger Reviewer 1
**Parent:** CR-W4
**Problem:** `os.fsync()` (= `FlushFileBuffers`) on mechanical disks or network shares
causes severe throughput degradation. Enterprise AV scanners can hold exclusive read
locks for 50-300 ms on newly written files; the 5-attempt retry loop at 50 ms backoff
may exhaust before AV releases.

**Fix:**
- Batch appends within a single poll cycle; call `fsync` once per batch, not per line.
- Increase retry attempts to 10; set first-attempt delay to 100 ms with exponential
  backoff (100 ms, 200 ms, 400 ms…) for a total wait budget of ~3 s before failing.

---

### CR-S1a [P0] `chmod 600` is dead code on Windows

**Found by:** Stranger Reviewer 2
**Parent:** CR-S1
**Problem:** `os.chmod(path, 0o600)` on Windows only controls the read-only attribute.
It does NOT set NTFS ACLs and provides no actual access restriction. Implementing CR-S1
as written ships a false sense of security.

**Fix:** Use `win32security` to restrict the secret file to the current user's SID:
```python
import win32security, ntsecuritycon as con
sd = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
dacl = win32security.ACL()
user_sid = win32security.GetTokenInformation(
    win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY),
    win32security.TokenUser)[0]
dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_ALL_ACCESS, user_sid)
sd.SetSecurityDescriptorDacl(True, dacl, False)
win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION, sd)
```

---

### CR-S2a [P1] Symlink/junction bypass in `project_identity` whitelist

**Found by:** Stranger Reviewer 2
**Parent:** CR-S2
**Problem:** Symlinks and NTFS junctions inside an allowed root can redirect the path
walk to outside that root, bypassing the whitelist.

**Fix:** Call `Path(cwd).resolve()` before the whitelist check. `Path.resolve()` on
Python 3.6+ follows symlinks and junctions on Windows.

---

### CR-S6 [P1] Auth token credential-in-log risk (NEW)

**Found by:** Stranger Reviewer 2
**Problem:** If the MCP auth secret travels as a tool call parameter (the mechanism
proposed by CR-S1), it will be written to the audit JSONL log in plaintext alongside
every tool call. This is a credential-in-log vulnerability.

**Fix:** Pass the auth token as an out-of-band credential — e.g. an HTTP header on
the MCP transport layer, or a pre-shared environment variable read at startup —
rather than as a named parameter that is logged. If the MCP transport does not support
custom headers, the secret must be explicitly excluded from the audit log by the
server's tool-call recorder.

---

### CR-S7 [P2] Static session secret does not expire (NEW)

**Found by:** Stranger Reviewer 2
**Problem:** A process that reads the secret file once can replay it for the lifetime
of the session. For a local single-user bridge this is borderline acceptable, but it
is not documented as a conscious tradeoff.

**Fix:** Document the tradeoff explicitly in CR-S1. If replay resistance is required
later, rotate the secret on every bridge restart and require callers to re-read the file.
No implementation change required now; acknowledgement of the tradeoff is the fix.

---

### CR-E1a / CR-E2a [P1] Corrupt files must be preserved, not silently overwritten

**Found by:** Stranger Reviewer 2
**Parent:** CR-E1, CR-E2
**Problem:** Both fixes say "reinitialize to a safe empty state." Without renaming
the corrupt file, the next restart silently overwrites it, destroying forensic evidence.

**Fix:** Before reinitializing, rename the corrupt file:
```python
import shutil
corrupt_path = path.with_suffix(f".corrupt.{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}")
shutil.copy2(path, corrupt_path)
```
Then reinitialize. Log the corrupt file path in the warning message.

---

### CR-E3a [P1] No supervisor/watchdog for outer watcher loop crashes

**Found by:** Stranger Reviewer 2
**Parent:** CR-E3
**Problem:** CR-E3 adds per-session exception handling but does not address crashes
in the outer loop (scheduler, fsync path, named-event check). An unhandled exception
outside `process_session_once` exits the watcher entirely with no user notification
and no automatic restart.

**Fix:** Add a top-level watchdog:
1. The outer `run()` loop must be wrapped in a try/except that catches all exceptions,
   logs them with full traceback, emits a terminal toast ("Watcher crashed; restarting
   in Xs"), and sleeps before restarting the inner loop.
2. After 3 consecutive top-level crashes within 60 s, halt with a clear error message
   rather than spinning indefinitely.

---

### CR-A6 [P1] `check_inbox` `session_id` semantics inconsistency (NEW)

**Found by:** Stranger Reviewer 2
**Problem:** CR-A1 renames `send_to_peer`'s `session_id` to `target_session_id`.
But `check_inbox` uses `session_id` to mean "whose inbox to read" (the caller's own
session). After the rename, two tools use `session_id` with different semantics.

**Fix:** Define a vocabulary convention in the API surface:
- `session_id`: always refers to the caller's own session (inbox read, mark_read, etc.)
- `target_session_id`: always refers to the recipient (send_to_peer)
- Document this in a `BRIDGE_API_CONVENTIONS.md` stub or inline in server.py's module docstring.

---

### CR-A7 [P2] No `wake_status` query tool (NEW)

**Found by:** Stranger Reviewer 2
**Problem:** The rate limiter and circuit breaker are internal mechanisms with no
observable MCP API. There is no tool to query current wake suppression state. An
orchestrator cannot distinguish "wake suppressed by rate limiter" from "wake command
failed silently."

**Fix:** Add a `wake_status(session_id)` MCP tool that returns:
```json
{
  "session_id": "<guid>",
  "rate_limit_remaining": 3,
  "rate_limit_reset_at": "2026-05-01T04:15:00Z",
  "breaker_state": "closed",
  "last_wake_result": {"exit_code": 0, "at": "2026-05-01T04:14:00Z"}
}
```

---

### CR-T6 [P0] Auth test fixture plan required (NEW)

**Found by:** Stranger Reviewer 3
**Problem:** All existing tests bypass the auth layer. Once CR-S1 lands, tests that
call MCP tools directly without an auth token will silently test the wrong code path
(either failing all tests or exercising a bypass).

**Fix:** Add a test fixture:
```python
@pytest.fixture
def auth_client():
    secret = read_mcp_secret()
    return BridgeClient(auth_token=secret)
```
All tests that exercise MCP tools must use `auth_client`. Add a test that asserts
`BridgeClient(auth_token="wrong")` is rejected with a 401-equivalent error.

---

### CR-T7 [P0] Schema migration test required (NEW)

**Found by:** Stranger Reviewer 3
**Parent:** CR-D2
**Problem:** CR-D2 proposes migration logic but has no corresponding test that verifies
it is correct, idempotent, and safe under concurrent access.

**Fix:** Add:
- `test_v1_rows_migrate_to_v2_on_open` — file contains v1 rows; after open, all rows
  are v2.
- `test_migration_is_idempotent` — run migration twice; second run is a no-op.
- `test_v2_reader_with_concurrent_v1_writer` — migration completes correctly while a
  legacy writer is appending v1 rows.

---

### CR-T8 [P0] Crash-and-restart re-delivery test required (NEW)

**Found by:** Stranger Reviewer 3
**Parent:** CR-C3
**Problem:** The seen_ids flush fix (CR-C3) can regress silently without a crash-restart
test.

**Fix:** Add `test_seen_ids_survive_watcher_kill`:
1. Deliver a message to the watcher.
2. `SIGKILL` the watcher process after delivery but before periodic flush.
3. Restart the watcher against the same state directory.
4. Assert the message is NOT re-delivered (seen_ids persisted correctly).

---

### CR-T3a [P1] CR-T3 scope must include crash recovery and hot-reload

**Found by:** Stranger Reviewer 3
**Parent:** CR-T3
**Problem:** The proposed CR-T3 integration test covers normal delivery and pause/resume
but not: recovery from corrupt watcher-state.json (CR-E1), hot-reload of a config
change mid-run (CR-E5), or shutdown handler (CR-W1a).

**Fix:** Extend CR-T3 to include:
- `test_watcher_recovers_from_corrupt_state_json` — write invalid JSON to watcher-state.json;
  assert watcher logs a warning, reinitializes, and continues processing.
- `test_watcher_hot_reload_updates_config` — change poll_interval_seconds at runtime;
  assert new interval takes effect within 2 cycles without restart.
- `test_watcher_shutdown_handler_flushes_state` — send named-event stop signal;
  assert seen_ids and watcher-state.json are flushed before process exits.

---

### CR-T4a [P0] Concurrency test must be process-based, not thread-based

**Found by:** Stranger Reviewer 3
**Parent:** CR-T4
**Problem:** A threading test does not exercise Windows file-handle locking semantics.
Two Python threads in the same process share file descriptors; the test can pass even if
the `filelock` is never acquired.

**Fix:** Rewrite CR-T4 to use `subprocess.Popen` to spawn 2 concurrent writer processes
against a shared temp directory. Assert zero corrupt JSON lines and zero duplicate IDs
after both processes exit.

---

### CR-D6 [P1] Control messages have no envelope versioning (NEW)

**Found by:** Stranger Reviewer 3
**Problem:** CR-D2 adds schema_version to inbox JSONL rows but control messages
(HANDSHAKE, SESSION_UPDATE, HANDSHAKE_ACK, etc.) have no `schema_version` field.
Shape changes to control messages will cause silent misparsing by older readers.

**Fix:** Add `"schema_version": "v1"` to every control message envelope. On read,
assert `schema_version` is present and in the allowed set. Unknown versions must be
logged and skipped, not silently processed.

---

### CR-D1a [P1] CR-D1 fix requires call-site audit

**Found by:** Stranger Reviewer 3
**Parent:** CR-D1
**Problem:** The CR-D1 fix raises `ValueError` on `replace_session_ids=None`, but
existing callers that pass `None` will crash at runtime rather than at lint/review time.

**Fix:** Add a call-site audit to CR-D1's acceptance criteria: every call to
`write_inbox_rows` in the codebase must be reviewed and the intent of the `None` vs
`[]` vs explicit list must be documented before the fix is merged.

---

### CR-D2a [P0] Per-row version tagging required; first-row probe is unsound

**Found by:** Stranger Reviewer 3
**Parent:** CR-D2
**Problem:** "Read the first row to detect schema version" is wrong under concurrent
writers. A v2 row may be the first row while v1 rows are appended in the middle by
an older watcher instance.

**Fix:** Replace the file-level version probe with mandatory per-row validation:
- Every row must contain `"schema_version": "v1"` or `"v2"`.
- On read, validate schema_version on every row. Rows without a version field are
  treated as v1 (backward-compat default).
- Rows with an unknown future version are logged and skipped (forward-compat).

---

### CR-D4a [P1] Float-to-ISO timestamp migration path required

**Found by:** Stranger Reviewer 3
**Parent:** CR-D4
**Problem:** After the CR-D4 fix, a running watcher that loads an existing state file
with float-format timestamps will fail to parse them. There is no migration path.

**Fix:** On read, detect float vs ISO string and convert:
```python
def parse_ts(val):
    if isinstance(val, (int, float)):
        return datetime.utcfromtimestamp(val).isoformat() + "Z"
    return val  # already ISO
```
Apply `parse_ts` on every timestamp field at read time. Write all new timestamps
as ISO UTC. Remove the compat shim after one release cycle.

---

## Amended Summary Table (v2 additions)

| ID | Dim | Priority | Title |
|---|---|---|---|
| CR-C6 | Correctness | P0 | Temp-file naming must be per-process unique |
| CR-C7 | Correctness | P0 | seen_ids cross-process compare-and-swap race |
| CR-C8 | Correctness | P1 | JSONL file growth bounding unspecified |
| CR-W1a | Windows | P0 | CTRL_BREAK + CREATE_NO_WINDOW mutually exclusive |
| CR-W2a | Windows | P1 | CR-C2 shutil.move same-volume clarification |
| CR-W4a | Windows | P2 | fsync performance + AV scanner interference |
| CR-S1a | Security | P0 | chmod 600 dead code on Windows |
| CR-S2a | Security | P1 | Symlink/junction bypass in whitelist |
| CR-S6 | Security | P1 | Auth token credential-in-log risk |
| CR-S7 | Security | P2 | Static session secret replay acknowledgement |
| CR-E1a | Error Handling | P1 | Corrupt files must be preserved not overwritten |
| CR-E3a | Error Handling | P1 | No supervisor/watchdog for outer loop crashes |
| CR-A6 | API Design | P1 | check_inbox session_id semantics inconsistency |
| CR-A7 | API Design | P2 | No wake_status query tool |
| CR-T6 | Tests | P0 | Auth test fixture plan required |
| CR-T7 | Tests | P0 | Schema migration test required |
| CR-T8 | Tests | P0 | Crash-and-restart re-delivery test required |
| CR-T3a | Tests | P1 | CR-T3 scope must include crash recovery + hot-reload |
| CR-T4a | Tests | P0 | Concurrency test must be process-based |
| CR-D6 | Data Model | P1 | Control messages have no envelope versioning |
| CR-D1a | Data Model | P1 | CR-D1 fix requires call-site audit |
| CR-D2a | Data Model | P0 | Per-row version tagging required; first-row probe unsound |
| CR-D4a | Data Model | P1 | Float-to-ISO timestamp migration path required |

**v2 additions: 5 new P0 · 12 new P1 · 3 new P2**
**Combined total: 22 P0 · 25 P1 · 8 P2**

---

## Revised Projected Post-Fix Scores (v2)

If all P0 and P1 items (v1 + v2 amendments) are resolved:

| Dimension | v1 projected | v2 projected (post-P0+P1) | Notes |
|---|---|---|---|
| Correctness & Concurrency | 9/10 | 9.5/10 | CR-C6/C7/C8 close remaining races |
| Security | 8.5/10 | 9/10 | CR-S1a + CR-S6 close Windows-platform auth gap |
| Error Handling | 9/10 | 9.5/10 | CR-E1a + CR-E3a add preservation + watchdog |
| Test Coverage | 9/10 | 9.5/10 | CR-T4a process-based + CR-T6-T8 close major gaps |
| API/MCP Design | 9/10 | 9.5/10 | CR-A6/A7 close vocabulary + observability |
| Windows Platform | 9/10 | 9.5/10 | CR-W1a closes CTRL_BREAK/console conflict |
| Data Model & Schema | 9/10 | 9.5/10 | CR-D2a per-row tagging + CR-D4a migration |

P2 items bring each to 10/10. No dimension has a structural blocker that
prevents 10/10 with full implementation.

[[handoff:codex]]
