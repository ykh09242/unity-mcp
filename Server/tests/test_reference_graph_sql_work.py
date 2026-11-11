"""Measure SQLite instructions separately from filesystem and authorization work."""

from types import SimpleNamespace

from services.reference_graph import path_proof
from services.reference_graph.queries import GraphQueries
from services.reference_graph.runtime import RuntimeObservations, blocked_bindings
from services.reference_graph.store import GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace


def measure(connection, action):
    steps = [0]
    statements = []

    def progress():
        steps[0] += 1
        return 0

    connection.set_progress_handler(progress, 1)
    connection.set_trace_callback(statements.append)
    try:
        result = action()
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_trace_callback(None)
    return result, steps[0], statements


def test_requested_binding_lookup_preserves_all_policies_and_scales_with_requested_handles(
    tmp_path,
):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    RuntimeObservations(GraphWorkspace(store))
    bindings = [
        ("allowed", "resources", "okay", "Assets/Allowed.cs", "Packages/Allowed.asset"),
        ("allowed", "custom", "also-okay", "", ""),
        ("mixed-path", "resources", "okay", "Assets/Allowed.cs", ""),
        ("mixed-path", "resources", "okay", "Assets/Private.cs", ""),
        ("mixed-target", "resources", "okay", "", "Assets/Private.asset"),
        ("mixed-key", "resources", "okay", "", ""),
        ("mixed-key", "resources", "excluded", "", ""),
        ("malformed", "", "", "Assets\\Private.cs", ""),
        ("empty", "", "", "", ""),
    ]
    requested = [
        "allowed",
        "mixed-path",
        "mixed-target",
        "mixed-key",
        "malformed",
        "empty",
        "missing",
        "allowed",
    ]
    catalog = SimpleNamespace(
        resolve_detail=lambda scheme, key: (None, "excluded" if key == "excluded" else "allowed")
    )
    admission = lambda path: "Private" not in path
    expected = {"mixed-path", "mixed-target", "mixed-key", "malformed"}
    samples = []
    with store._connect(write=True) as db:
        db.executemany(
            "INSERT INTO graph_runtime_bindings VALUES (?,?,?,?,?,?)",
            (("session", *binding) for binding in bindings),
        )
        db.execute(
            "INSERT INTO graph_runtime_bindings VALUES ('other','allowed','','','Assets/Private.cs','')"
        )
        for previous, count in ((0, 100), (100, 10000)):
            db.executemany(
                "INSERT INTO graph_runtime_bindings VALUES ('session',?,'','','','')",
                ((f"unrelated-{index}",) for index in range(previous, count)),
            )
            actual, steps, statements = measure(
                db, lambda: blocked_bindings(db, "session", iter(requested), admission, catalog)
            )
            assert actual == expected
            samples.append(steps)
            plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + statements[0])]
            print(
                f"runtime bindings unrelated={count} requested={len(set(requested))} vm_steps={steps} plan={plan}"
            )
        assert samples[1] <= samples[0] * 2 + 100
        assert samples[1] < 1000
        assert any("session=? AND handle=?" in detail for detail in plan)
        assert blocked_bindings(db, "session", [], admission, catalog) == set()


def test_proof_marker_sql_work_does_not_recount_accumulated_evidence(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    queries = GraphQueries(store)
    job = queries.path("root", "missing")["query_id"]
    samples = []
    with store._connect(write=True) as db:
        for previous, count in ((0, 1000), (1000, 10000)):
            db.executemany(
                "INSERT INTO graph_query_proofs(job,identity,payload) VALUES (?,?,'{}')",
                ((job, str(index)) for index in range(previous, count)),
            )
            marker, steps, _ = measure(db, lambda: path_proof.marker(db, store.scope.key, job))
            assert marker == {"scope": store.scope.key, "through": count, "count": count}
            samples.append(steps)
            print(f"proof marker rows={count} vm_steps={steps}")
        assert samples[1] <= samples[0] * 2
        assert samples[1] < 100
