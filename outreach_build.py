#!/usr/bin/env python3
"""PartnerOS Step 3: Outreach data. Builds the `candidates` (suggested-partner queue) and `campaigns` docs.

    python3 outreach_build.py                       # first run: build docs + batch manifests under OUT
    python3 outreach_build.py --db-dir DIR [--versions FILE]
        # DIR = ArtifactData list out_dir of the candidates collection (DIR/candidates/<id>.json). Existing docs are
        # merged: status, last_touch, notes (and created_at) are kept from the db, everything else is refreshed, and
        # the write is an `update` pinned to the version in FILE (json {doc_id: version}).

Company-level only. Contact names, emails and LinkedIn URLs go to docs/finance/partneros/candidates-contacts.csv, never
into the db. Every payload is scanned with shared.pii_hits; any hit aborts. Local reads only; the HeyReach campaign
stats are passed in through outreach/campaigns-input.json (written from the read-only MCP calls, see README)."""
import csv, json, os, re, sys, glob, datetime, argparse, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared

ROOT = '/Users/idod/dev'
AG = f'{ROOT}/docs/recurring/shopify-agencies'
OUT = f'{ROOT}/docs/finance/partneros/outreach'
CONTACTS = f'{ROOT}/docs/finance/partneros/candidates-contacts.csv'
NOW = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
CAP = 600
SUFFIX = re.compile(r'\b(inc|llc|ltd|limited|gmbh|ag|bv|b\.v|co|corp|corporation|company|pty|pvt|sa|s\.a|srl|plc|oy|ou|e\.u|llp|lp)\b\.?', re.I)
JUNK = re.compile(r'\b(seo|backlinks?|guest posting|university|academy|hostel|painting|mailbox)\b|link[- ]?(builder|publishers?|stone|optix)|golden link|linkyjuice|smallseotools|contentlinko|momseo|pro traders club', re.I)
CONNECT_KW = re.compile(r'pay|merchant|fin|processing|psp|gateway|wallet|bank|card|credit|risk|fraud|chargeback|dispute|ship|tax', re.I)


def norm_name(s):
    s = (s or '').lower().replace('&', ' and ')
    s = re.sub(r'\([^)]*\)', ' ', s)
    s = SUFFIX.sub(' ', s)
    return re.sub(r'[^a-z0-9]+', '', s)


def norm_domain(d):
    d = (d or '').strip().lower()
    d = re.sub(r'^https?://', '', d); d = re.sub(r'^www\.', '', d)
    return d.split('/')[0]


def cid(domain, name):
    return domain.replace('.', '_') if domain else 'name_' + (re.sub(r'[^a-z0-9]+', '_', (name or '').lower()).strip('_')[:60] or 'x')


def yes(v):
    return (v or '').strip().lower() in ('yes', 'true', '1')


def score(tier, fit, aff, rev):
    t = {'1': 1, '2': .66, '3': .33, '4': 0}.get(str(tier), 0)
    f = {'high': 1, 'medium': .5, 'low': 0}.get((fit or '').lower(), 0)
    a = (aff or '').strip().lower()
    a = 0 if a.startswith('not a') else 1 if a.startswith(('refer', 'recommend', 'could refer', 'can introduce', 'can refer')) else .5 if a else 0
    r = {'high': 1, 'medium': .5, 'low': 0}.get((rev or '').lower(), 0)
    return round(40 * t + 25 * f + 15 * a + 20 * r)


def load_registry():
    dom, names = {}, {}
    generic = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'me.com', 'icloud.com', 'proton.me', 'protonmail.com'}
    for f in glob.glob(f'{shared.REG}/partners/*.json'):
        d = json.load(open(f)); pid = int(d['partnerio_id'])
        for n in [d.get('name')] + list(d.get('aliases') or []):
            if norm_name(n) and len(norm_name(n)) > 2:
                names.setdefault(norm_name(n), pid)
        w = norm_domain((d.get('contacts') or {}).get('website'))
        if w and w not in generic:
            dom.setdefault(w, pid)
    p = f'{ROOT}/scripts/partner-activation/_classified.csv'
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            w = norm_domain(r.get('domain'))
            if w and w not in generic:
                dom.setdefault(w, int(r['id']))
    return dom, names


def build_candidates():
    stats = {}
    cands, contacts = {}, []
    byname = {}

    def add(domain, company, src, doc, contact=None):
        nk = norm_name(company)
        if domain:
            key = domain
            if key not in cands and nk in byname and cands[byname[nk]]['domain'] is None:
                old = cands.pop(byname[nk]); old['domain'] = domain
                cands[key] = old; byname[nk] = key
                for i, c in enumerate(contacts):
                    if c[0] == byname.get(nk) or c[0] == 'name:' + nk: contacts[i] = (key,) + c[1:]
        else:
            key = byname.get(nk) or 'name:' + nk
        if key in cands:
            c = cands[key]
            if src not in c['sources']: c['sources'].append(src)
            if doc.get('fit_score', 0) > c['fit_score']:
                for k, v in doc.items(): c[k] = v
            c['has_contact'] = c['has_contact'] or doc['has_contact']
        else:
            cands[key] = dict(doc, sources=[src], domain=domain or None, company=company)
            byname.setdefault(nk, key)
        if contact:
            contacts.append((key, company, src) + contact)

    rows = list(csv.DictReader(open(f'{AG}/agencies_classified.csv')))
    kept = 0; doms = set()
    for r in rows:
        if not yes(r['is_real_agency']) or yes(r['wrong_contact']): continue
        if not (r['tier'] in ('1', '2') or (r['chargeflow_fit'] or '').lower() == 'high'): continue
        kept += 1
        d = norm_domain(r['Domain'])
        typ = r['agency_type'] or 'Agency'
        loc = (r['Location'] or '').split(',')
        doc = dict(type=typ, fit=(r['chargeflow_fit'] or '').lower() or None,
                   fit_score=score(r['tier'], r['chargeflow_fit'], r['affiliate_fit'], r['revenue_potential']),
                   fit_reason=r['fit_reason'], revenue_potential=(r['revenue_potential'] or '').lower() or None,
                   suggested_play='connect' if typ == 'App/Tech vendor' else 'rev_share', tier=int(r['tier']) if r['tier'] else None,
                   region=r['Region'], country=loc[-1].strip() if r['Location'] else None, likely_event=r['likely_event'] or None,
                   has_contact=bool(r['Email'] or r['LinkedIn']))
        if d: doms.add(d)
        add(d, r['Company'], 'agencies_classified', doc, (r['Contact'], r['Title'], r['Email'], r['LinkedIn']))
    stats['agencies'] = {'rows': len(rows), 'qualifying_rows': kept, 'qualifying_candidates_after_collapse': len(cands), 'domains': len(doms)}
    ase = list(csv.DictReader(open(f'{AG}/ase-2026-partner-candidates.csv'))); n0 = len(cands)
    for r in ase:
        d = norm_domain(r['Domain']); t = r['Tier'].strip().upper()
        add(d, r['Company'], 'ase_2026', dict(
            type=f"DTC brand affiliate program ({r['Category']})", fit=None, fit_score={'A': 60, 'B': 45}.get(t, 35),
            fit_reason=r['Note'], revenue_potential=None, suggested_play='rev_share', tier={'A': 1, 'B': 2}.get(t),
            region=None, country=None, likely_event='Affiliate Summit East', has_contact=True), (r['Name'], r['Title'], '', ''))
    stats['ase'] = {'rows': len(ase), 'new_after_dedupe': len(cands) - n0}
    names = [l.strip() for l in open(f'{OUT}/airtable-companies.txt') if l.strip()]
    n0 = len(cands); skipped = 0
    for n in names:
        if JUNK.search(n): skipped += 1; continue
        pay = bool(CONNECT_KW.search(n))
        add(None, n, 'airtable_partner_leads', dict(
            type='Unclassified inbound (payments-like name)' if pay else 'Unclassified inbound', fit=None, fit_score=25 if pay else 20,
            fit_reason='Filled the partner form; not yet classified', revenue_potential=None,
            suggested_play='connect' if pay else 'rev_share', tier=None, region=None, country=None, likely_event=None, has_contact=True))
    stats['airtable'] = {'names': len(names), 'junk_skipped': skipped, 'new_after_dedupe': len(cands) - n0}
    return cands, contacts, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db-dir'); ap.add_argument('--versions')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    regd, regn = load_registry()
    cands, contacts, stats = build_candidates()
    # has_contact comes from what the contacts CSV will hold: true only when the candidate has an email or a LinkedIn URL
    # there. A name alone, an ASE affiliate manager without email, or an Airtable company name does not count.
    reachable = {c[0] for c in contacts if c[5] or c[6]}
    for key, c in cands.items():
        c['has_contact'] = key in reachable
    stats['has_contact_true'] = sum(1 for c in cands.values() if c['has_contact'])
    stats['has_contact_false'] = len(cands) - stats['has_contact_true']
    docs = []
    for key, c in cands.items():
        pid = (regd.get(c['domain']) if c['domain'] else None) or regn.get(norm_name(c['company']))
        c['id'] = cid(c['domain'], c['company'])
        c['in_registry'] = pid is not None; c['partner_id'] = pid
        c['status'] = 'in_registry' if pid else 'new'
        c.update(last_touch=None, notes='', created_at=NOW, updated_at=NOW)
        docs.append(c)
    stats['after_dedupe'] = len(docs)
    stats['in_registry_total'] = sum(d['in_registry'] for d in docs)
    # in_registry kept but ranked last, so the cap cuts them first
    docs.sort(key=lambda d: (d['in_registry'], -d['fit_score'], d['company'].lower()))
    pushed = docs[:CAP]
    stats['pushed'] = len(pushed)
    stats['cutoff_fit_score'] = pushed[-1]['fit_score'] if len(docs) > CAP else None
    stats['not_pushed_over_cap'] = max(0, len(docs) - CAP)
    stats['pushed_in_registry'] = sum(d['in_registry'] for d in pushed)
    stats['pushed_by_first_source'] = {s: sum(1 for d in pushed if d['sources'][0] == s) for s in ('agencies_classified', 'ase_2026', 'airtable_partner_leads')}
    existing = {}
    if a.db_dir:
        for f in glob.glob(f'{a.db_dir}/candidates/*.json'):
            existing[os.path.basename(f)[:-5]] = json.load(open(f))
    versions = json.load(open(a.versions)) if a.versions else {}
    for p in glob.glob(f'{OUT}/batch-*.json'): os.remove(p)
    shutil.rmtree(f'{OUT}/docs', ignore_errors=True)
    os.makedirs(f'{OUT}/docs/candidates'); os.makedirs(f'{OUT}/docs/campaigns')
    writes, merged, hits = [], 0, []
    for d in pushed:
        doc = {k: d[k] for k in ('domain', 'company', 'type', 'fit', 'fit_score', 'fit_reason', 'revenue_potential', 'suggested_play', 'tier', 'region', 'country',
                                 'likely_event', 'sources', 'has_contact', 'in_registry', 'partner_id', 'status', 'last_touch', 'notes', 'created_at', 'updated_at')}
        op = 'set'; old = existing.get(d['id'])
        if old:
            op = 'update'; merged += 1  # the whitelist above never carries `outreach`, so the Monday run's block survives the update
            for k in ('status', 'last_touch', 'notes', 'created_at'): doc.pop(k, None)
            if old.get('status') == 'new' and d['in_registry']: doc['status'] = 'in_registry'
        p = f'{OUT}/docs/candidates/{d["id"]}.json'
        json.dump(doc, open(p, 'w'), ensure_ascii=False)
        hits += shared.pii_hits(json.dumps(doc))
        w = {'op': op, 'collection': 'candidates', 'doc_id': d['id'], 'file_path': p}
        if op == 'update': w['if_version'] = versions.get(d['id'], 1)
        writes.append(w)
    ci = f'{OUT}/campaigns-input.json'
    ncamp = 0
    if os.path.exists(ci):
        for c in json.load(open(ci)):
            p = f'{OUT}/docs/campaigns/{c["key"]}.json'
            json.dump(c, open(p, 'w'), ensure_ascii=False)
            hits += shared.pii_hits(json.dumps(c)); ncamp += 1
            writes.append({'op': 'set', 'collection': 'campaigns', 'doc_id': c['key'], 'file_path': p})
    stats['campaign_docs'] = ncamp; stats['merged_existing'] = merged; stats['pii_hits'] = len(hits)
    if hits:
        print('PII HITS, aborting:', hits[:10]); sys.exit(1)
    for i in range(0, len(writes), 50):
        json.dump(writes[i:i + 50], open(f'{OUT}/batch-{i // 50 + 1:02d}.json', 'w'))
    keep = {d['id'] for d in pushed}; n = 0
    with open(CONTACTS, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['candidate_id', 'company', 'source', 'contact', 'title', 'email', 'linkedin'])
        for key, company, src, name, title, email, li in contacts:
            i = cid(cands[key]['domain'], cands[key]['company']) if key in cands else None
            if i in keep and (name or email or li): w.writerow([i, company, src, name, title, email, li]); n += 1
    stats['contacts_rows'] = n; stats['batches'] = (len(writes) + 49) // 50
    print(json.dumps(stats, indent=1))


if __name__ == '__main__':
    main()
