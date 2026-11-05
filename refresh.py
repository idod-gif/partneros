#!/usr/bin/env python3
"""Standalone refresher for the Affiliate Revenue vs Payout dashboard snapshot.

Standard library only; imports nothing from the repo (only slim.py, for --slim-out). Runs inside a Claude Code session
(see refresh-run.md): the inputs are fetched via MCP and handed in as files.

    python3 refresh.py print-queries --config config.json --leads leads.json [--registry registry.json] --today 2026-10-14
    python3 refresh.py --config config.json --leads leads.json \
        --monthly m1.txt [m2.txt ...] --lifetime l1.txt [l2.txt ...] [--company-dates c1.txt ...] --out snapshot.json [--registry registry.json] [--today YYYY-MM-DD]
        [--history history1.txt ...] [--trend-prev trend-prev.json] [--trend-out trend.json]
    python3 refresh.py make-config [--dir DATA_DIR] [--out config.json]     # local only, needs the data dir
    ... --slim-out latest.json   also writes the PartnerOS `snapshots/latest` doc (slim.py's shape), plus
        latest-stores-<i>.json shard docs when it is over 240000 bytes (see slim.split)

--slim-out imports slim.py (the one source of the PartnerOS snapshot shape, scripts/partneros/slim.py) from this
file's directory first. slim.py lives next to this file.

The snapshot is byte-for-byte the structure build.py writes to dashboard.json
(except `generated`). The series_sums cross-check and the Q3 reconciliation are NOT
part of this script. config.json is sensitive (payout and revenue data): never commit it.

registry.json (optional) = JSON array of PartnerOS partner registry docs (name, aliases, stores, agreement, flags). A partner's
no_commission is flags.no_commission or agreement.status != active; rate_override = agreement.rate only when it differs from defaults.rate (display_name = registry name); a dashboard partner with no
doc keeps the config-only no_commission_partners behaviour. The snapshot gets top-level registry {loaded, active, none, missing}.

leads.json = {"pulled_at": ..., "leads": [{lead_id, store, partner, status, hs_id}]}.
Field mapping from the raw Partner.io MCP get-leads lead object (every lead, any status):
    lead_id <- lead.id            (string)
    store   <- lead.name, else lead.company   (the store / company name)
    partner <- name of the partner entity for lead.partner_id (get-partners), else the rep name
    status  <- lead.status        (approved, won, lost, pending, rejected)
    hs_id   <- lead.customer_id   (HubSpot company id as a string; "" when null)
Test stores need no filtering: names containing "test" are dropped from additions here.
"""
import argparse
import calendar
import copy
import csv
import json
import math
import os
import re
import sys
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timezone

TEST_RE = re.compile(r'\btest\b', re.I)
APPROVED_WON = ('approved', 'won')


# ------------------------------------------------------------ inlined helpers --

def month_bounds(m):
    y, mo = int(m[:4]), int(m[5:7])
    return date(y, mo, 1).isoformat(), date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()


def month_range(m_from, m_to):
    y, mo = int(m_from[:4]), int(m_from[5:7])
    out = []
    while f'{y:04d}-{mo:02d}' <= m_to:
        out.append(f'{y:04d}-{mo:02d}')
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out


def parse_rows(text):
    """(alltime_rows, series_rows, truncated). Rows classified by content, not column order."""
    alltime, series = [], []
    truncated = re.search(r'Showing (\d+) of (\d+)', text)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        cells = [c.strip() for c in re.split(r'\t|\s{2,}|,', line) if c.strip()]
        if len(cells) < 2:
            continue
        month = next((c for c in cells if re.fullmatch(r'\d{4}-\d{2}(-\d{2})?', c)), None)
        rest = [c for c in cells if c != month]
        nums = [c for c in rest if re.fullmatch(r'-?\d+(\.\d+)?', c)]
        if len(nums) < 2:
            continue
        cid = max(nums, key=lambda c: (len(c.split('.')[0]), '.' not in c))
        amounts = [c for c in nums if c is not cid]
        if not amounts:
            continue
        amount = amounts[0]
        if len(cid) < 6:
            continue
        (series if month else alltime).append(
            (f'{month[:7]}-01 {amount} {cid}' if month else f'{amount} {cid}', month[:7] if month else None))
    return alltime, series, truncated


def money(s):
    return float(re.sub(r'[^0-9.\-]', '', str(s if s is not None else '')) or 0)


def is_test(*names):
    return any(TEST_RE.search(n or '') for n in names)


# ----------------------------------------------------------------- leads / stores --

def lead_fields(l, alias_map=None):
    g = lambda *ks: next((str(l[k]) for k in ks if l.get(k) not in (None, '')), '')  # noqa: E731
    out = {
        'lead_id': g('lead_id', 'id'),
        'status': g('lead_status', 'status'),
        'hs_id': g('hs_id', 'customer_id', 'hubspot_id'),
        'company': g('company', 'name', 'company_name'),
        'partner': g('partner', 'partner_name'),
        'handle': g('handle', 'shop', 'store'),
        'cfid': g('cfid', 'chargeflow_id'),
    }
    if alias_map is not None:
        out['partner'] = alias_map.get(out['partner'], out['partner'])
    return out


def build_alias(csv_stores, lead_lists):
    """Live Partner.io name -> the single CSV label sharing a lead id. Returns (alias, ambiguous)."""
    label = {s['lead_id']: s['partner'] for s in csv_stores if s['lead_id']}
    seen = defaultdict(set)
    live_names = set()
    for leads in lead_lists:
        for l in leads:
            lf = lead_fields(l)
            if not lf['partner']:
                continue
            live_names.add(lf['partner'])
            if lf['lead_id'] in label:
                seen[lf['partner']].add(label[lf['lead_id']])
    alias, ambiguous = {}, {}
    for name in sorted(live_names):
        labs = seen.get(name, set())
        if len(labs) == 1:
            alias[name] = next(iter(labs))
        elif len(labs) > 1:
            ambiguous[name] = sorted(labs)
    return alias, ambiguous


def order_leads(raw):
    """Approved/won leads that carry a HubSpot link first (stable), then the rest.
    Reproduces the old partnerio-leads.json (approved or won with customer_id) then all-leads order."""
    a = [l for l in raw if str(l.get('status') or '') in APPROVED_WON and l.get('hs_id')]
    b = [l for l in raw if not (str(l.get('status') or '') in APPROVED_WON and l.get('hs_id'))]
    return a + b


def load_leads(path):
    raw = json.load(open(path))
    if isinstance(raw, dict):
        raw = raw.get('leads') or []
    return [l for l in raw if isinstance(l, dict)]


def match_by_lead(cfg):
    return {r['partnerio_lead_id'].strip(): r for r in cfg.get('match_rows', [])
            if (r.get('hs_object_id') or '').strip()}


def assemble_stores(cfg, raw_leads):
    nc = set(cfg['no_commission_partners'])
    stores = copy.deepcopy(cfg['stores_cut'])
    override = cfg.get('ids_override', {})
    for s in stores:
        if not s['hs_id']:
            s['hs_id'] = override.get(s['cfid']) or override.get(s['handle']) or override.get(s['company']) or ''
    derived, ambiguous = build_alias(stores, [raw_leads])
    alias = dict(cfg.get('alias', {}))
    alias.update(derived)
    leads = [lead_fields(l, alias) for l in order_leads(raw_leads)]
    by_lead = {}
    for l in leads:
        if l['lead_id'] and l['status']:
            by_lead.setdefault(l['lead_id'], l)
    for s in stores:
        l = by_lead.get(s['lead_id'])
        if l and l['status']:
            s['lead_status'] = l['status']
    cands = list(leads)
    for lid, m in match_by_lead(cfg).items():
        base = by_lead.get(lid, {})
        cands.append({'lead_id': lid, 'status': m.get('partnerio_status') or base.get('status', ''),
                      'hs_id': m['hs_object_id'].strip(), 'company': m.get('hs_company_name') or m.get('lead_name', ''),
                      'partner': base.get('partner', ''), 'handle': m.get('store_name_id', ''),
                      'cfid': m.get('chargeflow_id', ''), 'from_match': True})
    known_hs = {s['hs_id'] for s in stores if s['hs_id']}
    known_lead = {s['lead_id'] for s in stores if s['lead_id']}
    adds, test_dropped = [], []
    for l in cands:
        if l['hs_id'] and l['hs_id'] not in known_hs and l['lead_id'] not in known_lead:
            if is_test(l['company'], l['handle']):
                test_dropped.append(l['company'] or l['handle'])
                continue
            adds.append({'partner': l['partner'], 'company': l['company'], 'handle': l['handle'],
                         'cfid': l['cfid'], 'hs_id': l['hs_id'], 'lead_id': l['lead_id'],
                         'lead_status': l['status'],
                         'note': 'from HubSpot match file' if l.get('from_match') else 'from Partner.io refresh'})
            known_hs.add(l['hs_id'])
    for a in cfg.get('additions_extra', []):
        if not isinstance(a, dict):
            continue
        hs = str(a.get('hs_id') or '')
        if hs and hs in known_hs:
            continue
        known_hs.add(hs)
        adds.append({'partner': alias.get(a.get('partner', ''), a.get('partner', '')), 'company': a.get('company', ''),
                     'handle': a.get('handle', ''), 'cfid': a.get('cfid', ''), 'hs_id': hs,
                     'lead_id': str(a.get('lead_id') or ''), 'lead_status': a.get('lead_status') or a.get('status') or '',
                     'note': a.get('note', '')})
    for a in adds:
        a.update(q3_reference=None, added_since_cut=True)
    for s in stores + adds:
        if s['partner'] in nc:
            s['no_commission'] = True
            s['no_commission_reason'] = cfg['no_commission_reason']
    return stores, adds, leads, alias, ambiguous, test_dropped


def linked_ids(cfg, raw_leads):
    stores, adds, *_ = assemble_stores(cfg, raw_leads)
    return sorted({s['hs_id'] for s in stores + adds if s['hs_id']})


# ------------------------------------------------------------------ gross margin --

PRODUCT_KEYS = ('alerts_billed', 'automation_billed', 'prevent_billed', 'other_billed')


def line_shares(e, viol=None, key=None):
    """(alerts, automation, prevent, other) shares of a store-month's line-item billed. Sums to 1: negatives are clamped and the
    rest renormalised; a violation (sum off by more than 0.001, or a negative share) is appended to `viol` as `key`. No billed: all other."""
    v = [float(e.get(k) or 0) for k in PRODUCT_KEYS]
    den = sum(v)
    if den <= 0:
        return (0.0, 0.0, 0.0, 1.0)
    sh = [x / den for x in v]
    if min(sh) < 0 or abs(sum(sh) - 1) > 0.001:
        if viol is not None:
            viol.append(key)
        sh = [max(0.0, x) for x in sh]
        t = sum(sh)
        sh = [x / t for x in sh] if t > 0 else [0.0, 0.0, 0.0, 1.0]
    return tuple(sh)


def split_dollars(paid, sh):
    """Paid revenue split by product shares, in dollars; other takes the rounding remainder so the parts sum to paid."""
    a, au, p = (round(paid * x, 2) for x in sh[:3])
    return {'alerts': a, 'automation': au, 'prevent': p, 'other': round(paid - a - au - p, 2)}


def apply_dates(allst, partners, company_dates):
    """Store joined (HubSpot company createdate), paying_since (first paid invoice date); partner active_since (earliest paying_since
    across its stores), signed_on and partnerio_created default to null (apply_registry fills them from the registry doc)."""
    first = {}
    for s in allst:
        fp = (s.get('lifetime') or {}).get('first_paid_invoice')
        s['joined'] = (company_dates.get(s['hs_id']) or None) if s.get('hs_id') else None
        s['paying_since'] = fp[:10] if fp else None
        if s['paying_since']:
            first[s['partner']] = min(first.get(s['partner'], s['paying_since']), s['paying_since'])
    for p in partners:
        p['active_since'] = first.get(p['name'])
        p.setdefault('signed_on', None)
        p.setdefault('partnerio_created', None)


PRODUCT_NAMES = ('alerts', 'automation', 'prevent', 'other')


def apply_lifetime_products(allst, partners, shares):
    """Lifetime product split in dollars: stripe_lifetime x share (lifetime-products.json shares, from lifetime invoice line items,
    used as ratios only). Store without lines: products null, products_source 'none'. Adds lifetime.shopify_unsplit (the Shopify-rail
    lifetime, not split by product). Partner lifetime_products = sum over its stores (stores without lines add nothing)."""
    psum = {}
    for s in allst:
        lt = s.get('lifetime')
        if not lt:
            continue
        lt['shopify_unsplit'] = round(lt.get('shopify') or 0.0, 2)
        sh = (shares or {}).get(s.get('hs_id')) if s.get('hs_id') else None
        if not sh:
            lt['products'] = None
            lt['products_source'] = 'none'
            continue
        st = float(lt.get('stripe') or 0.0)
        a, au, p = (round(st * float(sh.get(k) or 0.0), 2) for k in PRODUCT_NAMES[:3])
        lt['products'] = {'alerts': a, 'automation': au, 'prevent': p, 'other': round(st - a - au - p, 2)}
        lt['products_source'] = 'line_items'
        d = psum.setdefault(s['partner'], dict.fromkeys(PRODUCT_NAMES, 0.0))
        for k in PRODUCT_NAMES:
            d[k] += lt['products'][k]
    for p in partners:
        d = psum.get(p['name'])
        p['lifetime_products'] = {k: round(v, 2) for k, v in d.items()} if d else dict.fromkeys(PRODUCT_NAMES, 0.0)


def apply_gm(cfg, allst, months):
    g = cfg['gm']
    ratios, carry, last = g.get('ratios', {}), g.get('carry', {}), g.get('last_month')
    tot = {'paid': 0.0, 'comm': 0.0, 'alerts_rev': 0.0, 'alerts_gm_rev': 0.0, 'line_months': 0, 'none_months': 0,
           'carried_months': 0, 'share_violations': []}

    def shares(r, key):
        # config ratios from before the product split carry only alerts_share: the rest is "other"
        if r.get('automation_share') is None:
            return (r['alerts_share'], 0.0, 0.0, 1 - r['alerts_share'])
        return line_shares({'alerts_billed': r['alerts_share'], 'automation_billed': r['automation_share'],
                            'prevent_billed': r['prevent_share'], 'other_billed': r['other_share']}, tot['share_violations'], key)
    for s in allst:
        d = ratios.get(s['hs_id']) or {}
        cr = carry.get(s['hs_id'])
        gm, comm, pm = {}, {}, {}
        for m in months:
            paid = s['by_month'].get(m, 0.0)
            e = d.get(m)
            if e:
                share, margin = e['alerts_share'], e['alerts_margin']
                sh = shares(e, (s['hs_id'], m))
                gm[m] = {'alerts_share': round(share, 4), 'alerts_margin': None if margin is None else round(margin, 4),
                         'gm_source': 'line_items', 'automation_share': round(sh[1], 4), 'prevent_share': round(sh[2], 4),
                         'other_share': round(sh[3], 4)}
                pm[m] = split_dollars(paid, sh)
                c = paid * ((1 - share) + share * (margin or 0.0))
                tot['line_months'] += 1 if paid else 0
                tot['alerts_rev'] += paid * share
                tot['alerts_gm_rev'] += paid * share * (margin or 0.0)
            elif cr and last and m > last:
                share, margin = cr['alerts_share'], cr['alerts_margin']
                if paid:
                    sh = shares(cr, (s['hs_id'], m))
                    gm[m] = {'alerts_share': round(share, 4), 'alerts_margin': None if margin is None else round(margin, 4),
                             'gm_source': 'carried', 'automation_share': round(sh[1], 4), 'prevent_share': round(sh[2], 4),
                             'other_share': round(sh[3], 4)}
                    pm[m] = split_dollars(paid, sh)
                c = paid * ((1 - share) + share * (margin or 0.0))
                tot['carried_months'] += 1 if paid else 0
            else:
                c = paid
                if paid:
                    gm[m] = {'alerts_share': None, 'alerts_margin': None, 'gm_source': 'none',
                             'automation_share': None, 'prevent_share': None, 'other_share': None}
                    pm[m] = {'alerts': 0.0, 'automation': 0.0, 'prevent': 0.0, 'other': round(paid, 2)}
                    tot['none_months'] += 1
            comm[m] = round(c, 2)
            tot['paid'] += paid
            tot['comm'] += c
        s['gm_by_month'] = gm
        s['commissionable_by_month'] = comm
        s['product_by_month'] = pm
    return tot


# ------------------------------------------------------------------------ inputs --

def read_monthly(paths):
    data, warns = {}, []
    for p in paths:
        _, series, trunc = parse_rows(open(p).read())
        if trunc and int(trunc.group(2)) > int(trunc.group(1)):
            warns.append(f'{os.path.basename(p)}: TRUNCATED {trunc.group(1)} of {trunc.group(2)}; split and re-run')
        for line, m in series:
            _, amt, cid = line.split()
            data[(m, cid)] = money(amt)
    return data, warns


def read_company_dates(paths):
    """{hs_id: 'YYYY-MM-DD'} from pasted HubSpot COMPANY createdate results. Accepts tab/CSV rows (id and an ISO timestamp per row)
    and the raw JSON records query_crm_data returns for a plain SELECT (hs_object_id plus createdate_iso per record)."""
    out = {}
    for p in paths or []:
        text = open(p).read()
        for chunk in re.split(r'(?=\{\\?"objectTypeId)', text):
            m1 = re.search(r'hs_object_id\\?"\s*:\s*\\?"(\d{6,})', chunk)
            m2 = re.search(r'createdate_iso\\?"\s*:\s*\\?"(\d{4}-\d{2}-\d{2})', chunk)
            if m1 and m2:
                out[m1.group(1)] = m2.group(1)
        for line in text.splitlines():
            cells = [c.strip() for c in re.split(r'\t|\s{2,}|,', line.strip()) if c.strip()]
            ts = next((c for c in cells if re.fullmatch(r'\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?', c)), None)
            ids = [c for c in cells if re.fullmatch(r'\d{6,}', c)]
            if ts and ids and not line.lstrip().startswith(('#', '{', '"')):
                out[ids[0]] = ts[:10]
    return out


def read_lifetime(paths):
    """{hs_id: {'sum': float, 'first': iso or None}} from pasted SUM and MIN(hs_createdate) results."""
    out, warns = {}, []
    for p in paths:
        text = open(p).read()
        trunc = re.search(r'Showing (\d+) of (\d+)', text)
        if trunc and int(trunc.group(2)) > int(trunc.group(1)):
            warns.append(f'{os.path.basename(p)}: TRUNCATED {trunc.group(1)} of {trunc.group(2)}; split and re-run')
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            cells = [c.strip() for c in re.split(r'\t|\s{2,}|,', line) if c.strip()]
            ts = next((c for c in cells if re.fullmatch(r'\d{4}-\d{2}-\d{2}T[\d:.]+Z?', c)), None)
            rest = [c for c in cells if c != ts]
            nums = [c for c in rest if re.fullmatch(r'-?\d+(\.\d+)?', c)]
            if ts:
                ids = [c for c in nums if re.fullmatch(r'\d{6,}', c)]
                if not ids:
                    continue
                e = out.setdefault(ids[0], {'sum': None, 'first': None})
                if e['first'] is None or ts < e['first']:
                    e['first'] = ts
            else:
                if len(nums) < 2:
                    continue
                cid = max(nums, key=lambda c: (len(c.split('.')[0]), '.' not in c))
                if len(cid) < 6:
                    continue
                amt = next((c for c in nums if c is not cid), None)
                if amt is None:
                    continue
                out.setdefault(cid, {'sum': None, 'first': None})['sum'] = money(amt)
    return out, warns


# ---------------------------------------------------------------- partner registry --

def apply_registry(docs, allst, partners, alias, default_rate=25):
    """Partner registry docs (PartnerOS) -> commission flags and rates on stores and partner rows.
    Match a doc to a dashboard partner label by `stores[]` lead ids first, then name and aliases
    (through the live-name alias map). no_commission = flags.no_commission or agreement.status != active.
    rate_override = agreement.rate only when present and different from the default rate (standard-rate partners keep null so the what-if slider applies). A partner with no doc keeps the config-only behaviour.
    Returns (summary, mapping lines)."""
    by_lead = {str(s['lead_id']): s['partner'] for s in allst if s.get('lead_id')}
    labels = {p['name'] for p in partners}
    lower = {l.lower(): l for l in labels}
    matched, how, unmatched = {}, {}, []
    for d in docs:
        cnt = defaultdict(int)
        for i in d.get('stores') or []:
            if str(i) in by_lead:
                cnt[by_lead[str(i)]] += 1
        lab = max(cnt, key=lambda k: (cnt[k], k)) if cnt else None
        if lab and lab not in matched:
            matched[lab], how[lab] = d, 'lead id'
        elif not lab:
            unmatched.append(d)
    rest = []
    for d in unmatched:
        lab = None
        for c in [d.get('name')] + list(d.get('aliases') or []):
            for cand in (c, alias.get(c)):
                if cand and cand in labels:
                    lab = cand
                elif cand and cand.lower() in lower:
                    lab = lower[cand.lower()]
                if lab:
                    break
            if lab:
                break
        if lab and lab not in matched:
            matched[lab], how[lab] = d, 'name or alias'
        else:
            rest.append(d)
    for p in partners:
        d = matched.get(p['name'])
        if not d:
            continue
        ag = d.get('agreement') or {}
        fl = d.get('flags') or {}
        nc = bool(fl.get('no_commission')) or ag.get('status') != 'active'
        reason = 'registry: no-commission flag' if fl.get('no_commission') else f'registry: agreement {ag.get("status") or "none"}'
        p['display_name'] = d.get('name') or p['name']
        p['signed_on'] = ag.get('signed_on') or None
        ca = str(d.get('created_at') or d.get('partnerio_created_at') or '')
        p['partnerio_created'] = ca[:10] if ca and not ca.startswith('2026-06-08') else None   # Jun 8 = Partner.io migration stamp
        if ag.get('rate') is not None and ag['rate'] != default_rate:
            p['rate_override'] = ag['rate']
        if nc:
            p['no_commission'] = True
        else:
            p.pop('no_commission', None)
        for s in allst:
            if s['partner'] != p['name']:
                continue
            if nc:
                s['no_commission'] = True
                s.setdefault('no_commission_reason', reason)
            elif s.get('scope') == 'program':
                s['no_commission'] = False
                s.pop('no_commission_reason', None)
            else:
                s.pop('no_commission', None)
                s.pop('no_commission_reason', None)
    missing = sorted(l for l in labels if l not in matched)
    summary = {'loaded': len(docs), 'active': sum(1 for d in docs if (d.get('agreement') or {}).get('status') == 'active'),
               'none': len(missing), 'missing': missing}
    lines = [f'registry: {len(docs)} docs, {len(matched)} matched to dashboard partners '
             f'({sum(1 for v in how.values() if v == "lead id")} by lead id, {sum(1 for v in how.values() if v != "lead id")} by name or alias), '
             f'{len(rest)} docs match no dashboard partner',
             f'registry: {len(missing)} partners without a doc' + (f' ({", ".join(missing)})' if missing else '')]
    lines.append(f'registry: {sum(1 for p in partners if p.get("rate_override") is not None)} rate overrides (rate differs from default {default_rate})')
    for lab in sorted(matched):
        d = matched[lab]
        if d.get('name') != lab:
            lines.append(f'  registry map: {lab} <- {d.get("name")} (via {how[lab]})')
    return summary, lines


# ------------------------------------------------------------------- trend document --

TREND_START = '2024-08'
TREND_DOC = 'snapshots/trend'


def build_trend(data, hist):
    """trend.json: monthly series from TREND_START to the current month. `hist` = {(month, hs_id): dollars} for months before the
    window (data['months'][0]); window months come from the stores' by_month. Stores are every store with a hs_id (program stores
    have no window months and read 0 there). Integer dollars; t3m = mean of the month and the two before (null for the first two);
    mom_pct = percent change on the previous month (null when it is 0); by_product null before the window (no line items earlier)."""
    wm = data['months']
    w0 = wm[0]
    months = month_range(min(TREND_START, w0), wm[-1])
    sts = [s for s in data['stores'] if s.get('hs_id')]
    ser = {s['hs_id']: [float(round(hist.get((m, s['hs_id']), 0.0))) if m < w0 else float((s.get('by_month') or {}).get(m, 0.0)) for m in months]
           for s in sts}
    n = len(months)
    tot = [sum(a[i] for a in ser.values()) for i in range(n)]
    pay = [sum(ser[s['hs_id']][i] for s in sts if s.get('scope') != 'program') for i in range(n)]
    paying = [sum(1 for a in ser.values() if a[i] > 0) for i in range(n)]
    t3m = [None if i < 2 else round((tot[i] + tot[i - 1] + tot[i - 2]) / 3) for i in range(n)]
    mom = [None if i == 0 or tot[i - 1] == 0 else round((tot[i] - tot[i - 1]) / tot[i - 1] * 100, 1) for i in range(n)]
    by_p = {k: [None if m < w0 else round(sum((s.get('product_by_month') or {}).get(m, {}).get(k, 0.0) for s in sts)) for m in months]
            for k in PRODUCT_NAMES}
    parts = {}
    for s in sts:
        a = parts.setdefault(s['partner'], [0.0] * n)
        for i, v in enumerate(ser[s['hs_id']]):
            a[i] += v
    out = {'generated': data['generated'], 'months': months, 'earned': [round(x) for x in tot], 'earned_payout': [round(x) for x in pay],
           'paying_stores': paying, 't3m': t3m, 'mom_pct': mom, 'by_product': by_p,
           'stores': {h: [round(x) for x in a] for h, a in ser.items()},
           'partners': {k: [round(x) for x in a] for k, a in parts.items()}}
    # running totals (all months from TREND_START); the forecast is computed from the doc as built and the stores' product split
    cum = {}
    for k in ('earned', 'earned_payout'):
        run, a = 0, []
        for v in out[k]:
            run += v
            a.append(run)
        cum[k] = a
    out['cumulative'] = cum
    out['forecast'] = compute_forecast(out, data['stores'], (data.get('defaults') or {}).get('rate', 25), data['window']['to'], data['generated'])
    out['growth'] = build_growth(out, data['window']['to'])
    return out


# ---------------------------------------------------------------------------- forecast --
# Same code lives in build.py and refresh.py (refresh.py is standalone); keep the two copies identical.

FORECAST_METHOD = 'damped growth trend on the last 12 full months, shaped by detrended prior-year seasonality'
FORECAST_HORIZON = 5
FORECAST_FIT = 12
FORECAST_PHI = 0.85


def _fc_fit(y):
    """Least squares of y on t = 0..n-1: (intercept, slope, residual sd with n-2 degrees of freedom)."""
    n = len(y)
    tm, ym = (n - 1) / 2.0, sum(y) / n
    b = sum((t - tm) * (v - ym) for t, v in enumerate(y)) / sum((t - tm) ** 2 for t in range(n))
    a = ym - b * tm
    sig = math.sqrt(sum((v - (a + b * t)) ** 2 for t, v in enumerate(y)) / (n - 2))
    return a, b, sig


def _fc_seasonal(months, ser, last_i):
    """Detrended seasonal index per calendar month: mean over every occurrence i <= last_i of ser[i] / trend_fit(i), where
    trend_fit(i) is the log-linear fit over the (up to) 12 months ending at i, evaluated at i (windows shorter than 12 are
    used while history is short, at least 4 months). Clamped to [0.7, 1.5]. A calendar month with no occurrence reads 1.0."""
    acc = {}
    for i in range(3, last_i + 1):
        win = ser[max(0, i - FORECAST_FIT + 1):i + 1]
        if len(win) < 4 or min(win) <= 0:
            continue
        a, b, _ = _fc_fit([math.log(v) for v in win])
        acc.setdefault(int(months[i][5:7]), []).append(ser[i] / math.exp(a + b * (len(win) - 1)))
    return {c: min(1.5, max(0.7, sum(v) / len(v))) for c, v in acc.items()}


def _fc_project(months, ser, last_i, fut):
    """Fit the FORECAST_FIT months ending at last_i, project the months in `fut` (YYYY-MM, after months[last_i]) with a damped
    trend: level L = fitted value at last_i, growth factor at horizon h = g^(phi + phi^2 + ... + phi^h), times the seasonal index
    over its mean on the fitted months. Returns None when the window is short or has a non-positive month."""
    i0 = last_i - FORECAST_FIT + 1
    win = ser[i0:last_i + 1] if i0 >= 0 else []
    if len(win) < FORECAST_FIT or min(win) <= 0:
        return None
    a, b, sig = _fc_fit([math.log(v) for v in win])
    seas = _fc_seasonal(months, ser, last_i)
    norm = sum(seas.get(int(m[5:7]), 1.0) for m in months[i0:last_i + 1]) / FORECAST_FIT
    base, hs = [], []
    for m in fut:
        h = (int(m[:4]) - int(months[last_i][:4])) * 12 + int(m[5:7]) - int(months[last_i][5:7])
        damp = sum(FORECAST_PHI ** k for k in range(1, h + 1))
        base.append(math.exp(a + b * (FORECAST_FIT - 1) + b * damp) * seas.get(int(m[5:7]), 1.0) / norm)
        hs.append(h)
    return {'growth': math.exp(b), 'sigma': sig, 'seas': seas, 'base': base, 'h': hs, 'fitted': months[i0:last_i + 1]}


def _fc_add_months(m, k):
    y, mo = int(m[:4]), int(m[5:7]) - 1 + k
    return f'{y + mo // 12:04d}-{mo % 12 + 1:02d}'


def compute_forecast(trend, stores, rate, today_iso, generated):
    """The `forecast` object of trend.json. `trend` has months, earned, earned_payout; `stores` are the dashboard stores
    (payout scope feeds the shares); `rate` is the default payout rate in percent. The fit uses earned_payout (payout-scope
    revenue) for the last 12 FULL months before the month of today_iso; the current month is excluded and only reported as
    run_rate_current_month = partial x days_in_month / day_of_month. Payout per month = rate x earned x (automation_share +
    alerts_share x alerts_margin), shares and margin over the last 3 full months, payout-scope stores: product dollars
    (product_by_month) over their total, margin = dollar-weighted mean of gm_by_month alerts_margin over Alerts dollars
    (null = 0). Prevent and Other earn no payout here. Ceiling = rate x earned."""
    months, ser = trend['months'], trend['earned_payout']
    cur = today_iso[:7]
    full = [i for i, m in enumerate(months) if m < cur]
    fut = [_fc_add_months(cur, k) for k in range(1, FORECAST_HORIZON + 1)]
    out = {'generated': generated, 'method': FORECAST_METHOD, 'as_of': today_iso, 'current_month': cur,
           'rate_pct': rate, 'months': fut, 'warning': None}
    last_i = full[-1] if full else -1
    pr = _fc_project(months, ser, last_i, fut) if last_i >= 0 else None
    if pr is None:
        out['warning'] = f'fewer than {FORECAST_FIT} full months of positive history; no forecast'
        return out
    warns = []
    if not 0.8 <= pr['growth'] <= 1.3:
        warns.append(f'fitted monthly growth {pr["growth"]:.3f} is outside 0.8 to 1.3')
    dim = (date(int(cur[:4]) + int(cur[5:7]) // 12, int(cur[5:7]) % 12 + 1, 1) - date(int(cur[:4]), int(cur[5:7]), 1)).days
    day = int(today_iso[8:10])
    ci = months.index(cur) if cur in months else None
    rr = round(ser[ci] * dim / day) if ci is not None and day > 0 else None
    sig = pr['sigma']
    base = pr['base']
    low = [b * math.exp(-sig * math.sqrt(h)) for b, h in zip(base, pr['h'])]
    high = [b * math.exp(sig * math.sqrt(h)) for b, h in zip(base, pr['h'])]
    sel = [i for i in full[-3:]]
    sel_m = [months[i] for i in sel]
    tot = {k: 0.0 for k in ('alerts', 'automation', 'prevent', 'other')}
    mw = mn = 0.0
    for s in stores:
        if s.get('scope') == 'program':
            continue
        for m in sel_m:
            pm = (s.get('product_by_month') or {}).get(m)
            if not pm:
                continue
            for k in tot:
                tot[k] += float(pm.get(k) or 0.0)
            mg = ((s.get('gm_by_month') or {}).get(m) or {}).get('alerts_margin')
            mw += float(pm.get('alerts') or 0.0) * (mg or 0.0)
            mn += float(pm.get('alerts') or 0.0)
    den = sum(tot.values())
    shares = {'alerts_share': round(tot['alerts'] / den, 4) if den else 0.0,
              'alerts_margin': round(mw / mn, 4) if mn else 0.0,
              'automation_share': round(tot['automation'] / den, 4) if den else 0.0}
    eff = shares['automation_share'] + shares['alerts_share'] * shares['alerts_margin']
    r = rate / 100.0
    e_int = [round(x) for x in base]
    pay_gm = [round(r * x * eff) for x in base]
    pay_ce = [round(r * x) for x in base]
    # cumulative: all-stores running total through the last full month, plus the current month at run-rate, then the forecast
    cum_e, run = [], 0
    for v in trend['earned']:
        run += v
        cum_e.append(run)
    start = cum_e[last_i] + (rr if rr is not None else 0)
    cb, cl, ch = [], [], []
    sb = sl = sh = start
    for b, lo, hi in zip(base, low, high):
        sb += b
        sl += lo
        sh += hi
        cb.append(round(sb))
        cl.append(round(sl))
        ch.append(round(sh))
    # sanity (plan): Nov base within 0.6x to 1.6x of the last full month; the quarter of the first forecast month not
    # below the previous quarter by more than 20%
    if not 0.6 <= base[0] / ser[last_i] <= 1.6:
        warns.append(f'first forecast month is {base[0] / ser[last_i]:.2f}x the last full month (outside 0.6 to 1.6)')
    q0 = (int(fut[0][5:7]) - 1) // 3 * 3
    qm = [f'{fut[0][:4]}-{q0 + k + 1:02d}' for k in range(3)]
    pm_ = [_fc_add_months(m, -3) for m in qm]
    if all(m in months for m in pm_):
        prev_q = sum(ser[months.index(m)] for m in pm_)
        this_q = 0.0
        for m in qm:
            this_q += base[fut.index(m)] if m in fut else (rr if m == cur and rr is not None else (ser[months.index(m)] if m in months else 0))
        if prev_q > 0 and this_q < 0.8 * prev_q:
            warns.append(f'forecast quarter {qm[0]} to {qm[-1]} is {this_q / prev_q:.2f}x the previous quarter (below 0.8)')
    out.update({
        'fitted_months': pr['fitted'], 'growth_pct_monthly': round((pr['growth'] - 1) * 100, 2), 'phi': FORECAST_PHI,
        'effective_growth_pct': {'h1': round((pr['growth'] ** FORECAST_PHI - 1) * 100, 2), 'h5': round((pr['growth'] ** FORECAST_PHI ** 5 - 1) * 100, 2)}, 'sigma': round(sig, 4),
        'seasonality': {str(c): round(pr['seas'].get(c, 1.0), 4) for c in (int(m[5:7]) for m in fut)},
        'earned': {'base': e_int, 'low': [round(x) for x in low], 'high': [round(x) for x in high]},
        'payout_gm': pay_gm, 'payout_ceiling': pay_ce,
        'net_gm': [e - p for e, p in zip(e_int, pay_gm)], 'net_ceiling': [e - p for e, p in zip(e_int, pay_ce)],
        'run_rate_current_month': rr, 'shares_used': shares, 'shares_months': sel_m,
        'cumulative': {'base': cb, 'low': cl, 'high': ch, 'start': round(start)},
        'warning': '; '.join(warns) or None})
    # backtest: the same method as a Sep 2025 forecaster (fit Oct 2024 to Sep 2025, seasonality only from what existed then)
    bt = None
    if '2025-09' in months and months.index('2025-09') >= FORECAST_FIT - 1:
        bi = months.index('2025-09')
        tm = [_fc_add_months('2025-09', k) for k in range(2, 7)]
        bp = _fc_project(months, ser, bi, tm)
        if bp is not None and all(m in months for m in tm):
            rows = []
            for m, f in zip(tm, bp['base']):
                a_ = ser[months.index(m)]
                rows.append({'month': m, 'forecast': round(f), 'actual': a_, 'pct_error': round((f - a_) / a_ * 100, 1) if a_ else None})
            errs = [abs(x['pct_error']) for x in rows if x['pct_error'] is not None]
            bt = {'fitted': bp['fitted'], 'target': tm, 'mape_pct': round(sum(errs) / len(errs), 1) if errs else None, 'per_month': rows}
    out['backtest'] = bt
    return out


# ------------------------------------------------------------------------------ growth --
# Same code lives in build.py and refresh.py (refresh.py is standalone); keep the two copies identical.
GROWTH_MONTHS = 12
GROWTH_PAIRS = ((250000, 500000), (500000, 1000000), (1000000, 2000000))
_MONTH_NAMES = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _gr_label(m):
    return f'{_MONTH_NAMES[int(m[5:7]) - 1]} {m[:4]}'


def _gr_scope(trend, key, today_iso):
    """Growth block for one series (`key` = earned or earned_payout). Windows are the last 12 FULL months before the month of
    today_iso and the 12 before them, aligned by calendar month (prior[k] is the same month a year before now[k])."""
    months, ser = trend['months'], trend[key]
    cum = trend['cumulative'][key]
    cur = today_iso[:7]
    full = [i for i, m in enumerate(months) if m < cur]
    n = GROWTH_MONTHS
    if not full:
        return {'enough_history': False, 'note': 'not enough history'}
    li = full[-1]
    last_m = months[li]
    now_m = [_fc_add_months(last_m, k - (n - 1)) for k in range(n)]
    prior_m = [_fc_add_months(m, -12) for m in now_m]
    idx = {m: i for i, m in enumerate(months)}

    def val(m):
        return ser[idx[m]] if m in idx and idx[m] <= li else None

    now = [val(m) for m in now_m]
    prior = [val(m) for m in prior_m]
    now_ok = all(v is not None for v in now)
    prior_ok = all(v is not None for v in prior)
    ago = val(_fc_add_months(last_m, -12))

    def mult(a, b):
        return round(a / b, 2) if a is not None and b else None
    t12_now = sum(now) if now_ok else None
    t12_prior = sum(prior) if prior_ok else None
    # cumulative crossings: historical through the last full month, then the forecast (current month bridged at run-rate)
    fc = trend.get('forecast') or {}
    hist_c = [(months[i], cum[i]) for i in full]
    fut_c = []
    if (fc.get('earned') or {}).get('base'):
        run = cum[li] + (fc.get('run_rate_current_month') or 0)
        for m, b in zip(fc['months'], fc['earned']['base']):
            run += b
            fut_c.append((m, round(run)))

    def cross(amount):
        for m, c in hist_c:
            if c >= amount:
                return m, False
        for m, c in fut_c:
            if c >= amount:
                return m, True
        return None, False

    def mdiff(a, b):
        return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])
    doublings = []
    for lo, hi in GROWTH_PAIRS:
        (m0, f0), (m1, f1) = cross(lo), cross(hi)
        if m0 is None or m1 is None:
            continue
        doublings.append({'from_amount': lo, 'from_month': m0, 'to_amount': hi, 'to_month': m1, 'months': mdiff(m0, m1), 'forecast': f0 or f1})
    hist_d = [d for d in doublings if not d['forecast']]
    enough = len(full) >= 2 * n
    out = {'series': key, 'enough_history': enough, 'note': None if enough else 'not enough history',
           'window_now': [now_m[0], now_m[-1]], 'window_prior': [prior_m[0], prior_m[-1]],
           'span_now': f'{_gr_label(now_m[0])} to {_gr_label(now_m[-1])}', 'span_prior': f'{_gr_label(prior_m[0])} to {_gr_label(prior_m[-1])}',
           'months_now': now_m, 'months_prior': prior_m, 'labels': [_MONTH_NAMES[int(m[5:7]) - 1] for m in now_m],
           'now': now, 'prior': prior,
           'monthly_now': ser[li], 'monthly_month': last_m, 'monthly_year_ago': ago, 'monthly_multiple': mult(ser[li], ago),
           't12_now': t12_now, 't12_prior': t12_prior, 't12_multiple': mult(t12_now, t12_prior) if prior_ok else None,
           'paying_now': trend['paying_stores'][li], 'paying_year_ago': trend['paying_stores'][idx[_fc_add_months(last_m, -12)]] if _fc_add_months(last_m, -12) in idx else None,
           'doublings': doublings, 'doubling_months_latest': hist_d[-1]['months'] if hist_d else None}
    return out


def build_growth(trend, today_iso):
    """`trend.growth` = {all: earned (page scope All time), payout: earned_payout (dated periods)}. Reads only the trend doc
    (months, earned, earned_payout, paying_stores, cumulative, forecast); paying_stores is the all-stores count in both scopes."""
    return {'all': _gr_scope(trend, 'earned', today_iso), 'payout': _gr_scope(trend, 'earned_payout', today_iso)}


def hist_from_trend(prev, w0):
    """Pre-window months of a stored trend doc as a hist map (the routine carries history forward without re-pulling)."""
    out = {}
    ms = prev.get('months') or []
    for h, a in (prev.get('stores') or {}).items():
        for m, v in zip(ms, a):
            if m < w0:
                out[(m, h)] = float(v)
    return out


# ------------------------------------------------------------------------- model --

def asof_label(iso):
    d = date.fromisoformat(iso)
    return d.strftime('%b ') + str(d.day) + d.strftime(', %Y')


def load_registry(path):
    if not path:
        return None
    raw = json.load(open(path))
    if isinstance(raw, dict):
        raw = raw.get('partners') or raw.get('docs') or []
    return [d for d in raw if isinstance(d, dict) and d.get('name')]


def build(cfg, raw_leads, pull, life, today, registry=None, company_dates=None):
    months = month_range(cfg['from_month'], today[:7])
    nc = set(cfg['no_commission_partners'])
    stores, adds, leads, alias, ambiguous, test_dropped = assemble_stores(cfg, raw_leads)
    n_adds_in = len(adds)
    adds_rev = [a for a in adds if a['hs_id'] and sum(pull.get((m, a['hs_id']), 0.0) for m in months) > 0]
    meta_adds_all = adds
    adds = adds_rev
    allst = stores + adds
    portal = cfg['portal']
    for s in allst:
        s['hs_url'] = f'https://app.hubspot.com/contacts/{portal}/company/{s["hs_id"]}' if s['hs_id'] else None
        s['by_month'] = {m: round(pull.get((m, s['hs_id']), 0.0), 2) for m in months} if s['hs_id'] else {m: 0.0 for m in months}
    gm_stats = apply_gm(cfg, allst, months)
    order = OrderedDict()
    for s in allst:
        order.setdefault(s['partner'], []).append(s['handle'] or s['company'])
    q2 = cfg['q2']
    q1 = cfg.get('q1') or {}
    q3 = cfg.get('q3') or {}
    q2_matched, partners = set(), []
    for name, hs in order.items():
        d = q2.get(name)
        if d:
            q2_matched.add(name)
        partners.append({
            'name': name, 'stores': len(hs),
            'q1_actual_payout': round(q1[name]['payout'], 2) if name in q1 else None,
            'q1_actual_by_product': {k: round(v, 2) for k, v in sorted(q1[name]['by_product'].items())} if name in q1 else {},
            'q2_actual_payout_net': round(d['paid'] + d['loss'], 2) if d else None,
            'q2_actual_paid_rows': round(d['paid'], 2) if d else None,
            'q2_actual_by_product': {k: round(v, 2) for k, v in sorted(d['by_product'].items())} if d else {},
            **({'q3_actual_payout': round(q3[name]['payout'], 4) if name in q3 else 0.0,
                'q3_actual_by_product': {k: round(v, 4) for k, v in sorted(q3[name]['by_product'].items())} if name in q3 else {}}
               if q3 else {}),
            'rate_override': None,
            **({'no_commission': True} if name in nc else {}),
        })
    q3m = cfg['q3_months']
    periods = [
        {'key': 'q1', 'label': 'Q1', 'months': [m for m in months if '2026-01' <= m <= '2026-03'],
         'status': 'paid', 'spend_basis': 'actual'},
        {'key': 'q2', 'label': 'Q2', 'months': [m for m in months if '2026-04' <= m <= '2026-06'],
         'status': 'paid', 'spend_basis': 'actual'},
        {'key': 'q3', 'label': 'Q3', 'months': [m for m in months if m in q3m],
         **({'status': 'paid', 'spend_basis': 'actual', 'source': load_local()['q3_period_source']} if q3 else {'status': 'in progress', 'spend_basis': 'estimate'})},
        {'key': 'oct', 'label': 'Oct to date', 'months': [m for m in months if m >= '2026-10'],
         'status': 'open', 'spend_basis': 'estimate'},
    ]
    shop = cfg['shopify_lifetime']
    # lifetime: every linked id has an entry (a paid-invoice-less id is absent from a grouped SUM and means 0)
    ids_all = set(linked_ids(cfg, raw_leads)) | set(life)
    ast = {}
    for hid in ids_all:
        if hid not in life and sum(pull.get((m, hid), 0.0) for m in months) > 0:
            continue   # revenue in the window but no lifetime result: a missing pull, not zero; falls to the Stripe-only floor below
        e = life.get(hid) or {}
        ast[hid] = {'stripe_lifetime': e.get('sum') or 0.0, 'shopify_lifetime': shop.get(hid),
                    'first_paid_invoice': e.get('first')}
    for s in allst:
        e = ast.get(s['hs_id']) if s['hs_id'] else None
        if e is None:
            bm = round(sum(s['by_month'].values()), 2)
            if bm > 0:
                s['lifetime'] = {'stripe': bm, 'shopify': None, 'total': bm, 'first_paid_invoice': None}
            else:
                s['lifetime'] = None
            continue
        st, sh = float(e.get('stripe_lifetime') or 0), e.get('shopify_lifetime')
        sh = None if sh is None else float(sh)
        s['lifetime'] = {'stripe': round(st, 2), 'shopify': None if sh is None else round(sh, 2),
                         'total': round(st + (sh or 0.0), 2), 'first_paid_invoice': e.get('first_paid_invoice')}
    for s in allst:
        s['scope'] = 'payout'
    known = {s['hs_id'] for s in allst if s['hs_id']}
    pool = [lead_fields(l, alias) for l in raw_leads] + [{'lead_id': a['lead_id'], 'status': a['lead_status'], 'hs_id': a['hs_id'], 'company': a['company'],
                     'partner': a['partner'], 'handle': a['handle'], 'cfid': a['cfid']}
                    for a in meta_adds_all if a['hs_id']]
    program = []
    for l in pool:
        hs = l['hs_id']
        if not hs or hs in known:
            continue
        e = ast.get(hs)
        if e is None:
            continue
        st, sh = float(e.get('stripe_lifetime') or 0), e.get('shopify_lifetime')
        sh = None if sh is None else float(sh)
        total = round(st + (sh or 0.0), 2)
        if total <= 0:
            continue
        known.add(hs)
        p = {'partner': l['partner'], 'company': l['company'] or l['handle'], 'handle': l['handle'], 'cfid': l['cfid'],
             'hs_id': hs, 'lead_id': l['lead_id'], 'lead_status': l['status'], 'q3_reference': None,
             'added_since_cut': False, 'note': '', 'scope': 'program', 'no_commission': False,
             'hs_url': f'https://app.hubspot.com/contacts/{portal}/company/{hs}', 'by_month': {},
             'lifetime': {'stripe': round(st, 2), 'shopify': None if sh is None else round(sh, 2),
                          'total': total, 'first_paid_invoice': e.get('first_paid_invoice')}}
        if p['partner'] in nc:
            p['no_commission'] = True
            p['no_commission_reason'] = cfg['no_commission_reason']
        program.append(p)
    payout_partners = {s['partner'] for s in allst}
    by_p = OrderedDict()
    for p in program:
        if p['partner'] not in payout_partners:
            by_p.setdefault(p['partner'], 0)
            by_p[p['partner']] += 1
    for name, n in by_p.items():
        partners.append({'name': name, 'stores': n, 'scope': 'program', 'q1_actual_payout': None, 'q1_actual_by_product': {},
                         'q2_actual_payout_net': None,
                         'q2_actual_paid_rows': None, 'q2_actual_by_product': {}, 'rate_override': None,
                         **({'no_commission': True} if name in nc else {})})
    allst = allst + program
    reg, reg_lines = apply_registry(registry, allst, partners, alias, (cfg.get('defaults') or {}).get('rate', 25)) if registry is not None else (None, [])
    apply_lifetime_products(allst, partners, cfg.get('lifetime_products') or {})
    apply_dates(allst, partners, company_dates or {})
    unl = [{'store': l.get('store') or l.get('name') or '', 'partner': l.get('partner') or l.get('partner_name') or '',
            'status': l.get('status') or ''} for l in raw_leads if not (l.get('hs_id') or l.get('customer_id'))]
    pre = (cfg.get('pre_q1') or cfg['pre_q2'])['payouts']
    by = {}
    for r in pre:
        d = by.setdefault(r['partner'], {'confirmed': 0.0, 'computed': 0.0})
        d['confirmed' if r.get('paid_on') else 'computed'] += float(r['amount'])
    by = {k: {'confirmed': round(v['confirmed'], 2), 'computed': round(v['computed'], 2)} for k, v in by.items()}
    alltime = {'stripe_asof': today, 'shopify_asof': cfg['shopify_asof'],
               'payouts_pre_q1': pre, 'payouts_pre_q1_complete': bool((cfg.get('pre_q1') or cfg['pre_q2']).get('complete')),
               'pre_q1_by_partner': by,
               'payouts_pre_q2': pre, 'payouts_pre_q2_complete': bool((cfg.get('pre_q1') or cfg['pre_q2']).get('complete')),   # kept one release for older pages
               'pre_q2_by_partner': by,
               'unlinked_leads': {'count': len(unl), 'leads': unl},
               'program_stores': len(program), 'program_partners': len(by_p), 'leads_total': len(raw_leads)}
    unlinked = [{'partner': s['partner'], 'company': s['company'], 'handle': s['handle'], 'cfid': s['cfid']}
                for s in allst if not s['hs_id'] and s.get('scope') != 'program']
    limits_all = [t.replace('{asof}', asof_label(today)) for t in cfg['limits_alltime']]
    data = {
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'window': {'from': month_bounds(months[0])[0], 'to': min(today, month_bounds(months[-1])[1])},
        'months': months,
        'periods': periods,
        'stores': allst,
        'partners': partners,
        'defaults': cfg['defaults'],
        'alerts_unit_costs': cfg['alerts_unit_costs'],
        'alerts_notion_url': cfg['alerts_notion_url'],
        'q2_realized_alerts_margin': cfg['q2_realized_alerts_margin'],
        'realized_alerts_margin': round(gm_stats['alerts_gm_rev'] / gm_stats['alerts_rev'], 4) if gm_stats['alerts_rev'] else None,
        'q1_recorded_paid': cfg.get('q1_recorded_paid'),
        'q2_recorded_paid': cfg['q2_recorded_paid'],
        **({'q3_final_total': round(sum(v['payout'] for v in q3.values()), 2)} if q3 else {}),
        'unlinked': unlinked,
        'limits': ((cfg.get('limits_q3_final') or cfg['limits']) if q3 else cfg['limits']) + limits_all + [cfg['limit_program'].format(n=alltime['unlinked_leads']['count'])],
        'alltime': alltime,
    }
    if reg is not None:
        data['registry'] = reg
    data['trend_doc'] = TREND_DOC
    info = {'registry_lines': reg_lines, 'adds_considered': n_adds_in, 'adds_kept': len(adds), 'test_dropped': test_dropped,
            'ambiguous': ambiguous, 'gm': gm_stats, 'program': len(program), 'stores_cut': len(stores)}
    return data, info


# --------------------------------------------------------------------- commands --

def cmd_print_queries(a):
    cfg = json.load(open(a.config))
    raw = load_leads(a.leads)
    reg = load_registry(a.registry)
    if reg is not None:
        print(f'-- registry: {len(reg)} docs loaded (does not change the queries)')
    today = a.today or date.today().isoformat()
    months = month_range(cfg['from_month'], today[:7])
    ids = linked_ids(cfg, raw)
    start = month_bounds(months[0])[0]
    end = min(month_bounds(months[-1])[1], today)
    per = max(1, 450 // len(months))
    n = (len(ids) + per - 1) // per
    print(f'-- {len(ids)} linked companies, {len(months)} months ({months[0]} to {months[-1]}), window {start} to {end}')
    print(f'-- MONTHLY: {n} grouped queries of up to {per} ids. Run each with HubSpot query_crm_data and save the result')
    print('-- (header line included) to monthly1.txt, monthly2.txt, ... If a result says "Showing 500 of N" with N>500, split that batch.')
    for i in range(0, len(ids), per):
        b = ids[i:i + per]
        print(f'\n-- MONTHLY BATCH {i // per + 1}/{n}')
        print("SELECT COMPANY.hs_object_id, DATE_TRUNC(hs_createdate, 'MONTH'), SUM(hs_amount_paid) "
              "FROM INVOICE WHERE hs_invoice_status = 'paid' "
              f"AND hs_createdate BETWEEN '{start}' AND '{end}' "
              "AND COMPANY.hs_object_id IN (" + ','.join(f"'{x}'" for x in b) +
              ") GROUP BY COMPANY.hs_object_id, DATE_TRUNC(hs_createdate, 'MONTH')")
    hc = cfg.get('history') or {}
    if hc.get('pull'):
        hm = month_range(hc.get('from', TREND_START), hc.get('to', '2025-12'))
        hper = max(1, 450 // len(hm))
        hn = (len(ids) + hper - 1) // hper
        hs0, hs1 = month_bounds(hm[0])[0], month_bounds(hm[-1])[1]
        print(f'\n-- HISTORY: {hn} grouped queries of up to {hper} ids, {len(hm)} months ({hm[0]} to {hm[-1]}); config history.pull is true')
        print('-- Save each result (header line included) to history1.txt, history2.txt, ... and pass them with --history. Split a batch on "Showing 500 of N".')
        print('-- Drop any row whose month is outside the range (a stray bucket from the BETWEEN day match). Set history.pull false afterwards.')
        for i in range(0, len(ids), hper):
            b = ids[i:i + hper]
            print(f'\n-- HISTORY BATCH {i // hper + 1}/{hn}')
            print("SELECT COMPANY.hs_object_id, DATE_TRUNC(hs_createdate, 'MONTH'), SUM(hs_amount_paid) "
                  "FROM INVOICE WHERE hs_invoice_status = 'paid' "
                  f"AND hs_createdate BETWEEN '{hs0}' AND '{hs1}' "
                  "AND COMPANY.hs_object_id IN (" + ','.join(f"'{x}'" for x in b) +
                  ") GROUP BY COMPANY.hs_object_id, DATE_TRUNC(hs_createdate, 'MONTH')")
    lper = 110
    ln = (len(ids) + lper - 1) // lper
    print(f'\n-- LIFETIME: {ln} batches of up to {lper} ids, each as a SUM query and a MIN query (no date filter).')
    print('-- Save every result (SUM and MIN, any order) into lifetime1.txt, lifetime2.txt, ...; ids absent from a SUM result count as 0.')
    for i in range(0, len(ids), lper):
        b = ids[i:i + lper]
        inl = ','.join(f"'{x}'" for x in b)
        print(f'\n-- LIFETIME BATCH {i // lper + 1}/{ln} SUM')
        print("SELECT COMPANY.hs_object_id, SUM(hs_amount_paid) FROM INVOICE WHERE hs_invoice_status = 'paid' "
              f"AND COMPANY.hs_object_id IN ({inl}) GROUP BY COMPANY.hs_object_id")
        print(f'\n-- LIFETIME BATCH {i // lper + 1}/{ln} MIN')
        print("SELECT COMPANY.hs_object_id, MIN(hs_createdate) FROM INVOICE WHERE hs_invoice_status = 'paid' "
              f"AND COMPANY.hs_object_id IN ({inl}) GROUP BY COMPANY.hs_object_id")
    print(f'\n-- COMPANY-DATES: {ln} batches of up to {lper} ids (HubSpot company createdate = Joined). LIMIT 500 is required, the default page is 25.')
    print('-- Save every result (the raw JSON records are fine) into companydates1.txt, companydates2.txt, ... and pass them with --company-dates.')
    for i in range(0, len(ids), lper):
        b = ids[i:i + lper]
        print(f'\n-- COMPANY-DATES BATCH {i // lper + 1}/{ln}')
        print('SELECT hs_object_id, createdate FROM COMPANY WHERE hs_object_id IN (' + ','.join(f"'{x}'" for x in b) + ') LIMIT 500')


def cmd_refresh(a):
    cfg = json.load(open(a.config))
    require_cfg(cfg)
    raw = load_leads(a.leads)
    today = a.today or cfg.get('today') or date.today().isoformat()
    pull, w1 = read_monthly(a.monthly)
    life, w2 = read_lifetime(a.lifetime)
    warns = w1 + w2
    for w in warns:
        print('ERROR ' + w, file=sys.stderr)
    if warns:
        return 2
    if not pull:
        print('ERROR no monthly rows parsed', file=sys.stderr)
        return 2
    if not life:
        print('ERROR no lifetime rows parsed', file=sys.stderr)
        return 2
    cdates = read_company_dates(a.company_dates)
    data, info = build(cfg, raw, pull, life, today, load_registry(a.registry), cdates)
    json.dump(data, open(a.out, 'w'), indent=1, ensure_ascii=False)
    if a.slim_out:
        write_slim(data, a.slim_out)
    hist, hw = read_monthly(a.history or [])
    for w in hw:
        print('ERROR ' + w, file=sys.stderr)
    if hw:
        return 2
    if a.trend_prev:
        carried = hist_from_trend(json.load(open(a.trend_prev)), data['months'][0])
        carried.update(hist)   # a fresh history pull wins over the stored one
        hist = carried
    hist = {k: v for k, v in hist.items() if k[0] < data['months'][0]}
    if hist:
        trend = build_trend(data, hist)
        tp = a.trend_out or os.path.join(os.path.dirname(os.path.abspath(a.out)), 'trend.json')
        json.dump(trend, open(tp, 'w'), separators=(',', ':'), ensure_ascii=False)
        print(f'wrote {tp}: {os.path.getsize(tp):,} bytes, months {trend["months"][0]} to {trend["months"][-1]} '
              f'({len(a.history or [])} history files, {"stored trend carried" if a.trend_prev else "no stored trend"})')
    else:
        print('trend.json not written: no --history rows and no --trend-prev')
    stores = [s for s in data['stores'] if s.get('scope') != 'program']
    ids = set(linked_ids(cfg, raw))
    missing_life = sorted(ids - set(life))
    print(f'wrote {a.out}: months {data["months"][0]} to {data["months"][-1]}, window to {data["window"]["to"]}')
    print(f'stores {info["stores_cut"]} cut + {info["adds_kept"]} additions (of {info["adds_considered"]} considered) '
          f'= {len(stores)} payout, {info["program"]} program; partners {len(data["partners"])}')
    for p in data['periods']:
        t = sum(s['by_month'].get(m, 0.0) for s in stores for m in p['months'])
        print(f'  {p["label"]:<12} earned ${t:,.2f}')
    for ln in info['registry_lines']:
        print(ln)
    g = info['gm']
    stl = data['stores']
    print(f'company dates parsed: {len(cdates)}; joined on {sum(1 for s in stl if s.get("joined"))} of {len(stl)} stores; paying since on '
          f'{sum(1 for s in stl if s.get("paying_since"))}; partner active_since on {sum(1 for p in data["partners"] if p.get("active_since"))} of {len(data["partners"])}')
    if g['share_violations']:
        print(f'product shares: {len(g["share_violations"])} store-month(s) clamped and renormalised: {g["share_violations"]}')
    print(f'gm store-months with paid revenue: {g["line_months"]} line items, {g["carried_months"]} carried, {g["none_months"]} none')
    if info['ambiguous']:
        print(f'AMBIGUOUS partner aliases (kept live name): {info["ambiguous"]}')
    if info['test_dropped']:
        print(f'test stores dropped: {info["test_dropped"]}')
    if missing_life:
        print(f'note: {len(missing_life)} linked ids absent from lifetime results (treated as 0 paid)')
    unmatched_pull = sorted({k[1] for k in pull} - ids)
    if unmatched_pull:
        print(f'note: {len(unmatched_pull)} ids in monthly results are not linked ids (ignored)')
    return 0


def load_slim():
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.join(here, '..', 'partneros')):
        if os.path.exists(os.path.join(d, 'slim.py')):
            sys.path.insert(0, os.path.abspath(d))
            import slim
            return slim
    raise SystemExit('ERROR --slim-out needs slim.py next to refresh.py (or in ../partneros)')


def write_slim(data, path):
    """PartnerOS snapshots/latest (and snapshots/stores-<i> shards when over slim.LIMIT): <path>, <stem>-stores-<i>.json."""
    slim = load_slim()
    s = slim.slim_snapshot(copy.deepcopy(data))
    latest, shards = slim.split(s)
    stem = path[:-5] if path.endswith('.json') else path
    for old in [f for f in os.listdir(os.path.dirname(os.path.abspath(path))) if f.startswith(os.path.basename(stem) + '-stores-')]:
        os.remove(os.path.join(os.path.dirname(os.path.abspath(path)), old))
    for sh in shards:
        sp = f'{stem}-stores-{sh["i"]}.json'
        open(sp, 'w', encoding='utf-8').write(slim.js(sh))
        print(f'wrote {sp}: {slim.size(sh):,} bytes, {len(sh["stores"])} stores (snapshots/stores-{sh["i"]})')
    open(path, 'w', encoding='utf-8').write(slim.js(latest))
    print(f'wrote {path}: {slim.size(latest):,} bytes slim (snapshots/latest), '
          + (f'{len(shards)} store shards' if shards else f'not sharded, {len(latest.get("stores") or [])} stores'))


LOCAL_KEYS = ('q2_alias', 'no_commission_partners', 'q2_recorded_paid', 'q2_realized_alerts_margin', 'alerts_unit_costs',
              'alerts_notion_url', 'q3_period_source', 'limits', 'limits_q3_final', 'limits_alltime', 'test_leads')
CFG_REQUIRED = ('no_commission_partners', 'q2_recorded_paid', 'q2_realized_alerts_margin', 'alerts_unit_costs', 'alerts_notion_url',
                'limits', 'limits_q3_final', 'limits_alltime')


def load_local():
    """local-data.json (CF_PARTNEROS_LOCAL, default docs/finance/partneros/local-data.json): the text and figures that stay out
    of the code repo. make-config copies LOCAL_KEYS into config.json; a missing file or key is a hard error."""
    path = os.environ.get('CF_PARTNEROS_LOCAL') or '/Users/idod/dev/docs/finance/partneros/local-data.json'
    if not os.path.exists(path):
        raise SystemExit(f'ERROR: local data file not found: {path}')
    loc = json.load(open(path, encoding='utf-8'))
    miss = [k for k in LOCAL_KEYS if k not in loc]
    if miss:
        raise SystemExit(f'ERROR: {path} is missing keys: {", ".join(miss)}')
    return loc


def require_cfg(cfg):
    miss = [k for k in CFG_REQUIRED if k not in cfg]
    if miss:
        raise SystemExit(f'ERROR: config.json is missing {", ".join(miss)}; rebuild it with refresh.py make-config')


def cmd_make_config(a):
    loc = load_local()
    d = a.dir
    q3 = os.path.abspath(os.path.join(d, '..'))
    csv_path = a.csv or os.path.join(q3, 'outputs', 'q3-partner-list-revenue-only.csv')
    q2_path = a.q2 or os.path.join(q3, 'inputs', 'q2-source-tab.csv')
    match_path = a.match or os.path.join(q3, 'outputs', 'unlinked-leads-hubspot-match.csv')
    shop_path = a.shopify_csv or os.path.abspath(os.path.join(q3, '..', '..', 'affiliate-revenue-check-2026-10-06.csv'))
    jl = lambda n, default: json.load(open(os.path.join(d, n))) if os.path.exists(os.path.join(d, n)) else default  # noqa: E731
    stores = []
    for r in csv.DictReader(open(csv_path, encoding='utf-8-sig')):
        ref = (r.get('Q3 Stripe invoiced (reference)') or '').strip()
        stores.append({'partner': r['Partner'].strip(), 'company': r['Company name'].strip(),
                       'handle': r['Store Name (ID)'].strip(), 'cfid': r['Chargeflow ID'].strip(),
                       'hs_id': r['HubSpot ID'].strip(), 'lead_id': r['Partner.io lead id'].strip(),
                       'lead_status': r['Partner.io status'].strip(),
                       'q3_reference': money(ref) if ref else None, 'added_since_cut': False,
                       'note': (r.get('Note') or '').strip()})
    raw_ov = jl('ids-override.json', {})
    ov = {}
    if isinstance(raw_ov, dict):
        for k, v in raw_ov.items():
            ov[k] = str(v.get('hs_id') if isinstance(v, dict) else v)
    elif isinstance(raw_ov, list):
        for e in raw_ov:
            hid = str(e.get('hs_id') or e.get('id') or '')
            for k in ('handle', 'cfid', 'chargeflow_id', 'company'):
                if e.get(k):
                    ov[e[k]] = hid
    ov = {k: v for k, v in ov.items() if v and v != 'None'}
    match_rows = [r for r in csv.DictReader(open(match_path, encoding='utf-8-sig')) if (r.get('hs_object_id') or '').strip()]
    lead_lists = []
    for n in ('partnerio-all-leads.json', 'partnerio-leads.json', 'additions.json'):
        raw = jl(n, [])
        if isinstance(raw, dict):
            raw = raw.get('leads') or raw.get('data') or raw.get('additions') or raw.get('stores') or []
        lead_lists.append([l for l in raw if isinstance(l, dict)])
    alias, _ = build_alias(stores, lead_lists)
    q2_alias = loc['q2_alias']
    q2 = {}
    for r in csv.DictReader(open(q2_path, encoding='utf-8-sig')):
        p = q2_alias.get(r['Partner'].strip(), r['Partner'].strip())
        e = q2.setdefault(p, {'paid': 0.0, 'loss': 0.0, 'by_product': defaultdict(float)})
        v = money(r.get('Payout'))
        e['loss' if r.get('revenue_type', '').strip() == 'loss' else 'paid'] += v
        e['by_product'][(r.get('product') or 'other').strip()] += v
    q2 = {k: {'paid': v['paid'], 'loss': v['loss'], 'by_product': dict(v['by_product'])} for k, v in q2.items()}
    at = jl('alltime.json', {})
    shop = defaultdict(float)
    seen = set()
    for r in csv.DictReader(open(shop_path, encoding='utf-8-sig')):
        h = (r.get('hs_company_id') or '').strip()
        if h:
            shop[h] += float(r.get('shopify_collected') or 0)
            seen.add(h)
    li = jl('alerts-lineitems.json', {})
    lpj = jl('lifetime-products.json', {})
    lifetime_products = {h: e['shares'] for h, e in (lpj.get('stores') or {}).items() if e.get('shares')}
    ratios, carry, last, viol = {}, {}, None, []
    for hid, per in (li.get('stores') or {}).items():
        rr = {}
        for m in sorted(per):
            e = per[m]
            if not e:
                continue
            al, au, pv, o = (float(e.get(k) or 0) for k in PRODUCT_KEYS)
            den = al + au + pv + o
            share = al / den if den > 0 else 0.0
            margin = min(1.0, max(0.0, float(e.get('alerts_gm') or 0) / al)) if al > 0 else None
            sh = line_shares(e, viol, (hid, m))
            rr[m] = {'alerts_share': share, 'alerts_margin': margin, 'automation_share': sh[1], 'prevent_share': sh[2],
                     'other_share': sh[3]}
            last = m if last is None or m > last else last
        if rr:
            ratios[hid] = rr
            tail = [rr[m] for m in sorted(rr)][-3:]
            ms = [t['alerts_margin'] for t in tail if t['alerts_margin'] is not None]
            cs = {k: sum(t[k] for t in tail) / len(tail) for k in ('alerts_share', 'automation_share', 'prevent_share', 'other_share')}
            t = cs['automation_share'] + cs['prevent_share'] + cs['other_share']
            if abs(cs['alerts_share'] + t - 1) > 0.001:   # means of shares that each sum to 1 do; keep the renormalising guard anyway
                viol.append((hid, 'carry'))
                k = (1 - cs['alerts_share']) / t if t > 0 else 0
                for key in ('automation_share', 'prevent_share', 'other_share'):
                    cs[key] *= k
            carry[hid] = {**cs, 'alerts_margin': (sum(ms) / len(ms)) if ms else None}
    print(f'product shares: {len(viol)} store-month(s) clamped and renormalised' + (f': {viol}' if viol else ' (none)'))
    prev_cfg = jl('config.json', {}) if not a.out else {}
    prev_hist = prev_cfg.get('history')
    cfg = {
        'version': 1,
        'built_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'sensitive': 'payout and revenue data; never commit',
        'portal': loc['hubspot_portal'],
        'from_month': '2026-01',
        'window': {'from': '2026-01-01'},
        'q3_months': ['2026-07', '2026-08', '2026-09'],
        'no_commission_partners': loc['no_commission_partners'],
        'no_commission_reason': 'self-referral, no commission',
        'stores_cut': stores,
        'match_rows': match_rows,
        'ids_override': ov,
        'alias': alias,
        'additions_extra': [],
        'q1': (jl('q1-actuals.json', {}) or {}).get('partners') or {},
        'q1_recorded_paid': None,
        'q3': (jl('q3-actuals.json', {}) or {}).get('partners') or {},
        'q2': q2,
        'q2_recorded_paid': loc['q2_recorded_paid'],
        'q2_realized_alerts_margin': loc['q2_realized_alerts_margin'],
        'pre_q1': {'payouts': at.get('payouts_pre_q1') or at.get('payouts_pre_q2') or [], 'complete': bool(at.get('payouts_pre_q1_complete'))},
        'pre_q2': {'payouts': at.get('payouts_pre_q1') or at.get('payouts_pre_q2') or [], 'complete': bool(at.get('payouts_pre_q1_complete'))},
        'shopify_lifetime': {k: v for k, v in shop.items()},
        'shopify_asof': at.get('shopify_asof') or '2026-08-02',
        'lifetime_products': lifetime_products,
        'gm': {'last_month': last, 'ratios': ratios, 'carry': carry,
               'note': 'ratios and product shares (alerts, automation, prevent, other) per store-month from alerts-lineitems.json; months after last_month use carry (mean of the last 3 line-item months) with gm_source carried'},
        'alerts_unit_costs': loc['alerts_unit_costs'],
        'alerts_notion_url': loc['alerts_notion_url'],
        'defaults': {'rate': 25, 'spend_basis': 'gm'},
        'history': prev_hist or {'pull': False, 'from': TREND_START, 'to': '2025-12'},
        'limits': loc['limits'],
        'limits_q3_final': loc['limits_q3_final'],
        'limits_alltime': loc['limits_alltime'],
        'limit_program': LIMIT_PROGRAM,
        'test_leads': loc['test_leads'],   # exact store names the refresh run drops besides any name containing "test"
    }
    out = a.out or os.path.join(d, 'config.json')
    json.dump(cfg, open(out, 'w'), indent=1, ensure_ascii=False)
    print(f'wrote {out} ({os.path.getsize(out):,} bytes): {len(stores)} cut rows, {len(match_rows)} match rows, '
          f'{len(alias)} aliases, {len(q2)} Q2 partners, {len(shop)} Shopify ids, gm ratios for {len(ratios)} stores (last month {last}), lifetime product shares for {len(lifetime_products)} stores')


# LIMITS, LIMITS_Q3_FINAL and LIMITS_ALLTIME (the footnote text, which names partners and figures) live in local-data.json
LIMIT_PROGRAM = ('All time counts every Partner.io lead with a HubSpot link and paid revenue; {n} linked leads have no HubSpot '
                 'record and are not counted. The dated periods cover the Q3 payout list only.')


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == 'print-queries':
        p = argparse.ArgumentParser(prog='refresh.py print-queries')
        p.add_argument('--config', required=True)
        p.add_argument('--leads', required=True)
        p.add_argument('--registry')
        p.add_argument('--today')
        return cmd_print_queries(p.parse_args(argv[1:])) or 0
    if argv and argv[0] == 'make-config':
        here = os.path.dirname(os.path.abspath(__file__))
        p = argparse.ArgumentParser(prog='refresh.py make-config')
        p.add_argument('--dir', default=os.environ.get('CF_DASH_DIR') or os.path.join(here, '..', '..', 'docs/finance/payouts/2026-q3/dashboard'))
        p.add_argument('--out')
        p.add_argument('--csv')
        p.add_argument('--q2')
        p.add_argument('--match')
        p.add_argument('--shopify-csv')
        a = p.parse_args(argv[1:])
        a.dir = os.path.abspath(a.dir)
        return cmd_make_config(a) or 0
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', required=True)
    p.add_argument('--leads', required=True)
    p.add_argument('--monthly', required=True, nargs='+')
    p.add_argument('--lifetime', required=True, nargs='+')
    p.add_argument('--company-dates', nargs='*', default=[])
    p.add_argument('--history', nargs='*', default=[], help='history*.txt: monthly rows before the window (only when config history.pull is true)')
    p.add_argument('--trend-prev', help='stored trend doc (JSON): carries its pre-window months forward without re-pulling')
    p.add_argument('--trend-out', help='trend.json path (default: next to --out)')
    p.add_argument('--out', required=True)
    p.add_argument('--slim-out', help='PartnerOS snapshots/latest doc (slim.py shape); shards beside it when over 240000 bytes')
    p.add_argument('--registry')
    p.add_argument('--today')
    return cmd_refresh(p.parse_args(argv))


if __name__ == '__main__':
    sys.exit(main())
