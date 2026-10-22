"""Versioned external providers and optional fine identity preserve whole assets."""

import os

import pytest

from services.reference_graph.analysis import (
    AnalysisIndex,
    Catalog,
    parse_csharp,
    serialized_references,
)
from services.reference_graph.external import ExternalSource, analyze_document
from services.reference_graph.store import Edge, GraphScope, GraphStore, Observation


def catalog():
    return Catalog(
        [
            {
                "guid": "a" * 32,
                "path": "Assets/Resources/Icon.png",
                "main_type": "UnityEngine.Texture2D",
                "keys": [
                    {"scheme": "resources", "value": "Icon"},
                    {"scheme": "addressables_label", "value": "ui"},
                ],
            }
        ]
    )


def test_versioned_adapter_rejects_unknown_implementations_without_loading_code(tmp_path):
    config = {
        "root": str(tmp_path),
        "include": ["*.json"],
        "adapter": {"name": "json-csv", "version": 1},
    }
    assert ExternalSource(config).adapter == {"name": "json-csv", "version": 1}
    for declaration in (
        {"name": "arbitrary-module", "version": 1},
        {"name": "json-csv", "version": 2},
    ):
        with pytest.raises(ValueError, match="adapter"):
            ExternalSource({**config, "adapter": declaration})


def test_numeric_ids_only_resolve_with_an_explicit_value_map_and_keep_json_pointer():
    text = '{"entries":[{"icon":42},{"icon":43}],"flag":true}'
    assert not analyze_document("data.json", text, catalog(), []).edges
    result = analyze_document(
        "data.json",
        text,
        catalog(),
        [{"field": "/entries/*/icon", "scheme": "resources", "values": {"42": "Icon"}}],
    )
    assert len(result.edges) == 1
    assert result.edges[0].source_pointer == "/entries/0/icon"
    assert result.edges[0].key == "Icon"
    assert any(row["reason"] == "unmapped_value" for row in result.unresolved)


def test_csv_rows_and_label_mappings_have_distinct_locations():
    result = analyze_document(
        "rows.csv",
        "label\nui\nui\n",
        catalog(),
        [{"field": "label", "scheme": "addressables_label", "mode": "all"}],
    )
    assert [edge.source_pointer for edge in result.edges] == ["/rows/0/label", "/rows/1/label"]
    assert [edge.line for edge in result.edges] == [2, 3]


def test_git_provenance_is_opt_in_and_never_follows_an_external_gitdir(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    config = {"root": str(root), "include": ["*.json"]}
    (root / ".git").write_text("gitdir: ../private")
    assert ExternalSource(config).repository_revision() == {"state": "disabled"}
    assert (
        ExternalSource({**config, "git_revision": True}).repository_revision()["state"]
        == "unavailable"
    )
    # Separate fixtures avoid replacing a Git worktree marker while desktop
    # repository discovery may still have that marker open on Windows.
    root = tmp_path / "repository-metadata"
    root.mkdir()
    config = {"root": str(root), "include": ["*.json"]}
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git" / "refs" / "heads" / "main").write_text("1" * 40 + "\n")
    adapter = ExternalSource({**config, "git_revision": True})
    assert adapter.repository_revision() == {"state": "observed_head", "commit": "1" * 40}
    (root / ".git" / "HEAD").write_text("ref: refs/heads/packed\n")
    (root / ".git" / "packed-refs").write_text("2" * 40 + " refs/heads/packed\n")
    assert adapter.repository_revision()["commit"] == "2" * 40
    (root / ".git" / "HEAD").write_text("ref: ../private\n")
    assert adapter.repository_revision()["state"] == "unavailable"


def test_git_provenance_rejects_linked_metadata(tmp_path):
    (tmp_path / ".git").mkdir()
    target = tmp_path / "outside"
    target.write_text("1" * 40)
    os.link(target, tmp_path / ".git" / "HEAD")
    adapter = ExternalSource({"root": str(tmp_path), "include": ["*.json"], "git_revision": True})
    assert adapter.repository_revision()["state"] == "unavailable"


def test_provenance_matches_the_published_generation_and_old_store_migrates(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("u", "i", "p"))
    generation = store.observe_batch(
        [Observation("owner", "code", "old", provenance={"sha256": "old", "path": "Assets/Old.cs"})]
    )[0]
    store.publish(
        "owner",
        "code",
        generation,
        [Edge("target", "load", target_file_id="123", subobject="sprite")],
    )
    generation = store.observe_batch(
        [Observation("owner", "code", "new", provenance={"sha256": "new"})]
    )[0]
    row = store.query("owner")["edges"][0]
    assert row["stale"] and row["provenance"]["sha256"] == "old"
    store.publish("owner", "code", generation, [Edge("target", "load")])
    assert store.query("owner")["edges"][0]["provenance"]["sha256"] == "new"
    with store._connect(write=True) as connection:
        connection.execute("DROP TABLE evidence_context")
        connection.execute("PRAGMA user_version=1")
    reopened = GraphStore.open(tmp_path, store.scope)
    assert reopened.query("owner")["edges"][0]["provenance"] == {}


def test_serialized_subobject_and_file_id_stay_attached_to_their_own_mapping():
    source = f"""first: {{fileID: 123, guid: {"a" * 32}, type: 3}}
ref:
  m_AssetGUID: {"b" * 32}
  m_SubObjectName: icon
  m_SubObjectGUID: {"c" * 32}
unrelated:
  m_SubObjectName: must-not-leak
"""
    result = serialized_references(source, catalog())
    by_target = {edge.target: edge for edge in result.edges}
    assert by_target["asset:" + "a" * 32].target_file_id == "123"
    assert by_target["asset:" + "b" * 32].subobject == "icon"
    assert by_target["asset:" + "b" * 32].subobject_guid == "c" * 32
    assert len(by_target) == 2


def test_typed_resources_keeps_requested_type_without_excluding_unknown_subassets():
    item = parse_csharp(
        "caller",
        "Assets/Caller.cs",
        'class C {void M(){ UnityEngine.Resources.Load<UnityEngine.Sprite>("Icon"); }}',
    )
    result = AnalysisIndex([item], catalog()).analyze("caller")
    assert len(result.edges) == 1
    assert result.edges[0].target_type == "UnityEngine.Sprite"
    assert result.edges[0].target == "asset:" + "a" * 32


def test_addressable_subobject_keys_preserve_parent_guid_and_requested_name():
    assets = Catalog(
        [
            {
                "guid": "a" * 32,
                "path": "Assets/Atlas.png",
                "keys": [{"scheme": "addressables", "value": "atlas"}],
            }
        ]
    )
    item = parse_csharp(
        "caller",
        "Assets/Caller.cs",
        'class C {void M(){ UnityEngine.AddressableAssets.Addressables.LoadAssetAsync<UnityEngine.Sprite>("atlas[menu]"); }}',
    )
    edge = AnalysisIndex([item], assets).analyze("caller").edges[0]
    assert edge.target == "asset:" + "a" * 32 and edge.subobject == "menu"
    assert edge.target_type == "UnityEngine.Sprite"
    assert edge.confidence == "possible"
    external = analyze_document(
        "data.json",
        '{"icon":"atlas[menu]"}',
        assets,
        [{"field": "/icon", "scheme": "addressables"}],
    )
    assert external.edges[0].subobject == "menu"


def test_duplicate_serialized_identity_is_not_resolved_arbitrarily():
    with pytest.raises(ValueError, match="Duplicate"):
        serialized_references(
            f"ref: {{m_AssetGUID: {'a' * 32}, m_AssetGUID: {'b' * 32}}}", catalog()
        )
