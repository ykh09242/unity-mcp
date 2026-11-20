"""Import bounded, opt-in Player hooks into one candidate's change report."""

from hashlib import sha256
import json


def _valid_row(row, sequence):
    limits = {"scheme": 64, "key": 512, "owner_path": 512}
    if not isinstance(row, dict) or set(row) - {
        *limits,
        "sequence",
        "kind",
        "outcome",
        "asset_name",
    }:
        return False
    name = row.get("asset_name")
    return (
        type(row.get("sequence")) is int
        and row["sequence"] == sequence
        and row.get("kind") in ("load", "acquire")
        and row.get("outcome") in ("loaded", "no_object", "unknown")
        and all(isinstance(row.get(k), str) and len(row[k]) <= n for k, n in limits.items())
        and (name is None or isinstance(name, str) and len(name) <= 256)
    )


def import_player_loader_capture(run, *, expected, expected_keys=(), previous=None):
    fields = ("job_id", "build_id", "build_source_revision", "payload_hash")
    identity = {field: expected.get(field) for field in fields}
    reproduction = run.get("reproduction") or {}
    if (
        not all(isinstance(value, str) and value for value in identity.values())
        or run.get("execution_environment") != "player"
        or run.get("job_id") != identity["job_id"]
        or any(reproduction.get(field) != identity[field] for field in fields[1:])
    ):
        raise ValueError("player_loader_identity_mismatch")
    capture = run.get("loader_capture")
    rows = []
    dropped = None
    if capture is not None:
        if not isinstance(capture, dict) or any(capture.get(k) != v for k, v in identity.items()):
            raise ValueError("player_loader_identity_mismatch")
        rows = capture.get("observations")
        dropped = capture.get("dropped")
        if (
            type(capture.get("schema_version")) is not int
            or capture["schema_version"] != 1
            or not isinstance(rows, list)
            or len(rows) > 128
            or type(dropped) is not int
            or dropped < 0
            or not all(_valid_row(row, i) for i, row in enumerate(rows, 1))
        ):
            raise ValueError("invalid_player_loader_capture")
    digest = sha256(json.dumps(capture, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if previous is not None and (
        previous.get("identity") != identity or previous.get("capture_sha256") != digest
    ):
        raise ValueError("player_loader_capture_conflict")
    seen = {(row["scheme"], row["key"]) for row in rows}
    return {
        "identity": identity,
        "capture_sha256": digest,
        "observations": [dict(row) for row in rows],
        "coverage": {
            "state": "capture_unavailable" if capture is None else "instrumented_hooks_only",
            "observed_hooks": len(rows),
            "dropped": dropped,
            "unobserved_keys": [
                {"scheme": scheme, "key": key} for scheme, key in sorted(set(expected_keys) - seen)
            ],
        },
    }
