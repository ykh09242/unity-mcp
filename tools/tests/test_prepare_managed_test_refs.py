"""Managed test references must be verified before publishing their bytes."""

import hashlib
import io
import zipfile

import pytest

import prepare_managed_test_refs as preparation


def test_verified_archive_publishes_only_requested_reference(monkeypatch, tmp_path):
    # Given a trusted fixture archive with an unrelated traversal member.
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(preparation.NUNIT_MEMBER, b"fixture assembly")
        archive.writestr("../unrelated.txt", b"must not be extracted")
    payload = content.getvalue()
    monkeypatch.setattr(preparation, "NUNIT_SHA256", hashlib.sha256(payload).hexdigest())
    destination = tmp_path / "references/nunit.framework.dll"

    # When the verified reference is published.
    preparation.publish_reference(payload, destination)

    # Then no other archive content reaches the filesystem.
    assert destination.read_bytes() == b"fixture assembly"
    assert sorted(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
    ) == ["references/nunit.framework.dll"]


def test_changed_archive_preserves_existing_reference(tmp_path):
    # Given an existing owned reference and unverified replacement bytes.
    destination = tmp_path / "nunit.framework.dll"
    destination.write_bytes(b"previous reference")

    # When a download differs from the reviewed package digest.
    with pytest.raises(ValueError, match="digest"):
        preparation.publish_reference(b"altered archive", destination)

    # Then publication has not touched the previous bytes.
    assert destination.read_bytes() == b"previous reference"
