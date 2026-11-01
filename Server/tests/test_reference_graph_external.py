"""Only explicitly admitted external data contributes evidence."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from services.reference_graph.analysis import Catalog
from services.reference_graph.external import ExternalSource, analyze_document
from services.reference_graph.collection_protocol import (
    CollectionBudget,
    CollectionBudgetExceeded,
    CollectionLimits,
)


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
    assert "GameData/absent" not in json.dumps(result.unresolved)
    assert result.unresolved[0]["key_redacted"] is True
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


@pytest.mark.parametrize("extension", ["json", "csv"])
def test_external_source_budget_rejects_document_before_reading(tmp_path, monkeypatch, extension):
    path = tmp_path / ("large." + extension)
    path.write_bytes(b"x" * 1024)
    source = ExternalSource({"root": str(tmp_path), "include": ["*." + extension]})
    source.budget = CollectionBudget(CollectionLimits(max_source_bytes=1))
    monkeypatch.setattr(
        os, "fdopen", lambda *_: pytest.fail("Oversized source was opened for read")
    )
    with pytest.raises(CollectionBudgetExceeded, match="max_source_bytes=1"):
        source.read(path.name)


def test_external_documents_and_nested_ignore_files_share_aggregate_budget(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / ".gitignore").write_bytes(b"#root\n")
    (tmp_path / "nested" / ".gitignore").write_bytes(b"#nested\n")
    (tmp_path / "nested" / "data.json").write_bytes(b"{}")
    budget = CollectionBudget(CollectionLimits(max_source_bytes=10, max_bytes=15))
    source = ExternalSource({"root": str(tmp_path), "include": ["**/*.json"]})
    source.budget = budget
    assert list(source.files()) == ["nested/data.json"]
    assert budget.decoded_bytes == 14
    with pytest.raises(CollectionBudgetExceeded, match="max_bytes=15"):
        source.read("nested/data.json")


def test_external_growth_reads_only_remaining_budget_plus_overflow_probe(tmp_path, monkeypatch):
    path = tmp_path / "data.json"
    path.write_bytes(b"{}")
    source = ExternalSource({"root": str(tmp_path), "include": ["*.json"]})
    source.budget = CollectionBudget(CollectionLimits(max_source_bytes=7))
    fdopen = os.fdopen
    reads = []

    class GrowingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            path.write_bytes(b"x" * 100_000)
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= 8
            return self.stream.read(size)

    monkeypatch.setattr(os, "fdopen", lambda *args: GrowingStream(fdopen(*args)))
    with pytest.raises(CollectionBudgetExceeded, match="max_source_bytes=7"):
        source.read(path.name)
    assert reads == [8]
    assert source.budget.decoded_bytes == 0


@pytest.mark.parametrize("stop", ["items", "deadline", "cancel"])
def test_nonmatching_directory_enumeration_stops_before_draining(tmp_path, monkeypatch, stop):
    from services.reference_graph import collection_protocol

    for number in range(64):
        (tmp_path / f"unmatched-{number:03}.txt").write_text("ignored")
    source = ExternalSource({"root": str(tmp_path), "include": ["*.json"]}, respect_gitignore=False)
    source.budget = CollectionBudget(CollectionLimits(max_items=5))
    scanned = []
    checked = []
    scandir = os.scandir
    cancelled = False

    class Entry:
        def __init__(self, entry):
            self.entry = entry
            self.name, self.path = entry.name, entry.path

        def stat(self, **kwargs):
            checked.append(self.name)
            return self.entry.stat(**kwargs)

        def is_dir(self):
            return self.entry.is_dir()

    @contextmanager
    def entries(path):
        with scandir(path) as iterator:

            def counted():
                nonlocal cancelled
                for entry in iterator:
                    scanned.append(entry.name)
                    if len(scanned) == 3:
                        cancelled = True
                        if stop == "deadline":
                            monkeypatch.setattr(
                                collection_protocol.time,
                                "monotonic",
                                lambda: (
                                    source.budget.started + source.budget.limits.max_seconds + 1
                                ),
                            )
                    yield Entry(entry)

            yield counted()

    def check():
        if stop == "cancel" and cancelled:
            raise RuntimeError("scan cancelled")

    monkeypatch.setattr(os, "scandir", entries)
    if stop == "cancel":
        with pytest.raises(RuntimeError, match="scan cancelled"):
            list(source.files(check=check))
    else:
        with pytest.raises(
            CollectionBudgetExceeded, match="max_items" if stop == "items" else "max_seconds"
        ):
            list(source.files())
    assert len(scanned) <= (6 if stop == "items" else 3)
    assert len(checked) <= (5 if stop == "items" else 2)


def test_directory_buffer_is_bounded_and_scan_order_is_deterministic(tmp_path):
    for name in ("z.json", "a.json", "m.json"):
        (tmp_path / name).write_text("{}")
    for name in ("d", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "entry.json").write_text("{}")
    source = ExternalSource(
        {"root": str(tmp_path), "include": ["**/*.json"]}, respect_gitignore=False
    )
    assert list(source.files()) == ["a.json", "m.json", "z.json", "d/entry.json", "b/entry.json"]
    source.budget = CollectionBudget(CollectionLimits(max_directory_entries=2))
    with pytest.raises(CollectionBudgetExceeded, match="max_directory_entries=2"):
        list(source.files())


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
