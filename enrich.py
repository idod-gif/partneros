#!/usr/bin/env python3
"""PartnerOS Step 2: enrich partner docs with payout history, partner page state and Partner Connect state.

    python3 enrich.py                      # writes the new fields into the local registry files
    python3 enrich.py --db-dir DIR --out DIR2 [--versions FILE] [--history-only]
        # --history-only: payloads {history, payouts_summary, updated_at} for docs whose history changes, nothing else
        # (used for the Q3 final, 2026-10-21: with q3-actuals.json present, Q3 estimates are replaced by final entries)
        # DIR = ArtifactData list out_dir of the partners collection (DIR/partners/<id>.json). Builds, per doc,
        # an `update` payload (updated_at, history, payouts_summary, page, connect) merged onto the db history
        # (page-written entries kept, dedupe on (quarter, source)), a PII scan, and batch-NN.json manifests
        # (entries {op:update, collection, doc_id, file_path, if_version}). Versions come from --versions FILE
        # (json {doc_id: version}), default 1.

Fields: history[] payout entries {quarter, rate (percent), payout, paid_on, status, label, source};
payouts_summary; page {slug,state,url}; connect {wave,priority_bucket,has_linkedin,lookup_status,note_status,
excluded,exclude_reason}. Never copies rep names, emails or LinkedIn URLs. Idempotent. Local registry only plus
read-only inputs; agreement, flags and by:"page" history entries are never touched."""
import csv, json, os, sys, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared

ROOT = '/Users/idod/dev'
Q3 = f'{ROOT}/docs/finance/payouts/2026-q3'
DASH = f'{Q3}/dashboard'
PDIR = f'{shared.REG}/partners'
NOW = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
SRC = shared.load_local()['source_labels']   # source strings stamped on history entries; text lives in local-data.json
SRC_Q1, SRC_Q2, SRC_Q3, SRC_Q3_FINAL, SRC_PRE = SRC['q1'], SRC['q2'], SRC['q3_estimate'], SRC['q3_final'], SRC['pre_q1']
Q3_FINAL = f'{DASH}/q3-actuals.json'   # present = Q3 is final: these entries replace the SRC_Q3 estimates
LOOKUP_RANK = {'have': 3, 'found': 2, 'queued': 1, 'skipped': 0}


def norm(s):
    return (s or '').strip().lower()


def load_docs():
    docs = {}
    for f in os.listdir(PDIR):
        if f.endswith('.json'):
            d = json.load(open(f'{PDIR}/{f}'))
            docs[int(d['partnerio_id'])] = d
    return docs


def build_index(docs):
    idx = {}
    for i, d in docs.items():
        for n in [d['name']] + list(d.get('aliases') or []):
            idx.setdefault(norm(n), set()).add(i)
    return idx


def resolve(name, idx, alias):
    """name -> set of partner ids, via the name itself, then the config alias map target."""
    for cand in (name, alias.get(name)):
        if cand and norm(cand) in idx:
            return idx[norm(cand)]
    return set()


def payout_entries(docs, report):
    cfg = json.load(open(f'{DASH}/config.json'))
    alias = cfg.get('alias') or {}
    idx = build_index(docs)
    out = {}  # id -> list of entries
    add = lambda i, e: out.setdefault(i, []).append(e)
    # Q1
    for name, v in json.load(open(f'{DASH}/q1-actuals.json'))['partners'].items():
        ids = resolve(name, idx, alias)
        if len(ids) != 1:
            report['ambiguous'].append(name); continue
        add(next(iter(ids)), {'quarter': '2026-Q1', 'rate': None, 'payout': round(v['payout'], 2), 'paid_on': None,
                              'status': 'recorded', 'label': 'Q1 2026 payout', 'source': SRC_Q1})
    # Q2 from existing registry entries (seeded from the Q2 source tab)
    for i, d in docs.items():
        for h in d.get('history') or []:
            if h.get('quarter') == '2026-Q2' and 'source' not in h:
                paid_on = h.get('paid_on')
                add(i, {'quarter': '2026-Q2', 'rate': round(h['rate'] * 100, 2) if h.get('rate') is not None and h['rate'] <= 1 else h.get('rate'),
                        'payout': round(h['payout'], 2), 'paid_on': paid_on, 'status': 'paid' if paid_on else 'recorded',
                        'label': 'Q2 2026 payout', 'source': SRC_Q2})
    # pre-Q1 (2025 payments)
    pre = cfg.get('pre_q1') or {}
    by = {}
    for p in pre.get('payouts') or []:
        by.setdefault(p['partner'], []).append(p)
    for name, ps in by.items():
        ids = resolve(name, idx, alias)
        if len(ids) != 1:
            report['ambiguous'].append(name); continue
        add(next(iter(ids)), {'quarter': '2025', 'rate': None, 'payout': round(sum(p['amount'] for p in ps), 2),
                              'paid_on': max(p['paid_on'] for p in ps), 'status': 'paid', 'label': '2025 payouts (H2)',
                              'source': SRC_PRE})
    report['pre_q1'] = {n: round(sum(p['amount'] for p in ps), 2) for n, ps in by.items()}
    # Q3: final actuals when q3-actuals.json exists, else the statements estimate
    if os.path.exists(Q3_FINAL):
        for e in q3_final_entries(docs, idx, alias, report).items():
            add(*e)
        return out
    for r in csv.DictReader(open(f'{Q3}/outputs/statements.csv')):
        name = r['partner']
        ids = resolve(name, idx, alias)
        if len(ids) != 1:
            (report['unmatched'] if not ids else report['ambiguous']).append(name); continue
        i = next(iter(ids))
        nc = (r.get('no_commission flag') or '').strip().lower() in ('yes', 'true', '1')
        add(i, {'quarter': '2026-Q3', 'rate': 0 if nc else 25, 'payout': round(float(r['payout at 25%'] or 0), 2),
                'paid_on': None, 'status': 'estimate', 'label': 'Q3 2026 estimate', 'source': SRC_Q3})
        report['stmt_ids'][name] = i
    return out


def q3_final_entries(docs, idx, alias, report):
    """{id: entry} for Q3 final: partners on the final sheet get status paid (dates from local-data paid_on);
    every other partner on the 32-statement estimate gets payout 0, status not_eligible."""
    fin = json.load(open(Q3_FINAL))['partners']
    out = {}
    for name, v in fin.items():
        ids = resolve(name, idx, alias)
        if len(ids) != 1:
            (report['unmatched'] if not ids else report['ambiguous']).append(name); continue
        i = next(iter(ids))
        out[i] = {'quarter': '2026-Q3', 'rate': 25, 'payout': round(v['payout'], 4), 'paid_on': shared.paid_on()[name], 'status': 'paid',
                  'label': 'Q3 2026 payout (final, paid)', 'source': SRC_Q3_FINAL}
        if name in shared.paid_note():
            out[i]['paid_note'] = shared.paid_note()[name]
        report['stmt_ids'][name] = i
    for r in csv.DictReader(open(f'{Q3}/outputs/statements.csv')):
        ids = resolve(r['partner'], idx, alias)
        if len(ids) == 1 and next(iter(ids)) not in out:
            out[next(iter(ids))] = {'quarter': '2026-Q3', 'rate': 0, 'payout': 0.0, 'paid_on': None, 'status': 'not_eligible',
                                    'label': 'Q3 2026: not on the final sheet (no qualifying signed agreement)', 'source': SRC_Q3_FINAL}
    return out


def merge_history(history, entries):
    """Upgrade old {quarter,rate,payout,label} entries in place, append new ones, dedupe on (quarter, source)."""
    hist = list(history or [])
    added = []
    for e in entries:
        done = False
        for k, h in enumerate(hist):
            if h.get('by') == 'page':
                continue
            if 'source' in h and h.get('quarter') == e['quarter'] and h['source'] == e['source']:
                if e['source'] == SRC_Q3_FINAL:
                    hist[k] = dict(e)  # Q3 final: refresh on re-run (a corrected sheet)
                done = True; break  # already present
            if e.get('source') == SRC_Q3_FINAL and h.get('quarter') == e['quarter'] and h.get('source') == SRC_Q3:
                hist[k] = dict(e); done = True; break  # the final replaces the estimate in place
            if 'quarter' in h and 'source' not in h and h['quarter'] == e['quarter']:
                hist[k] = dict(e); done = True; break  # upgrade in place
        if not done:
            hist.append(dict(e)); added.append(e['quarter'])
    return hist, added


def summary(history):
    pe = [h for h in history if 'quarter' in h and 'status' in h]
    paid = [h for h in pe if h['status'] == 'paid']
    q3 = [h['payout'] for h in pe if h['quarter'] == '2026-Q3' and h['status'] == 'estimate']
    q3f = [h for h in pe if h['quarter'] == '2026-Q3' and h['status'] in ('approved', 'paid', 'not_eligible')]
    dates = [h['paid_on'] for h in paid if h.get('paid_on')]
    s = {'paid_total': round(sum(h['payout'] for h in paid), 2), 'paid_quarters': sorted({h['quarter'] for h in paid}),
         'last_paid_on': max(dates) if dates else None, 'q3_estimate': q3[0] if q3 else None}
    if q3f:
        s['q3_final'] = q3f[0]['payout']; s['q3_status'] = q3f[0]['status']
    return s


def page_state():
    r = json.load(open(f'{ROOT}/scripts/partner-pages/registry.json'))['partners']
    out = {}
    for k, v in r.items():
        if k.isdigit():
            live = v.get('state') == 'live'
            out[int(k)] = {'slug': v.get('slug'), 'state': v.get('state'),
                           'url': v.get('live_url') if live else v.get('preview_url')}
    return out


def connect_state():
    rows = list(csv.DictReader(open(f'{ROOT}/docs/projects/partner-connect/waves-2026-10.csv')))
    ex = list(csv.DictReader(open(f'{ROOT}/docs/projects/partner-connect/exclusions.csv')))
    ex_names = {norm(e['partner_name']) for e in ex}
    ex_dom = {norm(e['partner_domain']) for e in ex}
    by = {}
    for r in rows:
        by.setdefault(int(r['partner_id']), []).append(r)
    out = {}
    for i, rs in by.items():
        waves = [int(r['wave']) for r in rs if r['wave'].isdigit()]
        wave = min(waves) if waves else None
        top = next((r for r in rs if r['wave'].isdigit() and int(r['wave']) == wave), rs[0])
        reason = next((r['exclude_reason'] for r in rs if r['exclude_reason']), '')
        if not reason and (norm(rs[0]['partner_name']) in ex_names or norm(rs[0]['partner_domain']) in ex_dom):
            reason = 'signed_partner_dm'
        out[i] = {'wave': wave, 'priority_bucket': top['priority_bucket'] or None,
                  'has_linkedin': any(r['linkedin_url'].strip() for r in rs),
                  'lookup_status': max((r['lookup_status'] for r in rs), key=lambda s: LOOKUP_RANK.get(s, -1)) or None,
                  'note_status': 'qa_pass' if any(r['note_status'] == 'qa_pass' for r in rs) else (rs[0]['note_status'] or None),
                  'excluded': bool(reason), 'exclude_reason': reason or None}
    return out


def main():
    argv = sys.argv
    arg = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    docs = load_docs()
    report = {'unmatched': [], 'ambiguous': [], 'stmt_ids': {}}
    pay = payout_entries(docs, report)
    pages, conn = page_state(), connect_state()
    NOPAGE = {'slug': None, 'state': None, 'url': None}
    added = {}

    def fields(history, i):
        es = pay.get(i, [])
        if '--history-only' in argv:   # Q3 entries only: the db's Q1/Q2/2025 entries carry longer source strings, so re-merging would duplicate them
            es = [e for e in es if e['quarter'] == '2026-Q3']
        h, a = merge_history(history, es)
        for q in a:
            added[q] = added.get(q, 0) + 1
        return {'history': h, 'payouts_summary': summary(h), 'page': pages.get(i, NOPAGE),
                'connect': conn.get(i)}

    db_dir = arg('--db-dir')
    if not db_dir:
        n = 0
        for i, d in docs.items():
            f = fields(d.get('history'), i)
            if f['connect'] is None:
                f['connect'] = None
            d.update(f); d['updated_at'] = NOW
            json.dump(d, open(f'{PDIR}/{i}.json', 'w'), indent=2, ensure_ascii=False)
            n += 1
        report['local_updated'] = n
    else:
        out = arg('--out'); os.makedirs(f'{out}/docs', exist_ok=True)
        vers = json.load(open(arg('--versions'))) if arg('--versions') else {}
        writes, hits = [], []
        for fn in sorted(os.listdir(f'{db_dir}/partners')):
            i = int(fn[:-5])
            dbd = json.load(open(f'{db_dir}/partners/{fn}'))
            f = fields(dbd.get('history'), i)
            if '--history-only' in argv:   # payout history refresh only: skip unchanged docs, leave page/connect alone
                if f['history'] == (dbd.get('history') or []):
                    continue
                f = {'history': f['history'], 'payouts_summary': f['payouts_summary']}
            f['updated_at'] = NOW
            txt = json.dumps(f, ensure_ascii=False, separators=(',', ':'))
            hits += [(i, k) for k, _ in shared.pii_hits(txt)]
            p = f'{out}/docs/{i}.json'; open(p, 'w').write(txt)
            writes.append({'op': 'update', 'collection': 'partners', 'doc_id': str(i), 'file_path': p,
                           'if_version': int(vers.get(str(i), 1))})
        for b in range(0, len(writes), 50):
            json.dump(writes[b:b + 50], open(f'{out}/batch-{b // 50 + 1:02d}.json', 'w'))
        report['db_payloads'] = len(writes); report['batches'] = (len(writes) + 49) // 50
        report['pii_hits'] = hits
    q = {}
    for i, es in pay.items():
        for e in es:
            q[e['quarter']] = round(q.get(e['quarter'], 0) + e['payout'], 2)
    report['sums'] = q
    report['entries_added'] = added
    report['with_page'] = sum(1 for i in docs if i in pages)
    report['with_connect'] = sum(1 for i in docs if i in conn)
    report['stmt_not_active'] = [n for n, i in report['stmt_ids'].items() if docs[i]['agreement']['status'] != 'active']
    print(json.dumps({k: v for k, v in report.items() if k != 'stmt_ids'}, indent=1))


if __name__ == '__main__':
    main()
