"""Stream dependency inventories and compare upstream consumers in their original graph."""

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from cli.utils.play_scenario_analysis_io import safe_local_path
from services.verification.dependency_scan import _APP_ID, _setup, checkpoint_path, scan_summary


def _open_scan(db, path, schema):
    path = checkpoint_path(path)
    db.execute(f"ATTACH DATABASE ? AS {schema}", (path.as_uri() + "?mode=ro",))
    if db.execute(f"PRAGMA {schema}.application_id").fetchone()[0] != _APP_ID:
        raise ValueError("candidate_dependency_checkpoint_format_mismatch")
    summary = scan_summary(db, schema)
    if summary["state"] != "complete":
        raise ValueError(
            f"candidate_dependency_scan_incomplete: resume {schema} root {summary['root']} "
            f"at page {summary['pages'] + 1}"
        )
    return summary


def inventory(checkpoint):
    with closing(sqlite3.connect(":memory:", uri=True)) as db:
        _setup(db)
        db.execute("BEGIN")
        yield _open_scan(db, checkpoint, "selected")
        for row in db.execute(
            "SELECT a.* FROM selected.assets a JOIN selected.reachable USING(node) ORDER BY path"
        ):
            yield {"kind": "asset", **dict(row)}
        for row in db.execute(
            "SELECT e.* FROM selected.edges e JOIN selected.reachable r ON r.node=e.owner "
            "ORDER BY e.id"
        ):
            yield {"kind": "dependency", **dict(row)}
        for row in db.execute("SELECT * FROM selected.issues ORDER BY id"):
            yield {"kind": "unresolved", **dict(row)}
        for row in db.execute(
            "SELECT id FROM selected.edges e WHERE NOT EXISTS "
            "(SELECT 1 FROM selected.reachable r WHERE r.node=e.owner) ORDER BY id"
        ):
            yield {"kind": "unresolved", **dict(row), "reason": "unreachable_native_owner"}


def _side(db, side, other):
    # Compare an owner's outgoing relations only where both scans cover the owner.
    # A prefab that disappears from a scene's closure can still retain its own links.
    relation = "removed" if side == "baseline" else "added"
    membership = "left_closure" if side == "baseline" else "entered_closure"
    query = f"""
        SELECT DISTINCT e.owner, a.path AS owner_path, e.target, t.path AS target_path,
               CASE WHEN covered.node IS NULL THEN
                        CASE WHEN s.native_catalog_unfiltered=1 OR other_owner.node IS NOT NULL
                             THEN ? ELSE 'owner_visibility_unknown' END
                    WHEN s.native_catalog_unfiltered=1 OR other_target.node IS NOT NULL THEN ?
                    ELSE 'target_visibility_unknown' END AS change
        FROM {side}.edges e JOIN {side}.reachable r ON r.node=e.owner
        JOIN {side}.assets a ON a.node=e.owner JOIN {side}.assets t ON t.node=e.target
        LEFT JOIN {other}.reachable covered ON covered.node=e.owner
        CROSS JOIN {other}.scan s
        LEFT JOIN {other}.assets other_owner ON other_owner.node=e.owner
        LEFT JOIN {other}.assets other_target ON other_target.node=e.target
        WHERE covered.node IS NULL OR NOT EXISTS (
            SELECT 1 FROM {other}.edges n WHERE n.owner=e.owner AND n.target=e.target)
        ORDER BY e.owner,e.target
    """
    for row in db.execute(query, (membership, relation)):
        if row["change"] in (membership, relation):
            db.execute("INSERT OR IGNORE INTO affected VALUES (?,?)", (side, row["owner"]))
        if row["change"] == relation:
            db.execute("INSERT OR IGNORE INTO affected VALUES (?,?)", (other, row["owner"]))
        yield {"kind": "relation", "side": side, **dict(row)}


def _retargeted(db):
    # A native AssetDatabase dependency has no field identity. Confirm an actual
    # object/document/field slot in each immutable Git blob before joining targets.
    query = """
        SELECT old.owner, a.path AS baseline_owner_path, n.path AS candidate_owner_path,
               old.field, old.target AS baseline_target, t.path AS baseline_target_path,
               new.target AS candidate_target, u.path AS candidate_target_path,
               old.file_id AS baseline_target_file_id, new.file_id AS candidate_target_file_id
        FROM baseline.slots old JOIN candidate.slots new USING(owner,field)
        JOIN baseline.reachable b ON b.node=old.owner
        JOIN candidate.reachable c ON c.node=new.owner
        JOIN baseline.assets a ON a.node=old.owner JOIN candidate.assets n ON n.node=new.owner
        JOIN baseline.assets t ON t.node=old.target JOIN candidate.assets u ON u.node=new.target
        WHERE (old.target<>new.target OR old.file_id<>new.file_id)
        AND EXISTS (SELECT 1 FROM baseline.edges e WHERE e.owner=old.owner AND e.target=old.target)
        AND EXISTS (SELECT 1 FROM candidate.edges e WHERE e.owner=new.owner AND e.target=new.target)
        ORDER BY old.owner,old.field
    """
    for row in db.execute(query):
        for side in ("baseline", "candidate"):
            db.execute("INSERT OR IGNORE INTO affected VALUES (?,?)", (side, row["owner"]))
        yield {
            "kind": "retargeted",
            **dict(row),
            "field": json.loads(row["field"]),
            "evidence": "committed_serialized_slot_with_native_dependency_on_each_side",
        }


def _consumers(db, side):
    for (owner,) in db.execute("SELECT owner FROM affected WHERE side=? ORDER BY owner", (side,)):
        query = f"""
            WITH RECURSIVE upstream(node) AS (
                SELECT ? UNION SELECT e.owner FROM upstream u JOIN {side}.edges e ON e.target=u.node
                JOIN {side}.reachable r ON r.node=e.owner)
            SELECT a.node,a.path,o.path AS owner_path FROM upstream u
            JOIN {side}.assets a ON a.node=u.node JOIN {side}.assets o ON o.node=?
            WHERE a.node=? OR lower(a.path) LIKE '%.unity' OR lower(a.path) LIKE '%.prefab'
            ORDER BY a.path
        """
        for row in db.execute(query, (owner, owner, owner)):
            yield {"kind": "consumer", "side": side, "owner": owner, **dict(row)}


def compare_scans(baseline, candidate):
    with closing(sqlite3.connect(":memory:", uri=True)) as db:
        _setup(db)
        db.execute("BEGIN")
        before = _open_scan(db, baseline, "baseline")
        after = _open_scan(db, candidate, "candidate")
        if before["root_node"] != after["root_node"]:
            raise ValueError("candidate_dependency_root_guid_mismatch")
        for schema in ("baseline", "candidate"):
            if db.execute(
                f"SELECT 1 FROM {schema}.issues WHERE reason NOT IN "
                "('outside_selected_asset_types','outside_observed_native_dependencies') "
                "LIMIT 1"
            ).fetchone():
                raise ValueError(
                    f"candidate_dependency_comparison_partial: recollect unresolved {schema} "
                    "native references for this root"
                )
        db.execute("CREATE TEMP TABLE affected(side TEXT,owner TEXT,PRIMARY KEY(side,owner))")
        yield {
            "schema_version": 1,
            "kind": "verification_dependency_diff",
            "baseline": before,
            "candidate": after,
            "coverage": "observed_native_dependencies_under_selected_root",
        }
        yield from _side(db, "baseline", "candidate")
        yield from _side(db, "candidate", "baseline")
        yield from _retargeted(db)
        yield from _consumers(db, "baseline")
        yield from _consumers(db, "candidate")


def publish_rows(output, rows):
    """Publish only a complete JSONL export. A failed comparison leaves no result."""
    path = safe_local_path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    digest = sha256()
    with TemporaryDirectory(dir=path.parent) as staging:
        temporary = safe_local_path(Path(staging) / "report")
        with temporary.open("xb") as stream:
            for row in rows:
                encoded = (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode()
                digest.update(encoded)
                stream.write(encoded)
                rows_written += 1
        os.link(temporary, safe_local_path(path))
    return {"rows": rows_written, "sha256": digest.hexdigest()}
