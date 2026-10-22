"""Only explicitly admitted external data contributes evidence."""

import json
import os
from pathlib import Path

import pytest

from services.reference_graph.analysis import Catalog
from services.reference_graph.external import ExternalSource, analyze_document


def assets():
    return Catalog(
        [
            {
                "guid": "a" * 32,
                "path": "Assets/Resources/GameData/icon.png",
                "keys": [{"scheme": "resources", "value": "GameData/icon"}],
            }
        ]
    )


def test_json_pointer_mapping_and_csv_columns_keep_evidence():
    mapping = [{"field": "/items/*/icon", "scheme": "resources", "prefix": "GameData/"}]
    result = analyze_document(
        "settings.json",
        json.dumps({"items": [{"icon": "icon", "unrelated": "x"}, {"icon": "absent"}]}),
        assets(),
        mapping,
    )
    assert len(result.edges) == 1
    assert result.edges[0].target == "asset:" + "a" * 32
    assert result.edges[0].evidence == "/items/0/icon"
    assert result.unresolved[0]["reason"] == "key_not_found"
    csv = analyze_document(
        "settings.csv",
        "name,icon\nmenu,GameData/icon\n",
        assets(),
        [{"field": "icon", "scheme": "resources"}],
    )
    assert csv.edges[0].line == 2


def test_default_exact_matches_are_candidates_not_proven_schema_contracts():
    result = analyze_document("data.json", '{"name":"GameData/icon","false":"icon"}', assets(), [])
    assert len(result.edges) == 1
    assert result.edges[0].confidence == "possible"
    assert result.coverage == "exact_catalog_strings"


def test_allowlist_exclusion_and_links(tmp_path):
    root = tmp_path / "external"
    root.mkdir()
    (root / "data").mkdir()
    (root / "data" / "valid.json").write_text("{}")
    (root / "data" / "ignored.json").write_text("{}")
    (root / "data" / ".env.json").write_text("{}")
    (root / "outside.json").write_text("{}")
    source = ExternalSource(
        {"root": str(root), "include": ["data/**/*.json"], "exclude": ["**/ignored.json"]}
    )
    assert list(source.files()) == ["data/valid.json"]
    assert source.read("data/valid.json").text == "{}"
    for path in (
        "../outside.json",
        "data/../outside.json",
        "outside.json",
        "data/.env.json",
        "data/ignored.json",
        str(root / "outside.json"),
    ):
        with pytest.raises((ValueError, PermissionError)):
            source.read(path)
    try:
        os.symlink(root / "outside.json", root / "data" / "linked.json")
    except OSError:
        return
    with pytest.raises((ValueError, PermissionError)):
        source.read("data/linked.json")


def test_hardlinks_rejected_and_no_new_files_or_handles_retained(tmp_path):
    root = tmp_path / "external"
    root.mkdir()
    path = root / "data.json"
    path.write_text("{}")
    source = ExternalSource({"root": str(root), "include": ["*.json"]})
    first = source.read("data.json")
    path.write_text('{"changed":true}')
    assert source.read("data.json").fingerprint != first.fingerprint
    os.link(path, root / "copy.json")
    with pytest.raises((ValueError, PermissionError)):
        source.read("copy.json")
    (root / "copy.json").unlink()
    path.unlink()
    assert list(root.iterdir()) == []


def test_root_revocation_and_invalid_config_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        ExternalSource({"root": str(tmp_path)})
    with pytest.raises(ValueError):
        ExternalSource({"root": str(tmp_path), "include": ["../*.json"]})
    root = tmp_path / "source"
    root.mkdir()
    source = ExternalSource({"root": str(root), "include": ["*.json"]})
    root.rmdir()
    with pytest.raises(FileNotFoundError):
        list(source.files())


def test_unicode_case_sensitive_paths_and_large_csv_are_not_truncated(tmp_path):
    root = tmp_path / "Données"
    root.mkdir()
    path = root / "rows.csv"
    path.write_text("icon\n" + "GameData/icon\n" * 12000, encoding="utf-8")
    source = ExternalSource({"root": str(root), "include": ["*.csv"]})
    result = analyze_document(
        "rows.csv",
        source.read("rows.csv").text,
        assets(),
        [{"field": "icon", "scheme": "resources"}],
    )
    assert len(result.edges) == 12000
    assert result.edges[-1].line == 12001


def test_duplicate_json_keys_and_missing_mapped_fields_are_not_empty_success():
    with pytest.raises(ValueError, match="Duplicate"):
        analyze_document("bad.json", '{"icon":"one","icon":"two"}', assets(), [])
    result = analyze_document(
        "missing.json", '{"items":[]}', assets(), [{"field": "/wrong", "scheme": "resources"}]
    )
    assert result.unresolved[0]["reason"] == "mapping_not_found"


def test_large_csv_field_and_disallowed_subdirectories_are_supported(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "allowed").mkdir(parents=True)
    (root / "denied").mkdir()
    (root / "allowed" / "data.json").write_text("{}")
    original = os.scandir

    def guarded(path):
        assert Path(path) != root / "denied"
        return original(path)

    monkeypatch.setattr(os, "scandir", guarded)
    source = ExternalSource({"root": str(root), "include": ["allowed/*.json"]})
    assert list(source.files()) == ["allowed/data.json"]
    result = analyze_document(
        "large.csv",
        'text,icon\n"' + "x" * 300000 + '",GameData/icon\n',
        assets(),
        [{"field": "icon", "scheme": "resources"}],
    )
    assert len(result.edges) == 1


def test_unterminated_csv_quote_is_an_error():
    import csv

    with pytest.raises(csv.Error):
        analyze_document(
            "bad.csv", 'icon\n"GameData/icon', assets(), [{"field": "icon", "scheme": "resources"}]
        )
