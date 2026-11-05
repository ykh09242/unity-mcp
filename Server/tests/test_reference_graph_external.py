"""Only explicitly admitted external data contributes evidence."""

import json
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from services.reference_graph.analysis import Catalog
from services.reference_graph.external import ExternalSource, _pattern, analyze_document
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


def test_resources_folder_lookup_charges_only_selected_keys_and_targets():
    def asset(i, *keys, excluded=False):
        return {
            "guid": f"{i:032x}",
            "path": f"Assets/{i}.asset",
            "keys": [{"scheme": "resources", "value": key} for key in keys],
            "_excluded": excluded,
        }

    selected = [
        asset(3, "UI"),
        asset(2, "UI/part", "ui/PART/child"),
        asset(1, "uI/part"),
        asset(4, "UIplus"),
        asset(5, "UI/hidden", excluded=True),
    ]
    mapping = [{"field": "/*/folder", "scheme": "resources", "mode": "all"}]
    text = json.dumps([{"folder": "ui"}] * 10)
    costs = []
    for size in (0, 10_000):
        catalog = Catalog(selected + [asset(i + 6, f"Unrelated/{i}") for i in range(size)])
        budget = CollectionBudget(CollectionLimits(max_items=500))
        result = analyze_document(
            "data.json", text, catalog, mapping, visit=lambda budget=budget: budget.admit_items(1)
        )
        assert [edge.target for edge in result.edges] == [
            f"asset:{i:032x}" for _ in range(10) for i in (1, 2, 3)
        ]
        costs.append(budget.items)
    assert costs[0] == costs[1]
    catalog = Catalog(selected)
    assert catalog.resolve("resources", "UI/", "all")[0] == {f"asset:{i:032x}" for i in (1, 2)}
    assert catalog.resolve("resources", "", "all")[0] == {f"asset:{i:032x}" for i in (1, 2, 3, 4)}
    budget = CollectionBudget(CollectionLimits(max_items=3))
    with pytest.raises(CollectionBudgetExceeded, match="max_items=3"):
        catalog.resolve("resources", "UI", "all", visit=lambda: budget.admit_items(1))


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

    monkeypatch.setattr(
        os, "fdopen", lambda *args, **kwargs: GrowingStream(fdopen(*args, **kwargs))
    )
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


@pytest.mark.parametrize("pattern", ["private", "priv*", "**/private", "private/**"])
@pytest.mark.parametrize("policy", [False, True])
def test_directory_exclusion_is_shared_by_enumeration_and_direct_access(tmp_path, pattern, policy):
    (tmp_path / "private" / "nested").mkdir(parents=True)
    relative = "private/nested/item.json"
    (tmp_path / relative).write_text("{}")
    (tmp_path / "private-copy.json").write_text("{}")
    source = ExternalSource(
        {"root": str(tmp_path), "include": ["**/*.json"], "exclude": [] if policy else [pattern]},
        exclude=[pattern] if policy else [],
    )
    assert relative not in source.files()
    assert not source.admitted(relative)
    assert not source.watch_filter(relative)
    with pytest.raises(PermissionError):
        source.read(relative)
    with pytest.raises(PermissionError):
        source.stamp(relative)
    with pytest.raises(PermissionError):
        source.directory_identity(tmp_path / "private" / "nested")
    if pattern != "priv*":
        assert source.admitted("private-copy.json")


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


@pytest.mark.parametrize("extra", ["data[1].json", "notes[1].txt"])
def test_discovered_filenames_are_literals_not_patterns(tmp_path, extra):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / extra).write_text('{"value": 1}')
    source = ExternalSource({"root": str(tmp_path), "include": ["*.json"]})

    assert list(source.files()) == (["a.json", extra] if extra.endswith(".json") else ["a.json"])
    if extra.endswith(".json"):
        assert source.read(extra).text == '{"value": 1}'
        assert source.stamp(extra)
    with pytest.raises(ValueError, match="Patterns support"):
        ExternalSource({"root": str(tmp_path), "include": ["data[1].json"]})


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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
@pytest.mark.parametrize("operation", ["discover", "cached", "race_stamp", "race_read"])
def test_external_fifo_fails_without_waiting_for_a_writer(tmp_path, monkeypatch, operation):
    # These POSIX-only APIs are absent on the Windows lint host.
    # pylint: disable=no-member
    path = tmp_path / "data.json"
    source = ExternalSource({"root": str(tmp_path), "include": ["*.json"]}, respect_gitignore=False)
    if operation == "discover":
        os.mkfifo(path)
        run = lambda: [source.stamp(name) for name in source.files()]
    elif operation == "cached":
        path.write_bytes(b"{}")
        source.stamp(path.name)
        path.unlink()
        os.mkfifo(path)
        run = lambda: source.stamp(path.name)
    else:
        path.write_bytes(b"{}")
        original_open = os.open

        def swap_before_open(name, flags):
            path.unlink()
            os.mkfifo(path)
            return original_open(name, flags)

        monkeypatch.setattr(os, "open", swap_before_open)
        run = lambda: (source.read if operation == "race_read" else source.stamp)(path.name)

    def timed_out(*_):
        pytest.fail("Opening a FIFO blocked")

    previous = signal.signal(signal.SIGALRM, timed_out)
    timer = signal.setitimer(signal.ITIMER_REAL, 2)
    try:
        with pytest.raises(PermissionError):
            run()
    finally:
        signal.setitimer(signal.ITIMER_REAL, *timer)
        signal.signal(signal.SIGALRM, previous)


def test_hardlink_created_during_open_is_rejected_before_read(tmp_path, monkeypatch):
    path = tmp_path / "data.json"
    path.write_bytes(b"{}")
    source = ExternalSource({"root": str(tmp_path), "include": ["*.json"]}, respect_gitignore=False)
    original_open = os.open

    def link_before_open(name, flags):
        os.link(path, tmp_path / "copy.json")
        return original_open(name, flags)

    monkeypatch.setattr(os, "open", link_before_open)
    with pytest.raises(PermissionError):
        source.read(path.name)
    assert source.budget.decoded_bytes == 0


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


def test_json_traversal_preserves_order_and_escaped_pointers():
    from services.reference_graph.external import _values

    assert list(_values("data.json", '{"a/b":[1,{"~x":"v"}],"z":2}')) == [
        ("/a~1b/0", 1, 0, "/a~1b/0"),
        ("/a~1b/1/~0x", "v", 0, "/a~1b/1/~0x"),
        ("/z", 2, 0, "/z"),
    ]


def test_wide_json_traversal_has_bounded_auxiliary_memory():
    env = os.environ.copy()
    env.pop("PYTHONTRACEMALLOC", None)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import tracemalloc\n"
            "from services.reference_graph.analysis import Catalog\n"
            "from services.reference_graph.external import analyze_document\n"
            "text = '[' + ','.join(['0'] * 50000) + ']'\n"
            "tracemalloc.start()\n"
            "result = analyze_document('data.json', text, Catalog([]), [])\n"
            "peak = tracemalloc.get_traced_memory()[1]\n"
            "assert not result.edges and not result.unresolved\n"
            "assert peak < 3000000, peak\n",
        ],
        cwd=Path(__file__).resolve().parents[1] / "src",
        env=env,
        check=True,
    )


@pytest.mark.parametrize("tail", ["0]", "invalid]"])
def test_dense_json_stops_decoding_at_node_budget(tail):
    text = "[" + "0," * 1_000_000 + tail
    budget = CollectionBudget(CollectionLimits(max_items=8))
    checks = 0

    def check():
        nonlocal checks
        checks += 1

    with pytest.raises(CollectionBudgetExceeded, match="max_items=8"):
        analyze_document(
            "dense.json", text, Catalog([]), [], check=check, visit=lambda: budget.admit_items(1)
        )
    assert checks < 32
    assert budget.items == 8


def test_json_node_admission_counts_empty_containers_and_skips_string_punctuation():
    from services.reference_graph.external import _values

    text = r'{"\u002f": [ { }, [ ], "a\",\\[{},", 1e2, false, null ], "~": -1}'
    budget = CollectionBudget(CollectionLimits(max_items=9))
    assert list(_values("data.json", text, visit=lambda: budget.admit_items(1))) == [
        ("/~1/2", 'a",\\[{},', 0, "/~1/2"),
        ("/~0", -1, 0, "/~0"),
    ]
    assert budget.items == 9


@pytest.mark.parametrize(
    "name,text", [("data.json", "[null,false,{},[]]"), ("data.csv", "a,b\nx,y\n")]
)
def test_document_traversal_checks_cancellation_between_items(name, text):
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        analyze_document(name, text, assets(), [], check=check)


def test_document_checks_use_shared_deadline_without_charging_collection_items(monkeypatch):
    from services.reference_graph import collection_protocol

    budget = CollectionBudget(CollectionLimits(max_items=1, max_seconds=10))
    budget.admit_items(1)
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 4:
            monkeypatch.setattr(collection_protocol.time, "monotonic", lambda: budget.started + 11)
        budget.remaining()

    with pytest.raises(CollectionBudgetExceeded, match="max_seconds=10"):
        analyze_document("data.json", "[0,0,0,0]", assets(), [], check=check)
    assert budget.items == 1


@pytest.mark.parametrize(
    ("name", "text"),
    [("data.json", "[null,false,{},[]]"), ("data.csv", "a,b\nx,y\n")],
)
def test_document_items_share_collection_limit(name, text):
    budget = CollectionBudget(CollectionLimits(max_items=3))
    budget.admit_items(1)  # A directory entry already consumed part of this attempt.
    with pytest.raises(CollectionBudgetExceeded, match="max_items=3"):
        analyze_document(name, text, assets(), [], visit=lambda: budget.admit_items(1))
    assert budget.items == 3


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


@pytest.mark.parametrize(
    ("pattern", "paths", "expected"),
    [
        ("*.json", ["a.json", "x/a.json", "A.JSON"], ["a.json"]),
        ("*/data?.json", ["x/data1.json", "x/y/data1.json", "z/data.json"], ["x/data1.json"]),
        ("**/*.json", ["a.json", "x/a.json", "x/y/a.json"], ["a.json", "x/a.json", "x/y/a.json"]),
        (
            "**/**/a.json",
            ["a.json", "x/a.json", "x/y/a.json"],
            ["a.json", "x/a.json", "x/y/a.json"],
        ),
        ("**/x/**/a.json", ["x/a.json", "z/x/y/a.json", "z/a.json"], ["x/a.json", "z/x/y/a.json"]),
        ("a**b.json", ["ab.json", "a/x/b.json", "a\nb.json"], ["ab.json", "a/x/b.json"]),
    ],
)
def test_source_pattern_language(pattern, paths, expected):
    matcher = _pattern(pattern)
    assert [path for path in paths if matcher.fullmatch(path)] == expected
    for path in expected:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            assert matcher.could_descend("/".join(parts[:depth]))


def test_source_pattern_adversarial_failure_is_bounded():
    # A backtracking regression must fail this test without hanging the suite.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from services.reference_graph.external import _pattern\n"
            "assert not _pattern('*a' * 100 + 'b.json').fullmatch('a' * 200 + '.json')",
        ],
        cwd=Path(__file__).resolve().parents[1] / "src",
        timeout=15,
        check=True,
    )


def test_source_prunes_unmatched_directories_before_budget_is_spent(tmp_path, monkeypatch):
    (tmp_path / "root.json").write_text("{}")
    (tmp_path / "unrelated").mkdir()
    for number in range(20):
        (tmp_path / "unrelated" / f"{number}.json").write_text("{}")
    source = ExternalSource({"root": str(tmp_path), "include": ["*.json"]}, respect_gitignore=False)
    source.budget = CollectionBudget(CollectionLimits(max_items=2))
    assert list(source.files()) == ["root.json"]

    source = ExternalSource(
        {"root": str(tmp_path), "include": ["match*/data?.json"]}, respect_gitignore=False
    )
    scandir = os.scandir

    def checked_scandir(path):
        assert Path(path) != tmp_path / "unrelated"
        return scandir(path)

    monkeypatch.setattr(os, "scandir", checked_scandir)
    assert list(source.files()) == []
