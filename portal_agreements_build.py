#!/usr/bin/env python3
"""PartnerOS: portal agreement status. Rolls Partner.io partner-reps signed_status up per partner and builds
`partners/<id>` update payloads (portal_agreement, history, updated_at only). Read-only against Partner.io (GET only).

    python3 portal_agreements_build.py [--db-dir DIR] [--versions FILE]
        # DIR = db export holding DIR/partners/<id>.json (default: the session dbexport2). FILE = {'partners/<id>': version}
        # (default DIR/versions.json). Every write is op update with if_version. Missing versions file = fail.

Outputs under docs/finance/partneros/portal/: signed-reps.csv (the only file with names and emails), docs/partners/<id>.json,
batch-NN.json manifests of 50. Every payload is scanned with shared.pii_hits; any hit exits 1."""
import json, os, sys, csv, time, glob, shutil, datetime, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, '/Users/idod/dev/scripts/partnerio-revenue-sync')
import shared
from pio import call

BASE = '/Users/idod/dev/docs/finance/partneros/portal'
DEFAULT_DB = '/private/tmp/claude-501/-Users-idod-dev/fceb6d94-b40c-4bdd-b24a-a7b199902849/scratchpad/dbexport2'
SOURCE = 'Partner.io partner-reps signed_status filter'
NOW = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
TODAY = datetime.date.today().isoformat()
BY = 'portal-ingest'


def page_all(path):
    out = []
    for p in range(1, 60):
        sep = '&' if '?' in path else '?'
        st, d = call('GET', f'{path}{sep}per_page=100&page={p}')
        if st != 200 or not isinstance(d, dict):
            sys.exit(f'FAIL: GET {path} page {p} -> {st} {str(d)[:150]}')
        b = d.get('data') or []
        out += b
        if p >= (d.get('meta') or {}).get('last_page', 1) or not b: break
    return out


def pull(path):
    m = {}
    for r in page_all(path) + page_all(path): m[r['id']] = r  # twice: pages shift
    return m


def wait_db(db):
    for i in range(6):
        n = len(glob.glob(f'{db}/partners/*.json'))
        if os.path.isdir(f'{db}/partners') and n >= 300: return n
        if i < 5: print(f'db export has {n} partner files, waiting 30s', file=sys.stderr); time.sleep(30)
    sys.exit('FAIL: db export missing or under 300 files')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db-dir', default=DEFAULT_DB); ap.add_argument('--versions')
    a = ap.parse_args()
    vfile = a.versions or f'{a.db_dir}/versions.json'
    wait_db(a.db_dir)
    if not os.path.exists(vfile): sys.exit(f'FAIL: versions file not found: {vfile}')
    versions = json.load(open(vfile))

    signed = pull('/partner-reps?signed_status=signed')
    unsigned = pull('/partner-reps?signed_status=not-signed')
    allr = pull('/partner-reps')
    for _ in range(2): allr.update(pull('/partner-reps'))  # extra passes: unfiltered paging drops rows when pages shift
    st, d = call('GET', '/partner-reps?per_page=1'); meta_total = (d.get('meta') or {}).get('total')
    print('meta total', meta_total)
    both = sorted(set(signed) & set(unsigned))
    neither = sorted(set(allr) - set(signed) - set(unsigned))
    extra = sorted((set(signed) | set(unsigned)) - set(allr))
    totals = {'signed': len(signed), 'not_signed': len(unsigned), 'unfiltered_paged': len(allr), 'unfiltered_meta_total': meta_total}
    # the unfiltered listing pages short (drops rows), so the authoritative total is meta.total; ids only in the filtered pulls are reported
    sane = totals['signed'] + totals['not_signed'] == meta_total and not both
    print('totals', totals, 'sane', sane, 'in_both', both, 'in_neither', neither, 'only_in_filtered', extra)

    reps = {}
    for rid, r in {**unsigned, **signed}.items():
        reps[rid] = (r, rid in signed)
    roll = {}
    for rid, (r, s) in reps.items():
        pid = r.get('partner_id')
        if pid is None: continue
        c = roll.setdefault(str(pid), {'signed_reps': 0, 'total_reps': 0})
        c['total_reps'] += 1; c['signed_reps'] += int(s)

    db_docs = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(f'{a.db_dir}/partners/*.json')}
    os.makedirs(BASE, exist_ok=True)
    with open(f'{BASE}/signed-reps.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['rep_id', 'rep_name', 'email', 'partner_id', 'partner_name', 'signed'])
        for rid in sorted(reps):
            r, s = reps[rid]; pid = str(r.get('partner_id'))
            w.writerow([rid, r.get('name'), r.get('email'), pid, (db_docs.get(pid) or {}).get('name', ''), 'yes' if s else 'no'])

    shutil.rmtree(f'{BASE}/docs', ignore_errors=True)
    for p in glob.glob(f'{BASE}/batch-*.json'): os.remove(p)
    os.makedirs(f'{BASE}/docs/partners')
    writes, hits, missing, no_ver = [], [], [], []
    counts = {'signed': 0, 'not_signed': 0, 'no_reps': 0}
    for pid in sorted(roll, key=int):
        c = roll[pid]
        c['status'] = 'signed' if c['signed_reps'] > 0 else 'not_signed'
        counts[c['status']] += 1
        cur = db_docs.get(pid)
        if cur is None: missing.append(pid); continue
        if f'partners/{pid}' not in versions: no_ver.append(pid); continue
        hist = list(cur.get('history') or [])
        note = f"Portal agreement: {c['signed_reps']} of {c['total_reps']} reps signed"
        if not any(h.get('by') == BY and h.get('note') == note for h in hist):
            hist.append({'at': NOW, 'by': BY, 'note': note})
        upd = {'portal_agreement': {'status': c['status'], 'signed_reps': c['signed_reps'], 'total_reps': c['total_reps'],
                                    'source': SOURCE, 'as_of': TODAY}, 'updated_at': NOW, 'history': hist}
        hits += shared.pii_hits(json.dumps(upd, ensure_ascii=False))
        pth = f'{BASE}/docs/partners/{pid}.json'
        json.dump(upd, open(pth, 'w'), ensure_ascii=False)
        writes.append({'op': 'update', 'collection': 'partners', 'doc_id': pid, 'file_path': pth,
                       'if_version': versions[f'partners/{pid}']})
    counts['no_reps'] = len([p for p in db_docs if p not in roll])
    if hits: print('PII HITS, aborting:', hits[:10]); sys.exit(1)
    for i in range(0, len(writes), 50):
        json.dump(writes[i:i + 50], open(f'{BASE}/batch-{i // 50 + 1:02d}.json', 'w'))
    print(json.dumps({'totals': totals, 'sanity_ok': sane, 'partners_by_status': counts, 'writes': len(writes),
                      'batches': (len(writes) + 49) // 50, 'partners_not_in_export': len(missing),
                      'not_in_export_ids': [f'{p} (reps={roll[p]["total_reps"]})' for p in missing[:5]],
                      'missing_version': no_ver, 'pii_hits': 0}, indent=1))


if __name__ == '__main__':
    main()
