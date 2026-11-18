"""Compact, private lexical-reference documents backed by the existing graph database."""

import json

from services.reference_graph import document_delta
from services.reference_graph.analysis import SERIALIZED_COVERAGE, SerializedReference
from services.reference_graph.store import Edge, RevisionConflict, _json


FORMAT = "serialized_v1"
PAGE_BYTES = 64 * 1024


def data(format_, payload):
    if format_ != FORMAT:
        return json.loads(payload)
    result = {"coverage": SERIALIZED_COVERAGE}
    if payload is not None:
        result["references"] = [
            SerializedReference(*json.loads(line)) for line in payload.split(b"\n") if line
        ]
    return result


def save(workspace, worker, owner, fingerprint, references, check):
    """Stage complete pages privately; replace the document only after a successful scan."""
    staged = 0
    page = bytearray()
    size = 0

    def clear(db):
        db.execute(
            "DELETE FROM graph_staging WHERE worker=? AND owner=? AND kind='serialized_page'",
            (worker, owner),
        )

    def stage(data):
        nonlocal staged
        with workspace._write(worker) as db:
            check()
            if not staged:
                clear(db)
            db.execute(
                "INSERT INTO graph_staging VALUES (?,?,0,?,'serialized_page',?,?)",
                (worker, owner, f"{staged:016x}", data, staged),
            )
        staged += 1

    try:
        for reference in references:
            check()
            record = _json(reference).encode() + b"\n"
            size += len(record)
            # Even a single long subobject name may span several physical pages.
            view = memoryview(record)
            while view:
                n = min(PAGE_BYTES - len(page), len(view))
                page.extend(view[:n])
                view = view[n:]
                if len(page) == PAGE_BYTES:
                    stage(page)
                    page.clear()
        with workspace._write(worker) as db:
            check()
            if not db.execute(
                "SELECT 1 FROM graph_documents WHERE owner=? AND format=? AND fingerprint=?",
                (owner, FORMAT, fingerprint),
            ).fetchone():
                workspace._invalidate(db, [owner])
                rowid = db.execute(
                    "INSERT INTO graph_documents(owner,format,fingerprint,payload) VALUES (?,?,?,"
                    "CASE WHEN ? THEN zeroblob(?) ELSE ? END) ON CONFLICT(owner) DO UPDATE SET "
                    "format=excluded.format,fingerprint=excluded.fingerprint,payload=excluded.payload,"
                    "compilation_signature=NULL RETURNING rowid",
                    (owner, FORMAT, fingerprint, staged, size, page),
                ).fetchone()[0]
                if staged:
                    with db.blobopen("graph_documents", "payload", rowid) as blob:
                        for row in db.execute(
                            "SELECT payload FROM graph_staging WHERE worker=? AND owner=? "
                            "AND kind='serialized_page' ORDER BY identity",
                            (worker, owner),
                        ):
                            check()
                            blob.write(row[0])
                        blob.write(page)
                workspace.store._bump(db)
            if staged:
                clear(db)
            check()
        staged = 0
    finally:
        if staged:
            with workspace.store._connect(write=True) as db:
                clear(db)


def events(workspace, owner, generation, check):
    """Release each read snapshot before staging writes, including for a long logical record."""
    pending = bytearray()
    offset = 0
    while True:
        check()
        with workspace.store._connect() as db:
            document_delta.require_generation(db, generation)
            row = db.execute(
                "SELECT rowid FROM graph_documents WHERE owner=? AND format=?", (owner, FORMAT)
            ).fetchone()
            if row is None:
                raise RevisionConflict("Serialized document changed during analysis")
            with db.blobopen("graph_documents", "payload", row[0], readonly=True) as blob:
                blob.seek(offset)
                page = blob.read(PAGE_BYTES)
                complete = blob.tell() == len(blob)
        start = 0
        while (end := page.find(b"\n", start)) >= 0:
            if pending:
                pending.extend(page[start:end])
                record = json.loads(pending)
                pending.clear()
            else:
                record = json.loads(page[start:end])
            yield SerializedReference(*record).edge()
            start = end + 1
        pending.extend(page[start:])
        if complete:
            if pending:
                raise ValueError("Incomplete serialized reference document")
            return
        offset += len(page)


def legacy_events(workspace, owner, generation, check):
    """Read a pre-paging summary without hydrating its Python edge and issue collections."""
    with workspace.store._connect() as db:
        document_delta.require_generation(db, generation)
        for field in ("edges", "unresolved"):
            for row in db.execute(
                "SELECT item.value FROM graph_documents d, json_each(d.payload,?) item "
                "WHERE d.owner=? AND d.format='summary'",
                ("$." + field, owner),
            ):
                check()
                value = json.loads(row[0])
                yield Edge(**value) if field == "edges" else value
