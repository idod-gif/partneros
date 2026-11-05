#!/usr/bin/env python3
"""The PartnerOS snapshot shape: one source of truth for what the page reads from a dashboard snapshot.

Imported by build.py (the inlined seed) and by scripts/partneros/refresh.py (`--slim-out`, the
`snapshots/latest` doc the PartnerOS refresh run writes). Standard library only, no repo imports; it sits next to refresh.py.

    slim_dash(d)        dashboard.json -> the fields PartnerOS reads (unchanged since build 4)
    compact(s)          drops store-months that carry nothing and rounds money to cents (the page treats a missing
                        month as zero; an entry is dropped only when dropping it reads the same)
    slim_snapshot(d)    compact(slim_dash(d))
    split(s, limit)     (latest, shards): latest as is when it fits under `limit` bytes minified; else latest without
                        `stores` plus `stores_shards: n` and `stores_count`, and n shard docs {generated, i, n, stores}
                        for `snapshots/stores-<i>`, each under `limit`
    js(obj)             the minified JSON the size checks and the build use
"""
import json, re

LIMIT = 240000  # bytes per db doc, minified (the platform limit is 256 KiB; this keeps headroom)

STORE_KEEP = ['partner', 'company', 'handle', 'hs_id', 'lead_id', 'lead_status', 'scope', 'added_since_cut', 'by_month', 'paying_since',
              # build 4, Dashboard section (ported leadership dashboard): spend basis, product split, no-commission rule
              'no_commission', 'product_by_month', 'commissionable_by_month']
TOP_KEEP = ['generated', 'window', 'months', 'periods', 'registry', 'trend_doc',
            # build 4: rate default, partial month, the dashboard's limits and Q2 source figures for the Spend assumptions card
            'defaults', 'partial_month', 'cut_label', 'limits', 'q2_sources', 'q2_recorded_paid', 'q3_final_total',
            'realized_alerts_margin', 'q2_realized_alerts_margin']
# build 4: per-partner fields compute() reads (payout actuals per period come from <period>_actual_payout[_net])
PARTNER_KEEP = ['name', 'display_name', 'scope', 'no_commission', 'rate_override', 'active_since', 'signed_on', 'partnerio_created', 'lifetime_products']
LIFETIME_KEEP = ['stripe', 'shopify', 'total', 'products', 'shopify_unsplit', 'first_paid_invoice']
GM_KEEP = ['alerts_share', 'alerts_margin', 'gm_source']
ALLTIME_KEEP = ['stripe_asof', 'shopify_asof', 'pre_q2_by_partner']
PRE_Q1_KEEP = ['partner', 'period', 'amount', 'paid_on']


def js(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')


def size(obj):
    return len(js(obj).encode('utf-8'))


def slim_dash(d):
    if d is None:
        return None
    out = {k: d.get(k) for k in TOP_KEEP if k in d}
    out['stores'] = [{k: s.get(k) for k in STORE_KEEP} for s in d.get('stores', [])]
    # Overview: All time revenue = lifetime.total per store (Stripe to alltime.stripe_asof plus Shopify to alltime.shopify_asof), all stores
    for src, dst in zip(d.get('stores', []), out['stores']):
        lt = src.get('lifetime') or {}
        dst['lifetime_stripe'], dst['lifetime_shopify'], dst['lifetime_total'] = lt.get('stripe'), lt.get('shopify'), lt.get('total')
    out['stripe_asof'] = (d.get('alltime') or {}).get('stripe_asof')
    out['shopify_asof'] = (d.get('alltime') or {}).get('shopify_asof')
    # build 4: what the ported dashboard compute() needs on top (Dashboard section cards)
    for src, dst in zip(d.get('stores', []), out['stores']):
        lt = src.get('lifetime')
        if isinstance(lt, dict):
            dst['lifetime'] = {k: lt.get(k) for k in LIFETIME_KEEP if k in lt}
        gm = src.get('gm_by_month')
        if isinstance(gm, dict):
            dst['gm_by_month'] = {m: {k: v.get(k) for k in GM_KEEP if k in v} for m, v in gm.items() if isinstance(v, dict)}
    out['partners'] = [dict({k: p.get(k) for k in PARTNER_KEEP if k in p},
                            **{k: v for k, v in p.items() if re.search(r'_actual_payout(_net)?$', k)})
                       for p in d.get('partners', []) if isinstance(p, dict)]
    at = d.get('alltime')
    if isinstance(at, dict):
        out['alltime'] = {k: at.get(k) for k in ALLTIME_KEEP if k in at}
        if isinstance(at.get('payouts_pre_q1'), list):
            out['alltime']['payouts_pre_q1'] = [{k: r.get(k) for k in PRE_Q1_KEEP} for r in at['payouts_pre_q1'] if isinstance(r, dict)]
    return out


def _zero(v):
    return v is None or v == 0


def _cents(v):
    return round(v, 2) if isinstance(v, float) else v


def compact(s):
    """In place, returns s. Every page reader treats a missing store-month as zero (by_month), as "no split, all Other"
    (product_by_month; equal when the month earned 0), as "fall back to by_month" (commissionable_by_month) and as
    "no line items" (gm_by_month; it only weighs revenue of that month). So a month is dropped only where the store
    earned nothing in it and the entry adds nothing. Money (by_month, commissionable, product and lifetime dollars) is
    rounded to cents; shares and margins are left as they are."""
    if not s:
        return s
    for st in s.get('stores') or []:
        bm = st.get('by_month')
        bm = bm if isinstance(bm, dict) else {}
        pb, cb, gm = (st.get(k) if isinstance(st.get(k), dict) else None
                      for k in ('product_by_month', 'commissionable_by_month', 'gm_by_month'))
        for m in list(pb or {}):
            e = pb[m]
            if _zero(bm.get(m)) and (not isinstance(e, dict) or all(_zero(x) for x in e.values())):
                del pb[m]
            elif isinstance(e, dict):
                pb[m] = {k: _cents(x) for k, x in e.items()}
        for m in list(cb or {}):
            if cb[m] is None or (cb[m] == 0 and _zero(bm.get(m))):
                del cb[m]
            else:
                cb[m] = _cents(cb[m])
        for m in list(gm or {}):
            if _zero(bm.get(m)) and m not in (pb or {}):
                del gm[m]
        for m in list(bm):
            if _zero(bm[m]):
                del bm[m]
            else:
                bm[m] = _cents(bm[m])
        lt = st.get('lifetime')
        if isinstance(lt, dict):
            for k in ('stripe', 'shopify', 'total', 'shopify_unsplit'):
                if k in lt:
                    lt[k] = _cents(lt[k])
            if isinstance(lt.get('products'), dict):
                lt['products'] = {k: _cents(x) for k, x in lt['products'].items()}
        for k in ('lifetime_stripe', 'lifetime_shopify', 'lifetime_total'):
            if k in st:
                st[k] = _cents(st[k])
    return s


def slim_snapshot(d):
    return compact(slim_dash(d))


def split(s, limit=LIMIT):
    """(latest, shards). No shards while the whole doc fits; otherwise stores go to shard docs, in order, each under limit."""
    if s is None or size(s) <= limit:
        return s, []
    head = {k: v for k, v in s.items() if k != 'stores'}
    stores = s.get('stores') or []
    chunks, cur = [], []
    base = size({'generated': s.get('generated'), 'i': 999, 'n': 999, 'stores': []})
    run = base
    for st in stores:
        b = size(st) + 1
        if cur and run + b > limit:
            chunks.append(cur)
            cur, run = [], base
        cur.append(st)
        run += b
    if cur:
        chunks.append(cur)
    n = len(chunks)
    head['stores_shards'] = n
    head['stores_count'] = len(stores)
    shards = [{'generated': s.get('generated'), 'i': i, 'n': n, 'stores': c} for i, c in enumerate(chunks)]
    for sh in shards:
        if size(sh) > limit:
            raise ValueError(f'shard {sh["i"]} is {size(sh):,} bytes, over {limit:,} (one store too large?)')
    if size(head) > limit:
        raise ValueError(f'snapshot without stores is {size(head):,} bytes, over {limit:,}')
    return head, shards
