"""Reviewed artifact hashes remain independent of mutable registry metadata."""

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from tools import unity_ci_packages as packages
from tools import review_unity_registry_pins as review


def test_unreviewed_registry_version_fails_before_network_or_extraction(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(packages, "_read_registry_bytes", lambda *args: calls.append(args))
    with pytest.raises(packages.PreparationError, match="reviewed.*pin"):
        packages.fetch_registry_package("com.unity.unreviewed", "1.2.3", tmp_path / "output")
    assert calls == []
    assert not (tmp_path / "output").exists()


def test_registry_metadata_and_archive_cannot_replace_reviewed_digest(tmp_path, monkeypatch):
    name, version = "com.unity.nuget.newtonsoft-json", "3.2.2"
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as stream:
        payload = b"substituted archive with matching registry SHA1"
        entry = tarfile.TarInfo("package/substituted.txt")
        entry.size = len(payload)
        stream.addfile(entry, io.BytesIO(payload))
    content = archive.getvalue()
    metadata = {
        "versions": {
            version: {
                "name": name,
                "version": version,
                "dist": {
                    "tarball": "https://packages.unity.com/substitute.tgz",
                    "shasum": hashlib.sha1(content).hexdigest(),
                },
            }
        }
    }
    responses = iter((json.dumps(metadata).encode(), content))
    monkeypatch.setattr(packages, "_read_registry_bytes", lambda *args: next(responses))
    with pytest.raises(packages.PreparationError, match="(?i)reviewed.*mismatch"):
        packages.fetch_registry_package(name, version, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_committed_pins_cover_exact_registry_profile_inventory():
    profiles = packages.read_json(packages.PROFILES)
    identities = {
        f"{name}@{spec['version']}"
        for section in ("profiles", "optionalProfiles")
        for profile in profiles[section].values()
        for name, spec in profile["packages"].items()
        if spec["source"] == "registry"
    }
    pins = json.loads(Path(packages.REGISTRY_PINS).read_text(encoding="utf-8"))
    assert pins["schemaVersion"] == 1
    assert set(pins["artifacts"]) == identities
    for identity in identities:
        name, version = identity.split("@")
        assert packages.registry_pin(name, version) == pins["artifacts"][identity]


@pytest.mark.parametrize("digest", [None, "", "a" * 40, "G" * 64, 12])
def test_invalid_reviewed_pin_is_rejected(tmp_path, monkeypatch, digest):
    lock = tmp_path / "pins.json"
    lock.write_text(json.dumps({"schemaVersion": 1, "artifacts": {"com.unity.test@1.0.0": digest}}))
    monkeypatch.setattr(packages, "REGISTRY_PINS", lock)
    with pytest.raises(packages.PreparationError, match="reviewed.*pin"):
        packages.registry_pin("com.unity.test", "1.0.0")


@pytest.mark.parametrize("artifacts", [None, [], "invalid"])
def test_invalid_pin_inventory_fails_closed(tmp_path, monkeypatch, artifacts):
    lock = tmp_path / "pins.json"
    lock.write_text(json.dumps({"schemaVersion": 1, "artifacts": artifacts}))
    monkeypatch.setattr(packages, "REGISTRY_PINS", lock)
    with pytest.raises(packages.PreparationError, match="reviewed.*pin"):
        packages.registry_pin("com.unity.test", "1.0.0")


def test_review_proposal_hashes_actual_downloads_and_does_not_write_accepted_lock(
    tmp_path, monkeypatch
):
    profile = tmp_path / "profiles.json"
    profile.write_text(
        json.dumps(
            {
                "profiles": {
                    "test": {
                        "packages": {
                            "com.unity.test": {"source": "registry", "version": "1.0.0"},
                            "com.unity.editor": {"source": "editor"},
                        }
                    }
                }
            }
        )
    )
    content = b"official fixture bytes"
    calls = []

    def download(name, version):
        calls.append((name, version))
        return content

    monkeypatch.setattr(packages, "download_registry_archive", download)
    accepted = tmp_path / "accepted.json"
    accepted.write_text("accepted original")
    monkeypatch.setattr(packages, "REGISTRY_PINS", accepted)
    with pytest.raises(packages.PreparationError, match="review the diff"):
        review.propose(profile, accepted)
    assert calls == []
    output = tmp_path / "proposal.json"
    review.propose(profile, output)
    assert json.loads(output.read_text()) == {
        "schemaVersion": 1,
        "artifacts": {"com.unity.test@1.0.0": hashlib.sha256(content).hexdigest()},
    }
    assert calls == [("com.unity.test", "1.0.0")]
    assert accepted.read_text() == "accepted original"
    with pytest.raises(FileExistsError):
        review.propose(profile, output)
    assert calls == [("com.unity.test", "1.0.0")]


def test_failed_review_download_cannot_produce_completed_proposal(tmp_path, monkeypatch):
    profile = tmp_path / "profiles.json"
    profile.write_text(
        json.dumps(
            {
                "profiles": {
                    "test": {
                        "packages": {
                            "com.unity.test": {"source": "registry", "version": "1.0.0"},
                        }
                    }
                }
            }
        )
    )

    def fail(*args):
        raise packages.PreparationError("publisher download failed")

    monkeypatch.setattr(packages, "download_registry_archive", fail)
    output = tmp_path / "proposal.json"
    with pytest.raises(packages.PreparationError, match="publisher download failed"):
        review.propose(profile, output)
    assert not output.exists()
