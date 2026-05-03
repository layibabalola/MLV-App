import copy
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .auth import Identity
from .storage import append_jsonl, read_jsonl, write_jsonl


class BridgeTransport(ABC):
    @abstractmethod
    def append_inbox(self, identity: Identity, inbox_path: Path, row: Dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def read_inbox(
        self,
        identity: Identity,
        inbox_path: Path,
        *,
        session_ids: Optional[Iterable[str]] = None,
        unread_only: bool = True,
        max_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def write_inbox_rows(
        self,
        identity: Identity,
        inbox_path: Path,
        rows: Iterable[Dict[str, Any]],
        *,
        replace_session_ids: Optional[Iterable[str]] = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def append_audit(self, identity: Identity, audit_path: Path, row: Dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def read_audit(
        self,
        identity: Identity,
        audit_path: Path,
        *,
        action: Optional[str] = None,
        max_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError


class LocalFilesystemTransport(BridgeTransport):
    def append_inbox(self, identity: Identity, inbox_path: Path, row: Dict[str, Any]) -> str:
        payload = dict(row)
        payload["tenant_id"] = identity.tenant_id
        payload["originator_machine_id"] = identity.machine_id
        append_jsonl(inbox_path, payload)
        return str(payload.get("id") or "")

    def read_inbox(
        self,
        identity: Identity,
        inbox_path: Path,
        *,
        session_ids: Optional[Iterable[str]] = None,
        unread_only: bool = True,
        max_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        wanted = set(session_ids or [])
        rows: List[Dict[str, Any]] = []
        for row in read_jsonl(inbox_path):
            if not isinstance(row, dict):
                continue
            tenant_id = row.get("tenant_id") or identity.tenant_id
            if tenant_id != identity.tenant_id:
                continue
            if unread_only and row.get("read_at"):
                continue
            if wanted and row.get("session_id") not in wanted:
                continue
            normalized = copy.deepcopy(row)
            normalized.setdefault("tenant_id", identity.tenant_id)
            normalized.setdefault("originator_machine_id", identity.machine_id)
            rows.append(normalized)
            if max_count is not None and len(rows) >= max_count:
                break
        return rows

    def write_inbox_rows(
        self,
        identity: Identity,
        inbox_path: Path,
        rows: Iterable[Dict[str, Any]],
        *,
        replace_session_ids: Optional[Iterable[str]] = None,
    ) -> None:
        scoped_sessions = set(replace_session_ids or [])
        normalized: List[Dict[str, Any]] = []
        for row in read_jsonl(inbox_path):
            tenant_id = row.get("tenant_id") or identity.tenant_id
            if tenant_id != identity.tenant_id:
                normalized.append(dict(row))
                continue
            if scoped_sessions and row.get("session_id") not in scoped_sessions:
                normalized.append(dict(row))
        for row in rows:
            payload = dict(row)
            payload["tenant_id"] = identity.tenant_id
            payload["originator_machine_id"] = identity.machine_id
            normalized.append(payload)
        write_jsonl(inbox_path, normalized)

    def append_audit(self, identity: Identity, audit_path: Path, row: Dict[str, Any]) -> str:
        payload = dict(row)
        try:
            payload["schema_version"] = max(2, int(payload.get("schema_version") or 1))
        except (TypeError, ValueError):
            payload["schema_version"] = 2
        payload["tenant_id"] = identity.tenant_id
        payload["originator_machine_id"] = identity.machine_id
        append_jsonl(audit_path, payload)
        return str(payload.get("id") or "")

    def read_audit(
        self,
        identity: Identity,
        audit_path: Path,
        *,
        action: Optional[str] = None,
        max_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in read_jsonl(audit_path):
            if not isinstance(row, dict):
                continue
            tenant_id = row.get("tenant_id") or identity.tenant_id
            if tenant_id != identity.tenant_id:
                continue
            if action is not None and row.get("action") != action:
                continue
            normalized = copy.deepcopy(row)
            normalized.setdefault("tenant_id", identity.tenant_id)
            normalized.setdefault("originator_machine_id", identity.machine_id)
            rows.append(normalized)
        if max_count is not None:
            if max_count <= 0:
                return []
            rows = rows[-max_count:]
        return rows
