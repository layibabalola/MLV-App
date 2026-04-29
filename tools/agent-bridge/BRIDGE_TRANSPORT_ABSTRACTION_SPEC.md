# Agent Bridge - Transport Abstraction Spec

**Status:** Proposed (forward-compat)
**Authors:** Claude (proposal); Codex implementation pending
**Tier:** Tier 2 — architecture-now-for-future-LAN/WAN
**Motivation:** separate "where messages live" from "how they get there" so future LAN sync (`BRIDGE_LAN_TRANSPORT_SPEC.md`) and AWS cloud (`BRIDGE_AWS_MULTITENANT_DESIGN.md`) plug in without touching the MCP server, watcher, or call sites scattered through `agent_bridge.py`.

---

## Goal

Define a `BridgeTransport` interface that the rest of the bridge calls. v1 ships one implementation: `LocalFilesystemTransport` (does what the bridge does today). v2 adds `LANSyncTransport` (peer-to-peer over local network). v3 adds `AWSCloudTransport` (Cognito + SQS + DynamoDB + WebSocket).

After this spec lands, `agent_bridge.py` and `watcher.py` no longer have direct file I/O for inbox/audit/receipt operations. All persistence goes through the transport instance.

---

## Interface

Defined in `tools/agent-bridge/core/transport.py`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator, List, Optional


@dataclass(frozen=True)
class Identity:
    """Caller identity supplied to every transport call."""
    tenant_id: str
    machine_id: str
    agent: str                        # "claude" or "codex"
    session_id: Optional[str]         # active session GUID, if any
    auth_provider: str                # "local-user", "cognito", etc.


@dataclass(frozen=True)
class InboxRow:
    """Schema v2 inbox row; transport-agnostic."""
    schema_version: int               # 2
    id: str
    from_agent: str
    to_agent: str
    session_id: str
    parent_project: Optional[str]
    tenant_id: str
    originator_machine_id: str
    queued_at: str
    body: str
    control_type: Optional[str]
    hop_count: int
    seen_at: Optional[str]
    read_at: Optional[str]
    handled_at: Optional[str]
    failure_reason: Optional[str]
    extras: dict                      # forward-compat for fields the transport doesn't know


@dataclass(frozen=True)
class ReceiptUpdate:
    """One receipt field update on one row."""
    message_id: str
    field: str                        # "seen_at" | "read_at" | "handled_at" | "failure_reason"
    value: Any
    by_session: Optional[str]


@dataclass(frozen=True)
class PresenceSnapshot:
    """Multi-layer presence record per BRIDGE_PRESENCE_SPEC.md."""
    schema_version: int
    tenant_id: str
    machine_id: str
    agent: str
    checked_at: str
    layers: dict                      # layer_name -> {state, ...}
    overall: str                      # "ok" | "degraded" | "critical"


class BridgeTransport(ABC):
    """Abstract contract for every storage/transport implementation."""

    @abstractmethod
    def append_inbox(self, identity: Identity, row: InboxRow) -> str:
        """Append a row to the recipient's inbox. Returns the row's persistent id."""

    @abstractmethod
    def read_inbox(
        self,
        identity: Identity,
        agent: str,
        session_id: Optional[str],
        include_parents: bool,
        unread_only: bool,
        max_count: int,
    ) -> List[InboxRow]:
        """Read inbox rows for `agent` filtered by session bucket and tenant."""

    @abstractmethod
    def update_receipt(self, identity: Identity, update: ReceiptUpdate) -> bool:
        """Atomically update a receipt field on one row. Returns False if row not found."""

    @abstractmethod
    def append_audit(self, identity: Identity, action: str, payload: dict) -> None:
        """Append one row to the audit log (`messages.jsonl` or cloud equivalent)."""

    @abstractmethod
    def write_presence(self, identity: Identity, snapshot: PresenceSnapshot) -> None:
        """Persist a presence snapshot for the given agent."""

    @abstractmethod
    def read_presence(
        self,
        identity: Identity,
        agent: str,
    ) -> Optional[PresenceSnapshot]:
        """Read the most recent presence snapshot. Cloud transport may return TTL'd row."""

    @abstractmethod
    def heartbeat(self, identity: Identity, role: str, payload: dict) -> None:
        """Refresh per-machine liveness (lease, presence layer 4, etc.)."""

    @abstractmethod
    def subscribe(
        self,
        identity: Identity,
        agent: str,
        session_ids: List[str],
        callback: Callable[[InboxRow], None],
    ) -> "Subscription":
        """Long-lived subscription to new inbox rows. Cloud transport implements via WebSocket; LocalFilesystemTransport via polling."""

    @abstractmethod
    def acquire_lease(self, identity: Identity, role: str, ttl_seconds: int) -> Optional["Lease"]:
        """Best-effort singleton lease. Returns None if another holder is alive."""

    @abstractmethod
    def list_pending_receipts(
        self,
        identity: Identity,
        agent: str,
        cursor: Optional[str],
        page_size: int,
    ) -> dict:
        """Paginated read of receipts in non-handled state."""
```

`Subscription` and `Lease` are minimal dataclasses with `cancel()` / `release()` methods.

---

## v1 Implementation: `LocalFilesystemTransport`

Reproduces today's behavior exactly. Lives in `core/transport_local.py`.

```python
class LocalFilesystemTransport(BridgeTransport):
    def __init__(self, bridge_paths: BridgePaths):
        self._paths = bridge_paths

    def append_inbox(self, identity, row):
        # Atomic JSONL append with shared lock
        path = self._paths.inbox_path_for(row.to_agent)
        line = json.dumps(row_to_dict(row, tenant_id=identity.tenant_id))
        with file_lock(path):
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return row.id

    def read_inbox(self, identity, agent, session_id, include_parents, unread_only, max_count):
        path = self._paths.inbox_path_for(agent)
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                row = parse_row(line)
                if row.tenant_id != identity.tenant_id:
                    continue                # Tenant isolation
                if unread_only and row.read_at:
                    continue
                if session_id and not _matches_session(row, session_id, include_parents):
                    continue
                rows.append(row)
                if len(rows) >= max_count:
                    break
        return rows

    # ... (other methods follow same pattern)
```

Tenant filtering is inside the transport; consumers don't need to repeat the check.

---

## v2 Implementation: `LANSyncTransport` (sketch for forward planning)

Wraps `LocalFilesystemTransport` and adds peer replication:

```python
class LANSyncTransport(BridgeTransport):
    def __init__(self, local: LocalFilesystemTransport, peer_client: LANPeerClient):
        self._local = local
        self._peer = peer_client

    def append_inbox(self, identity, row):
        row_id = self._local.append_inbox(identity, row)
        if row.to_agent != identity.agent:
            # Forward to peer machine if recipient is on the other side
            self._peer.replicate_inbox_row(row, identity)
        return row_id

    def update_receipt(self, identity, update):
        ok = self._local.update_receipt(identity, update)
        if ok:
            self._peer.replicate_receipt_update(update, identity)
        return ok

    # Subscriptions can multiplex local FS poll + LAN events
```

Replication is best-effort; on partition, local writes succeed and queue for sync.

---

## v3 Implementation: `AWSCloudTransport` (sketch for forward planning)

Cloud-first: SQS for inbox, DynamoDB for receipts/presence/audit, API Gateway WebSocket for push:

```python
class AWSCloudTransport(BridgeTransport):
    def __init__(self, sqs_client, ddb_client, ws_client, identity_provider):
        self._sqs = sqs_client
        self._ddb = ddb_client
        self._ws = ws_client
        self._identity = identity_provider

    def append_inbox(self, identity, row):
        # SQS FIFO, MessageGroupId=tenant_id for per-tenant ordering
        msg_body = encrypt_with_tenant_dek(row, identity.tenant_id)
        self._sqs.send_message(
            QueueUrl=f"inbox-{row.to_agent}.fifo",
            MessageBody=msg_body,
            MessageGroupId=identity.tenant_id,
            MessageDeduplicationId=row.id,
        )
        return row.id

    def update_receipt(self, identity, update):
        # DynamoDB conditional update
        self._ddb.update_item(
            TableName="bridge-receipts",
            Key={"PK": f"{identity.tenant_id}#{update.message_id}", "SK": update.field},
            UpdateExpression="SET #v = :v",
            ConditionExpression="attribute_not_exists(#v)",  # Don't overwrite
            ...
        )
        return True

    def subscribe(self, identity, agent, session_ids, callback):
        # WebSocket connect; route incoming MessageDelivery events to callback
        return WebSocketSubscription(self._ws, identity, agent, session_ids, callback)
```

End-to-end encrypted bodies; cloud sees ciphertext only.

---

## Transport selection

`server_wrapper.py` adds `--transport <impl>` flag; `core/transport.py` exposes a factory:

```python
def make_transport(args, paths, auth) -> BridgeTransport:
    impl = args.transport or "local"
    if impl == "local":
        return LocalFilesystemTransport(paths)
    if impl == "lan":
        return LANSyncTransport(
            local=LocalFilesystemTransport(paths),
            peer_client=LANPeerClient(args, paths, auth),
        )
    if impl == "cloud":
        return AWSCloudTransport(
            sqs_client=make_sqs_client(args),
            ddb_client=make_ddb_client(args),
            ws_client=make_ws_client(args),
            identity_provider=auth.identity_provider(),
        )
    raise ValueError(f"unknown transport: {impl}")
```

`agent_bridge.py` consumes the resulting `BridgeTransport` and never reads files directly.

---

## Refactor surface in `agent_bridge.py`

The current `agent_bridge.py` is 2052 lines; criterion 19 targets a facade <200 lines. Routing transport calls through this abstraction unblocks that target by removing direct I/O from the orchestration layer.

Key call sites to migrate (non-exhaustive):

| Call site | Today | After |
|---|---|---|
| `send_to_peer` | direct `append_jsonl` to inbox | `transport.append_inbox(identity, row)` |
| `check_inbox` | direct read+filter of JSONL | `transport.read_inbox(identity, ...)` |
| `mark_read` / `mark_seen` / `mark_handled` | direct rewrite of JSONL | `transport.update_receipt(identity, update)` |
| `bridge_process_status` | direct stat of pid/runtime files | `transport.heartbeat()` + `read_presence()` |
| `compact.py` | direct file rewrite | `transport.compact()` (new method, optional) |
| `recover_state.py` | direct file scan | unchanged (recovery operates below the transport layer) |

---

## Configuration surface

| Flag (server_wrapper.py) | Purpose | Default |
|---|---|---|
| `--transport <local|lan|cloud>` | Select implementation | `local` |
| `--bridge-root <path>` | Local FS root | `%USERPROFILE%\.agent-bridge` |
| `--peer-discovery <mdns|static>` | LAN peer discovery method | (LAN only) |
| `--paired-machines <comma-list>` | LAN static peer addresses | (LAN only) |
| `--cloud-endpoint <wss-url>` | Cloud WebSocket URL | (cloud only) |
| `--auth <local-user|cognito|...>` | Auth provider | `local-user` |
| `--credential-store <name>` | OS keychain for credentials | (cloud / LAN PSK) |

---

## Testing strategy

**Per-implementation unit tests:** each `BridgeTransport` subclass has its own test module. Shared base test fixture `TransportContractTests` runs the same scenarios against all implementations:

- `test_append_inbox_returns_id`
- `test_read_inbox_returns_what_was_appended`
- `test_read_inbox_filters_by_tenant`
- `test_update_receipt_atomic`
- `test_update_receipt_returns_false_for_unknown_id`
- `test_subscribe_delivers_new_rows`
- `test_subscribe_cancellation_releases_resources`
- `test_lease_singleton_property`

`LocalFilesystemTransport` provides the regression net for v1 behavior.

`LANSyncTransport` and `AWSCloudTransport` add their own tests for replication/cloud-specific semantics (eventual consistency, partition tolerance, DynamoDB conditional update behavior, etc.).

**Integration tests:** end-to-end MCP-tool-call scenarios run against each transport in CI matrix.

---

## Acceptance criteria

- TR1. `BridgeTransport` interface defined in `core/transport.py` with all methods above; tests assert subclass conformance.
- TR2. `LocalFilesystemTransport` implementation; passes the contract test suite; existing `agent_bridge.py` tests pass when re-wired through it.
- TR3. `agent_bridge.py` direct file I/O for inbox/receipt/audit operations is replaced by transport calls; tests assert no `inbox-*.jsonl` write outside transport layer.
- TR4. `server_wrapper.py` exposes `--transport` flag with default `local`; tests assert.
- TR5. Tenant filtering applied at transport layer (no consumer-side filter needed); tests assert.
- TR6. Forward-compat: contract tests can be run against a stub `LANSyncTransport` and `AWSCloudTransport` (with mocked AWS clients) without bridge code changes.
- TR7. After this spec lands, the `agent_bridge.py` facade is meaningfully smaller (criterion 19 progress: target <200 lines for the facade portion; transport layer takes the remaining bulk).

---

## Coordination model

Codex implements; Claude reviews. This is the largest Tier-2 refactor by line count (`agent_bridge.py` is 2052 lines today). Suggest splitting into sub-phases:

- **Phase TR.1** Define interface + `LocalFilesystemTransport` skeleton (no behavioral change)
- **Phase TR.2** Migrate `send_to_peer` and `check_inbox` through the transport
- **Phase TR.3** Migrate receipt and audit calls
- **Phase TR.4** Migrate bridge_process_status / heartbeat / lease
- **Phase TR.5** Verify `agent_bridge.py` facade size; close criterion 19

Each phase audited independently per audit profile
`tools/agent-bridge/audit-profiles/transport-abstraction.md` (to be added).

[[handoff:codex]]
