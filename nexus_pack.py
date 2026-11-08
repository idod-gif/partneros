#!/usr/bin/env python3
"""Pack a PartnerOS refresh WORK dir into one Nexus admin-import file.

Reads slim.json, slim-stores-<i>.json shards, trend.json and run.json from
WORK and writes {"docs":[{"path","data"}]} for snapshots/latest,
snapshots/stores-<i>, snapshots/trend and runs/<today>. Exit 2 on refusal.
Adds nothing beyond what the inputs hold.
"""
import argparse
import json
import os
import sys

MAX_DOC = 240000


def fail(msg):
    print("nexus_pack: " + msg, file=sys.stderr)
    sys.exit(2)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    out = a.out or os.path.join(a.work, "nexus-import.json")

    slim_path = os.path.join(a.work, "slim.json")
    if not os.path.isfile(slim_path):
        fail("slim.json missing in " + a.work)
    slim = load(slim_path)
    if slim.get("generated") != a.today:
        fail("slim.json generated %r is not %r" % (slim.get("generated"), a.today))

    docs = [("snapshots/latest", slim)]
    i = 0
    while True:
        p = os.path.join(a.work, "slim-stores-%d.json" % i)
        if not os.path.isfile(p):
            break
        docs.append(("snapshots/stores-%d" % i, load(p)))
        i += 1
    n = slim.get("stores_shards")
    if isinstance(n, int) and i < n:
        fail("slim.json stores_shards %d but only %d shard files found" % (n, i))

    for name, dest in (("trend.json", "snapshots/trend"), ("run.json", "runs/" + a.today)):
        p = os.path.join(a.work, name)
        if os.path.isfile(p):
            docs.append((dest, load(p)))
        else:
            print("nexus_pack: warning: %s missing, skipping %s" % (name, dest), file=sys.stderr)

    for path, data in docs:
        size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size > MAX_DOC:
            fail("%s is %d bytes, over %d" % (path, size, MAX_DOC))
    payload = {"docs": [{"path": p, "data": d} for p, d in docs]}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("nexus pack: %d docs, %d bytes, %s" % (len(docs), os.path.getsize(out), out))


if __name__ == "__main__":
    main()
