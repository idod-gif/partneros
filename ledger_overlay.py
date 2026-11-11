#!/usr/bin/env python3
"""BI look-back ledger (BI's all-time look-back ledger, published in Notion) -> per-merchant all-time figures.

Reads the ledger xlsx (v2 tab layout) and returns one record per primary chargeflow_id:
    stripe_collected_alltime, stripe_invoiced_alltime, shopify_billed_alltime, first_paid, last_paid,
    invoice_count (Stripe invoices above zero, any status), paid_invoice_count, shopify_charges, lead_id, store, partner
Second accounts (alt_chargeflow_id) are merged into their lead's primary chargeflow_id.
Only rows with counted == "yes" are kept. Totals come from the transaction tabs (Invoices, Collected, Shopify charges)
and are cross-checked against "Summary per merchant"; a mismatch over one dollar is a hard error.

apply_overlay() puts those figures on a PartnerOS full snapshot (refresh.py --out snapshot.json): per store
lifetime.stripe = ledger Stripe collected, lifetime.shopify = ledger Shopify billed, total, shopify_unsplit and the product
split recomputed, partner lifetime_products re-summed, alltime as-of dates and source set to the ledger, and the
"All time: Stripe lifetime ..." limits line replaced. A store with no ledger merchant keeps its HubSpot Stripe lifetime and
gets shopify 0 (never the Aug 2 total_store_spent export), flagged lifetime_source "hubspot_fallback". Monthly series,
periods, payouts and Q3 figures are not touched. Idempotent.

Standard library plus openpyxl (loader only). CLI:
    python3 -I ledger_overlay.py LEDGER.xlsx [--json ledger.json]          # loader: totals and as-of date
    python3 -I ledger_overlay.py apply --snapshot snapshot.json --ledger LEDGER.xlsx|ledger.json --leads referred-merchants.csv
        [--config config.json] --out snapshot.json --slim-out slim.json [--report overlay-report.json]
"""
import json, re, sys
from collections import defaultdict

for _p in ('/Users/idod/Library/Python/3.9/lib/python/site-packages',):
    if _p not in sys.path:
        sys.path.append(_p)
import openpyxl  # noqa: E402

LABEL = 'BI ledger v2, 2026-11-08'


def _rows(ws):
    it = ws.iter_rows(values_only=True)
    head = [str(h).strip() if h is not None else '' for h in next(it)]
    for r in it:
        if r is None or all(c is None for c in r):
            continue
        yield dict(zip(head, r))


def _s(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _d(v):
    if v is None or v == '':
        return None
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d')
    return str(v)[:10]


def load_ledger(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    need = ('README', 'Summary per merchant', 'Invoices', 'Collected', 'Shopify charges')
    miss = [t for t in need if t not in wb.sheetnames]
    if miss:
        raise SystemExit(f'ERROR: ledger is missing tabs {miss}; not the v2 layout')
    v4 = 'Shopify payouts' in wb.sheetnames
    # as-of: the README "Run" row ("ClickHouse, read-only, 8 Nov 2026: ...")
    asof, version = None, None
    months = {m: i + 1 for i, m in enumerate(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'))}
    for r in wb['README'].iter_rows(values_only=True):
        k, v = (_s(r[0]) if r else ''), (_s(r[1]) if r and len(r) > 1 else '')
        if k == 'Run' and not asof:
            ms = list(re.finditer(r'(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{4})', v))
            if ms:   # v2/v3: the first date of the Run row; v4 adds later queries and the Shopify export, so its latest date
                ds = [f'{m.group(3)}-{months[m.group(2)]:02d}-{int(m.group(1)):02d}' for m in ms]
                asof = max(ds) if v4 else ds[0]
        if k == 'Version' and not version:
            m = re.match(r'(v\d+)', v)
            version = m.group(1) if m else None
    if not asof:
        raise SystemExit('ERROR: no run date in README')

    # leads: counted rows, primary and alt chargeflow ids
    lead_cf, alt_to_primary, summary = {}, {}, {}
    for r in _rows(wb['Summary per merchant']):
        if _s(r.get('counted')).lower() != 'yes':
            continue
        lid, cf, alt = _s(r.get('lead_id')), _s(r.get('chargeflow_id')), _s(r.get('alt_chargeflow_id'))
        if not cf:
            continue
        lead_cf[lid] = cf
        if alt:
            alt_to_primary[alt] = cf
        summary[cf] = r
    out = {cf: {'chargeflow_id': cf, 'alt_chargeflow_id': _s(summary[cf].get('alt_chargeflow_id')) or None, 'lead_id': lid,
                'store': _s(summary[cf].get('store')), 'partner': _s(summary[cf].get('partner')),
                'stripe_collected_alltime': 0.0, 'stripe_invoiced_alltime': 0.0, 'shopify_billed_alltime': 0.0,
                'first_paid': None, 'last_paid': None, 'first_billed': _d(summary[cf].get('first_billed_date')),
                'last_billed': _d(summary[cf].get('last_billed_date')),
                'invoice_count': 0, 'paid_invoice_count': 0, 'shopify_charges': 0,
                # v4 rails (collected): Stripe rail = collected in Stripe, Shopify rail = gross the merchant paid on the Shopify bill
                'stripe_rail_collected': 0.0, 'shopify_gross_collected': 0.0, 'shopify_fees': 0.0, 'shopify_net_received': 0.0}
           for lid, cf in lead_cf.items()}

    def key(r):
        lid, cf = _s(r.get('lead_id')), _s(r.get('chargeflow_id'))
        if lid in lead_cf:
            return lead_cf[lid]
        return alt_to_primary.get(cf, cf if cf in out else None)

    skipped = defaultdict(float)
    for r in _rows(wb['Invoices']):
        k = key(r)
        if k is None:
            skipped['invoices'] += _f(r.get('attributed_invoiced_usd'))
            continue
        if _s(r.get('invoice_status')).lower() not in ('draft', 'void'):
            out[k]['stripe_invoiced_alltime'] += _f(r.get('attributed_invoiced_usd'))
        out[k]['invoice_count'] += 1
    for r in _rows(wb['Collected']):
        k = key(r)
        if k is None:
            skipped['collected'] += _f(r.get('attributed_collected_usd'))
            continue
        e = out[k]
        e['stripe_collected_alltime'] += _f(r.get('attributed_collected_usd'))
        if v4 and not _s(r.get('collected_via')).lower().startswith('shopify'):
            e['stripe_rail_collected'] += _f(r.get('attributed_collected_usd'))
        e['paid_invoice_count'] += 1
        pd = _d(r.get('paid_date'))
        if pd:
            e['first_paid'] = min(e['first_paid'] or pd, pd)
            e['last_paid'] = max(e['last_paid'] or pd, pd)
    for r in _rows(wb['Shopify charges']):
        k = key(r)
        if k is None:
            skipped['shopify'] += _f(r.get('shopify_billed_usd'))
            continue
        if _s(r.get('counted')).lower().startswith('no'):      # v3+: a charge that pays a Stripe invoice already counted is listed, not counted
            continue
        out[k]['shopify_billed_alltime'] += _f(r.get('shopify_billed_usd'))
        out[k]['shopify_charges'] += 1

    if v4:
        for r in _rows(wb['Shopify payouts']):
            k = key(r)
            if k is None:
                skipped['shopify_payouts'] += _f(r.get('shopify_gross_usd'))
                continue
            e = out[k]
            e['shopify_gross_collected'] += _f(r.get('shopify_gross_usd'))
            e['shopify_fees'] += _f(r.get('shopify_fees_usd'))
            e['shopify_net_received'] += _f(r.get('shopify_net_usd'))

    # cross-check against the Summary tab
    bad = []
    for cf, e in out.items():
        sr = summary[cf]
        for mine, theirs in (('stripe_collected_alltime', 'stripe_collected_usd'), ('stripe_invoiced_alltime', 'stripe_invoiced_usd'),
                             ('shopify_billed_alltime', 'shopify_billed_usd')):
            e[mine] = round(e[mine], 2)
            if abs(e[mine] - _f(sr.get(theirs))) > 1:
                bad.append((cf, mine, e[mine], _f(sr.get(theirs))))
    if v4:
        for cf, e in out.items():
            sr = summary[cf]
            for mine, theirs in (('stripe_rail_collected', 'collected_in_stripe_usd'), ('shopify_gross_collected', 'shopify_gross_collected_usd'),
                                 ('shopify_fees', 'shopify_fees_usd'), ('shopify_net_received', 'shopify_net_received_usd')):
                e[mine] = round(e[mine], 2)
                if abs(e[mine] - _f(sr.get(theirs))) > 1:
                    bad.append((cf, mine, e[mine], _f(sr.get(theirs))))
    if bad:
        raise SystemExit(f'ERROR: transaction tabs disagree with Summary per merchant on {len(bad)} values, first: {bad[:3]}')
    return {'rail_mode': 'collected' if v4 else 'billed', 'asof': asof, 'version': version, 'label': f'BI ledger {version}, {asof}', 'merchants': out,
            'lead_to_cfid': lead_cf, 'alt_to_primary': alt_to_primary, 'skipped_uncounted': dict(skipped)}


def totals(led):
    m = led['merchants'].values()
    t4 = {}
    if any('shopify_gross_collected' in e for e in m) and led.get('version', '') >= 'v4':
        t4 = {'stripe_rail_collected': round(sum(e['stripe_rail_collected'] for e in m), 2),
              'shopify_gross_collected': round(sum(e['shopify_gross_collected'] for e in m), 2),
              'shopify_fees': round(sum(e['shopify_fees'] for e in m), 2),
              'shopify_net_received': round(sum(e['shopify_net_received'] for e in m), 2)}
    return {**t4, 'merchants': len(led['merchants']),
            'billed': sum(1 for e in m if e['stripe_invoiced_alltime'] > 0 or e['shopify_billed_alltime'] > 0),
            'stripe_collected': round(sum(e['stripe_collected_alltime'] for e in m), 2),
            'stripe_invoiced': round(sum(e['stripe_invoiced_alltime'] for e in m), 2),
            'shopify_billed': round(sum(e['shopify_billed_alltime'] for e in m), 2)}



# ---- overlay onto a PartnerOS snapshot -------------------------------------------------------------------------------
import csv, os  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

PRODUCT_NAMES = ('alerts', 'automation', 'prevent', 'other')
OLD_LIMIT_PREFIX = 'All time: '   # the pre-ledger limits_alltime line (local-data.json), replaced
MARK = 'from BI\'s verified ledger'


def _rails(led, e):
    """(stripe rail, shopify rail) for a ledger merchant. v4: collected in Stripe, and what the merchant paid on the Shopify bill
    (gross, before Shopify's fees). Older ledgers: Stripe collected and Shopify billed."""
    if led.get('rail_mode') == 'collected':
        return round(e['stripe_rail_collected'], 2), round(e['shopify_gross_collected'], 2)
    return round(e['stripe_collected_alltime'], 2), round(e['shopify_billed_alltime'], 2)


def limits_lines(led, n_fallback, unmatched):
    a = led['asof']
    d = datetime.strptime(a, '%Y-%m-%d')
    lab = f'{d.strftime("%b")} {d.day}, {d.year}'
    um = round(sum(sum(_rails(led, e)) for e in unmatched))
    if led.get('rail_mode') == 'collected':
        return [
            f'All time: collected from referred merchants {MARK} ({led["label"]}, to {lab}): the Stripe rail is cash paid on Stripe invoices '
            f'(card, bank transfer, earlier billing system); the Shopify rail is what merchants paid on their Shopify bill for Chargeflow, '
            f'gross, refunds taken off. Shopify keeps a fee, so what Chargeflow received is lower: per store it is in the Shopify fees and '
            f'net fields of the snapshot. Open invoices and money billed but not collected are not counted.',
            f'Months, periods, the trend and the forecast still use HubSpot Stripe paid invoices by invoice-created month, which miss the old '
            f'billing system and Shopify, so they do not tie to All time. ' + _count(n_fallback, 'store', 'with no ledger figure shows', 'with no ledger figure show')
            + f' HubSpot Stripe and no Shopify; ' + _count(len(unmatched), 'collected ledger merchant', 'with no PartnerOS store', 'with no PartnerOS store')
            + f' (${um:,}) ' + ('is' if len(unmatched) == 1 else 'are') + ' not counted.']
    return [
        f'All time: Stripe collected (paid invoices of both billing systems, from Sep 2023) plus Shopify Billing charges billed '
        f'(from Mar 2022) per store, {MARK} ({led["label"]}, to {lab}); Shopify is billed, its collection is not in the data, and '
        f'refunds after payment are not deducted.',
        f'Months, periods, the trend and the forecast still use HubSpot Stripe paid invoices, which miss the old billing system, '
        f'so months before 2026 understate against All time. ' + _count(n_fallback, 'store', 'with no ledger figure shows', 'with no ledger figure show')
        + f' HubSpot Stripe and no Shopify; ' + _count(len(unmatched), 'billed ledger merchant', 'with no PartnerOS store', 'with no PartnerOS store')
        + f' (${um:,}) ' + ('is' if len(unmatched) == 1 else 'are') + ' not counted.']


def _count(n, noun, one, many):
    return f'{n} {noun} {one}' if n == 1 else f'{n} {noun}s {many}'


def _shares(store, cfg_shares):
    sh = (cfg_shares or {}).get(store.get('hs_id')) if store.get('hs_id') else None
    if sh:
        return sh
    lt = store.get('lifetime') or {}
    pr, st = lt.get('products'), float(lt.get('stripe') or 0)
    if isinstance(pr, dict) and st > 0:
        return {k: float(pr.get(k) or 0) / st for k in PRODUCT_NAMES}
    return None


def apply_overlay(snap, led, leads_csv_rows=(), cfg_shares=None):
    L2C, A2P, M = led['lead_to_cfid'], led['alt_to_primary'], led['merchants']
    hs2lead = {}
    for r in leads_csv_rows:
        h = (r.get('hubspot_company_id') or '').strip()
        if h:
            hs2lead.setdefault(h, []).append((r.get('lead_id') or '').strip())
    rep = {'matched': [], 'fallback': [], 'ledger_zero_hubspot_paid': [], 'ledger_unmatched': []}
    used = set()
    for s in snap['stores']:
        cf, how = None, None
        lid, cfid, hs = (s.get('lead_id') or '').strip(), (s.get('cfid') or '').strip(), (s.get('hs_id') or '').strip()
        if lid and lid in L2C:
            cf, how = L2C[lid], 'lead_id'
        elif cfid and (cfid in M or cfid in A2P):
            cf, how = A2P.get(cfid, cfid), 'cfid'
        elif hs:
            c = {L2C[x] for x in hs2lead.get(hs, []) if x in L2C}
            if len(c) == 1:
                cf, how = c.pop(), 'hs_id'
        if cf in used:            # never count one ledger merchant on two stores
            cf, how = None, 'duplicate'
        old = dict(s.get('lifetime') or {})
        s.setdefault('lifetime_prev', {'stripe': old.get('stripe'), 'shopify': old.get('shopify'), 'total': old.get('total')})
        lt = s['lifetime'] = dict(old) if old else {'stripe': 0.0, 'shopify': None, 'total': 0.0, 'first_paid_invoice': None}
        sh = _shares(s, cfg_shares)
        if cf:
            used.add(cf)
            e = M[cf]
            st, sp = _rails(led, e)
            s['lifetime_source'], s['ledger_cfid'], s['ledger_match'] = 'ledger', cf, how
            s['ledger'] = {k: e[k] for k in ('stripe_collected_alltime', 'stripe_invoiced_alltime', 'shopify_billed_alltime',
                                             'first_paid', 'last_paid', 'invoice_count', 'paid_invoice_count', 'shopify_charges',
                                             'stripe_rail_collected', 'shopify_gross_collected', 'shopify_fees', 'shopify_net_received') if k in e}
            if led.get('rail_mode') == 'collected':
                lt['shopify_fees'], lt['shopify_net'] = round(e['shopify_fees'], 2), round(e['shopify_net_received'], 2)
            rep['matched'].append(s)
            if st == 0 and float(s['lifetime_prev'].get('stripe') or 0) > 0:
                rep['ledger_zero_hubspot_paid'].append(s)
        else:
            st, sp = round(float(old.get('stripe') or 0), 2), 0.0
            if s.get('lifetime_source') == 'ledger':      # re-run on an overlaid snapshot: keep the previous HubSpot figure
                st = round(float(s['lifetime_prev'].get('stripe') or 0), 2)
            s['lifetime_source'], s['ledger_cfid'], s['ledger_match'] = 'hubspot_fallback', None, how
            s.pop('ledger', None)
            lt.pop('shopify_fees', None), lt.pop('shopify_net', None)
            rep['fallback'].append(s)
        lt['stripe'], lt['shopify'], lt['total'] = st, sp, round(st + sp, 2)
        lt['shopify_unsplit'] = sp
        if sh:
            a, au, p = (round(st * float(sh.get(k) or 0), 2) for k in PRODUCT_NAMES[:3])
            lt['products'] = {'alerts': a, 'automation': au, 'prevent': p, 'other': round(st - a - au - p, 2)}
            lt['products_source'] = 'line_items'
        elif st > 0:   # no line items: the page's rule for a store-month without lines, all Other, so the split sums to the total
            lt['products'] = {'alerts': 0.0, 'automation': 0.0, 'prevent': 0.0, 'other': st}
            lt['products_source'] = 'none_as_other'
        else:
            lt['products'], lt['products_source'] = None, 'none'
    psum = {}
    for s in snap['stores']:
        pr = (s.get('lifetime') or {}).get('products')
        if isinstance(pr, dict):
            d = psum.setdefault(s['partner'], dict.fromkeys(PRODUCT_NAMES, 0.0))
            for k in PRODUCT_NAMES:
                d[k] += pr[k]
    for p in snap.get('partners') or []:
        d = psum.get(p['name'])
        p['lifetime_products'] = {k: round(v, 2) for k, v in d.items()} if d else dict.fromkeys(PRODUCT_NAMES, 0.0)
    rep['ledger_unmatched'] = [e for k, e in M.items() if k not in used and any(_rails(led, e))]
    at = snap.setdefault('alltime', {})
    at.update({'stripe_asof': led['asof'], 'shopify_asof': led['asof'], 'source': led['label'],
               'stripe_basis': 'collected (BI ledger)',
               'shopify_basis': 'collected gross (BI ledger)' if led.get('rail_mode') == 'collected' else 'billed (BI ledger)',
               'ledger_unmatched': [{**{k: e[k] for k in ('lead_id', 'store', 'partner')}, 'stripe_collected_alltime': _rails(led, e)[0],
                                     'shopify_billed_alltime': _rails(led, e)[1]} for e in rep['ledger_unmatched']],
               'ledger_fallback_stores': len(rep['fallback'])})
    if led.get('rail_mode') == 'collected':
        at.update({'shopify_fees': round(sum((s.get('lifetime') or {}).get('shopify_fees') or 0 for s in snap['stores']), 2),
                   'shopify_net': round(sum((s.get('lifetime') or {}).get('shopify_net') or 0 for s in snap['stores']), 2)})
    new = limits_lines(led, len(rep['fallback']), rep['ledger_unmatched'])
    lim = snap.get('limits') or []
    idx = [i for i, t in enumerate(lim) if t.startswith(OLD_LIMIT_PREFIX) or MARK in t or t.startswith('Months, periods, the trend and the forecast still use')]
    if idx:
        lim = lim[:idx[0]] + new + [t for i, t in enumerate(lim) if i > idx[0] and i not in idx]
    else:
        lim = lim + new
    snap['limits'] = lim
    snap['generated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return rep


def cmd_apply(argv):
    import argparse
    ap = argparse.ArgumentParser(prog='ledger_overlay.py apply')
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--ledger', required=True)
    ap.add_argument('--leads')
    ap.add_argument('--config')
    ap.add_argument('--out', required=True)
    ap.add_argument('--slim-out')
    ap.add_argument('--slim-dir', default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument('--report')
    a = ap.parse_args(argv)
    led = json.load(open(a.ledger)) if a.ledger.endswith('.json') else load_ledger(a.ledger)
    snap = json.load(open(a.snapshot))
    rows = list(csv.DictReader(open(a.leads, encoding='utf-8-sig'))) if a.leads else []
    shares = (json.load(open(a.config)).get('lifetime_products') or {}) if a.config else {}
    rep = apply_overlay(snap, led, rows, shares)
    json.dump(snap, open(a.out, 'w'), indent=1, ensure_ascii=False)
    tot = round(sum((s.get('lifetime') or {}).get('total') or 0 for s in snap['stores']), 2)
    print(f'overlay {led["label"]}: {len(rep["matched"])} stores on the ledger, {len(rep["fallback"])} HubSpot fallback, '
          f'{len(rep["ledger_unmatched"])} billed ledger merchants with no store; all time {tot:,.2f}')
    if a.slim_out:
        sys.path.insert(0, a.slim_dir)
        import slim  # noqa: E402
        sl = slim.slim_snapshot(snap)
        latest, shards = slim.split(sl)
        open(a.slim_out, 'w').write(slim.js(latest))
        for sh in shards:
            open(a.slim_out.replace('.json', f'-stores-{sh["i"]}.json'), 'w').write(slim.js(sh))
        print(f'slim {slim.size(latest):,} bytes, {len(shards)} shard(s)')
    if a.report:
        pick = lambda s: {k: s.get(k) for k in ('lead_id', 'hs_id', 'company', 'partner', 'scope', 'ledger_match', 'lifetime_prev', 'lifetime')}  # noqa: E731
        json.dump({k: [pick(s) for s in v] if k != 'ledger_unmatched' else v for k, v in rep.items()}, open(a.report, 'w'), indent=1, default=str)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'apply':
        cmd_apply(sys.argv[2:])
        sys.exit(0)
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    led = load_ledger(sys.argv[1])
    print(json.dumps({'asof': led['asof'], 'version': led['version'], 'label': led['label'], **totals(led),
                      'alt_merged': len(led['alt_to_primary']), 'skipped_uncounted': led['skipped_uncounted']}, indent=1))
    if '--json' in sys.argv:
        json.dump(led, open(sys.argv[sys.argv.index('--json') + 1], 'w'), indent=1)
