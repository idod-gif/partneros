#!/usr/bin/env python3
"""PartnerOS Step 4: builds payouts/*, leads/* and settings/{program,roadmap} docs plus batch-NN.json manifests
(50 `set` entries each, enrich.py format) under docs/finance/partneros/step4/. Local reads only; pushes nothing.
Doc shapes: step4_schema.md. Every lead and settings payload is scanned with shared.pii_hits; any hit aborts.

    python3 step4_build.py
"""
import csv, json, os, re, sys, shutil, datetime, glob
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared

ROOT = '/Users/idod/dev'
OUT = os.environ.get('CF_STEP4_OUT') or f'{ROOT}/docs/finance/partneros/step4'
Q3 = f'{ROOT}/docs/finance/payouts/2026-q3'
DASH = f'{Q3}/dashboard'
LEADS = f'{ROOT}/docs/projects/partner-leads-handoff/leads.json'
MISSING = f'{ROOT}/docs/projects/partnerio-missing-deals-2026-10/leads.csv'
PLAN = f'{ROOT}/docs/projects/partnerio-optimization/plan.md'
NOW = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
TYPE_IDS = {'Agencies': 3077, 'Affiliates': 3075, 'Influencers': 3076, 'PSP': 3080, 'ISO': 3081}  # scripts/partner-activation/classify.py
norm = lambda s: (s or '').strip().lower()
r2 = lambda x: round(x + 1e-9, 2)


def registry():
    docs = {}
    for f in glob.glob(f'{shared.REG}/partners/*.json'):
        d = json.load(open(f)); docs[int(d['partnerio_id'])] = d
    return docs


def payouts(docs, cfg):
    comp = {}
    for d in docs.values():
        for h in d.get('history') or []:
            if h.get('payout') is not None and h.get('quarter'):
                comp[h['quarter']] = comp.get(h['quarter'], 0) + h['payout']
    comp = {k: r2(v) for k, v in comp.items()}
    pre = r2(sum(p['amount'] for p in cfg['pre_q1']['payouts']))
    paid_on = max(p['paid_on'] for p in cfg['pre_q1']['payouts'])
    stmts = sum(1 for _ in csv.DictReader(open(f'{Q3}/outputs/statements.csv')))
    q1 = json.load(open(f'{DASH}/q1-actuals.json'))['partners']
    q1_total = r2(sum(v['payout'] for v in q1.values()))
    q3f_path = f'{DASH}/q3-actuals.json'
    q3f = json.load(open(q3f_path)) if os.path.exists(q3f_path) else None
    rec = lambda q: sum(1 for d in docs.values() if any(h.get('quarter') == q and h.get('payout') for h in d.get('history') or []))
    common = {'history': [], 'generated_at': NOW, 'updated_at': NOW}
    # Static spec text (labels, notes, sources, payers, expected totals) lives in local-data.json `payout_quarters`;
    # the code fills only what it computes from the registry and the dashboard inputs, in place so key order is kept.
    loc = shared.load_local()
    pick = 'q3_final' if q3f else 'q3_estimate'
    specs = [dict((k, v) for k, v in q.items() if k != 'when') for q in loc['payout_quarters'] if q.get('when') in (None, pick)]
    calc = {
        '2025': dict(total_due=pre, total_paid=pre, paid_on=paid_on, computed=comp.get('2025')),
        '2026-Q1': dict(total_due=q1_total, partners_count=len(q1), computed=comp.get('2026-Q1')),
        '2026-Q2': dict(total_due=comp.get('2026-Q2'), partners_count=rec('2026-Q2'), computed=comp.get('2026-Q2')),
        '2026-Q3': (dict(total_due=r2(q3f['total']), total_paid=r2(q3f['total']), paid_on=shared.paid_on_quarter(),
                         partners_count=len(q3f['partners']), computed=comp.get('2026-Q3'), expected=r2(q3f['total']),
                         notes=[q for q in specs if q['id'] == '2026-Q3'][0]['notes'].format(
                             total=f"{q3f['total']:,.2f}", n=len(q3f['partners']), received=q3f['received']))
                   if q3f else dict(total_due=comp.get('2026-Q3'), partners_count=stmts, statements_count=stmts, computed=comp.get('2026-Q3'))),
    }
    for sp in specs:
        sp.update(calc.get(sp['id'], {}))
    out, rep = [], {}
    for s in specs:
        c, e = s.pop('computed'), s.pop('expected')
        d = {'id': s.pop('id')}; d.update(s)
        d['computed_total'] = c; d['expected_total'] = e
        d['reconciles'] = (c is not None and e is not None and abs(c - e) < 0.01) if e is not None else None
        d.update(common)
        out.append(('payouts', d['id'], d)); rep[d['id']] = (c, e)
    return out, rep


def leads(docs, cfg):
    idx = {}
    for i, d in docs.items():
        for n in [d['name']] + list(d.get('aliases') or []):
            idx.setdefault(norm(n), set()).add(i)
    alias = cfg.get('alias') or {}

    def resolve(name):
        for cand in (name, alias.get(name)):
            if cand and norm(cand) in idx and len(idx[norm(cand)]) == 1:
                return next(iter(idx[norm(cand)]))
        return None
    miss = list(csv.DictReader(open(MISSING)))
    miss_ids = {r['lead_id'] for r in miss}
    miss_co = {norm(r['company']) for r in miss if norm(r['company'])}
    out, st = [], {'status': {}, 'cf_status': {}, 'resolved': 0, 'missing': 0, 'unresolved_partners': set()}
    for l in json.load(open(LEADS))['leads']:
        pid = resolve(l.get('partner'))
        miss_flag = str(l['lead_id']) in miss_ids or (norm(l.get('company')) in miss_co and bool(norm(l.get('company'))))
        d = {'lead_id': l['lead_id'], 'partner': l.get('partner'), 'partner_id': pid, 'company': l.get('company'), 'store_handle': l.get('store_handle'),
             'status': l.get('status'), 'bucket': l.get('bucket'), 'cf_status': l.get('cf_status'), 'paying': bool(l.get('paying')),
             'collected_usd': l.get('collected_usd'), 'referred_at': l.get('referred_at'), 'lost_at': l.get('lost_at'),
             'hubspot_company_id': l.get('hubspot_company_id'), 'hubspot_company_url': l.get('hubspot_company_url'), 'source': l.get('source'),
             'has_contact': any(l.get(k) for k in ('contact_name', 'emails', 'phone', 'linkedin_person')),
             'missing_deal': miss_flag, 'notes': '', 'history': [], 'updated_at': NOW}
        st['status'][d['status']] = st['status'].get(d['status'], 0) + 1
        k = d['cf_status'] or 'none'; st['cf_status'][k] = st['cf_status'].get(k, 0) + 1
        st['resolved'] += pid is not None; st['missing'] += miss_flag
        if pid is None: st['unresolved_partners'].add(l.get('partner'))
        out.append(('leads', str(d['lead_id']), d))
    st['unresolved_partners'] = sorted(x or '' for x in st['unresolved_partners'])
    st['missing_total_in_file'] = len(miss_ids)
    return out, st


def roadmap():
    txt = open(PLAN).read()
    pending = {1: 'D1 commission number', 5: 'D2 auto-approve', 6: 'D3 automation platform', 7: 'D3 automation platform', 8: 'D3 automation platform',
               9: 'D3 automation platform', 10: 'D4 send order', 11: 'D4 send order', 14: 'D5 referral path (quick_refer_url)'}
    items, tier = [], None
    body = txt.split('## Roadmap', 1)[1].split('### Dashboard walkthrough', 1)[0]
    for line in body.splitlines():
        m = re.match(r'### (Tier \d)[:.]\s*(.*)', line)
        if m: tier = f'{m.group(1)}: {m.group(2)}'; continue
        m = re.match(r'(\d+)\. (.*)', line)
        if m and tier:
            n = int(m.group(1)); full = m.group(2)
            title = re.split(r'(?<=[a-z\)])\.\s', full, 1)[0].rstrip('.')
            items.append({'n': n, 'title': title, 'tier': tier, 'status': 'open', 'decision_pending': n in pending,
                          'note': (pending[n] + '. ' if n in pending else '') + full})
    dec = re.findall(r'^\d+\. (.*)$', txt.split('## Decisions needed from Ido', 1)[1].split('## Published', 1)[0], re.M)
    pub = re.search(r'\*\*Interactive roadmap.*', txt)
    return {'items': items, 'decisions_open': dec, 'source': 'docs/projects/partnerio-optimization/plan.md (2026-10-12); status "open" for all: the plan records none done',
            'generated_at': NOW}


def settings(docs):
    tiers, types = {}, {}
    for d in docs.values():
        tiers[d.get('tier')] = tiers.get(d.get('tier'), 0) + 1
        types[d.get('partner_type')] = types.get(d.get('partner_type'), 0) + 1
    loc = shared.load_local()
    prog = {'commission': loc['program_commission'],
            'tiers': [{'tier': k or 'Unassigned', 'count': v} for k, v in sorted(tiers.items(), key=lambda x: -x[1])],
            'partner_types': [{'id': TYPE_IDS.get(k), 'name': k or 'Unassigned', 'count': v} for k, v in sorted(types.items(), key=lambda x: -x[1])]
                             + [{'id': i, 'name': n, 'count': 0} for n, i in TYPE_IDS.items() if n not in types],
            'revenue_sync': loc['links']['revenue_sync'],
            'refresh_routine': loc['links']['refresh_routine'],
            'links': loc['links']['program_links'],
            'generated_at': NOW, 'updated_at': NOW}
    return prog


def main():
    docs = registry(); cfg = json.load(open(f'{DASH}/config.json'))
    po, prep = payouts(docs, cfg)
    ld, lst = leads(docs, cfg)
    rm = roadmap(); pg = settings(docs)
    allw = po + ld + [('settings', 'program', pg), ('settings', 'roadmap', rm)]
    hits = []
    for col, i, d in allw:
        if col != 'payouts': hits += shared.pii_hits(json.dumps(d))
    if hits:
        print('PII HITS, aborting:', hits[:10]); sys.exit(1)
    for p in glob.glob(f'{OUT}/batch-*.json'): os.remove(p)
    for c in ('payouts', 'leads', 'settings'): shutil.rmtree(f'{OUT}/{c}', ignore_errors=True); os.makedirs(f'{OUT}/{c}')
    writes = []
    for col, i, d in allw:
        p = f'{OUT}/{col}/{i}.json'
        json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1)
        writes.append({'op': 'set', 'collection': col, 'doc_id': i, 'file_path': p})
    for k in range(0, len(writes), 50):
        json.dump(writes[k:k + 50], open(f'{OUT}/batch-{k // 50 + 1:02d}.json', 'w'))
    print(json.dumps({'docs': {'payouts': len(po), 'leads': len(ld), 'settings': 2}, 'batches': (len(writes) + 49) // 50,
                      'payouts_computed_vs_expected': prep, 'leads': lst, 'pii_hits': 0,
                      'roadmap_items': len(rm['items']), 'decisions': len(rm['decisions_open']), 'tiers': pg['tiers'], 'types': pg['partner_types']}, indent=1))


if __name__ == '__main__':
    main()
