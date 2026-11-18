"""Addressables entry deltas update the same graph facts as a full candidate census."""

import pytest

from tests.test_reference_graph_reconcile import setup


def catalog_event(editor, **fields):
    editor.events.append(
        {"revision": len(editor.events) + 1, "kind": "addressables_catalog", **fields}
    )


@pytest.mark.asyncio
async def test_single_entry_updates_address_and_label_lookups_without_inventory(setup):
    store, workspace, editor, engine = setup
    workspace.set_policy({"exclude": ["Assets/Unrelated/**"]}, expected_version=0)
    editor.inventory_page_size = 256

    def keys(prefix):
        return [
            {"scheme": "addressables", "value": prefix + "Address"},
            {"scheme": "addressables_label", "value": prefix + "Label"},
        ]

    target = editor.add(1, "Assets/Target.asset", keys=keys("Old"))
    callers = {}
    for index, key in enumerate(keys("Old") + keys("New"), start=2):
        name = key["value"]
        callers[name] = editor.add(
            index,
            f"Assets/{name}.cs",
            "using UnityEngine.AddressableAssets; "
            f'class {name} {{ void M() {{ Addressables.LoadAssetAsync<object>("{name}"); }} }}',
        )
    for index in range(6, 1002):
        editor.add(index, f"Assets/Unrelated/{index}.asset")
    assert await engine.reconcile()

    def matching_keys():
        return {
            name
            for name, owner in callers.items()
            if {edge["target"] for edge in store.query(owner)["edges"]} == {target}
        }

    assert matching_keys() == {"OldAddress", "OldLabel"}
    editor.assets[target[6:]]["keys"] = keys("New")
    catalog_event(editor, entry_guid=target[6:])
    editor.calls.clear()
    assert await engine.reconcile()
    assert matching_keys() == {"NewAddress", "NewLabel"}
    assert [params for action, params in editor.calls if action == "asset"] == [
        {"guid": target[6:]}
    ]
    assert not any(action in {"inventory", "dependencies", "source"} for action, _ in editor.calls)

    # An older producer (or an unscoped group, batch or folder event) supplies no GUID.
    editor.assets[target[6:]]["keys"] = keys("Old")
    catalog_event(editor)
    editor.calls.clear()
    assert await engine.reconcile()
    assert matching_keys() == {"OldAddress", "OldLabel"}
    assert [p["offset"] for action, p in editor.calls if action == "inventory"] == [
        0,
        256,
        512,
        768,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["malformed", "missing", "different-guid", "missing-keys"])
async def test_uncertain_entry_identity_falls_back_to_census(setup, evidence):
    _, workspace, editor, engine = setup
    target = editor.add(1, "Assets/Target.asset")
    assert await engine.reconcile()
    editor.assets[target[6:]]["keys"] = [{"scheme": "addressables", "value": "new-address"}]
    guid = "invalid" if evidence == "malformed" else "f" * 32
    catalog_event(editor, entry_guid=guid)
    if evidence in {"different-guid", "missing-keys"}:
        original = engine.collect

        async def collect(action, **params):
            if action == "asset" and params.get("guid") == guid:
                asset = editor.assets[target[6:]].copy()
                if evidence == "missing-keys":
                    asset["guid"] = guid
                    del asset["keys"]
                return asset
            return await original(action, **params)

        engine.collect = collect
    editor.calls.clear()
    assert await engine.reconcile()
    assert any(action == "inventory" for action, _ in editor.calls)
    assert workspace.catalog()[0]["keys"] == editor.assets[target[6:]]["keys"]
