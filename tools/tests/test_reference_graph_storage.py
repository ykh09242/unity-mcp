"""Protect the experiment's result oracle and incremental graph semantics."""

import tempfile
import unittest
from pathlib import Path

from tools.experiments.reference_graph_storage import SQLiteGraph, digest, generate, reachable


class ReferenceGraphStorageTests(unittest.TestCase):
    def test_cyclic_reachability_excludes_root_and_keeps_last_depth(self):
        self.assertEqual(reachable({0: [1], 1: [0, 2], 2: [3]}, 0, 2), [1, 2])

    def test_digest_detects_missing_rows_but_ignores_database_order(self):
        self.assertEqual(digest([3, 1, 2]), digest([2, 3, 1]))
        self.assertNotEqual(digest([1, 2]), digest([1, 2, 3]))

    def test_replace_removes_stale_outgoing_edges_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate(root, 32)
            path = root / "graph.sqlite"
            graph = SQLiteGraph(path)
            graph.ingest(root)
            self.assertEqual(graph.query("direct", 1), [18, 19, 20, 21])
            graph.replace({1: [30, 31]})
            graph.close()
            graph = SQLiteGraph(path)
            try:
                self.assertEqual(graph.query("direct", 1), [30, 31])
                self.assertNotIn(1, graph.query("reverse", 18))
                self.assertIn(1, graph.query("reverse", 31))
            finally:
                graph.close()


if __name__ == "__main__":
    unittest.main()
