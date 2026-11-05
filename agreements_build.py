#!/usr/bin/env python3
"""PartnerOS: Agreements data. Builds `agreements/<slug>` docs (db-only) and the partner updates that attach them.

    python3 agreements_build.py                       # first run: seed partner docs -> docs + batch manifests under OUT
    python3 agreements_build.py --db-dir DIR [--versions FILE]
        # DIR = ArtifactData list out_dir holding DIR/agreements/<slug>.json and DIR/partners/<id>.json. Existing docs are
        # read from the db instead of the seed and every write is pinned with if_version (FILE = json {doc_id: version};
        # keys are the doc ids, or 'agreements/<slug>' / 'partners/<id>' to disambiguate). Existing agreements docs never
        # get status, notes, partner_id, match_confidence, needs_confirmation or created_at overwritten.

Input: docs/finance/partneros/contracts/matches.json. Partner updates change only agreement.{contract_url, contract_file,
signed_on (when null), terms_version}, agreement_id, history, updated_at; never agreement.status, rate or basis.
Every payload is scanned with shared.pii_hits; any hit aborts."""
import json, os, re, sys, glob, datetime, argparse, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared

ROOT = '/Users/idod/dev'
BASE = f'{ROOT}/docs/finance/partneros'
MATCHES = f'{BASE}/contracts/matches.json'
SEED = f'{BASE}/seed/partners'
OUT = f'{BASE}/agreements'
FOLDER = shared.load_local()['links']['agreements_drive_folder']
SOURCE = 'Drive folder Referral Agreement, ingested 2026-10-18'
NOW = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
KEEP_ON_RERUN = ('status', 'notes', 'partner_id', 'match_confidence', 'needs_confirmation', 'created_at')
TWO_WAY = re.compile(r'two[- ]way|both directions|bi-?directional|second (executed )?paper|companion|opposite direction', re.I)
STANDARD = re.compile(r'standard form|template default', re.I)
INFERRED = 'direction inferred from Chargeflow paper'
INBOUND = re.compile(r'\binbound\b|\bthey pay\b|partner pays chargeflow|pays chargeflow', re.I)
OUTBOUND = re.compile(r'\boutbound\b|\bwe pay\b|chargeflow pays', re.I)


# Per-folder corrections applied before slugging; matches.json stays untouched. The map (folder name -> brand, counterparty,
# slug, add_flag) holds personal names, so it lives in docs/finance/partneros/contracts/overrides.json, not in the repo.
_OV = f'{BASE}/contracts/overrides.json'
if not os.path.exists(_OV):
    raise SystemExit(f'ERROR: overrides file not found: {_OV}')
OVERRIDES = json.load(open(_OV, encoding='utf-8'))
CORP = re.compile(r'\b(inc|llc|ltd|limited|gmbh|ag|bv|co|corp|corporation|company|pty|pvt|sa|srl|plc|services|service)\b', re.I)


def norm_cp(s):
    return re.sub(r'[^a-z0-9]+', '', CORP.sub(' ', re.sub(r'\([^)]*\)', ' ', (s or '').lower())))


def finality(e):
    executed = e.get('signed_evidence') == 'signed' and not e.get('primary_is_draft')
    return (executed, e.get('signed_on') or '')


def prepare(entries):
    """Apply OVERRIDES, then merge entries sharing a normalized counterparty into one. Returns (entries, merges)."""
    out = []
    for e in entries:
        e = dict(e); o = OVERRIDES.get(e.get('partner_folder'), {})
        for k in ('brand', 'counterparty', 'slug'):
            if k in o: e[k] = o[k]
        e['flags'] = list(e.get('flags') or []) + ([o['add_flag']] if o.get('add_flag') else [])
        out.append(e)
    groups, order = {}, []
    for e in out:
        k = norm_cp(e.get('counterparty'))
        key = k if k else id(e)
        if key not in groups: groups[key] = []; order.append(key)
        groups[key].append(e)
    res, merges = [], []
    for key in order:
        g = groups[key]
        if len(g) == 1: res.append(g[0]); continue
        p = max(g, key=finality)
        m = dict(p)
        m['partner_folder'] = '; '.join(x['partner_folder'] for x in g)
        of = list(p.get('other_files') or [])
        for x in g:
            if x is p: continue
            of.append({'path': x['primary_file'], 'classification': 'primary of merged folder'})
            of += x.get('other_files') or []
        m['other_files'] = of
        for fld in ('flags', 'departures'):
            seen_, u = set(), []
            for x in g:
                for v in x.get(fld) or []:
                    if v not in seen_: seen_.add(v); u.append(v)
            m[fld] = u
        m['flags'].append('two folders merged')
        res.append(m); merges.append(m['partner_folder'])
    return res, merges


def slugify(s):
    return re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-') or 'agreement'


def direction(e):
    t = ' | '.join((e.get('departures') or []) + (e.get('flags') or []))
    if TWO_WAY.search(t): return 'two_way'
    i, o = bool(INBOUND.search(t)), bool(OUTBOUND.search(t))
    if i and o: return 'two_way'
    return 'inbound' if i else 'outbound' if o else None


def status(e):
    if e.get('primary_is_draft'): return 'draft'
    return 'executed' if e.get('signed_evidence') == 'signed' else (e.get('signed_evidence') or 'unclear')


def short(s):
    s = s or ''
    return s if len(s) <= 60 else s[:57].rstrip() + '...'


def scan(obj):
    """shared.pii_hits over the payload. File names (primary_file, other_files paths, contract_file) carry dates and
    DocuSign ids that match the phone shape, so phone hits are ignored there; email, @ and LinkedIn still run on everything."""
    full = shared.pii_hits(json.dumps(obj, ensure_ascii=False))
    def strip(o, k=None):
        if isinstance(o, dict): return {kk: strip(v, kk) for kk, v in o.items()}
        if isinstance(o, list): return [strip(v, k) for v in o]
        return 'FILE' if k in ('primary_file', 'path', 'contract_file') else o
    phone_ok = [h for h in shared.pii_hits(json.dumps(strip(obj), ensure_ascii=False)) if h[0] == 'phone']
    return [h for h in full if h[0] != 'phone'] + phone_ok


def readj(p):
    return json.load(open(p)) if os.path.exists(p) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db-dir'); ap.add_argument('--versions')
    a = ap.parse_args()
    entries, merges = prepare(json.load(open(MATCHES)))
    versions = json.load(open(a.versions)) if a.versions else {}

    def ver(coll, did):
        return versions.get(f'{coll}/{did}', versions.get(did, 1))

    shutil.rmtree(f'{OUT}/docs', ignore_errors=True)
    for p in glob.glob(f'{OUT}/batch-*.json'): os.remove(p)
    os.makedirs(f'{OUT}/docs/agreements'); os.makedirs(f'{OUT}/docs/partners')
    seen, ag_writes, pt_writes, hits = set(), [], [], []
    by_status, by_conf, needs, oddities, rerun_ag = {}, {}, 0, [], 0
    dirs, two_way, collisions = {}, [], []
    for e in entries:
        slug = base = e.get('slug') or slugify(e.get('brand') or e.get('counterparty') or e.get('partner_folder'))
        n = 2
        while slug in seen: slug = f'{base}-{n}'; n += 1
        if slug != base:
            collisions.append(f'{base} -> {slug} ({e.get("partner_folder")})')
            print(f'WARNING: slug collision {base} -> {slug} ({e.get("partner_folder")})', file=sys.stderr)
        seen.add(slug)
        pid = str(e['registry_partner_id']) if e.get('registry_partner_id') is not None else None
        conf = e.get('match_confidence') or 'none'
        if conf not in ('exact', 'domain', 'fuzzy', 'none'): oddities.append((slug, 'match_confidence', conf)); conf = 'none'
        if e.get('signed_evidence') not in ('signed', 'unsigned', 'unclear'): oddities.append((slug, 'signed_evidence', e.get('signed_evidence')))
        if (conf == 'none') != (pid is None): oddities.append((slug, 'confidence_vs_partner_id', f'{conf}/{pid}'))
        doc = {'slug': slug, 'brand': e.get('brand'), 'counterparty': e.get('counterparty'), 'partner_folder': e.get('partner_folder'),
               'primary_file': e.get('primary_file'), 'contract_url': FOLDER, 'signed_on': e.get('signed_on'),
               'signed_evidence': e.get('signed_evidence'), 'status': status(e), 'rate': e.get('rate'), 'basis': e.get('basis'),
               'direction': direction(e), 'terms_version': short(e.get('terms_version')), 'terms_version_full': e.get('terms_version_full') or e.get('terms_version'),
               'primary_is_draft': bool(e.get('primary_is_draft')), 'primary_is_sow_or_order_form': bool(e.get('primary_is_sow_or_order_form')),
               'departures': e.get('departures') or [], 'other_files': e.get('other_files') or [], 'flags': e.get('flags') or [],
               'partner_id': pid, 'registry_name': e.get('registry_name'), 'match_confidence': conf, 'match_basis': e.get('match_basis'),
               'registry_status': e.get('registry_status'), 'registry_rate': e.get('registry_rate'),
               'needs_confirmation': conf in ('domain', 'fuzzy'), 'notes': '', 'source': SOURCE, 'created_at': NOW, 'updated_at': NOW}
        if doc['direction'] is None and doc['rate'] is not None and (
                os.path.basename(doc['primary_file'] or '').startswith('Referral Agreement - Chargeflow') or
                STANDARD.search(' | '.join(doc['departures']))):
            doc['direction'] = 'outbound'; doc['flags'] = doc['flags'] + [INFERRED]
        dirs[doc['direction']] = dirs.get(doc['direction'], 0) + 1
        if doc['direction'] == 'two_way': two_way.append(slug)
        op = 'set'
        old = readj(f'{a.db_dir}/agreements/{slug}.json') if a.db_dir else None
        if old:
            op = 'update'; rerun_ag += 1
            for k in KEEP_ON_RERUN: doc.pop(k, None)
        pth = f'{OUT}/docs/agreements/{slug}.json'
        json.dump(doc, open(pth, 'w'), ensure_ascii=False)
        hits += scan(doc)
        w = {'op': op, 'collection': 'agreements', 'doc_id': slug, 'file_path': pth}
        if op == 'update': w['if_version'] = ver('agreements', slug)
        ag_writes.append(w)
        shown = old if old else doc
        by_status[shown.get('status')] = by_status.get(shown.get('status'), 0) + 1
        by_conf[shown.get('match_confidence')] = by_conf.get(shown.get('match_confidence'), 0) + 1
        needs += bool(shown.get('needs_confirmation'))
        if pid is None: continue
        cur = readj(f'{a.db_dir}/partners/{pid}.json') if a.db_dir else None
        if a.db_dir and cur is None:
            oddities.append((slug, 'partner_missing_in_db_export', pid))
        if cur is None: cur = readj(f'{SEED}/{pid}.json')
        if cur is None: oddities.append((slug, 'partner_doc_not_found', pid)); continue
        ag = dict(cur.get('agreement') or {})
        ag['contract_url'] = FOLDER; ag['contract_file'] = e.get('primary_file'); ag['terms_version'] = short(e.get('terms_version'))
        if not ag.get('signed_on') and e.get('signed_on'): ag['signed_on'] = e['signed_on']
        hist = list(cur.get('history') or [])
        note = f'Agreement {slug} attached from Drive folder; match {conf}'
        if not any(h.get('by') == 'contracts-ingest' and h.get('note') == note for h in hist):
            hist.append({'at': NOW, 'by': 'contracts-ingest', 'note': note})
        upd = {'agreement': ag, 'agreement_id': slug, 'updated_at': NOW, 'history': hist}
        pp = f'{OUT}/docs/partners/{pid}.json'
        json.dump(upd, open(pp, 'w'), ensure_ascii=False)
        hits += scan(upd)
        w = {'op': 'update', 'collection': 'partners', 'doc_id': pid, 'file_path': pp}
        if a.db_dir and cur is not None and os.path.exists(f'{a.db_dir}/partners/{pid}.json'): w['if_version'] = ver('partners', pid)
        pt_writes.append(w)
    if hits:
        print('PII HITS, aborting:', hits[:10]); sys.exit(1)
    writes = ag_writes + pt_writes
    for i in range(0, len(writes), 50):
        json.dump(writes[i:i + 50], open(f'{OUT}/batch-{i // 50 + 1:02d}.json', 'w'))
    print(json.dumps({'agreements_written': len(ag_writes), 'partner_updates_written': len(pt_writes), 'by_status': by_status,
                      'by_match_confidence': by_conf, 'direction': dirs, 'two_way': two_way, 'needs_confirmation': needs, 'merged_existing_agreements': rerun_ag,
                      'merged_folders': merges, 'slug_collisions': collisions, 'oddities': oddities, 'pii_hits': 0, 'batches': (len(writes) + 49) // 50}, indent=1))


if __name__ == '__main__':
    main()
