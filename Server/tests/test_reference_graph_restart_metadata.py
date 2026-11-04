"""Readable prior SQLite files still require valid ownership and policy metadata."""

import sqlite3

import pytest

from services.reference_graph.restart import open_workspace
from services.reference_graph.store import GraphScope, RevisionConflict


@pytest.mark.parametrize("damage", ["application_id", "policy_table", "identity_row"])
def test_invalid_prior_metadata_cannot_silently_authorize_collection(tmp_path, damage):
    old = open_workspace(tmp_path, GraphScope("alice", "old", "project"))
    old.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    with sqlite3.connect(old.store.path) as db:
        if damage == "application_id":
            db.execute("PRAGMA application_id=0")
        elif damage == "policy_table":
            db.execute("DROP TABLE graph_state")
        else:
            db.execute("DELETE FROM graph_meta")
    current = open_workspace(tmp_path, GraphScope("alice", "new", "project"))
    with pytest.raises(RevisionConflict, match="set_policy"):
        current.require_restart_policy()
    current.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    current.require_restart_policy()
