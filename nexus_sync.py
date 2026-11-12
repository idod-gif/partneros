#!/usr/bin/env python3
"""Sync a PartnerOS Nexus import pack into the pos_docs table with `nexus db import`.

Reads the pack nexus_pack.py wrote ({"docs":[{"path","data"}]}), validates it, writes an NDJSON rows file next to the
pack, and runs `nexus db import partneros pos_docs <rows> --format ndjson --on-conflict update`. DRY RUN by default
(--dry-run, the CLI rolls the import back); the real write needs an explicit --apply.

Row shape = what the app's store.ts writes: id = full doc path, parent = collection path, doc_id = last segment,
data = compact JSON string, updated_at = now (UTC). `version` is NOT in the rows: a new row takes the column default
(1) and, if the CLI's update only sets the columns present in the file, an existing row keeps its version.
Gaps against the /import page (Replace deletes nothing, so nothing is lost; Merge on runs/* is an overwrite here)
are listed in refresh-run.md. Rows files hold payout data: they stay under docs/finance/.

Exit 0 ok, 2 refusal or bad pack, 3 the nexus command failed.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nexus_pack  # noqa: E402

SLUG = "partneros"
TABLE = "pos_docs"
FINANCE_ROOT = "/Users/idod/dev/docs/finance"


class SyncError(Exception):
    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


def split_path(path):
    """(id, parent, doc_id) as the app's docPath does: an even number of non-empty segments."""
    segs = [s for s in str(path).split("/") if s]
    if not segs or len(segs) % 2 != 0:
        raise SyncError("%r is not a document path (needs an even number of segments)" % (path,))
    return "/".join(segs), "/".join(segs[:-1]), segs[-1]


def load_pack(path):
    try:
        with open(path, encoding="utf-8") as f:
            pack = json.load(f)
    except (OSError, ValueError) as e:
        raise SyncError("cannot read pack: %s" % e)
    docs = pack.get("docs") if isinstance(pack, dict) else None
    if not isinstance(docs, list) or not docs:
        raise SyncError("pack has no docs")
    out, seen = [], set()
    for d in docs:
        if not isinstance(d, dict) or not isinstance(d.get("path"), str) or not isinstance(d.get("data"), dict):
            raise SyncError("every doc needs a string path and an object data")
        doc_id, _, _ = split_path(d["path"])
        if doc_id in seen:
            raise SyncError("duplicate path " + doc_id)
        seen.add(doc_id)
        out.append((doc_id, d["data"]))
    try:
        nexus_pack.check_docs(out)
    except SystemExit:
        raise SyncError("a doc is over the size cap (nexus_pack.MAX_DOC)")
    return out


def now_stamp(now=None):
    n = now or datetime.datetime.now(datetime.timezone.utc)
    return n.strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % (n.microsecond // 1000)


def to_rows(docs, now=None):
    """[(path, data)] -> list of pos_docs row dicts (no version key, see module doc)."""
    stamp = now_stamp(now)
    rows = []
    for path, data in docs:
        rid, parent, doc_id = split_path(path)
        rows.append({
            "id": rid,
            "parent": parent,
            "doc_id": doc_id,
            "data": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            "updated_at": stamp,
        })
    return rows


def write_ndjson(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_report(text):
    """Pull inserted / updated / skipped counts out of the CLI report; None when a count is not found."""
    out = {}
    for k in ("inserted", "updated", "skipped"):
        m = re.search(r"(\d+)[ \t]+(?:rows?[ \t]+)?%s\b" % k, text, re.I) or re.search(r"\b%s\b[ \t]*[:=][ \t]*(\d+)" % k, text, re.I)
        out[k] = int(m.group(1)) if m else None
    return out


def under_finance(path):
    return os.path.realpath(path).startswith(os.path.realpath(FINANCE_ROOT) + os.sep)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pack", required=True, help="nexus-import-<date>.json from nexus_pack.py")
    ap.add_argument("--rows", help="NDJSON rows file to write (default: next to the pack)")
    ap.add_argument("--apply", action="store_true", help="really write production rows (default is a dry run)")
    a = ap.parse_args(argv)

    pack = os.path.abspath(a.pack)
    rows_path = os.path.abspath(a.rows) if a.rows else os.path.join(
        os.path.dirname(pack), os.path.basename(pack).replace("nexus-import", "nexus-rows").rsplit(".", 1)[0] + ".ndjson")
    try:
        if not under_finance(pack) or not under_finance(rows_path):
            raise SyncError("pack and rows file must live under " + FINANCE_ROOT)
        docs = load_pack(pack)
        rows = to_rows(docs)
        write_ndjson(rows, rows_path)
    except SyncError as e:
        print("nexus_sync: " + str(e), file=sys.stderr)
        return e.code

    cmd = ["nexus", "db", "import", SLUG, TABLE, rows_path, "--format", "ndjson", "--on-conflict", "update"]
    if not a.apply:
        cmd.append("--dry-run")
    size = os.path.getsize(rows_path)
    print("nexus sync: %d docs, %d bytes, rows %s, mode %s" % (len(rows), size, rows_path, "APPLY" if a.apply else "DRY RUN"))
    print("command: " + " ".join(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("nexus_sync: nexus command failed: %s" % e, file=sys.stderr)
        return 3
    report = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        print("nexus_sync: nexus exited %d:\n%s" % (r.returncode, report.strip()), file=sys.stderr)
        return 3
    c = parse_report(report)
    print("reported: inserted %s, updated %s, skipped %s" % (c["inserted"], c["updated"], c["skipped"]))
    print("cli output:\n" + report.strip())
    if all(v is None for v in c.values()):
        print("nexus_sync: could not read counts from the CLI output", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
