#!/usr/bin/env python3
"""Build the Nexus import pack for the Finance ledger series (the Revenue explorer's data).

Reads the local series file (docs/finance/partneros/finance-ledger-series.json, written from the Finance ledger) and
writes a pack {"docs":[{"path","data"}]} for nexus_sync.py:
  snapshots/ledger          {generated, source, shards, merchants, payout_adjustments?}
  snapshots/ledger-<i>      {generated, i, merch:[...]}   each under MAX_DOC bytes of compact JSON
The page reads snapshots/ledger, then every shard, and only uses the series when every shard carries the same
generated stamp and the merchant count matches. The pack holds revenue and names: it must stay under docs/finance/.

Usage: python3 ledger_pack.py --series FILE --out PACK [--generated ISO]
Then:  python3 -I nexus_sync.py --pack PACK            (dry run)
       python3 -I nexus_sync.py --pack PACK --apply    (write)
Exit 0 ok, 2 refusal.
"""
import argparse
import datetime
import json
import os
import sys

MAX_DOC = 240000
SHARD_LIMIT = 200000  # headroom under the app's 240 KB cap
FINANCE_ROOT = "/Users/idod/dev/docs/finance"


def size(o):
    return len(json.dumps(o, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def shard(merch, generated, limit=SHARD_LIMIT):
    """Greedy shards in file order, each doc under limit bytes. A single merchant over the limit is a refusal."""
    out, cur = [], []
    for m in merch:
        if size({"generated": generated, "i": 0, "merch": [m]}) >= limit:
            raise ValueError("one merchant row is over the document size cap")
        if cur and size({"generated": generated, "i": len(out), "merch": cur + [m]}) >= limit:
            out.append(cur)
            cur = []
        cur.append(m)
    if cur:
        out.append(cur)
    return out


def build(series, generated):
    merch = series.get("merch")
    if not isinstance(merch, list) or not merch:
        raise ValueError("series has no merch rows")
    parts = shard(merch, generated)
    meta = {"generated": generated, "source": series.get("source") or {}, "shards": len(parts), "merchants": len(merch)}
    if series.get("payout_adjustments"):
        meta["payout_adjustments"] = series["payout_adjustments"]
    docs = [{"path": "snapshots/ledger", "data": meta}]
    for i, p in enumerate(parts):
        docs.append({"path": "snapshots/ledger-%d" % i, "data": {"generated": generated, "i": i, "merch": p}})
    for d in docs:
        if size(d["data"]) >= MAX_DOC:
            raise ValueError("%s is over the document size cap" % d["path"])
    return {"docs": docs}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--generated", default=None)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    if not out.startswith(FINANCE_ROOT + os.sep):
        print("ledger_pack: the pack holds revenue data; write it under %s" % FINANCE_ROOT, file=sys.stderr)
        return 2
    try:
        series = json.load(open(a.series, encoding="utf-8"))
        gen = a.generated or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pack = build(series, gen)
    except (OSError, ValueError) as e:
        print("ledger_pack: %s" % e, file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(pack, open(out, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
    sizes = [size(d["data"]) for d in pack["docs"]]
    print("ledger_pack: %d docs (1 meta, %d shards), largest %d bytes, %s" % (len(sizes), len(sizes) - 1, max(sizes), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
