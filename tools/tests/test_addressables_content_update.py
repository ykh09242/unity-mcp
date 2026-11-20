"""A valid address and cached bundle cannot substitute for the loaded generation."""

from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_addressables_content_update import (
    require_player_observation,
    require_selective_observation,
)


def warm_receipt():
    return {
        "session": "run",
        "unity_version": "6000.0.69f1",
        "build_guid": "3" * 32,
        "observed": {
            "stage": "complete",
            "location_count": 1,
            "catalog_ids": ["locator"],
            "location_id": "marker",
            "loaded_marker": "old-run",
            "update_count": 1,
            "updated_catalog_count": 0,
            "download_bytes_before": 0,
            "download_bytes_after": 0,
            "bundles_before": [{"hash": "a" * 32, "cached": True}],
            "bundles_after": [{"hash": "a" * 32, "cached": True}],
        },
    }


def verify(receipt, requests=()):
    return require_player_observation(
        receipt, "run", "6000.0.69f1", "old", False, {1}, False, requests
    )


def test_stale_catalog_can_load_previous_cached_marker_with_update_available():
    assert verify(warm_receipt(), ["catalog.hash"]) == "a" * 32


@pytest.mark.parametrize(
    "change",
    [
        {"loaded_marker": "new-run"},
        {"location_count": 0},
        {"update_count": 0},
        {"bundles_before": [{"hash": "a" * 32, "cached": False}]},
        {"bundles_after": [{"hash": "b" * 32, "cached": True}]},
        {"download_bytes_before": 100},
    ],
)
def test_independent_marker_location_catalog_and_cache_evidence(change):
    receipt = warm_receipt()
    receipt["observed"].update(change)
    with pytest.raises(ValueError):
        verify(receipt)


def test_warm_case_fails_if_player_requested_any_remote_bundle():
    with pytest.raises(ValueError):
        verify(warm_receipt(), ["old.bundle"])


@pytest.fixture
def selective_case():
    primary, secondary = "1" * 64, "2" * 64
    both = [primary, secondary]

    def marker(value, bundle, pending, cold=False):
        return dict(
            warm_receipt()["observed"],
            loaded_marker=value + "-run",
            catalog_ids=both,
            pending_catalog_ids=pending,
            update_count=len(pending),
            bundles_before=[{"hash": bundle * 32, "cached": not cold}],
            bundles_after=[{"hash": bundle * 32, "cached": True}],
            download_bytes_before=42 if cold else 0,
        )

    def rejected(error, pending):
        return {
            "stage": "update_catalog",
            "error": error,
            "updated_catalog_count": 0,
            "pending_catalog_ids": pending,
        }

    data = {
        "primary_id": primary,
        "secondary_id": secondary,
        "primary_before": marker("old", "a", []),
        "primary_after": marker("old", "a", [primary]),
        "secondary_before": marker("secondary-old", "b", []),
        "selected": dict(
            marker("secondary-new", "c", both, True),
            updated_catalog_count=1,
            updated_catalog_id=secondary,
            installed_hash_id=sha256(b"published").hexdigest(),
        ),
        "ambiguous": rejected("multiple_pending_catalogs", both),
        "unknown": rejected("catalog_not_found", both),
        "duplicate": dict(rejected("ambiguous_catalog_id", []), stage="check_catalogs"),
        "repeated": rejected("catalog_not_pending", [primary]),
    }
    catalogs = {
        "primary": {"new": {"name": "primary.hash"}},
        "secondary": {"new": {"name": "secondary/catalog.hash", "published_hash": "published"}},
    }
    before = ["secondary/catalog.json", "secondary/old.bundle"]
    after = [
        "primary.hash",
        "secondary/catalog.hash",
        "secondary/catalog.json",
        "secondary/new.bundle",
    ]
    return data, "run", "a" * 32, catalogs, before, after


def test_selective_receipt_preserves_unselected_pending_catalog(selective_case):
    require_selective_observation(*selective_case)


@pytest.mark.parametrize(
    ("row", "change"),
    [
        ("selected", {"updated_catalog_id": "1" * 64}),
        ("selected", {"updated_catalog_count": 2}),
        ("selected", {"installed_hash_id": "f" * 64}),
        ("primary_after", {"loaded_marker": "new-run"}),
        ("primary_after", {"bundles_after": [{"hash": "d" * 32, "cached": True}]}),
        ("primary_after", {"pending_catalog_ids": []}),
        ("ambiguous", {"updated_catalog_count": 1}),
        ("repeated", {"error": None}),
    ],
)
def test_selective_receipt_rejects_wrong_generation_or_selection(selective_case, row, change):
    selective_case[0][row].update(change)
    with pytest.raises(ValueError):
        require_selective_observation(*selective_case)


def test_selective_receipt_rejects_unselected_bundle_fetch(selective_case):
    selective_case[-1].append("primary-new.bundle")
    with pytest.raises(ValueError):
        require_selective_observation(*selective_case)
