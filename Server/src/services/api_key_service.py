"""API Key validation service for remote-hosted mode."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from dataclasses import dataclass, field, replace
from contextvars import Context
from typing import Any

from services.credential_lease import CredentialLease, CredentialLeaseError
from services.operation_permissions import PermissionClaimError, parse_permissions
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
    lease: CredentialLease | None = field(default=None, repr=False)


@dataclass
class _ValidationFlight:
    task: asyncio.Task[ValidationResult]
    waiters: int = 0
    generation: object = field(default_factory=object)
    invalidated: bool = False


@dataclass
class _LeaseRecord:
    """Mutable service-owned state; only this private record retains credential material."""

    lease: CredentialLease
    api_key: str = field(repr=False)
    digest: str = field(repr=False)
    deadline: float
    owners: dict[object, asyncio.Task] = field(default_factory=dict, repr=False)
    owner_refs: dict[object, int] = field(default_factory=dict, repr=False)
    retired: bool = False
    renewal: asyncio.Task[None] | None = field(default=None, repr=False)
    renewal_deadline: float | None = None
    monitor: asyncio.Task[None] | None = field(default=None, repr=False)
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


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
    _global_lease_owners: int = 0
    _global_leases: int = 0
    _global_validations: int = 0
    _global_validation_waiters: int = 0

    # Request defaults (sensible hardening)
    REQUEST_TIMEOUT: float = 5.0
    MAX_RETRIES: int = 1

    # Confirmed-invalid keys are cached too, so a bad key does not re-hit the auth service on
    # every call. That also means an unauthenticated caller grows the cache by one entry per
    # random key it tries; the cap keeps that bounded and negatives are the first to go.
    MAX_ACTIVE_LEASES: int = 1024
    MAX_LEASE_OWNERS: int = 128
    MAX_OWNERS_PER_LEASE: int = 16
    REVALIDATION_TIMEOUT: float = 11.0
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
        *,
        max_auth_age: float = 300.0,
        permissions_strict: bool = False,
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
        if not math.isfinite(cache_ttl) or cache_ttl < 0:
            raise ValueError("API key cache TTL must be finite and nonnegative")
        if not math.isfinite(max_auth_age) or max_auth_age <= 0:
            raise ValueError("API key maximum authentication age must be finite and positive")
        self._max_auth_age = max_auth_age
        self._permissions_strict = permissions_strict
        self._lease_records: dict[object, _LeaseRecord] = {}
        self._key_leases: dict[str, object] = {}
        self._lease_owners = 0
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
        previous = ApiKeyService._instance
        if previous is not None:
            previous._retire_service()
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

    async def validate(
        self, api_key: str, *, source_id: str | None = None, _fresh: bool = False
    ) -> ValidationResult:
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
            self._collect_leases()
            cached = self._cache.get(api_key) if not _fresh and self._cache_ttl > 0 else None
            if cached is not None:
                valid, user_id, metadata, expires_at = cached
                if time.time() < expires_at:
                    if valid:
                        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
                        generation = self._key_leases.get(digest)
                        record = self._lease_records.get(generation)
                        if (
                            record is not None
                            and not record.retired
                            and self._now() < record.deadline
                        ):
                            return ValidationResult(
                                valid=True, user_id=user_id, metadata=metadata, lease=record.lease
                            )
                        # A cache TTL cannot extend the original authorization age.
                        self._cache.pop(api_key, None)
                    else:
                        return ValidationResult(valid=False, error="Invalid API key")
                else:
                    # Expired, remove from cache
                    del self._cache[api_key]

            # Admit without a queue: both unique work and coalesced callers are bounded.
            digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            flight = self._inflight.get(digest)
            if ApiKeyService._global_validation_waiters >= self.MAX_VALIDATION_WAITERS:
                return self._overload()
            if flight is not None:
                if (
                    flight.invalidated
                    or flight.waiters == 0
                    or flight.waiters >= self.MAX_WAITERS_PER_KEY
                ):
                    return self._overload()
            elif ApiKeyService._global_validations >= self.MAX_INFLIGHT_VALIDATIONS:
                return self._overload()
            if source_id is not None and not self._admit_source(source_id):
                return self._overload()
            if flight is None:
                generation = object()
                flight = _ValidationFlight(
                    asyncio.create_task(
                        self._validate_and_cache(api_key, digest, generation), context=Context()
                    ),
                    generation=generation,
                )
                self._inflight[digest] = flight
                ApiKeyService._global_validations += 1
                flight.task.add_done_callback(lambda task: self._finish_flight(digest, flight))
            source_budget = self._sources[source_id] if source_id is not None else None
            flight.waiters += 1
            self._validation_waiters += 1
            ApiKeyService._global_validation_waiters += 1

        try:
            # A cancelled caller must not cancel another caller's shared validation.
            result = await asyncio.shield(flight.task)
            # Invalidation fences returned positives as well as cache publication.
            if self._closed or flight.invalidated or self._inflight.get(digest) is not flight:
                return ValidationResult(
                    valid=False, error="Authentication changed", cacheable=False
                )
            return result
        finally:
            # No checkpoint here: repeated/level cancellation cannot strand admission.
            # These event-loop-owned mutations, like the locked transitions above,
            # are synchronous and never yield to another validator.
            flight.waiters -= 1
            self._validation_waiters -= 1
            ApiKeyService._global_validation_waiters -= 1
            if source_budget is not None:
                source_budget.waiters -= 1
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
            ApiKeyService._global_validations -= 1
            self._collect_leases()

    @staticmethod
    def _overload() -> ValidationResult:
        return ValidationResult(
            valid=False, error="Authentication temporarily busy", cacheable=False, overloaded=True
        )

    def _admit_source(self, source_id: str) -> bool:
        """Called under the cache lock with the transport's verified source identity.

        NAT peers share cold-miss capacity; cached credentials bypass this budget.
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

    async def _validate_and_cache(
        self, api_key: str, digest: str, generation: object
    ) -> ValidationResult:

        # Call external validation URL
        result = await self._validate_external(api_key)

        flight = self._inflight.get(digest)
        if (
            self._closed
            or flight is None
            or flight.generation is not generation
            or flight.invalidated
            or flight.waiters == 0
        ):
            return ValidationResult(valid=False, error="Authentication changed", cacheable=False)
        result = self._lease_verdict(api_key, digest, result)

        # Only cache definitive results (valid keys and confirmed-invalid keys).
        # Transient failures (auth service unavailable, timeouts, etc.) should
        # not be cached to avoid locking out users during service outages.
        if result.cacheable and self._cache_ttl > 0:
            async with self._cache_lock:
                flight = self._inflight.get(digest)
                if (
                    self._closed
                    or flight is None
                    or flight.generation is not generation
                    or flight.invalidated
                    or flight.waiters == 0
                ):
                    return ValidationResult(
                        valid=False, error="Authentication changed", cacheable=False
                    )
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
                self._retire_service()
                tasks = [flight.task for flight in self._inflight.values()]
                tasks.extend(
                    task
                    for record in self._lease_records.values()
                    for task in (record.monitor, record.renewal)
                    if task is not None
                )
                # Cancel before yielding: admitted tasks must not create a client
                # after shutdown has captured the client it owns.
                for task in tasks:
                    task.cancel()
                client, self._client = self._client, None
                self._close_task = asyncio.create_task(
                    self._close_resources(tasks, client), context=Context()
                )
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _close_resources(
        self, tasks: list[asyncio.Task], client: httpx.AsyncClient | None
    ) -> None:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                if client is not None:
                    await client.aclose()
            finally:
                async with self._cache_lock:
                    for digest, flight in tuple(self._inflight.items()):
                        if flight.task.done():
                            self._finish_flight(digest, flight)
                    self._cache.clear()
                    self._sources.clear()
                    self._collect_leases()
                    if ApiKeyService._instance is self:
                        ApiKeyService._instance = None

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

    def _now(self) -> float:
        return time.monotonic()

    def _record(self, lease: CredentialLease) -> _LeaseRecord:
        record = self._lease_records.get(lease.generation)
        if (
            lease.service is not self
            or record is None
            or record.lease is not lease
            or record.retired
            or self._closed
        ):
            raise CredentialLeaseError()
        return record

    def lease_retired(self, lease: CredentialLease) -> bool:
        record = self._lease_records.get(lease.generation)
        return self._closed or record is None or record.lease is not lease or record.retired

    def check_lease_now(self, lease: CredentialLease) -> None:
        record = self._record(lease)
        if record.renewal is not None or self._now() >= record.deadline:
            raise CredentialLeaseError()

    async def check_lease(self, lease: CredentialLease) -> None:
        record = self._record(lease)
        if self._now() >= record.deadline and record.renewal is None:
            record.changed = asyncio.Event()
            record.renewal_deadline = asyncio.get_running_loop().time() + self.REVALIDATION_TIMEOUT
            record.renewal = asyncio.create_task(self._renew_lease(record), context=Context())
        renewal = record.renewal
        if renewal is not None:
            # One state transition owns one validator waiter, regardless of consumers.
            await record.changed.wait()
        self.check_lease_now(lease)

    def acquire_lease_owner(self, lease: CredentialLease) -> object:
        record = self._record(lease)
        task = asyncio.current_task()
        if task is None:
            raise CredentialLeaseError()
        for owner, existing in record.owners.items():
            if existing is task:
                record.owner_refs[owner] += 1
                return owner
        if (
            ApiKeyService._global_lease_owners >= self.MAX_LEASE_OWNERS
            or len(record.owners) >= self.MAX_OWNERS_PER_LEASE
        ):
            raise CredentialLeaseError(overloaded=True)
        owner = object()
        record.owners[owner] = task
        record.owner_refs[owner] = 1
        self._lease_owners += 1
        ApiKeyService._global_lease_owners += 1
        if record.monitor is None or record.monitor.done() or record.monitor.cancelling():
            record.monitor = asyncio.create_task(self._monitor_lease(record), context=Context())
        return owner

    def release_lease_owner(self, lease: CredentialLease, owner: object) -> None:
        record = self._lease_records.get(lease.generation)
        if record is not None and owner in record.owners:
            record.owner_refs[owner] -= 1
            if record.owner_refs[owner]:
                return
            del record.owner_refs[owner]
            del record.owners[owner]
            self._lease_owners -= 1
            ApiKeyService._global_lease_owners -= 1
            if not record.owners and record.monitor is not None:
                record.monitor.cancel()
        self._collect_leases()

    def _retire_lease(self, record: _LeaseRecord) -> None:
        if record.retired:
            return
        record.retired = True
        record.changed.set()
        flight = self._inflight.get(record.digest)
        if flight is not None:
            flight.invalidated = True
        self._cache.pop(record.api_key, None)
        if self._key_leases.get(record.digest) is record.lease.generation:
            del self._key_leases[record.digest]
        # Fence first, then request cancellation. Charges remain until owner finally.
        for task in set(record.owners.values()):
            task.cancel()
        monitor = record.monitor
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if monitor is not None and monitor is not current:
            monitor.cancel()

    def _retire_service(self) -> None:
        self._closed = True
        self._cache.clear()
        for source, budget in tuple(self._sources.items()):
            if budget.waiters == 0:
                del self._sources[source]
        for flight in self._inflight.values():
            flight.invalidated = True
        for record in tuple(self._lease_records.values()):
            self._retire_lease(record)
        self._collect_leases()

    def _collect_leases(self) -> None:
        for token, record in tuple(self._lease_records.items()):
            pending = any(
                task is not None and not task.done() for task in (record.monitor, record.renewal)
            )
            if record.owners or pending or record.digest in self._inflight:
                continue
            if not record.retired and self._now() < record.deadline:
                continue
            self._lease_records.pop(token, None)
            ApiKeyService._global_leases -= 1
            if self._key_leases.get(record.digest) is token:
                del self._key_leases[record.digest]

    def _lease_verdict(
        self, api_key: str, digest: str, result: ValidationResult
    ) -> ValidationResult:
        token = self._key_leases.get(digest)
        record = self._lease_records.get(token)
        principal = result.user_id
        try:
            permissions = parse_permissions(result.metadata, strict=self._permissions_strict)
        except PermissionClaimError:
            if record is not None:
                self._retire_lease(record)
            return ValidationResult(
                valid=False, error="Invalid operation permissions", cacheable=False
            )
        if (
            not result.valid
            or not isinstance(principal, str)
            or not principal
            or (record is not None and record.lease.user_id != principal)
        ):
            if record is not None:
                self._retire_lease(record)
            return replace(result, valid=False, lease=None)
        if record is not None and not record.retired and record.lease.permissions != permissions:
            # Retire before cancelling owners; invalidating this validation flight
            # also prevents its positive result from crossing CORE07's cache fence.
            self._retire_lease(record)
            return ValidationResult(valid=False, error="Authentication changed", cacheable=False)
        if record is None or record.retired:
            self._collect_leases()
            if ApiKeyService._global_leases >= self.MAX_ACTIVE_LEASES:
                return self._overload()
            token = object()
            lease = CredentialLease(self, token, principal, permissions)
            record = _LeaseRecord(lease, api_key, digest, 0)
            self._lease_records[token] = record
            ApiKeyService._global_leases += 1
            self._key_leases[digest] = token
        age = (
            min(self._cache_ttl, self._max_auth_age) if self._cache_ttl > 0 else self._max_auth_age
        )
        deadline = self._now() + age
        if not math.isfinite(deadline) or deadline <= self._now():
            self._retire_lease(record)
            return ValidationResult(
                valid=False, error="Authentication age exhausted", cacheable=False
            )
        record.deadline = deadline
        return replace(result, lease=record.lease)

    async def _renew_lease(self, record: _LeaseRecord) -> None:
        validation = asyncio.create_task(
            self.validate(record.api_key, _fresh=True), context=Context()
        )
        try:
            # asyncio.wait has a finite independent deadline even when the provider
            # suppresses cancellation. The underlying validation slot remains charged.
            deadline = record.renewal_deadline
            assert deadline is not None
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            done, _ = await asyncio.wait({validation}, timeout=remaining)
            if not done or asyncio.get_running_loop().time() >= deadline:
                self._retire_lease(record)
                validation.cancel()
                return
            if validation.cancelled() or validation.exception() is not None:
                self._retire_lease(record)
                return
            result = validation.result()
            if not result.valid or result.lease is not record.lease or record.retired:
                self._retire_lease(record)
        except asyncio.CancelledError:
            self._retire_lease(record)
            validation.cancel()
            raise
        finally:
            record.renewal = None
            record.renewal_deadline = None
            record.changed.set()
            validation.add_done_callback(self._consume_renewal)
            self._collect_leases()

    @staticmethod
    def _consume_renewal(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def _monitor_lease(self, record: _LeaseRecord) -> None:
        try:
            while record.owners and not record.retired:
                await asyncio.sleep(max(0, record.deadline - self._now()))
                await self.check_lease(record.lease)
        except CredentialLeaseError:
            self._retire_lease(record)
        finally:
            if record.monitor is asyncio.current_task():
                record.monitor = None
            self._collect_leases()

    async def invalidate_cache(self, api_key: str) -> None:
        """Fence current authorization; a later fresh validation may create a new generation."""
        async with self._cache_lock:
            self._cache.pop(api_key, None)
            digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            flight = self._inflight.get(digest)
            if flight is not None:
                flight.invalidated = True
            record = self._lease_records.get(self._key_leases.get(digest))
            if record is not None:
                self._retire_lease(record)
            self._collect_leases()

    async def clear_cache(self) -> None:
        """Invalidate all captured generations without permanently revoking credentials."""
        async with self._cache_lock:
            self._cache.clear()
            for flight in self._inflight.values():
                flight.invalidated = True
            for record in tuple(self._lease_records.values()):
                self._retire_lease(record)
            self._collect_leases()
