"""Reuse verified native inputs without transferring process-owned graph state."""

from contextlib import closing
from pathlib import Path
import re
import sqlite3

from services.reference_graph.analysis import ANALYSIS_VERSION
from services.reference_graph.collection_protocol import CollectionBudget
from services.reference_graph.response_budget import GraphResponseTooLarge, PageBudget, bounded_rows
from services.reference_graph.storage_budget import SIDECARS, _regular
from services.reference_graph.store import GraphScope, GraphStore, _json
from services.reference_graph.workspace import GraphWorkspace


def _readonly(path: Path) -> str:
    for suffix in SIDECARS:
        _regular(Path(str(path) + suffix))
    return path.absolute().as_uri() + "?mode=ro"


def _previous(store: GraphStore) -> tuple[str | None, list[str] | None, dict[str, int]]:
    scope = store.scope
    donor = None
    exclusions: set[str] = set()
    retained_bytes = 0
    versions: dict[str, int] = {}
    covered: dict[str, int] = {}
    missing: set[str] = set()
    acknowledged_missing: set[str] = set()
    uncertain = False
    for name in store._budget.previous_files(scope.project_id):
        if not re.fullmatch(r"[a-f0-9]{64}\.sqlite", name):
            continue
        path = store.path.with_name(name)
        key = name[:-7]
        try:
            path.lstat()
            # Before the new Editor owns an explicit policy, an unreadable
            # candidate cannot safely be treated as a permissive or absent one.
            with closing(sqlite3.connect(_readonly(path), uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")
                if db.execute("PRAGMA application_id").fetchone()[0] != GraphStore.APPLICATION_ID:
                    continue
                if not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_state'"
                ).fetchone():
                    continue
                # Recover the legacy Editor ID from its native status and
                # independently reproduce the authenticated filename and DB ID.
                identity = db.execute(
                    "SELECT m.scope,coalesce(json_extract(i.value,'$.instance_id'),"
                    "json_extract(p.value,'$.editor.instance_id')),coalesce("
                    "json_extract(i.value,'$.project_id'),json_extract(p.value,'$.editor.project_id')) "
                    "FROM graph_meta m LEFT JOIN graph_state i ON i.key='editor_identity' "
                    "LEFT JOIN graph_state p ON p.key='progress'"
                ).fetchone()
                if identity is None:
                    continue
                # "default" is the native fallback when the physical project path
                # could not be identified. Two such values cannot establish a match.
                identified = scope.project_id != "default" and identity[2] not in (
                    None,
                    "",
                    "default",
                )
                if identified and identity[2] != scope.project_id:
                    continue
                version, restricted = db.execute(
                    "SELECT coalesce(json_extract(value,'$.version'),0),json_array_length(value,'$.exclude')>0 "
                    "FROM graph_state WHERE key='policy'"
                ).fetchone() or (0, False)
                if not identified or not identity[1]:
                    if restricted:
                        versions[key] = version
                        uncertain |= covered.get(key, -1) < version
                    continue
                previous = GraphScope(scope.user_id, identity[1], scope.project_id)
                if previous.key != identity[0] or previous.key != key:
                    raise ValueError("Reference graph identity does not match")
                versions[key] = version
                pending = GraphWorkspace._state(db, "restart_policy_pending", False)
                if not pending:
                    # Only versions explicitly incorporated by a newer resolved
                    # scope can be superseded. Later edits in a still-live Editor
                    # contribute their restrictions to the next Editor as well.
                    for ancestor, seen in GraphWorkspace._state(
                        db, "restart_policy_inputs", {}
                    ).items():
                        # Earlier records cannot acknowledge a later disappearance
                        # or conceal a policy restored after an acknowledged absence.
                        if seen == -1 and ancestor not in covered:
                            acknowledged_missing.add(ancestor)
                        elif seen >= 0 and ancestor not in acknowledged_missing:
                            covered[ancestor] = max(covered.get(ancestor, -1), seen)
                if covered.get(key, -1) < version:
                    uncertain |= pending
                    if restricted and not uncertain:
                        try:
                            policy = GraphWorkspace._state(db, "policy", bounded=True)
                        except GraphResponseTooLarge:
                            uncertain = True
                        else:
                            for pattern in policy["exclude"]:
                                if pattern not in exclusions:
                                    exclusions.add(pattern)
                                    retained_bytes += len(_json(pattern).encode("utf-8")) + 1
                                    if retained_bytes > PageBudget().capacity:
                                        uncertain = True
                                        break
                if donor is None and not pending:
                    earlier = GraphWorkspace._state(db, "restart_from")
                    if (
                        earlier
                        and not db.execute(
                            "SELECT 1 FROM graph_assets WHERE dirty=0 LIMIT 1"
                        ).fetchone()
                    ):
                        donor = earlier
                    else:
                        donor = previous.instance_id
        except (OSError, ValueError, sqlite3.DatabaseError):
            # Unknown prior policy must remain repairable without treating the
            # damaged candidate as permission to collect. Preserve its bytes.
            versions[key] = -1
            missing.add(key)
    return (
        donor,
        None if uncertain or missing - acknowledged_missing else sorted(exclusions),
        versions,
    )


def open_workspace(root: Path, scope: GraphScope) -> GraphWorkspace:
    workspace = GraphWorkspace(GraphStore.open(root, scope))
    with workspace.store._connect() as db:
        if workspace._state(db, "editor_identity") is not None:
            workspace.restart_policy_pending = workspace._state(db, "restart_policy_pending", False)
            workspace.store._budget.identify_project(scope.project_id)
            return workspace
        fresh = not db.execute("SELECT 1 FROM graph_state LIMIT 1").fetchone()
    previous = _previous(workspace.store) if fresh else None
    with workspace.store._connect(write=True) as db:
        if workspace._state(db, "editor_identity") is not None:
            workspace.restart_policy_pending = workspace._state(db, "restart_policy_pending", False)
            workspace.store._budget.identify_project(scope.project_id)
            return workspace
        # An existing Editor retains its own policy and its collection history.
        if (
            previous
            and previous[2]
            and not db.execute("SELECT 1 FROM graph_state LIMIT 1").fetchone()
        ):
            instance, exclusions, versions = previous
            workspace._set(db, "restart_policy_inputs", versions)
            workspace._set(db, "restart_from", instance)
            if exclusions:
                policy = {"version": 1, "exclude": exclusions, "respect_gitignore": True}
                try:
                    PageBudget().require(
                        bounded_rows(db, "SELECT ? AS payload", (_json(policy),)).fetchone()
                    )
                except GraphResponseTooLarge:
                    exclusions = None
            if exclusions is None:
                workspace._set(db, "restart_policy_pending", True)
                workspace.restart_policy_pending = True
            elif exclusions:
                workspace._set(db, "policy", policy)
            db.execute("UPDATE graph_admission SET ready=0 WHERE id=1")
        workspace._set(
            db,
            "editor_identity",
            {"instance_id": scope.instance_id, "project_id": scope.project_id},
        )
    workspace.store._budget.identify_project(scope.project_id)
    return workspace


def _attach_donor(db: sqlite3.Connection, path: Path) -> bool:
    """Cache misses after policy recovery never prevent normal collection."""
    db.execute("PRAGMA busy_timeout=0")
    try:
        db.execute("ATTACH DATABASE ? AS prior", (_readonly(path),))
        if db.execute("PRAGMA prior.application_id").fetchone()[0] != GraphStore.APPLICATION_ID:
            return False
        if db.execute("PRAGMA prior.user_version").fetchone()[0] != GraphStore.SCHEMA_VERSION:
            return False
        version = db.execute("SELECT version FROM prior.graph_workspace").fetchone()
        if not version or version[0] not in (6, 7, 8, 9):
            return False
        # Pin a read snapshot and verify the donor projection *before* any
        # receiver writes. Failures on the receiver still propagate and roll back.
        db.execute(
            "SELECT m.scope,a.ready,s.key,s.value,g.guid,g.path,g.dirty,g.payload,v.guid,v.payload,"
            "o.owner,o.provider,o.state,o.fingerprint,e.id,e.owner,e.provider,e.target,e.kind,e.payload,"
            "d.owner,d.format,d.fingerprint,d.payload,d.compilation_signature,f.guid,x.id "
            "FROM prior.graph_meta m,prior.graph_admission a,prior.graph_state s,prior.graph_assets g,"
            "prior.graph_collected_versions v,prior.observations o,prior.edges e,prior.graph_documents d,"
            "prior.graph_asset_failures f,prior.graph_excluded_nodes x LIMIT 0"
        )
    except (OSError, ValueError, sqlite3.DatabaseError):
        return False
    finally:
        db.execute("PRAGMA busy_timeout=30000")
    return True


def reuse_assets(
    workspace: GraphWorkspace,
    worker: str,
    instance: str,
    proofs: list[dict],
    budget: CollectionBudget,
) -> dict[str, dict | None]:
    """Promote only verified inputs in one receiver-owned, lease-fenced transaction."""
    scope = workspace.store.scope
    if scope.project_id == "default":
        return {}
    previous = GraphScope(scope.user_id, instance, scope.project_id)
    path = workspace.store.path.with_name(previous.key + ".sqlite")
    if not proofs or not path.exists():
        return {}
    with workspace._write(worker) as db:
        if not _attach_donor(db, path):
            return {}
        if not db.execute(
            """SELECT 1 FROM prior.graph_meta m, prior.graph_admission a
            JOIN prior.graph_state p ON p.key='progress'
            LEFT JOIN prior.graph_state policy ON policy.key='policy'
            LEFT JOIN prior.graph_state i ON i.key='ignore_status'
            WHERE m.scope=? AND json_extract(p.value,'$.initialized')
              AND (json_extract(policy.value,'$.respect_gitignore')=0 OR (
                json_extract(i.value,'$.snapshot')<>''
                AND json_extract(i.value,'$.epoch')=json_extract(p.value,'$.editor.epoch')
                AND json_extract(i.value,'$.ignore_revision')=json_extract(p.value,'$.editor.ignore_revision')
                AND json_extract(p.value,'$.editor.ignore_state')='available'
                AND (a.ready OR (
                    (SELECT value FROM prior.graph_state WHERE key='ignore_key')
                    =(SELECT value FROM main.graph_state WHERE key='ignore_key')
                    AND coalesce((SELECT json_extract(value,'$.respect_gitignore')
                        FROM main.graph_state WHERE key='policy'),1)))))""",
            (previous.key,),
        ).fetchone():
            return {}
        # All candidates are from the new Editor's completed census. Do not
        # replace an observation already owned by the current process. The
        # count detects any dependencies suppressed by the donor's old policy.
        db.execute(
            """CREATE TEMP TABLE verified AS
            SELECT a.guid, 'asset:'||a.guid AS owner, v.payload AS version, o.fingerprint,
                   json_extract(a.payload,'$.source_format') AS format,
                   (json_extract(a.payload,'$.source_format')='none' OR (
                (SELECT value FROM prior.graph_state WHERE key='analysis_version')=?
                AND ((json_extract(a.payload,'$.source_format')='unity_yaml' AND d.format='summary') OR
                     (json_extract(a.payload,'$.source_format')='csharp' AND d.format='csharp'
                      AND d.compilation_signature=json_extract(p.value,'$.binding'))))) AS source_ready
            FROM json_each(?) p
            JOIN main.graph_assets a ON a.guid=json_extract(p.value,'$.guid') AND a.dirty=1
            JOIN prior.graph_assets old ON old.guid=a.guid AND old.path=a.path AND old.dirty=0
            JOIN prior.graph_collected_versions v ON v.guid=a.guid
            JOIN prior.observations o ON o.owner='asset:'||a.guid AND o.provider='unity' AND o.state='ready'
            LEFT JOIN prior.graph_documents d ON d.owner=o.owner
            WHERE a.path=json_extract(p.value,'$.path')
              AND NOT EXISTS (SELECT 1 FROM main.observations WHERE owner=o.owner AND provider='unity')
              AND NOT EXISTS (SELECT 1 FROM prior.graph_asset_failures f WHERE f.guid=a.guid)
              AND NOT EXISTS (SELECT 1 FROM prior.graph_excluded_nodes x WHERE x.id=o.owner)
              AND json_extract(v.payload,'$.stamp')=json_extract(p.value,'$.stamp')
              AND json_extract(v.payload,'$.dependency')=json_extract(p.value,'$.dependency')
              AND o.fingerprint=json_extract(v.payload,'$.dependency')
              AND (SELECT count(*) FROM prior.edges e WHERE e.owner=o.owner AND e.provider='unity')
                    =json_extract(p.value,'$.dependency_count')
              AND NOT EXISTS (SELECT 1 FROM prior.edges e WHERE e.owner=o.owner AND e.provider='unity'
                AND NOT EXISTS (SELECT 1 FROM main.graph_asset_paths t WHERE t.guid=substr(e.target,7)))
              AND json_extract(a.payload,'$.source_format')=json_extract(old.payload,'$.source_format')
              """,
            (_json(ANALYSIS_VERSION), _json(proofs)),
        )
        reused = {
            row["guid"]: None
            if row["source_ready"]
            else {"stamp": row["stamp"], "dependency": row["fingerprint"]}
            for row in db.execute(
                "SELECT guid,source_ready,json_extract(version,'$.stamp') AS stamp,fingerprint FROM verified"
            )
        }
        if not reused:
            return reused
        for row in proofs:
            if row["guid"] in reused:
                budget.dependencies(row["dependency_count"])
                budget.admit_items(row["dependency_count"])
        db.execute("CREATE UNIQUE INDEX verified_owner ON verified(owner)")
        db.execute(
            "INSERT INTO main.observations SELECT owner,'unity',fingerprint,1,'ready',NULL FROM verified"
        )
        db.execute(
            "INSERT INTO main.edges SELECT e.id,e.owner,e.provider,e.target,e.kind,e.payload FROM verified v CROSS JOIN prior.edges e ON e.owner=v.owner "
            "AND e.provider='unity' WHERE NOT EXISTS (SELECT 1 FROM main.graph_excluded_nodes x WHERE x.id=e.target)"
        )
        db.execute(
            "INSERT INTO main.graph_analysis SELECT owner,'unity','direct_asset_database_dependencies',1 FROM verified"
        )
        db.execute(
            "INSERT INTO main.graph_documents(owner,format,fingerprint,payload,compilation_signature) "
            "SELECT d.owner,d.format,d.fingerprint,d.payload,d.compilation_signature FROM verified v "
            "JOIN prior.graph_documents d ON d.owner=v.owner WHERE v.source_ready AND v.format<>'none'"
        )
        db.execute(
            "INSERT OR IGNORE INTO main.graph_changed_inputs SELECT owner FROM verified WHERE source_ready AND format<>'none'"
        )
        db.execute(
            "INSERT INTO main.graph_collected_versions SELECT guid,version FROM verified WHERE source_ready"
        )
        db.execute(
            "UPDATE main.graph_assets SET dirty=0 WHERE guid IN (SELECT guid FROM verified WHERE source_ready)"
        )
        workspace._set(db, "analysis_dirty", True)
        workspace.store._bump(db)
        return reused
