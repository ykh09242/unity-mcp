"""Private in-memory authorization handles; credential material stays in the service."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from services.operation_permissions import LEGACY_GRANTS, OperationGrants

if TYPE_CHECKING:
    from services.api_key_service import ApiKeyService


class CredentialLeaseError(RuntimeError):
    """Authorization ended; this exception contains no credential or provider response."""

    def __init__(self, *, overloaded: bool = False) -> None:
        super().__init__("Remote authorization unavailable")
        self.overloaded = overloaded


@dataclass(frozen=True, slots=True)
class CredentialLease:
    """Opaque exact-service/generation and immutable principal, never serializable state."""

    service: ApiKeyService = field(repr=False)
    generation: object = field(repr=False)
    user_id: str
    permissions: OperationGrants = LEGACY_GRANTS

    async def check(self) -> None:
        """Park at expiry until the one bounded renewal has a definitive outcome."""
        await self.service.check_lease(self)

    def check_now(self) -> None:
        """Non-yielding dispatch fence; an expired generation cannot dispatch."""
        self.service.check_lease_now(self)

    @property
    def retired(self) -> bool:
        return self.service.lease_retired(self)

    @asynccontextmanager
    async def owner_lifetime(self):
        """Charge through actual cleanup; the enclosing boundary supplies I/O checks."""
        owner = self.service.acquire_lease_owner(self)
        token = _current_lease.set(self)
        try:
            yield self
        finally:
            _current_lease.reset(token)
            self.service.release_lease_owner(self, owner)

    @asynccontextmanager
    async def own(self):
        """Own an ordinary guarded operation, retaining the existing entry check."""
        async with self.owner_lifetime():
            await self.check()
            yield self


_current_lease: ContextVar[CredentialLease | None] = ContextVar(
    "unity_remote_credential_lease", default=None
)


def current_credential_lease() -> CredentialLease | None:
    """Return an opaque handle; callers must check it at their authorization boundary."""
    return _current_lease.get()


@contextmanager
def bind_credential_lease(lease: CredentialLease | None):
    """Restore only the captured handle in detached work, never request credentials."""
    token = _current_lease.set(lease)
    try:
        yield
    finally:
        _current_lease.reset(token)


async def require_credential_lease(user_id: str | None = None) -> CredentialLease:
    lease = current_credential_lease()
    if lease is None or (user_id is not None and user_id != lease.user_id):
        raise CredentialLeaseError()
    await lease.check()
    return lease
