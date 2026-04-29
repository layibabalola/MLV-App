# Audit Profile - Auth Abstraction

**Trigger:** commit modifying `core/auth.py` or wiring `Identity` through MCP server
**Reference spec:** `BRIDGE_AUTH_SPEC.md` A1-A8
**Phasing:** A.1 (interface + LocalUserAuth) → A.2 (Identity flow) → A.3 (CredentialStore) → A.4 (Cognito JWT)

---

## Files this profile covers

- `tools/agent-bridge/core/auth.py` (new — interface + LocalUserAuth)
- `tools/agent-bridge/core/auth_local.py`
- `tools/agent-bridge/core/credential_store.py` (Windows Credential Manager / macOS Keychain / libsecret)
- `tools/agent-bridge/server.py` (auth wiring at MCP server startup)
- Future: `core/auth_cognito.py` for Tier-3

---

## A-by-A audit checklist

### A1 - BridgeAuth interface

- [ ] `core/auth.py` exists with `BridgeAuth(ABC)` class
- [ ] Abstract methods: `authenticate(credentials) -> Identity`, `authorize(identity, op, resource) -> None`, `current_identity() -> Identity`, `refresh() -> Identity`, `credential_store() -> CredentialStore`
- [ ] Frozen `Identity` dataclass with: `tenant_id`, `user_id`, `machine_id`, `agent`, `session_id`, `auth_provider`, `expires_at`
- [ ] Exceptions: `AuthError`, `AuthenticationFailed`, `AuthorizationDenied`
- [ ] Subclass conformance test passes

### A2 - LocalUserAuth (v1)

- [ ] `LocalUserAuth.authenticate({"agent": "claude"|"codex"})` returns Identity with `tenant_id="local-default"`, `user_id=getpass.getuser()`
- [ ] Permissive `authorize()` — only check is tenant_id match (always passes for local-default)
- [ ] `current_identity()` cached after first authentication
- [ ] `refresh()` returns cached identity (OS identity doesn't expire)
- [ ] Tests: OS user resolved, cross-tenant denied, cache works

### A3 - Identity flow through MCP server

- [ ] `server.py` startup calls `make_auth(args)` and `auth.authenticate({"agent": <agent>})` once
- [ ] Resolved Identity passed to every transport call (`transport.append_inbox(identity, ...)`)
- [ ] No MCP tool reads `os.environ["USER"]` directly — all identity goes through `auth.current_identity()`
- [ ] Integration test asserts identity.agent matches `--agent` arg

### A4 - CredentialStore abstraction

- [ ] Interface in `core/credential_store.py`: `get(name) -> bytes`, `set(name, value)`, `delete(name)`
- [ ] Platform implementations:
  - Windows: `WindowsCredentialManagerStore` (uses `pywin32` `win32cred`)
  - macOS: `MacOSKeychainStore` (uses `keyring` lib)
  - Linux: `LinuxSecretStore` (uses `keyring` with libsecret)
  - Dev: `EnvVarStore` (refused if `BRIDGE_PRODUCTION=1`)
- [ ] Tests run platform-conditionally
- [ ] `EnvVarStore` refusal in production verified

### A5 - --auth flag

- [ ] `server_wrapper.py` exposes `--auth <local-user|cognito|...>` with default `local-user`
- [ ] `core/auth.py:make_auth(args)` factory routes by flag
- [ ] Default behavior unchanged from today

### A6 - Forward-compat CognitoJWTAuth stub

- [ ] Stub `CognitoJWTAuth` with mocked Cognito client passes contract tests
- [ ] JWT decode mock returns Identity with `tenant_id` from claim
- [ ] Refresh logic tested with mocked clock

### A7 - JWT expiry triggers refresh

- [ ] When `Identity.expires_at` is in the past, next `current_identity()` call triggers `refresh()`
- [ ] Refresh failure surfaces as `AuthenticationFailed`
- [ ] Test with mocked clock + mocked Cognito refresh endpoint

### A8 - Credentials never logged

- [ ] grep audit log fixture for any field that looks like a credential (jwt, password, token)
- [ ] Test asserts: after authenticate(...), audit log contains action but NOT credential value

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_phase0_contract test_auth
```

Tests are platform-conditional; CI runs all 3 platforms separately.

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Auth abstraction Phase A.<N>
ACTION_REQUESTED: none | next-phase
NONCE: audit-auth-<phase>-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage vs A1-A8:

| A# | Status |
|---|---|
| A1 | DONE / PARTIAL / DEFERRED / MISSING |
| ...up through A8 |

Platform-specific notes (CredentialStore implementations): [Windows/macOS/Linux pass status]

Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

Auth is sensitive code. Recommend per-phase audit. A.1 + A.2 + A.3 (LocalUserAuth + Identity flow + CredentialStore) is the v1 deliverable. A.4 + Cognito is Tier-3.
