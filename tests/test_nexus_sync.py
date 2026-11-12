"""nexus_sync.py conversion and validation. Synthetic docs only, no network, no nexus call."""
import datetime
import json
import os
import tempfile
import unittest

from tests import _path  # noqa: F401
import nexus_sync

NOW = datetime.datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=datetime.timezone.utc)


class SplitPath(unittest.TestCase):
    def test_doc_path(self):
        self.assertEqual(nexus_sync.split_path("snapshots/latest"), ("snapshots/latest", "snapshots", "latest"))

    def test_deep_doc_path(self):
        self.assertEqual(nexus_sync.split_path("data/users/x/profile"), ("data/users/x/profile", "data/users/x", "profile"))

    def test_odd_segments_refused(self):
        with self.assertRaises(nexus_sync.SyncError):
            nexus_sync.split_path("snapshots")

    def test_empty_refused(self):
        with self.assertRaises(nexus_sync.SyncError):
            nexus_sync.split_path("")


class ToRows(unittest.TestCase):
    def test_shape_and_no_version(self):
        rows = nexus_sync.to_rows([("runs/2026-01-02", {"a": 1, "b": "é"})], NOW)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(set(r), {"id", "parent", "doc_id", "data", "updated_at"})
        self.assertEqual((r["id"], r["parent"], r["doc_id"]), ("runs/2026-01-02", "runs", "2026-01-02"))
        self.assertEqual(r["updated_at"], "2026-01-02 03:04:05.678")

    def test_data_is_compact_json_string_roundtrip(self):
        data = {"k": [1, 2], "s": "é"}
        r = nexus_sync.to_rows([("a/b", data)], NOW)[0]
        self.assertIsInstance(r["data"], str)
        self.assertEqual(r["data"], '{"k":[1,2],"s":"é"}')
        self.assertEqual(json.loads(r["data"]), data)

    def test_ndjson_one_line_per_row(self):
        rows = nexus_sync.to_rows([("a/b", {"x": "line\nbreak"}), ("c/d", {})], NOW)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.ndjson")
            nexus_sync.write_ndjson(rows, p)
            lines = open(p, encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["id"], "a/b")


class LoadPack(unittest.TestCase):
    def pack(self, obj):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(obj, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_ok(self):
        docs = nexus_sync.load_pack(self.pack({"docs": [{"path": "a/b", "data": {"x": 1}}]}))
        self.assertEqual(docs, [("a/b", {"x": 1})])

    def test_empty_refused(self):
        with self.assertRaises(nexus_sync.SyncError):
            nexus_sync.load_pack(self.pack({"docs": []}))

    def test_non_object_data_refused(self):
        with self.assertRaises(nexus_sync.SyncError):
            nexus_sync.load_pack(self.pack({"docs": [{"path": "a/b", "data": [1]}]}))

    def test_duplicate_refused(self):
        d = {"path": "a/b", "data": {}}
        with self.assertRaises(nexus_sync.SyncError):
            nexus_sync.load_pack(self.pack({"docs": [d, d]}))

    def test_oversize_refused(self):
        big = {"x": "y" * 250000}
        with self.assertRaises(nexus_sync.SyncError):
            nexus_sync.load_pack(self.pack({"docs": [{"path": "a/b", "data": big}]}))

    def test_missing_file_refused(self):
        with self.assertRaises(nexus_sync.SyncError):
            nexus_sync.load_pack("/nonexistent/pack.json")


class Report(unittest.TestCase):
    def test_counts(self):
        c = nexus_sync.parse_report("Dry run: 2 inserted, 1 updated, 0 skipped")
        self.assertEqual(c, {"inserted": 2, "updated": 1, "skipped": 0})

    def test_label_first(self):
        c = nexus_sync.parse_report("inserted: 3\nupdated: 0\nskipped: 1")
        self.assertEqual(c, {"inserted": 3, "updated": 0, "skipped": 1})

    def test_unreadable(self):
        self.assertEqual(nexus_sync.parse_report("ok"), {"inserted": None, "updated": None, "skipped": None})


class Guard(unittest.TestCase):
    def test_outside_finance_refused(self):
        self.assertFalse(nexus_sync.under_finance("/tmp/x.json"))
        self.assertTrue(nexus_sync.under_finance(nexus_sync.FINANCE_ROOT + "/partneros/x.json"))

    def test_main_refuses_outside_finance_before_any_call(self):
        self.assertEqual(nexus_sync.main(["--pack", "/tmp/none.json"]), 2)


if __name__ == "__main__":
    unittest.main()
