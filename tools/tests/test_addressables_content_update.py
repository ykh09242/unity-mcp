"""A valid address and cached bundle cannot substitute for the loaded generation."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_addressables_content_update import require_player_observation


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
