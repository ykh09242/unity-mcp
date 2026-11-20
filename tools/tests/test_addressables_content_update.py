"""A valid address and cached bundle cannot substitute for the loaded generation."""

from functools import partial
from hashlib import sha256
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
import sys
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_addressables_content_update import (
    CatalogServer,
    require_player_observation,
    require_selective_observation,
    require_torn_observation,
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


def torn_case(mode):
    receipt = warm_receipt()
    cold = mode == "torn-cold"
    new_cached = mode == "torn-new-warm"
    identity = "f" * 64
    catalogs = {name: {"name": "catalog.hash", "published_hash": name} for name in ("old", "new")}
    bundles = {"old": "a" * 32, "new": "b" * 32}
    paths = {"old": "old.bundle", "new": "changed.bundle"}

    def marker(name, cached):
        return dict(
            receipt["observed"],
            catalog_ids=[identity],
            pending_catalog_ids=[identity],
            updated_catalog_count=1,
            updated_catalog_id=identity,
            installed_hash_id=sha256(name.encode()).hexdigest(),
            bundles_before=[{"hash": bundles[name], "cached": cached}],
            bundles_after=[{"hash": bundles[name], "cached": True}],
            loaded_marker=name + "-run",
            load_status="Succeeded",
            download_bytes_before=0 if cached else 52,
        )

    torn = marker("new", new_cached)
    if not new_cached:
        torn.update(
            stage="load_marker",
            error="InvalidOperationException",
            load_status="Failed",
            loaded_marker=None,
            bundles_after=None,
            download_bytes_after=-1,
        )
    receipt["deployment"] = {
        "old_bytes": 52 if cold else 0,
        "torn": torn,
        "recovered": marker("old", not cold),
        "stable": dict(
            marker("old", True), update_count=0, pending_catalog_ids=[], updated_catalog_count=0
        ),
    }

    def gets(generation, *files):
        return [
            {"generation": generation, "path": path, "status": status} for path, status in files
        ]

    traffic = {
        "baseline": gets("old", ("catalog.hash", 200)),
        "torn": gets("new", ("catalog.hash", 200), ("catalog.json", 200)),
        "rollback": gets("old", ("catalog.hash", 200), ("catalog.json", 200)),
        "stable": gets("old", ("catalog.hash", 200)),
    }
    if not new_cached:
        traffic["torn"].extend(gets("new", ("changed.bundle", 404)))
    if cold:
        traffic["rollback"].extend(gets("old", ("old.bundle", 200)))
    if mode == "torn-old-warm":
        traffic["baseline"].extend(gets("old", ("old.bundle", 200)))
    return receipt, mode, catalogs, bundles, paths, traffic


@pytest.mark.parametrize("mode", ["torn-cold", "torn-old-warm", "torn-new-warm"])
def test_torn_receipt_preserves_installed_generation_across_rollback(mode):
    require_torn_observation(*torn_case(mode))


@pytest.mark.parametrize(
    ("phase", "change"),
    [
        ("torn", {"stage": "locate_marker"}),
        ("torn", {"load_status": "Succeeded"}),
        ("torn", {"updated_catalog_count": 0}),
        ("recovered", {"loaded_marker": "new-run"}),
        ("recovered", {"installed_hash_id": sha256(b"new").hexdigest()}),
        ("stable", {"update_count": 1}),
    ],
)
def test_torn_receipt_rejects_false_failure_or_recovery(phase, change):
    case = torn_case("torn-cold")
    case[0]["deployment"][phase].update(change)
    with pytest.raises(ValueError):
        require_torn_observation(*case)


@pytest.mark.parametrize(
    "wrong_response", [{"path": "unrelated.bundle"}, {"status": 200}, {"generation": "old"}]
)
def test_missing_bundle_must_match_real_load_in_expected_generation(wrong_response):
    case = torn_case("torn-old-warm")
    case[-1]["torn"][-1].update(wrong_response)
    with pytest.raises(ValueError):
        require_torn_observation(*case)


def test_origin_withholds_one_required_bundle_and_restores_previous_bytes(tmp_path):
    for generation in ("old", "new"):
        root = tmp_path / generation
        root.mkdir()
        for path in ("catalog.json", "catalog.hash", "required.bundle", "other.bundle"):
            (root / path).write_bytes(generation.encode())
    source = {
        "session": "s",
        "directory": tmp_path / "old",
        "switch_to": tmp_path / "new",
        "rollback_to": tmp_path / "old",
        "deny_bundles": False,
        "withheld": ("new", "required.bundle"),
        "requests": [],
        "responses": [],
    }
    with HTTPServer(("127.0.0.1", 0), partial(CatalogServer, source=source)) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)

        def exchange(method, path):
            client.request(method, "/" + path)
            response = client.getresponse()
            return response.status, response.read()

        try:
            assert exchange("PUT", "switch/s")[0] == 204
            assert exchange("GET", "catalog.hash") == (200, b"new")
            assert exchange("GET", "catalog.json") == (200, b"new")
            assert exchange("GET", "required.bundle")[0] == 404
            assert exchange("GET", "other.bundle") == (200, b"new")
            assert exchange("PUT", "rollback/s")[0] == 204
            assert exchange("GET", "catalog.json") == (200, b"old")
            assert exchange("GET", "required.bundle") == (200, b"old")
            assert source["responses"][2] == {
                "path": "required.bundle",
                "generation": "new",
                "status": 404,
            }
        finally:
            client.close()
            server.shutdown()
            thread.join()
