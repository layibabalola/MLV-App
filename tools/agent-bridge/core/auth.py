import dataclasses
import getpass
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


LOCAL_DEFAULT_TENANT_ID = "local-default"
LOCAL_DEFAULT_MACHINE_ID = "local-machine"


@dataclasses.dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str
    machine_id: str
    agent: str
    session_id: Optional[str]
    auth_provider: str
    expires_at: Optional[str] = None


class AuthError(Exception):
    pass


class AuthenticationFailed(AuthError):
    pass


class AuthorizationDenied(AuthError):
    pass


class CredentialStore(ABC):
    @abstractmethod
    def get(self, name: str) -> Optional[bytes]:
        raise NotImplementedError

    @abstractmethod
    def set(self, name: str, value: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, name: str) -> None:
        raise NotImplementedError


class NullCredentialStore(CredentialStore):
    def get(self, name: str) -> Optional[bytes]:
        return None

    def set(self, name: str, value: bytes) -> None:
        return None

    def delete(self, name: str) -> None:
        return None


class BridgeAuth(ABC):
    @abstractmethod
    def authenticate(self, credentials: Dict[str, Any]) -> Identity:
        raise NotImplementedError

    @abstractmethod
    def authorize(self, identity: Identity, op: str, resource: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def current_identity(self) -> Identity:
        raise NotImplementedError

    @abstractmethod
    def refresh(self) -> Identity:
        raise NotImplementedError

    @abstractmethod
    def credential_store(self) -> CredentialStore:
        raise NotImplementedError


class LocalUserAuth(BridgeAuth):
    def __init__(self, machine_id: str = LOCAL_DEFAULT_MACHINE_ID) -> None:
        self._machine_id = machine_id
        self._cached_identity: Optional[Identity] = None
        self._store = NullCredentialStore()

    def authenticate(self, credentials: Dict[str, Any]) -> Identity:
        agent = str(credentials.get("agent") or "").strip().lower()
        if agent not in {"claude", "codex"}:
            raise AuthenticationFailed("agent must be claude or codex")
        identity = Identity(
            tenant_id=LOCAL_DEFAULT_TENANT_ID,
            user_id=self._local_user_id(),
            machine_id=self._machine_id,
            agent=agent,
            session_id=credentials.get("session_id"),
            auth_provider="local-user",
        )
        self._cached_identity = identity
        return identity

    def authorize(self, identity: Identity, op: str, resource: Dict[str, Any]) -> None:
        tenant_id = resource.get("tenant_id")
        if tenant_id and tenant_id != identity.tenant_id:
            raise AuthorizationDenied(
                "identity tenant %s cannot access %s" % (identity.tenant_id, tenant_id)
            )

    def current_identity(self) -> Identity:
        if self._cached_identity is None:
            raise AuthenticationFailed("not yet authenticated")
        return self._cached_identity

    def refresh(self) -> Identity:
        if self._cached_identity is None:
            raise AuthenticationFailed("not yet authenticated")
        return self._cached_identity

    def credential_store(self) -> CredentialStore:
        return self._store

    def _local_user_id(self) -> str:
        try:
            user = getpass.getuser()
        except OSError:
            user = os.environ.get("USERNAME") or os.environ.get("USER")
        return str(user or "local-user")
