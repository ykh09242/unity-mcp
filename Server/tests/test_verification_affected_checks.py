import unittest

from services.verification.affected_checks import select_affected_checks


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
