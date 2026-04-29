# Agent Bridge - Authentication Spec

**Status:** Proposed (forward-compat)
**Authors:** Claude (proposal); Codex implementation pending
**Tier:** Tier 2 — architecture-now-for-future-LAN/WAN
**Motivation:** today's bridge implicitly trusts "whoever owns the local OS user is the user." For multi-tenant cloud (`BRIDGE_AWS_MULTITENANT_DESIGN.md`, Tier 3), explicit identity is required. This spec defines a `BridgeAuth` abstraction so cloud auth providers (Cognito, OAuth) plug in without touching call sites; v1 ships a `LocalUserAuth` that preserves today's behavior.

---

## Goal

Every MCP tool call resolves through an authenticated `Identity` (matching `BRIDGE_TRANSPORT_ABSTRACTION_SPEC.md`'s definition). The auth provider determines:

- `tenant_id` (single-tenant local: `local-default`; cloud: from JWT)
- `user_id` (OS user in v1; Cognito sub in cloud)
- `machine_id` (stable per-machine UUID from `machine.json`)
- `agent` (claude or codex, from invocation)

Authorization (whether this identity can do this op) is also abstracted, but defaults are permissive in v1 (single-user, all ops).

---

## Interface

Defined in `tools/agent-bridge/core/auth.py`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str
    machine_id: str
    agent: str                        # "claude" or "codex"
    session_id: Optional[str]
    auth_provider: str                # "local-user" | "cognito" | ...
    expires_at: Optional[str] = None  # ISO8601; cloud tokens are time-bounded


class AuthError(Exception):
    """Base for all auth failures."""


class AuthenticationFailed(AuthError):
    """Caller could not be authenticated."""


class AuthorizationDenied(AuthError):
    """Caller is authenticated but not authorized for this op."""


class BridgeAuth(ABC):
    """Abstract auth provider."""

    @abstractmethod
    def authenticate(self, credentials: dict) -> Identity:
        """Validate credentials and return resolved Identity. Raises AuthenticationFailed."""

    @abstractmethod
    def authorize(
        self,
        identity: Identity,
        op: str,
        resource: dict,
    ) -> None:
        """Verify identity can perform op on resource. Raises AuthorizationDenied."""

    @abstractmethod
    def current_identity(self) -> Identity:
        """Return the identity for this MCP server subprocess (cached after first auth)."""

    @abstractmethod
    def refresh(self) -> Identity:
        """Re-authenticate; useful when token nears expiry."""

    @abstractmethod
    def credential_store(self) -> "CredentialStore":
        """Returns the OS-level credential storage abstraction (Windows Credential Manager, macOS Keychain, libsecret)."""
```

`CredentialStore` is a small interface for read/write/delete of named credentials, with implementations per OS:

```python
class CredentialStore(ABC):
    @abstractmethod
    def get(self, name: str) -> Optional[bytes]: ...
    @abstractmethod
    def set(self, name: str, value: bytes) -> None: ...
    @abstractmethod
    def delete(self, name: str) -> None: ...
```

Implementations:
- `WindowsCredentialManagerStore` (uses `pywin32` `win32cred`)
- `MacOSKeychainStore` (uses `keyring` library)
- `LinuxSecretStore` (uses `keyring` with `libsecret` backend)
- `EnvVarStore` (development; reads from env vars; not for production)

---

## v1 Implementation: `LocalUserAuth`

Preserves today's behavior. Lives in `core/auth_local.py`.

```python
class LocalUserAuth(BridgeAuth):
    """Single-user OS-bound auth. tenant_id is always 'local-default'."""

    def __init__(self, machine_id: str):
        self._machine_id = machine_id
        self._cached_identity: Optional[Identity] = None

    def authenticate(self, credentials: dict) -> Identity:
        # No credentials needed; use OS identity
        os_user = getpass.getuser()
        agent = credentials.get("agent")
        if agent not in {"claude", "codex"}:
            raise AuthenticationFailed("agent must be claude or codex")
        identity = Identity(
            tenant_id="local-default",
            user_id=os_user,
            machine_id=self._machine_id,
            agent=agent,
            session_id=credentials.get("session_id"),
            auth_provider="local-user",
            expires_at=None,
        )
        self._cached_identity = identity
        return identity

    def authorize(self, identity, op, resource):
        # Permissive in v1: single user, all ops allowed within their tenant
        if resource.get("tenant_id") and resource["tenant_id"] != identity.tenant_id:
            raise AuthorizationDenied(
                f"identity tenant {identity.tenant_id} cannot access {resource['tenant_id']}"
            )
        # else: allow

    def current_identity(self) -> Identity:
        if self._cached_identity is None:
            raise AuthenticationFailed("not yet authenticated")
        return self._cached_identity

    def refresh(self) -> Identity:
        return self._cached_identity  # OS identity doesn't expire

    def credential_store(self) -> CredentialStore:
        # On Windows, return WindowsCredentialManagerStore; etc.
        return _platform_credential_store()
```

In v1, every MCP server subprocess at startup calls
`LocalUserAuth.authenticate({"agent": <agent>})` and gets back a permissive `Identity` with `tenant_id="local-default"`. All subsequent calls use that.

---

## v3 Implementation: `CognitoJWTAuth` (sketch for forward planning)

```python
class CognitoJWTAuth(BridgeAuth):
    def __init__(self, user_pool_id, client_id, region, machine_id, cred_store):
        self._user_pool_id = user_pool_id
        self._client_id = client_id
        self._region = region
        self._machine_id = machine_id
        self._cred_store = cred_store
        self._jwt: Optional[str] = None
        self._identity: Optional[Identity] = None

    def authenticate(self, credentials: dict) -> Identity:
        # On first call: SRP-A flow with Cognito
        # On subsequent calls: load JWT from cred store, validate signature + expiry
        cached_jwt = self._cred_store.get("agent-bridge-jwt")
        if cached_jwt and not _is_expired(cached_jwt):
            jwt = cached_jwt.decode("utf-8")
        else:
            jwt = _cognito_login(
                pool=self._user_pool_id,
                client=self._client_id,
                username=credentials["username"],
                password=credentials["password"],
                region=self._region,
            )
            self._cred_store.set("agent-bridge-jwt", jwt.encode("utf-8"))
        claims = _verify_jwt(jwt, self._user_pool_id, self._region)
        return Identity(
            tenant_id=claims["custom:tenant_id"],
            user_id=claims["sub"],
            machine_id=self._machine_id,
            agent=credentials["agent"],
            session_id=credentials.get("session_id"),
            auth_provider="cognito",
            expires_at=claims["exp"],
        )

    def authorize(self, identity, op, resource):
        # Tenant boundary enforcement
        if resource.get("tenant_id") and resource["tenant_id"] != identity.tenant_id:
            raise AuthorizationDenied(...)
        # Optional: per-op permission check from claims["custom:permissions"]
```

JWT cached in OS credential store; auto-refresh on expiry.

---

## Authorization model

**v1 (single-tenant local):** permissive. Only check is tenant_id boundary (which is always `local-default`).

**v3 (multi-tenant cloud):** authorization is enforced at:

1. **Tenant boundary:** identity.tenant_id MUST match resource.tenant_id for any read/write
2. **Op allowlist (optional, per-product policy):** if Cognito JWT carries `custom:permissions` claim, the op must be in the allowlist. Default: all ops allowed for the user's own tenant.
3. **Cross-project pairing:** for `CROSS_PROJECT_PAIR_*` ops, the link's permission tier (read_and_advise, write_with_confirmation, etc.) gates writes. See `CROSS_PROJECT_PAIR_TEST_MATRIX.md`.

---

## Identity flow

```
MCP server subprocess startup
  │
  ▼
server.py reads --auth flag and --credential-store flag
  │
  ▼
core/auth.py:make_auth(args) → BridgeAuth instance
  │
  ▼
auth.authenticate({"agent": "claude"}) → Identity
  │
  ▼ (cached for subprocess lifetime)
agent_bridge.py uses identity for every transport call
  │
  ▼
transport.append_inbox(identity, row) → enforces tenant filter
```

For cloud: identity may expire (JWT TTL). On expiry, the next call triggers `auth.refresh()`. If refresh fails (token revoked, password changed), the MCP server subprocess prompts for re-auth (out-of-band UI flow); meanwhile bridge ops fail with `AuthenticationFailed` which the MCP client surfaces to the agent.

---

## Configuration surface

| Flag | Purpose | Default |
|---|---|---|
| `--auth <local-user|cognito|oauth-device-code|...>` | Auth provider | `local-user` |
| `--credential-store <wcm|keychain|libsecret|env>` | OS credential storage | platform default |
| `--cognito-pool-id <id>` | Cognito user pool ID | (cognito only) |
| `--cognito-client-id <id>` | Cognito app client ID | (cognito only) |
| `--cognito-region <region>` | AWS region | (cognito only) |

---

## Credential storage model

Credentials are NEVER passed via env var or config file in production. They live in OS credential storage:

- **Windows:** Windows Credential Manager (per-user, encrypted by DPAPI)
- **macOS:** Keychain Services
- **Linux:** libsecret (gnome-keyring, kwallet, etc.)

**Development override:** `--credential-store env` reads from env vars (`BRIDGE_USERNAME`, `BRIDGE_PASSWORD`, etc.). Refused in production builds (`BRIDGE_PRODUCTION=1`).

**Initial credential entry:** out-of-band flow. For Cognito: a one-time CLI tool (`tools/agent-bridge/auth_setup.py --provider cognito`) prompts user, calls Cognito SRP-A, stores resulting JWT in cred store. After that, MCP server reads from cred store on every subprocess start.

**Credential rotation:** Cognito JWT auto-refresh; Cognito refresh token rotation per AWS best practice. User-visible re-auth required when refresh token also expires (typically 30 days).

---

## Acceptance criteria

- A1. `BridgeAuth` interface defined; `LocalUserAuth` implementation passes contract tests; tests assert.
- A2. Every MCP tool call resolves an `Identity` via `auth.current_identity()`; tests assert via integration test.
- A3. Tenant boundary enforced in `LocalUserAuth.authorize`; tests assert with multi-tenant fixture.
- A4. `CredentialStore` abstraction with platform-specific implementations; tests run platform-conditionally.
- A5. `--auth local-user` is the default; tests assert via flag introspection.
- A6. Forward-compat: stub `CognitoJWTAuth` with mocked Cognito client passes contract tests.
- A7. JWT expiry triggers refresh; tests assert with mocked clock.
- A8. Credentials never logged or audited (verify via grep on audit log fixture).

---

## Tests required

1. `test_local_user_auth_uses_os_user`
2. `test_local_user_auth_tenant_id_is_local_default`
3. `test_local_user_auth_cross_tenant_denied`
4. `test_authenticate_caches_identity`
5. `test_credential_store_set_get_delete_roundtrip` (per-platform)
6. `test_credential_store_env_var_refused_in_production`
7. `test_cognito_jwt_auth_decodes_claim` (with mocked Cognito)
8. `test_cognito_jwt_refresh_on_expiry`
9. `test_cross_tenant_op_denied_with_authorization_denied`
10. `test_credentials_never_appear_in_audit_log`

---

## Forward-compat with `BRIDGE_TENANT_SCOPING_SPEC.md`

The `Identity.tenant_id` field is the source of truth for tenant scoping. Transport implementations enforce filtering against it; auth implementations populate it. The two specs together close the multi-tenant safety story.

---

## Coordination model

Codex implements; Claude reviews. Auth is sensitive code; recommend:

- **Phase A.1** Define interface; ship `LocalUserAuth` in v1 mode (no behavior change from today; just adds the abstraction)
- **Phase A.2** Wire `Identity` through MCP server entry points (each tool call resolves it once); tests assert
- **Phase A.3** Add `CredentialStore` abstraction with platform implementations
- **Phase A.4** (Tier 3 ramp) Add `CognitoJWTAuth` and the cloud auth flow

Each phase audited per `tools/agent-bridge/audit-profiles/auth.md` (to be added).

[[handoff:codex]]
