"""API Key validation service for remote-hosted mode."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("mcp-for-unity-server")


@dataclass
class ValidationResult:
    """Result of an API key validation."""

    valid: bool
    user_id: str | None = None
    metadata: dict[str, Any] | None = None
    error: str | None = None
    cacheable: bool = True
    overloaded: bool = False


@dataclass
class _ValidationFlight:
    task: asyncio.Task[ValidationResult]
    waiters: int = 0


@dataclass
class _SourceBudget:
    tokens: float
    updated_at: float
    waiters: int = 0


class ApiKeyService:
    """Service for validating API keys against an external auth endpoint.

    Follows the class-level singleton pattern for global access by MCP tools.
    """

    _instance: "ApiKeyService | None" = None

    # Request defaults (sensible hardening)
    REQUEST_TIMEOUT: float = 5.0
    MAX_RETRIES: int = 1

    # Confirmed-invalid keys are cached too, so a bad key does not re-hit the auth service on
    # every call. That also means an unauthenticated caller grows the cache by one entry per
    # random key it tries; the cap keeps that bounded and negatives are the first to go.
    MAX_CACHE_ENTRIES: int = 1024
    MAX_KEY_LENGTH: int = 4096
    MAX_INFLIGHT_VALIDATIONS: int = 16
    MAX_VALIDATION_WAITERS: int = 128
    MAX_WAITERS_PER_KEY: int = 16
    MAX_SOURCE_WAITERS: int = 8
    MAX_SOURCE_BUCKETS: int = 1024
    SOURCE_BURST: float = 16.0
    SOURCE_REFILL_PER_SECOND: float = 4.0
    SOURCE_IDLE_TTL: float = 30.0

    def __init__(
        self,
        validation_url: str,
        cache_ttl: float = 300.0,
        service_token_header: str | None = None,
        service_token: str | None = None,
    ):
        """Initialize the API key service.

        Args:
            validation_url: External URL to validate API keys (POST with {"api_key": "..."})
            cache_ttl: Cache TTL for validated keys in seconds (default: 300)
            service_token_header: Optional header name for service authentication (e.g. "X-Service-Token")
            service_token: Optional token value for service authentication
        """
        error = "API key validation URL must be absolute HTTPS without userinfo or fragments"
        try:
            endpoint = urlsplit(validation_url)
            allowed = (
                endpoint.scheme == "https"
                and bool(endpoint.hostname)
                and endpoint.username is None
                and endpoint.password is None
                and not endpoint.fragment
                and endpoint.port != 0
                and not any(char.isspace() or ord(char) < 32 for char in validation_url)
                and "\\" not in validation_url
            )
        except ValueError:
            raise ValueError(error) from None
        if not allowed:
            raise ValueError(error)
        self._validation_url = validation_url
        self._cache_ttl = cache_ttl
        self._service_token_header = service_token_header
        self._service_token = service_token
        # Cache: api_key -> (valid, user_id, metadata, expires_at)
        self._cache: dict[str, tuple[bool, str | None, dict[str, Any] | None, float]] = {}
        self._cache_lock = asyncio.Lock()
        self._inflight: dict[str, _ValidationFlight] = {}
        self._validation_waiters = 0
        self._sources: dict[str, _SourceBudget] = {}
        self._client: httpx.AsyncClient | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        ApiKeyService._instance = self

    @classmethod
    def get_instance(cls) -> "ApiKeyService":
        """Get the singleton instance.

        Raises:
            RuntimeError: If the service has not been initialized.
        """
        if cls._instance is None:
            raise RuntimeError("ApiKeyService not initialized")
        return cls._instance

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if the service has been initialized."""
        return cls._instance is not None

    async def validate(self, api_key: str, *, source_id: str | None = None) -> ValidationResult:
        """Validate an API key.

        Returns:
            ValidationResult with valid=True and user_id if valid,
            or valid=False with error message if invalid.
        """
        if not api_key:
            return ValidationResult(valid=False, error="API key required")
        if len(api_key) > self.MAX_KEY_LENGTH:
            return ValidationResult(valid=False, error="API key too long", cacheable=False)

        # Check cache first
        async with self._cache_lock:
            if self._closed:
                return ValidationResult(
                    valid=False, error="Auth service unavailable", cacheable=False
                )
            cached = self._cache.get(api_key)
            if cached is not None:
                valid, user_id, metadata, expires_at = cached
                if time.time() < expires_at:
                    if valid:
                        return ValidationResult(valid=True, user_id=user_id, metadata=metadata)
                    else:
                        return ValidationResult(valid=False, error="Invalid API key")
                else:
                    # Expired, remove from cache
                    del self._cache[api_key]

            # Admit without a queue: both unique work and coalesced callers are bounded.
            digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            flight = self._inflight.get(digest)
            if self._validation_waiters >= self.MAX_VALIDATION_WAITERS:
                return self._overload()
            if flight is not None:
                if flight.waiters == 0 or flight.waiters >= self.MAX_WAITERS_PER_KEY:
                    return self._overload()
            elif len(self._inflight) >= self.MAX_INFLIGHT_VALIDATIONS:
                return self._overload()
            if source_id is not None and not self._admit_source(source_id):
                return self._overload()
            if flight is None:
                flight = _ValidationFlight(
                    asyncio.create_task(self._validate_and_cache(api_key, digest))
                )
                self._inflight[digest] = flight
                flight.task.add_done_callback(lambda task: self._finish_flight(digest, flight))
            flight.waiters += 1
            self._validation_waiters += 1

        try:
            # A cancelled caller must not cancel another caller's shared validation.
            return await asyncio.shield(flight.task)
        finally:
            # No checkpoint here: repeated/level cancellation cannot strand admission.
            # These event-loop-owned mutations, like the locked transitions above,
            # are synchronous and never yield to another validator.
            flight.waiters -= 1
            self._validation_waiters -= 1
            if source_id is not None:
                self._sources[source_id].waiters -= 1
            if flight.waiters == 0:
                if flight.task.done():
                    self._finish_flight(digest, flight)
                else:
                    # Keep the slot until actual outbound cancellation completes.
                    flight.task.cancel()

    def _finish_flight(self, digest: str, flight: _ValidationFlight) -> None:
        """Synchronous task completion bookkeeping cannot itself be cancelled."""
        if not flight.task.cancelled():
            flight.task.exception()  # Retrieve orphaned failures; active waiters still receive them.
        if flight.waiters == 0 and self._inflight.get(digest) is flight:
            del self._inflight[digest]

    @staticmethod
    def _overload() -> ValidationResult:
        return ValidationResult(
            valid=False, error="Authentication temporarily busy", cacheable=False, overloaded=True
        )

    def _admit_source(self, source_id: str) -> bool:
        """Called under the cache lock, using actual peer identity rather than forwarded headers.

        NAT/proxy peers share cold-miss capacity; cached credentials bypass this budget.
        """
        now = time.monotonic()
        budget = self._sources.get(source_id)
        if budget is None:
            if len(self._sources) >= self.MAX_SOURCE_BUCKETS:
                for key in [
                    key
                    for key, value in self._sources.items()
                    if value.waiters == 0 and now - value.updated_at >= self.SOURCE_IDLE_TTL
                ]:
                    del self._sources[key]
            if len(self._sources) >= self.MAX_SOURCE_BUCKETS:
                return False
            budget = _SourceBudget(self.SOURCE_BURST, now)
            self._sources[source_id] = budget
        budget.tokens = min(
            self.SOURCE_BURST,
            budget.tokens + max(0.0, now - budget.updated_at) * self.SOURCE_REFILL_PER_SECOND,
        )
        budget.updated_at = now
        if budget.waiters >= self.MAX_SOURCE_WAITERS or budget.tokens < 1.0:
            return False
        budget.tokens -= 1.0
        budget.waiters += 1
        return True

    async def _validate_and_cache(self, api_key: str, digest: str) -> ValidationResult:

        # Call external validation URL
        result = await self._validate_external(api_key)

        # Only cache definitive results (valid keys and confirmed-invalid keys).
        # Transient failures (auth service unavailable, timeouts, etc.) should
        # not be cached to avoid locking out users during service outages.
        if result.cacheable:
            async with self._cache_lock:
                flight = self._inflight.get(digest)
                if self._closed or flight is None or flight.waiters == 0:
                    return result
                now = time.time()
                if len(self._cache) >= self.MAX_CACHE_ENTRIES:
                    for stale in [k for k, v in self._cache.items() if v[3] <= now]:
                        del self._cache[stale]
                if api_key not in self._cache and len(self._cache) >= self.MAX_CACHE_ENTRIES:
                    if not result.valid:
                        # Full of live entries: a negative verdict is not worth evicting
                        # anything for. The caller still gets the answer.
                        return result
                    # Make room for a validated key: drop a negative entry if there is
                    # one, otherwise the validated key that expires soonest.
                    negatives = [k for k, v in self._cache.items() if not v[0]]
                    pool = negatives or list(self._cache)
                    del self._cache[min(pool, key=lambda k: self._cache[k][3])]
                self._cache[api_key] = (
                    result.valid,
                    result.user_id,
                    result.metadata,
                    now + self._cache_ttl,
                )

        return result

    async def aclose(self) -> None:
        """Reject new work, cancel outstanding validation and close the pooled client once."""
        async with self._cache_lock:
            if self._close_task is None:
                self._closed = True
                tasks = [flight.task for flight in self._inflight.values()]
                # Cancel before yielding: admitted tasks must not create a client
                # after shutdown has captured the client it owns.
                for task in tasks:
                    task.cancel()
                client, self._client = self._client, None
                self._close_task = asyncio.create_task(self._close_resources(tasks, client))
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _close_resources(
        self, tasks: list[asyncio.Task[ValidationResult]], client: httpx.AsyncClient | None
    ) -> None:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                if client is not None:
                    await client.aclose()
            finally:
                async with self._cache_lock:
                    self._inflight.clear()

    @staticmethod
    def _fingerprint(api_key: str) -> str:
        """One-way handle for log lines. Eight literal characters of a key were enough to
        correlate a leaked log with a key; a hash prefix correlates without exposing any."""
        return "sha256:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]

    async def _validate_external(self, api_key: str) -> ValidationResult:
        """Call external validation endpoint.

        Failure mode: fail closed (treat as invalid on errors).
        """
        redacted_key = self._fingerprint(api_key)

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                if self._closed:
                    return ValidationResult(
                        valid=False, error="Auth service unavailable", cacheable=False
                    )
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self.REQUEST_TIMEOUT,
                        follow_redirects=False,
                        limits=httpx.Limits(
                            max_connections=self.MAX_INFLIGHT_VALIDATIONS,
                            max_keepalive_connections=self.MAX_INFLIGHT_VALIDATIONS,
                        ),
                    )
                client = self._client
                # Build request headers
                headers = {"Content-Type": "application/json"}
                if self._service_token_header and self._service_token:
                    headers[self._service_token_header] = self._service_token

                response = await client.post(
                    self._validation_url,
                    json={"api_key": api_key},
                    headers=headers,
                )

                if response.status_code == 200:
                    data = response.json()
                    verdict = data.get("valid")
                    if verdict is not True and verdict is not False:
                        return ValidationResult(
                            valid=False,
                            error="Auth service error (invalid validation verdict)",
                            cacheable=False,
                        )
                    if verdict is True:
                        return ValidationResult(
                            valid=True,
                            user_id=data.get("user_id"),
                            metadata=data.get("metadata"),
                        )
                    else:
                        return ValidationResult(
                            valid=False,
                            error=data.get("error", "Invalid API key"),
                        )
                elif response.status_code == 401:
                    return ValidationResult(valid=False, error="Invalid API key")
                else:
                    logger.warning(
                        "API key validation returned status %d for key %s",
                        response.status_code,
                        redacted_key,
                    )
                    # Fail closed but don't cache (transient service error)
                    return ValidationResult(
                        valid=False,
                        error=f"Auth service error (status {response.status_code})",
                        cacheable=False,
                    )

            except httpx.TimeoutException:
                if attempt < self.MAX_RETRIES:
                    logger.debug(
                        "API key validation timeout for key %s, retrying...",
                        redacted_key,
                    )
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.warning(
                    "API key validation timeout for key %s after %d attempts",
                    redacted_key,
                    attempt + 1,
                )
                return ValidationResult(
                    valid=False,
                    error="Auth service timeout",
                    cacheable=False,
                )
            except httpx.RequestError as exc:
                if attempt < self.MAX_RETRIES:
                    logger.debug(
                        "API key validation request error for key %s: %s, retrying...",
                        redacted_key,
                        exc,
                    )
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.warning(
                    "API key validation request error for key %s: %s",
                    redacted_key,
                    exc,
                )
                return ValidationResult(
                    valid=False,
                    error="Auth service unavailable",
                    cacheable=False,
                )
            except Exception as exc:
                logger.error(
                    "Unexpected error validating API key %s: %s",
                    redacted_key,
                    exc,
                )
                return ValidationResult(
                    valid=False,
                    error="Auth service error",
                    cacheable=False,
                )

        # Should not reach here, but fail closed
        return ValidationResult(valid=False, error="Auth service error", cacheable=False)

    async def invalidate_cache(self, api_key: str) -> None:
        """Remove an API key from the cache."""
        async with self._cache_lock:
            self._cache.pop(api_key, None)

    async def clear_cache(self) -> None:
        """Clear all cached validations."""
        async with self._cache_lock:
            self._cache.clear()


__all__ = ["ApiKeyService", "ValidationResult"]
