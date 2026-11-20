import unittest

import pytest

from services.verification.affected_checks import bounded_selection, select_affected_checks


def test_mapped_test_and_saved_scenario_retain_distinct_change_reasons():
    path = "Assets/UI/Shop.prefab"
    result = select_affected_checks(
        [path + ".meta", path, path],
        [{"path": path, "tests": ["shop.ready"], "scenarios": ["shop.ready"]}],
    )
    assert result["mapping_complete"] and not result["outstanding"]
    assert result["checks"] == [
        {"kind": kind, "id": "shop.ready", "reasons": [path, path + ".meta"]}
        for kind in ("scenario", "test")
    ]


def test_unmapped_failure_is_caught_only_when_broader_suite_runs():
    class Fixture(unittest.TestCase):
        def test_shop_ready(self):
            self.assertTrue(state["shop_ready"])

        def test_inventory_ready(self):
            self.assertTrue(state["inventory_ready"])

    state = {"shop_ready": True, "inventory_ready": False}
    selection = select_affected_checks(
        ["Assets/Shop.prefab", "Assets/Inventory.prefab"],
        [{"path": "Assets/Shop.prefab", "tests": ["test_shop_ready"]}],
        broader_check="full",
    )
    narrow = unittest.TestResult()
    for row in selection["checks"]:
        if row["kind"] == "test":
            Fixture(row["id"]).run(narrow)
    assert narrow.testsRun == 1 and narrow.wasSuccessful()
    assert selection["unmapped_paths"] == ["Assets/Inventory.prefab"]
    assert {"kind": "profile", "id": "full", "reasons": ["unmapped_affected_paths"]} in selection[
        "checks"
    ]
    full = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(Fixture).run(full)
    assert full.testsRun == 2 and [case.id().split(".")[-1] for case, _ in full.failures] == [
        "test_inventory_ready"
    ]


def test_no_broader_check_leaves_partial_coverage_outstanding():
    result = select_affected_checks(
        ["Assets/Shop.unity"],
        [{"path": "Assets/Shop.unity", "scenarios": ["shop"]}],
        impact_complete=False,
    )
    assert result["outstanding"] == ["impact_coverage_incomplete"]
    assert not result["mapping_complete"]
    assert select_affected_checks([], [])["outstanding"] == ["no_affected_paths"]


def test_explanation_limit_preserves_complete_mapping_coverage():
    paths = [f"Assets/{'Garden/' * 30}Plant{i}.cs" for i in range(100)]
    names = ["Plant.Ready", "Plant.Move", "Plant.Refresh", "Plant.Save"]
    selection = bounded_selection(
        select_affected_checks(
            paths, [{"path": path, "tests": names} for path in paths], broader_check="full"
        )
    )
    assert selection["evidence_truncated"] and selection["mapping_complete"]
    assert selection["outstanding"] == [] and selection["unmapped_path_count"] == 0
    assert {row["id"] for row in selection["checks"]} == set(names)
    assert all(
        row["reasons_truncated"] and row["reason_count"] == 100 for row in selection["checks"]
    )


def test_irreducible_selection_size_refuses_partial_execution():
    paths = [f"Assets/Plant{i}.cs" for i in range(32)]
    mappings = [
        {"path": path, "tests": [f"Inventory.Seasonal.Plant{index}.Test{i}" for i in range(32)]}
        for index, path in enumerate(paths)
    ]
    with pytest.raises(ValueError, match="candidate_selected_check_ids_limit"):
        bounded_selection(select_affected_checks(paths, mappings))
