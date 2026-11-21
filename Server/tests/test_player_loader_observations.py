import pytest

from services.verification.loader_observations import import_player_loader_capture


def test_capture_is_build_bound_and_reimport_is_idempotent():
    identity = dict(
        job_id="job", build_id="build", payload_hash="payload", build_source_revision="source"
    )
    run = {"execution_environment": "player", "job_id": "job", "reproduction": dict(identity)}
    expected_keys = [
        ("resources", "Inventory/Icon"),
        ("resources", "Other"),
        ("addressables", "Icon"),
    ]
    missing = import_player_loader_capture(run, expected=identity, expected_keys=expected_keys)
    assert (
        missing["coverage"]["state"] == "capture_unavailable"
        and missing["coverage"]["dropped"] is None
    )
    run["loader_capture"] = {
        **identity,
        "schema_version": 1,
        "dropped": 2,
        "observations": [
            {
                "kind": "load",
                "sequence": 1,
                "scheme": "resources",
                "key": "inventory/icon",
                "owner_path": "Assets/Inventory.cs",
                "outcome": "loaded",
                "asset_name": "Icon",
            },
            {
                "kind": "load",
                "sequence": 2,
                "scheme": "addressables",
                "key": "icon",
                "owner_path": "Assets/Inventory.cs",
                "outcome": "loaded",
            },
        ],
    }
    observed = import_player_loader_capture(run, expected=identity, expected_keys=expected_keys)
    assert len(observed["observations"]) == 2 and observed["coverage"]["dropped"] == 2
    assert observed["observations"][0]["key"] == "inventory/icon"
    assert observed["coverage"]["unobserved_keys"] == [
        {"scheme": "addressables", "key": "Icon"},
        {"scheme": "resources", "key": "Other"},
    ]
    assert (
        import_player_loader_capture(
            run, expected=identity, expected_keys=expected_keys, previous=observed
        )
        == observed
    )
    with pytest.raises(ValueError, match="identity_mismatch"):
        import_player_loader_capture(run, expected={**identity, "payload_hash": "different"})
    run["loader_capture"]["observations"][0]["outcome"] = "no_object"
    with pytest.raises(ValueError, match="capture_conflict"):
        import_player_loader_capture(run, expected=identity, previous=observed)
    run["loader_capture"]["observations"][0]["sequence"] = 2
    with pytest.raises(ValueError, match="invalid_player_loader_capture"):
        import_player_loader_capture(run, expected=identity)
