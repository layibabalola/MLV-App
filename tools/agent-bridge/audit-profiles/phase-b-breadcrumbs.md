# Audit Profile - Phase B Peer Breadcrumbs

**Trigger:** AUTO_PAIR_SPEC.md Migration Plan Phase B; detailed design in
`PHASE_B_BREADCRUMB_DESIGN.md`.

**Audit timing:** at every commit during Phase B implementation. Likely
2-3 commits (writers, watcher template support, integration).

---

## Required artifacts

### Schema implementation

- [ ] `peer-<agent>.runtime.json` schema documented in
  `STATE_LAYOUT.md` or equivalent
- [ ] Schema includes: schema_version, agent, session_id, desktop_app,
  desktop_thread_id (optional for Claude Code), deeplink_template
  (optional), written_by_pid, written_at, bridge_root, manifest_root_id

### Writers

- [ ] `tools/agent-bridge/bootstrap_session.py` writes
  `peer-claude.runtime.json` after activation, before HANDSHAKE
- [ ] Codex's bootstrap mirror writes `peer-codex.runtime.json` similarly
- [ ] Atomic write: `.tmp` → fsync → os.replace (no torn-read window)
- [ ] Claude Code without thread id: breadcrumb omits `desktop_thread_id`
  and `deeplink_template`; doesn't fail
- [ ] Codex Desktop with `CODEX_THREAD_ID` env or `PARENT_THREAD_ID_KEY`
  config: breadcrumb includes thread id

### Watcher reader

- [ ] `run_command_for_session` (or upstream) reads peer breadcrumb at
  fire time
- [ ] schema_version check: accept >= MAX_BREADCRUMB_SCHEMA, warn on
  newer with optional fields, fail on newer with required fields
- [ ] bridge_root mismatch: refuse breadcrumb (treat as missing)
- [ ] Missing breadcrumb: emit `peer_breadcrumb_missing` audit event,
  mark message seen with `wake_skipped_no_peer`, no retry

### Watcher-config schema

- [ ] New optional keys: `session_id_source: "peer_breadcrumb"`,
  `on_message_command_template`
- [ ] Watcher detects schema by presence of `_template` / `_source`
  suffix and routes accordingly
- [ ] Legacy inline `on_message_command` + hardcoded `session_id`
  continue to work for one release

### Wake script (`wake_codex.ps1`)

- [ ] New `-ExpectedThreadId <UUID>` param plumbed through (replaces the
  removed `-ExpectedTitleMarker` from the title-revert)
- [ ] Phase B v1: param is plumbed but post-foreground verification is
  NOT yet implemented (deferred to B.2)
- [ ] Documentation comment indicates B.2 is where the verification
  goes (UIA or filesystem correlation, TBD)

---

## Test coverage

Per PHASE_B_BREADCRUMB_DESIGN.md:

Unit tests:
- [ ] `test_peer_breadcrumb_write_atomic`
- [ ] `test_peer_breadcrumb_overwrites_on_rebootstrap`
- [ ] `test_peer_breadcrumb_missing_yields_wake_skipped_no_peer`
- [ ] `test_watcher_resolves_template_from_breadcrumb`
- [ ] `test_breadcrumb_schema_version_newer_warned_not_rejected`
- [ ] `test_breadcrumb_bridge_root_mismatch_refused`
- [ ] `test_legacy_inline_command_still_works`

Integration tests:
- [ ] End-to-end pair test (Claude bootstraps → Codex bootstraps → wake
  uses correct thread_id)
- [ ] Stale-breadcrumb recovery (old peer crashed, new bootstrap
  overwrites)
- [ ] Two bootstraps in <1s (rapid restart race)

If any unit test from the above list is missing, push back.

---

## Acceptance criteria

Per AUTO_PAIR_SPEC.md:

- [ ] B1. Breadcrumb schema implemented; round-trip tests green
- [ ] B2. Bootstrap writers atomic; tests assert
- [ ] B3. Watcher resolves template from breadcrumb at fire time
- [ ] B4. wake_codex.ps1 accepts -ExpectedThreadId; v1 doesn't yet verify
  post-foreground
- [ ] B5. Watcher continues to honor legacy form for one release
- [ ] B6. Two-side end-to-end test passes

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Phase B Peer Breadcrumbs - <subscope>
ACTION_REQUESTED: none
NONCE: audit-phase-b-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage:

Schema:
- peer-<agent>.runtime.json schema: CHECK | MISSING
- Documentation in STATE_LAYOUT.md: CHECK | MISSING

Writers:
- bootstrap_session.py atomic write: CHECK | MISSING
- Codex bootstrap mirror: CHECK | MISSING (deferred to next commit?)
- Claude Code without thread_id handling: CHECK | MISSING

Watcher:
- Breadcrumb read at fire time: CHECK | MISSING
- Schema version check: CHECK | MISSING
- bridge_root mismatch refused: CHECK | MISSING
- Missing breadcrumb path: CHECK | MISSING

Watcher-config schema:
- New _template / _source keys: CHECK | MISSING
- Legacy form preserved: CHECK | MISSING

wake_codex.ps1:
- -ExpectedThreadId param plumbed: CHECK | MISSING
- B.2 deferral documented: CHECK | MISSING

Tests at HEAD: <N> pass; Phase B tests green: CHECK | MISSING

[Push back any deviations; otherwise PASS]

[[handoff:codex]]
```
