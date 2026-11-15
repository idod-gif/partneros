"""ledger_pack: shards stay under the size cap, keep every merchant in order, and the meta doc counts them. Invented data only."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ledger_pack  # noqa: E402


def fake(n, months=40):
    return [{"id": str(1000 + i), "cf": "cf%d" % i, "c": [["2025-%02d" % (m % 12 + 1), 1.5, 2.5, 0, 0] for m in range(months)],
             "b": [], "g": {"c": [0.4, 0.3]}, "sf": []} for i in range(n)]


class LedgerPack(unittest.TestCase):
    def test_shards_under_cap_and_complete(self):
        merch = fake(400)
        pack = ledger_pack.build({"merch": merch, "source": {"label": "Test ledger"}}, "2026-01-01T00:00:00Z")
        docs = pack["docs"]
        self.assertEqual(docs[0]["path"], "snapshots/ledger")
        meta = docs[0]["data"]
        self.assertEqual(meta["shards"], len(docs) - 1)
        self.assertEqual(meta["merchants"], 400)
        got = [m["id"] for d in docs[1:] for m in d["data"]["merch"]]
        self.assertEqual(got, [m["id"] for m in merch])
        for d in docs:
            self.assertLess(ledger_pack.size(d["data"]), ledger_pack.MAX_DOC)
        self.assertGreater(len(docs) - 1, 1)

    def test_small_cap_splits(self):
        parts = ledger_pack.shard(fake(10, 5), "g", limit=800)
        self.assertEqual(sum(len(p) for p in parts), 10)
        self.assertGreater(len(parts), 1)

    def test_empty_refused(self):
        with self.assertRaises(ValueError):
            ledger_pack.build({"merch": []}, "g")


if __name__ == "__main__":
    unittest.main()
