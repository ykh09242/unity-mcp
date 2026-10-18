"""Explicit real credential fixtures for remote transport tests and SDK child programs."""

from contextlib import asynccontextmanager
from unittest.mock import Mock, patch

from services.api_key_service import ApiKeyService, ValidationResult
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


class RemoteCredentials:
    """Mint distinct requester/plugin handles without bypassing service authorization."""

    def __init__(self, service, users):
        self.service = service
        self._users = users

    async def lease(self, key):
        result = await self.service.validate(key)
        assert result.valid and result.lease is not None
        assert result.lease.user_id == self._users[key]
        return result.lease

    @asynccontextmanager
    async def requester(self, key):
        lease = await self.lease(key)
        async with lease.own():
            yield lease

    async def call(self, key, function, *args, **kwargs):
        # Create the operation only after entry, so refused admission leaks no coroutine.
        async with self.requester(key):
            return await function(*args, **kwargs)

    async def attach_socket(self, socket, key):
        assert not isinstance(socket.state, Mock), "Synthetic sockets need concrete state"
        lease = await self.lease(key)
        user = getattr(socket.state, "user_id", None)
        assert user is None or user == lease.user_id
        socket.state.user_id = lease.user_id
        setattr(socket.state, AUTHENTICATED_LEASE_STATE, lease)
        return lease


@asynccontextmanager
async def remote_credentials(users):
    """Mock only external verdicts; close the exact real service on its active loop."""
    assert users and all(
        isinstance(key, str) and key and isinstance(user, str) and user
        for key, user in users.items()
    )
    with patch.object(ApiKeyService, "_instance", None):
        service = ApiKeyService("https://fixture.invalid/validate")

        async def validate(key):
            return ValidationResult(valid=key in users, user_id=users.get(key))

        with patch.object(service, "_validate_external", validate):
            try:
                yield RemoteCredentials(service, dict(users))
            finally:
                await service.aclose()
