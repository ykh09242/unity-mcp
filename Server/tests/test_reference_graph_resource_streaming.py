"""LoadAll starts producing evidence before traversing its complete catalog fanout."""

import pytest

from services.reference_graph.analysis import AnalysisIndex, Catalog, parse_csharp
from services.reference_graph.store import Edge


class ObservedKeys(list):
    reads = 0

    def __getitem__(self, index):
        self.reads += 1
        return super().__getitem__(index)


def make_index(size=2000):
    assets = [
        {
            "guid": f"{i:032x}",
            "path": f"Assets/{i}",
            "keys": [{"scheme": "resources", "value": f"Folder/{i:04}"}],
        }
        for i in reversed(range(size))
    ]
    catalog = Catalog(assets)
    catalog.resource_keys = ObservedKeys(catalog.resource_keys)
    facts = parse_csharp(
        "caller",
        "Assets/Caller.cs",
        'class Caller { void M() { UnityEngine.Resources.LoadAll("Folder"); } }',
    )
    return AnalysisIndex([facts], catalog, []), catalog


def test_first_load_all_evidence_does_not_scan_every_resource_key():
    index, catalog = make_index()
    events = index.events("caller")
    try:
        first = next(events)
        assert isinstance(first, Edge)
        assert first.target == "asset:" + "0" * 32
        assert catalog.resource_keys.reads < 30
    finally:
        events.close()


def test_load_all_cancellation_interrupts_catalog_traversal():
    index, catalog = make_index()

    def stop():
        if catalog.resource_keys.reads >= 20:
            raise InterruptedError("stop resource walk")

    index.check = stop
    with pytest.raises(InterruptedError, match="stop resource walk"):
        list(index.events("caller"))
    assert catalog.resource_keys.reads < 30
    # Closing the cancelled iterator must release the catalog lock for future updates.
    catalog.update([{"guid": "new", "path": "Assets/New"}])


def test_streamed_resources_remain_deduplicated_ordered_and_current_after_delta():
    index, catalog = make_index(3)
    catalog.update(
        [
            {
                "guid": "0" * 32,
                "path": "Assets/0",
                "keys": [
                    {"scheme": "resources", "value": "Folder"},
                    {"scheme": "resources", "value": "Folder/0000"},
                    {"scheme": "resources", "value": "Folder/alias"},
                ],
            }
        ]
    )
    targets = [event.target for event in index.events("caller") if isinstance(event, Edge)]
    assert targets == ["asset:" + f"{i:032x}" for i in range(3)]
    assert set(targets) == catalog.resolve("resources", "Folder", "all")[0]
    catalog.update([], removed=["0" * 32])
    assert [event.target for event in index.events("caller") if isinstance(event, Edge)] == targets[
        1:
    ]
