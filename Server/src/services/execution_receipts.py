"""Default-off execution receipt ledger, independent of native/domain job state.

Schema 2 adds opaque, exact public-result bytes to the unregistered first slice.
The caller validates and encodes public application output before supplying bytes;
this layer neither redacts arbitrary output nor promises that it contains no secrets.
Callers provide server-owned scope/floors and recheck live authorization around I/O.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from services.execution_receipt_identity import (
    HEX_ID,
    MAX_ARGUMENT_BYTES,
    PERMISSIONS,
    PROJECT_HASH,
    ReceiptCapacity,
    ReceiptConflict,
    ReceiptInvalid,
    ReceiptNotFound,
    ReceiptOptions,
    ReceiptScope,
    ReceiptUnavailable,
    RequestIdentity,
    permission_floor,
)
from services.execution_receipt_storage import (
    MAX_RECORDS,
    METADATA_BYTES,
    METADATA_QUOTA,
    RESULT_BYTES,
    RESULT_QUOTA,
    ReceiptFiles,
)

TTL_SECONDS = 7200
RESULT_TTL_SECONDS = 600
MAX_OWNER_RECORDS = 256
MAX_PROJECT_RECORDS = 128
MAX_ACTIVE = 256
MAX_OWNER_ACTIVE = 32
MAX_PROJECT_ACTIVE = 16
OWNER_RESULT_QUOTA = 16 * 1024 * 1024
PROJECT_RESULT_QUOTA = 8 * 1024 * 1024
MAX_ATTEMPTS = 8
MAX_ATTEMPT_COUNT = 2_147_483_647
Status = Literal["pending", "completed", "not_dispatched", "unknown"]
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_UNKNOWN_CAUSES = frozenset(
    {
        "observation_lost",
        "wrapper_cancelled",
        "authorization_ended",
        "server_restarted",
    }
)
_ATTEMPT_CAUSES = frozenset(
    {
        "dispatching",
        "observed",
        "observation_lost",
        "not_dispatched",
        "disconnected",
        "retired_generation",
        "wrapper_cancelled",
        "authorization_ended",
        "server_restarted",
    }
)
_RESULT_REASONS = frozenset(
    {
        "not_requested",
        "pending",
        "available",
        "result_too_large",
        "result_expired",
        "observation_lost",
        "not_dispatched",
    }
)
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9_.:@/-]{1,128}\Z")


@dataclass(frozen=True, slots=True)
class ReceiptAttempt:
    kind: Literal["http", "stdio"]
    attempt_id: str
    session_id: str | None
    generation: str | int | None
    dispatched: bool
    observed: bool
    cause: str


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    schema_version: int
    policy_version: int
    store_id: str
    receipt_id: str
    owner_digest: str
    project_hash: str
    key_digest: str | None
    key_length: int
    fingerprint: str
    fingerprint_bytes: int
    permissions: tuple[str, ...]
    created_at: float
    updated_at: float
    expires_at: float
    status: Status
    cause: str
    intent: bool
    retain_result: bool
    result_reason: str
    result_bytes: int
    result_sha256: str | None
    result_observed_at: float | None
    result_expires_at: float | None
    attempts: tuple[ReceiptAttempt, ...]
    attempt_count: int
    wall_clock_high_water: float | None = None


@dataclass(frozen=True, slots=True)
class ReceiptView:
    """Safe metadata view; contains no original input, key, principal or digest."""

    receipt_id: str
    store_id: str
    project_hash: str
    status: Status
    created_at: float
    updated_at: float
    expires_at: float
    result_available: bool = False
    result_reason: str = "not_requested"
    result_bytes: int = 0
    result_expires_at: float | None = None
    attempts: tuple[ReceiptAttempt, ...] = ()
    attempt_count: int = 0

    @property
    def result_unavailable_reason(self) -> str:
        """Compatibility spelling used by the metadata-only first slice."""
        return self.result_reason


@dataclass(slots=True)
class _Owned:
    token: str
    wrapper_open: bool
    entered: bool
    workers: set[str]


class ReceiptWorker:
    """Release only from the actual worker's finally/completion, never its waiter."""

    def __init__(self, store: ExecutionReceiptStore, receipt_id: str, token: str) -> None:
        self._store, self._receipt_id, self._token = store, receipt_id, token

    def close(self) -> None:
        self._store._release_worker(self._receipt_id, self._token)


class ReceiptInvocation:
    """One admitted wrapper; cancel/UNKNOWN does not release owned worker pins."""

    def __init__(self, store: ExecutionReceiptStore, receipt_id: str, token: str) -> None:
        self._store, self.receipt_id, self._token = store, receipt_id, token
        # Constructed by admit while it holds the metadata lock. The immutable
        # option remains readable without waiting for later journal operations.
        self._retain_result = store._records[receipt_id].retain_result

    @property
    def retain_result(self) -> bool:
        return self._retain_result

    def enter_handler(self) -> None:
        with self._store._owners_lock:
            owned = self._store._invocation(self.receipt_id, self._token)
            if owned.entered:
                raise ReceiptInvalid("receipt_handler_already_entered")
            owned.entered = True

    def acquire_worker(self) -> ReceiptWorker:
        """Acquire before scheduling, release after actual exit, including launch failure."""
        with self._store._owners_lock:
            owned = self._store._invocation(self.receipt_id, self._token)
            if len(owned.workers) >= 64:
                raise ReceiptCapacity("receipt_worker_pin_capacity")
            token = uuid4().hex
            owned.workers.add(token)
            return ReceiptWorker(self._store, self.receipt_id, token)

    def extend_floor(self, floor: frozenset[str], *, grants: frozenset[str]) -> None:
        """Commit the server-classified union before any newly required native send."""
        self._store._extend_floor(self.receipt_id, self._token, floor, grants)

    def observe_attempt(
        self,
        kind: Literal["http", "stdio"],
        attempt_id: str,
        *,
        session_id: str | None = None,
        generation: str | int | None = None,
        dispatched: bool,
        observed: bool,
        cause: str,
        new_attempt: bool = True,
    ) -> None:
        self._store._observe_attempt(
            self.receipt_id,
            self._token,
            ReceiptAttempt(
                kind,
                attempt_id,
                session_id,
                generation,
                dispatched,
                observed,
                cause,
            ),
            new_attempt=new_attempt,
        )

    def finish(
        self,
        status: Status,
        *,
        cause: str = "observation_lost",
        result: bytes | None = None,
        result_reason: str | None = None,
    ) -> ReceiptView:
        return self._store._finish(
            self.receipt_id,
            self._token,
            status,
            cause,
            result,
            result_reason,
        )


class ExecutionReceiptStore:
    """Bounded single-writer synchronous ledger; no tasks, waits, retries or dispatch.

    Every admitted call commits intent before returning its invocation handle.
    Actual integration must own synchronous journal workers through their real
    completion, as well as native workers; wrapper cancellation cannot detach them.
    Metadata operations take _lock before the short _owners_lock. Ownership-only
    operations never take _lock; no file operation holds _owners_lock.
    """

    def __init__(
        self,
        root: Path | None,
        *,
        private_root: bool = False,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._owners_lock = threading.RLock()
        self._clock = clock
        self._monotonic = monotonic
        self._clock_lock = threading.Lock()
        self._clock_origin: tuple[float, float] | None = None
        self._last_monotonic: float | None = None
        self._last_wall = 0.0
        self._recovered_logical_floor = 0.0
        self._poisoned = False
        self._closed = False
        self._records: dict[str, ReceiptRecord] = {}
        self._sizes: dict[str, int] = {}
        self._keys: dict[tuple[str, str, str], str] = {}
        self._owned: dict[str, _Owned] = {}
        self._files = ReceiptFiles(root, private_root=private_root)
        try:
            for receipt_id in self._files.ids():
                raw = self._files.read(receipt_id)
                record = self._decode(raw, receipt_id)
                self._records[receipt_id] = record
                self._sizes[receipt_id] = len(raw)
                wall_floor = record.wall_clock_high_water
                self._last_wall = max(
                    self._last_wall, record.updated_at if wall_floor is None else wall_floor
                )
                self._recovered_logical_floor = max(
                    self._recovered_logical_floor, record.updated_at
                )
                key = self._key(record)
                if key is not None:
                    if key in self._keys:
                        raise ReceiptUnavailable("duplicate_persisted_receipt_key")
                    self._keys[key] = receipt_id
            self._check_record_counts()
            now = self._now()
            for record in tuple(self._records.values()):
                if record.status == "pending":
                    self._commit(
                        replace(
                            record,
                            status="unknown",
                            cause="server_restarted",
                            updated_at=now,
                            result_reason=(
                                "observation_lost" if record.retain_result else "not_requested"
                            ),
                        )
                    )
            self._prune(now)
            for record in self._records.values():
                if record.result_reason == "available":
                    self._read_result_record(record)
            self._files.reconcile_results(
                frozenset(
                    record.receipt_id
                    for record in self._records.values()
                    if record.result_reason == "available"
                )
            )
        except Exception as error:
            self._files.close()
            if isinstance(error, ReceiptUnavailable):
                raise
            raise ReceiptUnavailable("receipt_store_recovery_failed") from error

    @property
    def store_id(self) -> str:
        return self._files.store_id

    @property
    def available(self) -> bool:
        """Report admission health without waiting on journal I/O."""
        with self._owners_lock:
            if self._closed or self._poisoned:
                return False
        try:
            self._now()
        except ReceiptUnavailable:
            return False
        return True

    @property
    def local_owner(self) -> str:
        return f"local:execution:v1:{self.store_id}"

    @property
    def active_count(self) -> int:
        with self._owners_lock:
            return len(self._owned)

    def _owned_ids(self) -> frozenset[str]:
        with self._owners_lock:
            return frozenset(self._owned)

    @property
    def charged_metadata_bytes(self) -> int:
        with self._lock:
            owned_ids = self._owned_ids()
            return self._files.overhead + sum(
                2 * METADATA_BYTES if receipt_id in owned_ids else size
                for receipt_id, size in self._sizes.items()
            )

    @property
    def charged_result_bytes(self) -> int:
        with self._lock:
            owned_ids = self._owned_ids()
            return sum(self._result_charge(record, owned_ids) for record in self._records.values())

    @staticmethod
    def _result_charge(record: ReceiptRecord, owned_ids: frozenset[str]) -> int:
        if record.retain_result and record.receipt_id in owned_ids:
            return 2 * RESULT_BYTES
        return record.result_bytes if record.result_reason == "available" else 0

    def _result_capacity(self, scope: ReceiptScope, extra: int) -> bool:
        owner = project = total = 0
        owned_ids = self._owned_ids()
        for record in self._records.values():
            charge = self._result_charge(record, owned_ids)
            total += charge
            if record.owner_digest == scope.owner_digest:
                owner += charge
                if record.project_hash == scope.project_hash:
                    project += charge
        return (
            total + extra <= RESULT_QUOTA
            and owner + extra <= OWNER_RESULT_QUOTA
            and project + extra <= PROJECT_RESULT_QUOTA
        )

    def _check(self) -> None:
        if self._closed or self._poisoned:
            raise ReceiptUnavailable("receipt_store_unavailable")

    def _now(self) -> float:
        # This short clock-only lock never waits for metadata or journal I/O.
        with self._clock_lock:
            try:
                wall, monotonic = self._clock(), self._monotonic()
                if any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in (wall, monotonic)
                ):
                    raise ValueError("invalid clock sample")
                if wall < self._last_wall or (
                    self._last_monotonic is not None and monotonic < self._last_monotonic
                ):
                    raise ValueError("clock moved backward")
                origin = self._clock_origin or (
                    max(float(wall), self._recovered_logical_floor),
                    float(monotonic),
                )
                now = origin[0] + (monotonic - origin[1])
                if not math.isfinite(now):
                    raise ValueError("invalid elapsed clock")
            except Exception as error:
                # Clock health can recover after validated catch-up. It must not
                # clear storage poisoning or replace persisted deduplication keys.
                raise ReceiptUnavailable("receipt_clock_unavailable") from error
            self._clock_origin = origin
            self._last_wall = float(wall)
            self._last_monotonic = float(monotonic)
            return float(now)

    def _wall_high_water(self) -> float:
        """Snapshot validated raw wall time without holding this lock during I/O."""
        with self._clock_lock:
            return self._last_wall

    @staticmethod
    def _key(record: ReceiptRecord) -> tuple[str, str, str] | None:
        if record.key_digest is None:
            return None
        return record.owner_digest, record.project_hash, record.key_digest

    def _decode(self, raw: bytes, receipt_id: str) -> ReceiptRecord:
        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError("duplicate_metadata_member")
            return result

        try:
            data = json.loads(raw, object_pairs_hook=unique)
            if (
                type(data) is not dict
                or type(data.get("permissions")) is not list
                or type(data.get("attempts")) is not list
            ):
                raise ValueError("invalid_metadata_shape")
            data["permissions"] = tuple(data["permissions"])
            if any(type(item) is not dict for item in data["attempts"]):
                raise ValueError("invalid_attempt_shape")
            data["attempts"] = tuple(ReceiptAttempt(**item) for item in data["attempts"])
            record = ReceiptRecord(**data)
            self._encode(record)
            if record.receipt_id != receipt_id or record.store_id != self.store_id:
                raise ValueError("wrong_record_identity")
            return record
        except (ValueError, TypeError, RecursionError, AttributeError) as error:
            raise ReceiptUnavailable("invalid_receipt_record") from error

    @staticmethod
    def _encode(record: ReceiptRecord) -> bytes:
        if (
            type(record.schema_version) is not int
            or record.schema_version not in {2, 3}
            or type(record.policy_version) is not int
            or record.policy_version != 1
            or not HEX_ID.fullmatch(record.receipt_id)
            or not HEX_ID.fullmatch(record.store_id)
            or not PROJECT_HASH.fullmatch(record.project_hash)
            or not _DIGEST.fullmatch(record.owner_digest)
            or not _DIGEST.fullmatch(record.fingerprint)
            or type(record.fingerprint_bytes) is not int
            or not 0 < record.fingerprint_bytes <= MAX_ARGUMENT_BYTES
            or type(record.key_length) is not int
            or (record.key_digest is None and record.key_length != 0)
            or (
                record.key_digest is not None
                and (not _DIGEST.fullmatch(record.key_digest) or not 16 <= record.key_length <= 128)
            )
            or tuple(sorted(set(record.permissions))) != record.permissions
            or not set(record.permissions) <= PERMISSIONS
            or record.intent is not True
            or record.status not in {"pending", "unknown", "completed", "not_dispatched"}
            or type(record.retain_result) is not bool
            or record.result_reason not in _RESULT_REASONS
            or type(record.result_bytes) is not int
            or not 0 <= record.result_bytes <= RESULT_BYTES
            or type(record.attempts) is not tuple
            or len(record.attempts) > MAX_ATTEMPTS
            or type(record.attempt_count) is not int
            or not 0 <= record.attempt_count <= MAX_ATTEMPT_COUNT
            or len(record.attempts) != min(record.attempt_count, MAX_ATTEMPTS)
        ):
            raise ReceiptInvalid("invalid_receipt_record")
        wall_floor = record.wall_clock_high_water
        if (record.schema_version == 2 and wall_floor is not None) or (
            record.schema_version == 3
            and (
                type(wall_floor) not in (int, float)
                or not math.isfinite(wall_floor)
                or wall_floor < 0
            )
        ):
            raise ReceiptInvalid("invalid_receipt_record")
        times = record.created_at, record.updated_at, record.expires_at
        if (
            any(type(value) not in (float, int) or not math.isfinite(value) for value in times)
            or record.created_at < 0
            or record.updated_at < record.created_at
            or record.expires_at != record.created_at + TTL_SECONDS
            or record.expires_at <= record.created_at
            or record.cause
            != {
                "pending": "intent_committed",
                "completed": "observed",
                "not_dispatched": "pre_entry",
            }.get(record.status, record.cause)
            or (record.status == "unknown" and record.cause not in _UNKNOWN_CAUSES)
        ):
            raise ReceiptInvalid("invalid_receipt_record")
        for attempt in record.attempts:
            ExecutionReceiptStore._validate_attempt(attempt)
        ExecutionReceiptStore._validate_result_metadata(record)
        data = asdict(record)
        if record.schema_version == 2:
            data.pop("wall_clock_high_water")
        raw = json.dumps(data, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode(
            "ascii"
        )
        if len(raw) > METADATA_BYTES:
            raise ReceiptInvalid("receipt_metadata_limit")
        return raw

    @staticmethod
    def _validate_attempt(attempt: ReceiptAttempt) -> None:
        if (
            type(attempt) is not ReceiptAttempt
            or attempt.kind not in {"http", "stdio"}
            or type(attempt.attempt_id) is not str
            or not _ATTEMPT_ID.fullmatch(attempt.attempt_id)
            or (
                attempt.session_id is not None
                and (
                    type(attempt.session_id) is not str
                    or not _ATTEMPT_ID.fullmatch(attempt.session_id)
                )
            )
            or (
                attempt.generation is not None
                and not (
                    (type(attempt.generation) is int and 0 <= attempt.generation <= 2**63 - 1)
                    or (
                        type(attempt.generation) is str
                        and _ATTEMPT_ID.fullmatch(attempt.generation)
                    )
                )
            )
            or type(attempt.dispatched) is not bool
            or type(attempt.observed) is not bool
            or attempt.cause not in _ATTEMPT_CAUSES
        ):
            raise ReceiptInvalid("invalid_receipt_attempt")

    @staticmethod
    def _validate_result_metadata(record: ReceiptRecord) -> None:
        reason = record.result_reason
        if not record.retain_result:
            valid = reason == "not_requested"
        else:
            valid = (
                reason
                in {
                    "pending": {"pending"},
                    "completed": {
                        "available",
                        "result_too_large",
                        "result_expired",
                        "observation_lost",
                    },
                    "unknown": {"observation_lost"},
                    "not_dispatched": {"not_dispatched"},
                }[record.status]
            )
        if not valid:
            raise ReceiptInvalid("invalid_receipt_result_metadata")
        if reason in {"available", "result_expired"}:
            observed, expires = record.result_observed_at, record.result_expires_at
            if (
                type(observed) not in (int, float)
                or type(expires) not in (int, float)
                or not math.isfinite(observed)
                or not math.isfinite(expires)
                or not record.created_at <= observed <= record.updated_at
                or expires != min(observed + RESULT_TTL_SECONDS, record.expires_at)
                or (reason == "available" and expires <= observed)
                or (reason == "result_expired" and expires > record.updated_at)
            ):
                raise ReceiptInvalid("invalid_receipt_result_metadata")
        elif record.result_observed_at is not None or record.result_expires_at is not None:
            raise ReceiptInvalid("invalid_receipt_result_metadata")
        if reason == "available":
            if type(record.result_sha256) is not str or not _DIGEST.fullmatch(record.result_sha256):
                raise ReceiptInvalid("invalid_receipt_result_metadata")
        elif record.result_sha256 is not None or record.result_bytes != 0:
            raise ReceiptInvalid("invalid_receipt_result_metadata")

    def _commit(self, record: ReceiptRecord) -> None:
        record = replace(record, schema_version=3, wall_clock_high_water=self._wall_high_water())
        raw = self._encode(record)
        extra_stage = 0 if record.receipt_id in self._owned_ids() else METADATA_BYTES
        if self.charged_metadata_bytes + extra_stage > METADATA_QUOTA:
            raise ReceiptCapacity("receipt_storage_capacity")
        try:
            self._files.write(record.receipt_id, raw)
        except Exception as error:
            self._poisoned = True
            raise ReceiptUnavailable("receipt_commit_uncertain") from error
        self._records[record.receipt_id] = record
        self._sizes[record.receipt_id] = len(raw)

    def _check_record_counts(self) -> None:
        owners: dict[str, int] = {}
        projects: dict[tuple[str, str], int] = {}
        for record in self._records.values():
            owners[record.owner_digest] = owners.get(record.owner_digest, 0) + 1
            key = record.owner_digest, record.project_hash
            projects[key] = projects.get(key, 0) + 1
        if (
            len(self._records) > MAX_RECORDS
            or any(count > MAX_OWNER_RECORDS for count in owners.values())
            or any(count > MAX_PROJECT_RECORDS for count in projects.values())
            or self.charged_metadata_bytes > METADATA_QUOTA
        ):
            raise ReceiptUnavailable("receipt_persisted_capacity")
        owner_results: dict[str, int] = {}
        project_results: dict[tuple[str, str], int] = {}
        owned_ids = self._owned_ids()
        for record in self._records.values():
            charge = self._result_charge(record, owned_ids)
            owner_results[record.owner_digest] = owner_results.get(record.owner_digest, 0) + charge
            domain = record.owner_digest, record.project_hash
            project_results[domain] = project_results.get(domain, 0) + charge
        if (
            self.charged_result_bytes > RESULT_QUOTA
            or any(size > OWNER_RESULT_QUOTA for size in owner_results.values())
            or any(size > PROJECT_RESULT_QUOTA for size in project_results.values())
        ):
            raise ReceiptUnavailable("receipt_persisted_result_capacity")

    def _expire_result(self, record: ReceiptRecord, now: float) -> ReceiptRecord:
        if record.result_reason != "available":
            return record
        if record.result_expires_at is None:
            self._poisoned = True
            raise ReceiptUnavailable("invalid_receipt_result_metadata")
        if now < record.result_expires_at:
            return record
        expired = replace(
            record,
            result_reason="result_expired",
            result_bytes=0,
            result_sha256=None,
            updated_at=now,
        )
        # Publish the unavailable state first. A crash before deletion leaves an
        # orphan that exclusive recovery removes, never an advertised missing file.
        self._commit(expired)
        try:
            self._files.remove_result(record.receipt_id)
        except (OSError, ReceiptUnavailable) as error:
            self._poisoned = True
            raise ReceiptUnavailable("receipt_result_prune_uncertain") from error
        return expired

    def _prune(self, now: float) -> None:
        for record in tuple(self._records.values()):
            record = self._expire_result(record, now)
            if record.receipt_id not in self._owned_ids() and now >= record.expires_at:
                try:
                    self._files.remove(record.receipt_id)
                except Exception as error:
                    self._poisoned = True
                    raise ReceiptUnavailable("receipt_prune_uncertain") from error
                self._records.pop(record.receipt_id)
                self._sizes.pop(record.receipt_id)
                key = self._key(record)
                if key is not None:
                    self._keys.pop(key)

    @staticmethod
    def _view(record: ReceiptRecord) -> ReceiptView:
        return ReceiptView(
            record.receipt_id,
            record.store_id,
            record.project_hash,
            record.status,
            record.created_at,
            record.updated_at,
            record.expires_at,
            record.result_reason == "available",
            record.result_reason,
            record.result_bytes,
            record.result_expires_at,
            record.attempts,
            record.attempt_count,
        )

    def _authorize(
        self, record: ReceiptRecord, scope: ReceiptScope, grants: frozenset[str]
    ) -> None:
        permission_floor(grants)
        if (
            record.owner_digest != scope.owner_digest
            or record.project_hash != scope.project_hash
            or not set(record.permissions) <= grants
        ):
            raise ReceiptNotFound("receipt_not_found")

    def admit(
        self,
        scope: ReceiptScope,
        options: ReceiptOptions,
        identity: RequestIdentity,
        *,
        floor: frozenset[str],
        grants: frozenset[str],
    ) -> ReceiptInvocation | ReceiptView:
        """Return a handle only after durable intent, or metadata for a duplicate.

        No duplicate path returns an invocation or schedules handler work. Historical
        lookup/replay permission must be rechecked by the transport after storage I/O.
        """
        options.validate()
        required = permission_floor(floor)
        permission_floor(grants)
        if not floor <= grants:
            raise ReceiptNotFound("receipt_not_found")
        with self._lock:
            self._check()
            if options.store_id != self.store_id or options.project_hash != scope.project_hash:
                raise ReceiptNotFound("receipt_not_found")
            duplicate = self.lookup(scope, options, identity, grants=grants)
            if duplicate is not None:
                return duplicate
            now = self._now()
            self._prune(now)
            key = options.idempotency_key
            key_digest = hashlib.sha256(key.encode("ascii")).hexdigest() if key else None
            domain = scope.owner_digest, scope.project_hash, key_digest
            owner_records = [
                record
                for record in self._records.values()
                if record.owner_digest == scope.owner_digest
            ]
            project_records = [r for r in owner_records if r.project_hash == scope.project_hash]
            owned_ids = self._owned_ids()
            if (
                len(self._records) >= MAX_RECORDS
                or len(owner_records) >= MAX_OWNER_RECORDS
                or len(project_records) >= MAX_PROJECT_RECORDS
                or len(owned_ids) >= MAX_ACTIVE
                or sum(r.receipt_id in owned_ids for r in owner_records) >= MAX_OWNER_ACTIVE
                or sum(r.receipt_id in owned_ids for r in project_records) >= MAX_PROJECT_ACTIVE
                or self.charged_metadata_bytes + 2 * METADATA_BYTES > METADATA_QUOTA
                or (options.retain_result and not self._result_capacity(scope, 2 * RESULT_BYTES))
            ):
                raise ReceiptCapacity("receipt_capacity")
            receipt_id, token = uuid4().hex, uuid4().hex
            record = ReceiptRecord(
                3,
                1,
                self.store_id,
                receipt_id,
                scope.owner_digest,
                scope.project_hash,
                key_digest,
                len(key) if key else 0,
                identity.digest,
                identity.encoded_bytes,
                required,
                now,
                now,
                now + TTL_SECONDS,
                "pending",
                "intent_committed",
                True,
                options.retain_result,
                "pending" if options.retain_result else "not_requested",
                0,
                None,
                None,
                None,
                (),
                0,
                self._wall_high_water(),
            )
            self._encode(record)
            self._sizes[receipt_id] = 0
            with self._owners_lock:
                self._owned[receipt_id] = _Owned(token, True, False, set())
            try:
                self._commit(record)
            except Exception:
                # The synchronous write has exited and no handler/worker was admitted.
                # Poisoning preserves any published key on disk for exclusive reopen.
                with self._owners_lock:
                    self._owned.pop(receipt_id)
                if not self._poisoned:
                    self._sizes.pop(receipt_id)
                raise
            if key_digest is not None:
                self._keys[domain] = receipt_id
            return ReceiptInvocation(self, receipt_id, token)

    def lookup(
        self,
        scope: ReceiptScope,
        options: ReceiptOptions,
        identity: RequestIdentity,
        *,
        grants: frozenset[str],
    ) -> ReceiptView | None:
        """Find only a duplicate before live target selection; never create intent.

        An expired receipt with an actual live wrapper/worker still pins the key.
        Its duplicate metadata suppresses dispatch even though public get is expired.
        """
        options.validate()
        permission_floor(grants)
        with self._lock:
            self._check()
            if options.store_id != self.store_id or options.project_hash != scope.project_hash:
                raise ReceiptNotFound("receipt_not_found")
            now = self._now()
            key = options.idempotency_key
            if key is None:
                return None
            digest = hashlib.sha256(key.encode("ascii")).hexdigest()
            receipt_id = self._keys.get((scope.owner_digest, scope.project_hash, digest))
            if receipt_id is None:
                return None
            record = self._records[receipt_id]
            self._authorize(record, scope, grants)
            if now >= record.expires_at and receipt_id not in self._owned_ids():
                return None
            if (
                record.fingerprint,
                record.fingerprint_bytes,
                record.key_length,
                record.retain_result,
            ) != (
                identity.digest,
                identity.encoded_bytes,
                len(key),
                options.retain_result,
            ):
                raise ReceiptConflict("idempotency_conflict")
            record = self._read_record(receipt_id)
            self._authorize(record, scope, grants)
            now = self._now()
            if now >= record.expires_at and receipt_id not in self._owned_ids():
                return None
            return self._view(self._expire_result(record, now))

    def _read_record(self, receipt_id: str) -> ReceiptRecord:
        try:
            record = self._decode(self._files.read(receipt_id), receipt_id)
            if record != self._records[receipt_id]:
                raise ReceiptUnavailable("receipt_record_changed_outside_writer")
            return record
        except (OSError, ReceiptUnavailable) as error:
            self._poisoned = True
            raise ReceiptUnavailable("receipt_lookup_unavailable") from error

    def get(
        self,
        scope: ReceiptScope,
        *,
        store_id: str,
        grants: frozenset[str],
        receipt_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ReceiptView:
        with self._lock:
            self._check()
            self._now()
            if store_id != self.store_id or (receipt_id is None) == (idempotency_key is None):
                raise ReceiptNotFound("receipt_not_found")
            if idempotency_key is not None:
                options = ReceiptOptions(store_id, scope.project_hash, idempotency_key)
                options.validate()
                digest = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
                receipt_id = self._keys.get((scope.owner_digest, scope.project_hash, digest))
            if (
                type(receipt_id) is not str
                or not HEX_ID.fullmatch(receipt_id)
                or receipt_id not in self._records
            ):
                raise ReceiptNotFound("receipt_not_found")
            self._authorize(self._records[receipt_id], scope, grants)
            record = self._read_record(receipt_id)
            self._authorize(record, scope, grants)
            now = self._now()
            if now >= record.expires_at:
                raise ReceiptNotFound("receipt_not_found")
            return self._view(self._expire_result(record, now))

    def _read_result_record(self, record: ReceiptRecord) -> bytes:
        try:
            raw = self._files.read_result(record.receipt_id)
            if (
                len(raw) != record.result_bytes
                or hashlib.sha256(raw).hexdigest() != record.result_sha256
            ):
                raise ReceiptUnavailable("receipt_result_hash_mismatch")
            return raw
        except (OSError, ReceiptUnavailable) as error:
            self._poisoned = True
            raise ReceiptUnavailable("receipt_result_unavailable") from error

    def read_result(
        self,
        scope: ReceiptScope,
        *,
        store_id: str,
        grants: frozenset[str],
        receipt_id: str,
    ) -> bytes | None:
        """Return exact retained bytes only after scoped metadata and hash checks.

        The caller owns bounded delivery memory and rechecks the current credential
        lease after this synchronous I/O, immediately before exposing any bytes.
        """
        with self._lock:
            self.get(scope, store_id=store_id, grants=grants, receipt_id=receipt_id)
            record = self._records[receipt_id]
            if record.result_reason != "available":
                return None
            self._authorize(record, scope, grants)
            raw = self._read_result_record(record)
            self._authorize(record, scope, grants)
            now = self._now()
            if now >= record.expires_at:
                raise ReceiptNotFound("receipt_not_found")
            if self._expire_result(record, now).result_reason != "available":
                return None
            return raw

    def _invocation(self, receipt_id: str, token: str) -> _Owned:
        """Validate under owners lock; callers must also hold it to mutate the entry."""
        with self._owners_lock:
            self._check()
            owned = self._owned.get(receipt_id)
            if owned is None or owned.token != token or not owned.wrapper_open:
                raise ReceiptInvalid("receipt_invocation_closed")
            return owned

    def _extend_floor(
        self, receipt_id: str, token: str, floor: frozenset[str], grants: frozenset[str]
    ) -> None:
        with self._lock:
            self._invocation(receipt_id, token)
            permission_floor(floor)
            permission_floor(grants)
            record = self._records[receipt_id]
            combined = frozenset(record.permissions) | floor
            if not combined <= grants:
                raise ReceiptNotFound("receipt_not_found")
            if combined != frozenset(record.permissions):
                self._commit(
                    replace(record, permissions=permission_floor(combined), updated_at=self._now())
                )

    def _observe_attempt(
        self,
        receipt_id: str,
        token: str,
        attempt: ReceiptAttempt,
        *,
        new_attempt: bool,
    ) -> None:
        with self._lock:
            self._invocation(receipt_id, token)
            if type(new_attempt) is not bool:
                raise ReceiptInvalid("invalid_receipt_attempt")
            self._validate_attempt(attempt)
            record = self._records[receipt_id]
            previous = next(
                (
                    item
                    for item in record.attempts
                    if (item.kind, item.attempt_id) == (attempt.kind, attempt.attempt_id)
                ),
                None,
            )
            recent = tuple(
                item
                for item in record.attempts
                if (item.kind, item.attempt_id) != (attempt.kind, attempt.attempt_id)
            )
            if previous is not None:
                if (
                    previous.session_id is not None
                    and attempt.session_id is not None
                    and previous.session_id != attempt.session_id
                ) or (
                    previous.generation is not None
                    and attempt.generation is not None
                    and previous.generation != attempt.generation
                ):
                    raise ReceiptInvalid("receipt_attempt_target_changed")
                attempt = replace(
                    attempt,
                    dispatched=previous.dispatched or attempt.dispatched,
                    observed=previous.observed or attempt.observed,
                    session_id=(
                        previous.session_id if attempt.session_id is None else attempt.session_id
                    ),
                    generation=(
                        previous.generation if attempt.generation is None else attempt.generation
                    ),
                )
            self._commit(
                replace(
                    record,
                    attempts=(*recent, attempt)[-MAX_ATTEMPTS:],
                    attempt_count=min(
                        record.attempt_count + (new_attempt and previous is None),
                        MAX_ATTEMPT_COUNT,
                    ),
                    updated_at=self._now(),
                )
            )

    def _completion_result(
        self,
        record: ReceiptRecord,
        now: float,
        result: bytes | None,
        result_reason: str | None,
    ) -> ReceiptRecord:
        """Persist caller-owned, already encoded public bytes without interpretation."""
        if not record.retain_result:
            return record
        if record.status != "completed":
            return replace(
                record,
                result_reason=(
                    "not_dispatched" if record.status == "not_dispatched" else "observation_lost"
                ),
            )
        if result is None:
            return replace(record, result_reason=result_reason or "observation_lost")
        if len(result) > RESULT_BYTES:
            return replace(record, result_reason="result_too_large")
        expires = min(now + RESULT_TTL_SECONDS, record.expires_at)
        if now >= expires:
            return replace(
                record,
                result_reason="result_expired",
                result_observed_at=now,
                result_expires_at=expires,
            )
        available = replace(
            record,
            result_reason="available",
            result_bytes=len(result),
            result_sha256=hashlib.sha256(result).hexdigest(),
            result_observed_at=now,
            result_expires_at=expires,
        )
        self._encode(available)
        try:
            self._files.write_result(record.receipt_id, result)
        except (OSError, ReceiptUnavailable) as error:
            self._poisoned = True
            raise ReceiptUnavailable("receipt_result_commit_uncertain") from error
        return available

    def _finish(
        self,
        receipt_id: str,
        token: str,
        status: Status,
        cause: str,
        result: bytes | None,
        result_reason: str | None,
    ) -> ReceiptView:
        with self._lock:
            with self._owners_lock:
                owned = self._owned.get(receipt_id)
                if owned is None or owned.token != token or not owned.wrapper_open:
                    raise ReceiptInvalid("receipt_invocation_closed")
                if status not in {"completed", "unknown", "not_dispatched"}:
                    raise ReceiptInvalid("invalid_receipt_completion")
                if (status == "completed" and not owned.entered) or (
                    status == "not_dispatched" and (owned.entered or owned.workers)
                ):
                    raise ReceiptInvalid("invalid_receipt_completion")
            if status == "unknown" and cause not in _UNKNOWN_CAUSES:
                raise ReceiptInvalid("invalid_receipt_cause")
            record = self._records[receipt_id]
            permitted_reasons = (
                {
                    "completed": {"observation_lost", "result_too_large"},
                    "unknown": {"observation_lost"},
                    "not_dispatched": {"not_dispatched"},
                }[status]
                if record.retain_result
                else {"not_requested"}
            )
            if (
                (result is not None and type(result) is not bytes)
                or (result is not None and (not record.retain_result or status != "completed"))
                or (result is not None and result_reason is not None)
                or (result_reason is not None and result_reason not in permitted_reasons)
            ):
                raise ReceiptInvalid("invalid_receipt_result")
            final_cause = (
                cause
                if status == "unknown"
                else {
                    "completed": "observed",
                    "not_dispatched": "pre_entry",
                }[status]
            )
            try:
                self._check()
                now = self._now()
                record = replace(record, status=status, updated_at=now, cause=final_cause)
                record = self._completion_result(record, now, result, result_reason)
                self._commit(record)
                return self._view(record)
            finally:
                with self._owners_lock:
                    owned.wrapper_open = False
                    self._settle(receipt_id)

    def _release_worker(self, receipt_id: str, token: str) -> None:
        with self._owners_lock:
            owned = self._owned.get(receipt_id)
            if owned is not None:
                owned.workers.discard(token)
                self._settle(receipt_id)

    def _settle(self, receipt_id: str) -> None:
        """Called with _owners_lock held, without touching metadata or files."""
        owned = self._owned[receipt_id]
        if not owned.wrapper_open and not owned.workers:
            self._owned.pop(receipt_id)

    def close(self) -> None:
        with self._lock:
            with self._owners_lock:
                if self._owned:
                    raise ReceiptUnavailable("receipt_workers_still_owned")
                self._closed = True
            self._files.close()
