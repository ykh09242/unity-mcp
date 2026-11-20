import pytest

from services.reference_graph.analysis import AnalysisIndex, Catalog, parse_csharp
from services.reference_graph.change_evidence import explain_asset_references


@pytest.mark.parametrize("defines", [[], ["SHOP_TEXT"]])
def test_default_analysis_leaves_extension_receiver_unresolved_without_a_host(defines):
    source = """class Shop {}
static class Loader {
    public static object Load(this Shop shop, string key) => UnityEngine.Resources.Load(key);
    public static object Load(this Shop shop, int key) => null;
}
class Caller {
    object Open(Shop shop) {
#if SHOP_TEXT
        var key = "Shop";
#else
        var key = 17;
#endif
        return shop.Load(key);
    }
}"""
    context = {
        "state": "available",
        "assembly": "Game",
        "defines": defines,
        "references": [],
        "revision": "candidate",
    }
    facts = parse_csharp("owner", "Assets/Changed.cs", source, context=context)
    result = AnalysisIndex([facts], Catalog([])).analyze("owner")
    assert {"line": 13, "call": "shop.Load", "reason": "unresolved_receiver"} in result.unresolved
    assert not result.edges


def test_prefab_move_preserves_serialized_guid_and_separates_unobserved_dynamic_key():
    guid = "b" * 32
    source = """using UnityEngine;
class Inventory {
    const string Badge = "Inventory/Badge";
    Object Open() => Resources.Load(Badge);
    Object Preview(string runtimeKey) => Resources.Load(runtimeKey);
}"""
    yaml = f"%YAML 1.1\n--- !u!114 &72\nMonoBehaviour:\n  badge: {{fileID: 1, guid: {guid}, type: 3}}\n"

    def snapshot(key, script):
        return explain_asset_references(
            asset_guid=guid,
            assets=[
                {
                    "guid": guid,
                    "path": f"Assets/Resources/{key}.prefab",
                    "keys": [{"scheme": "resources", "value": key}],
                }
            ],
            scripts={"Assets/Inventory.cs": script},
            serialized={"Assets/Market.unity": yaml},
            previous_keys=["Inventory/Badge"],
        )

    original = snapshot("Inventory/Badge", source)
    broken = snapshot("Market/Badge", source)
    repaired = snapshot("Market/Badge", source.replace("Inventory/Badge", "Market/Badge"))
    for observed, state in [
        (original, "catalog_match"),
        (broken, "key_not_found"),
        (repaired, "catalog_match"),
    ]:
        rows = {row["kind"]: row for row in observed["findings"]}
        assert rows["serialized_guid"]["state"] == "target_guid_retained"
        assert (
            rows["serialized_guid"]["path"] == "Assets/Market.unity"
            and rows["serialized_guid"]["line"] == 4
        )
        assert rows["loader_key"]["state"] == state and rows["loader_key"]["line"] == 4
        assert rows["dynamic_key"]["state"] == "unresolved" and rows["dynamic_key"]["line"] == 5
        assert "guid" not in rows["dynamic_key"]


@pytest.mark.parametrize("provider", ["not_installed", "not_configured"])
def test_absent_addressables_and_reconfigured_catalog_preserve_guid_and_label_evidence(provider):
    guid = "1" * 32
    catalog = Catalog([{"guid": guid, "path": "Assets/Badge.prefab", "addressables": provider}])
    assert catalog.resolve("addressables", "badges") == (set(), "provider_unavailable")
    assert catalog.resolve("guid", guid) == ({"asset:" + guid}, None)
    catalog.update(
        [
            {
                "guid": n * 32,
                "path": f"Assets/{name}.prefab",
                "addressables": "available",
                "keys": [
                    {"scheme": "addressables", "value": name},
                    {"scheme": "addressables_label", "value": "badges"},
                ],
            }
            for n, name in (("1", "InventoryBadge"), ("2", "MarketBadge"))
        ]
    )
    assert catalog.resolve("addressables", "OldBadge") == (set(), "key_not_found")
    assert catalog.resolve("guid", guid) == ({"asset:" + guid}, None)
    assert catalog.resolve("addressables", "badges", "all") == (
        {"asset:" + n * 32 for n in "12"},
        None,
    )
    assert catalog.resolve("addressables", "badges") == (set(), "ambiguous_key")
