"""Review the exact project settings and dependency lock admitted as candidate inputs."""

from hashlib import sha256
import json

from models.script_quality import QualityOptions
from services.verification.input_chunks import CONFIG_PATHS, LOCK_PATH, Digest, decode_object


class PackageLockEvidence(QualityOptions):
    raw_sha256: Digest
    semantic_sha256: Digest


def package_lock_evidence(content):
    document = decode_object(content)
    if not isinstance(document.get("dependencies"), dict):
        raise ValueError("candidate_invalid_package_lock")
    # Package versions, IDs and hashes are strings; depth is an integer. Reject
    # floats rather than lose precision when comparing nonstandard lock schemas.
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, float):
            raise ValueError("candidate_invalid_package_lock_number")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return PackageLockEvidence(
        raw_sha256=sha256(content).hexdigest(),
        semantic_sha256=sha256(canonical.encode()).hexdigest(),
    )


class ConfigurationEvidence:
    def __init__(self):
        self.inputs = {}
        self.lock = None

    def observe(self, path, data):
        if path not in CONFIG_PATHS:
            return
        if path == LOCK_PATH:
            self.lock = package_lock_evidence(data)
            self.inputs[path] = self.lock.raw_sha256
        else:
            self.inputs[path] = sha256(data).hexdigest()

    def registration(self, selected_inputs):
        if self.lock and LOCK_PATH in selected_inputs:
            if selected_inputs[LOCK_PATH] != self.lock.raw_sha256:
                raise ValueError("candidate_package_lock_digest_mismatch")
            return {"package_lock_evidence": self.lock.model_dump()}
        return {}

    def compare(self, candidate):
        return configuration_changes(self.inputs, candidate.inputs, self.lock, candidate.lock)


def configuration_changes(before, after, old_lock, new_lock):
    rows = []
    for path in sorted(CONFIG_PATHS):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if path != LOCK_PATH:
            effect = "render_settings_changed"
        elif old and new and (old_lock is None or new_lock is None):
            effect = "package_resolution_unverified"
        elif old_lock and new_lock and old_lock.semantic_sha256 == new_lock.semantic_sha256:
            effect = "representation_only"
        else:
            effect = "package_resolution_changed"
        rows.append(
            {"path": path, "baseline_sha256": old, "candidate_sha256": new, "effect": effect}
        )
    return rows
