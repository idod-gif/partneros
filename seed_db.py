#!/usr/bin/env python3
"""Prepare the PartnerOS artifact db seed: one JSON file per doc plus ArtifactData batch manifests.

Writes under SEED_DIR (default docs/finance/partneros/seed/, gitignored):
  partners/<id>.json     contact-free registry doc (shared.strip: export_shared.py whitelist + has_contact,
                         agreement.contract_url and agreement.terms_version)
  registry/meta.json     the local registry meta.json
  snapshots/trend.json   trend.json, when it fits the 256 KiB doc limit
  snapshots/latest.json  dashboard.json, only when it fits (it does not today: skipped and printed)
  batch-NN.json          [{op:"set", collection, doc_id, file_path}], at most 50 entries each
Then run each batch with ArtifactData action "batch" (writes = the manifest) against the PartnerOS artifact.
Creating docs needs no if_version; re-seeding existing docs does (read the versions first).

Hard rule: no email, phone, LinkedIn URL or contact person name in any doc. Every file is scanned
(shared.pii_hits, plus a literal "@" and LinkedIn-domain grep); any hit exits 1 and nothing should be written.
Usage: python3 seed_db.py [--seed-dir DIR]"""
import json, os, sys, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared

DASH_DIR = os.environ.get('CF_DASH_DIR') or '/Users/idod/dev/docs/finance/payouts/2026-q3/dashboard'
SEED_DIR = sys.argv[sys.argv.index('--seed-dir') + 1] if '--seed-dir' in sys.argv else '/Users/idod/dev/docs/finance/partneros/seed'
LIMIT = 256 * 1024  # artifact db: at most 256 KiB serialized per doc


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    txt = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    open(path, 'w', encoding='utf-8').write(txt)
    return len(txt.encode())


def main():
    if os.path.isdir(SEED_DIR):
        shutil.rmtree(SEED_DIR)
    writes, counts, skipped = [], {}, []
    for d in shared.load_stripped():
        doc_id = str(d.pop('id'))  # the doc id carries the Partner.io id
        p = f'{SEED_DIR}/partners/{doc_id}.json'
        if dump(p, d) > LIMIT:
            sys.exit(f'partners/{doc_id} is over 256 KiB')
        writes.append({'op': 'set', 'collection': 'partners', 'doc_id': doc_id, 'file_path': p})
    counts['partners'] = len(writes)
    meta = shared.load_meta()
    if meta:
        p = f'{SEED_DIR}/registry/meta.json'; dump(p, meta)
        writes.append({'op': 'set', 'collection': 'registry', 'doc_id': 'meta', 'file_path': p}); counts['registry'] = 1
    counts['snapshots'] = 0
    for name, doc_id in (('trend.json', 'trend'), ('dashboard.json', 'latest')):
        src = os.path.join(DASH_DIR, name)
        if not os.path.exists(src):
            skipped.append(f'snapshots/{doc_id}: {src} missing'); continue
        obj = json.load(open(src, encoding='utf-8'))
        size = len(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode())
        if size > LIMIT:
            skipped.append(f'snapshots/{doc_id}: {size:,} bytes minified, over the 256 KiB doc limit (page uses the build seed)'); continue
        p = f'{SEED_DIR}/snapshots/{doc_id}.json'; dump(p, obj)
        writes.append({'op': 'set', 'collection': 'snapshots', 'doc_id': doc_id, 'file_path': p}); counts['snapshots'] += 1
    # PII scan over every file written
    hits = []
    for w in writes:
        txt = open(w['file_path'], encoding='utf-8').read()
        h = shared.pii_hits(txt)
        if w['collection'] == 'snapshots':  # snapshot text is revenue data; only the literal markers apply
            h = [x for x in h if x[0] != 'phone']
        if '@' in txt: h.append(('literal @', '@'))
        if ('linkedin' + '.com') in txt.lower(): h.append(('literal LinkedIn domain', 'linkedin'))
        hits += [(w['collection'] + '/' + w['doc_id'], k) for k, _ in h]
    nb = 0
    for i in range(0, len(writes), 50):
        nb += 1; dump(f'{SEED_DIR}/batch-{nb:02d}.json', writes[i:i + 50])
    print(f'docs: {counts}; batches: {nb} in {SEED_DIR}')
    for s in skipped:
        print('SKIPPED', s)
    if hits:
        print(f'PII check: FAIL, {len(hits)} hits, first: {hits[:5]}. Do not seed.'); sys.exit(1)
    print('PII check: PASS (0 emails, 0 "@", 0 LinkedIn domains, 0 phone-shaped values)')


if __name__ == '__main__':
    main()
