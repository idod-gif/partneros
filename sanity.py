#!/usr/bin/env python3
"""Pre-write sanity gate for the PartnerOS refresh (refresh-run.md step 4). Standard library only.

    python3 sanity.py NEW.json PREV.json --today YYYY-MM-DD [--json-out checks.json]
    python3 sanity.py NEW.json --first-run --today YYYY-MM-DD

NEW and PREV are snapshots (full snapshot.json or the joined slim doc): stores under `stores`, each with `scope`, `by_month`
(a missing month is 0), `lifetime.total` (null counts 0), `hs_id`, `lead_id`; PREV also needs `window.to` or `generated` (its build date), else pass --prev-date.

Prints one PASS or FAIL line per check with the two numbers compared, plus INFO lines, and exits 0 when every check passes,
1 when any fails, 2 on bad input. Thresholds default to the runbook's (20, 2, 1, 5 percent) and are arguments.

Where the runbook is silent this script fails closed: a previous value of 0 with a non-zero new value is a FAIL (the percent
change is undefined), and a missing previous file is an error unless --first-run is given.
"""
import argparse
import json
import sys

CLOSED_MONTHS = ('2026-07', '2026-08', '2026-09')   # runbook: Q3 (closed)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def payout_stores(snap):
    return [s for s in snap.get('stores') or [] if s.get('scope') == 'payout']


def month_sum(stores, months):
    return round(sum(_num((s.get('by_month') or {}).get(m)) for s in stores for m in months), 2)


def alltime(snap):
    return round(sum(_num((s.get('lifetime') or {}).get('total')) for s in snap.get('stores') or []), 2)


def store_key(s):
    """Same hs_id, else lead_id (runbook step 4, check 2)."""
    if s.get('hs_id'):
        return 'h:' + str(s['hs_id'])
    if s.get('lead_id'):
        return 'l:' + str(s['lead_id'])
    return None


def common_stores(new, prev):
    """(new payout stores, prev payout stores) restricted to keys present in both; plus the new stores with no match."""
    pk = {store_key(s) for s in payout_stores(prev)} - {None}
    nk = {store_key(s) for s in payout_stores(new)} - {None}
    both = pk & nk
    n = [s for s in payout_stores(new) if store_key(s) in both]
    p = [s for s in payout_stores(prev) if store_key(s) in both]
    only_new = [s for s in payout_stores(new) if store_key(s) not in pk]
    return n, p, only_new


def pct_change(new, prev):
    """Percent change of new on prev; None when prev is 0 and new is not (undefined)."""
    if prev == 0:
        return 0.0 if new == 0 else None
    return (new - prev) / abs(prev) * 100.0


def prev_last_full_month(prev, prev_date=None):
    """The month before the month the previous snapshot was built: --prev-date, else window.to, else generated. A joined shard
    file (refresh-run.md step 0.3) carries only `stores`, so then --prev-date is required."""
    to = prev_date or (prev.get('window') or {}).get('to') or (prev.get('generated') or '')[:10]
    if not to:
        raise ValueError('previous snapshot has no window.to or generated; pass --prev-date YYYY-MM-DD (its build date)')
    y, m = int(to[:4]), int(to[5:7])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f'{y:04d}-{m:02d}'


def _fmt(v):
    return f'{v:,.2f}'


def run_checks(new, prev, today, closed_months=CLOSED_MONTHS, payout_pct=20.0, q3_pct=2.0, alltime_drop_pct=1.0, month_pct=5.0,
               prev_date=None):
    """-> (checks, totals, previous_totals, info). checks = list of {id, name, ok, new, prev, detail}."""
    cur = today[:7]
    pn, pp = payout_stores(new), payout_stores(prev)
    totals = {'q3_earned': month_sum(pn, closed_months), 'current_month_earned': month_sum(pn, [cur]),
              'alltime_earned': alltime(new), 'payout_stores': len(pn)}
    previous = {'q3_earned': month_sum(pp, closed_months), 'current_month_earned': month_sum(pp, [cur]),
                'alltime_earned': alltime(prev), 'payout_stores': len(pp)}
    checks = []

    # 1. payout_stores within payout_pct of previous
    ch = pct_change(totals['payout_stores'], previous['payout_stores'])
    ok = ch is not None and abs(ch) <= payout_pct
    checks.append({'id': 1, 'name': 'payout_stores', 'ok': ok, 'new': totals['payout_stores'], 'prev': previous['payout_stores'],
                   'detail': f'change {"undefined" if ch is None else f"{ch:+.2f}%"}, limit {payout_pct:g}%'})

    # 2. closed quarter within q3_pct of previous, like for like over payout stores present in both
    n, p, only_new = common_stores(new, prev)
    nq, pq = month_sum(n, closed_months), month_sum(p, closed_months)
    ch = pct_change(nq, pq)
    ok = ch is not None and abs(ch) <= q3_pct
    checks.append({'id': 2, 'name': 'closed quarter like for like', 'ok': ok, 'new': nq, 'prev': pq,
                   'detail': f'{len(n)} common payout stores, change {"undefined" if ch is None else f"{ch:+.2f}%"}, limit {q3_pct:g}%'})

    # 3. alltime_earned not lower than previous by more than alltime_drop_pct
    ch = pct_change(totals['alltime_earned'], previous['alltime_earned'])
    ok = ch is not None and ch >= -alltime_drop_pct
    checks.append({'id': 3, 'name': 'alltime_earned', 'ok': ok, 'new': totals['alltime_earned'], 'prev': previous['alltime_earned'],
                   'detail': f'change {"undefined" if ch is None else f"{ch:+.2f}%"}, may not fall more than {alltime_drop_pct:g}%'})

    # 4. no month before the previous snapshot's last full month differs by more than month_pct (like for like as in 2)
    cutoff = prev_last_full_month(prev, prev_date)
    months = sorted({m for s in n + p for m in (s.get('by_month') or {}) if m < cutoff})
    worst, bad = None, []
    for m in months:
        a, b = month_sum(n, [m]), month_sum(p, [m])
        c = pct_change(a, b)
        mag = float('inf') if c is None else abs(c)
        if worst is None or mag > worst[0]:
            worst = (mag, m, a, b, c)
        if mag > month_pct:
            bad.append(m)
    if worst is None:
        checks.append({'id': 4, 'name': 'months before last full month like for like', 'ok': True, 'new': 0.0, 'prev': 0.0,
                       'detail': f'no months before {cutoff}'})
    else:
        _, m, a, b, c = worst
        checks.append({'id': 4, 'name': 'months before last full month like for like', 'ok': not bad, 'new': a, 'prev': b,
                       'detail': f'{len(months)} months before {cutoff}, worst {m} change {"undefined" if c is None else f"{c:+.2f}%"}, '
                                 f'limit {month_pct:g}%' + (f', failing: {", ".join(bad)}' if bad else '')})
    info = {'q3_new_payout_stores': month_sum(only_new, closed_months), 'new_payout_stores': len(only_new),
            'last_full_month_prev': cutoff}
    return checks, totals, previous, info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('new')
    ap.add_argument('prev', nargs='?')
    ap.add_argument('--first-run', action='store_true', help='no baseline: skip the checks and pass')
    ap.add_argument('--today', required=True, help='run date YYYY-MM-DD (UTC)')
    ap.add_argument('--closed-months', default=','.join(CLOSED_MONTHS), help='months of the closed quarter, default the runbook Q3')
    ap.add_argument('--payout-stores-pct', type=float, default=20.0)
    ap.add_argument('--closed-quarter-pct', type=float, default=2.0)
    ap.add_argument('--alltime-drop-pct', type=float, default=1.0)
    ap.add_argument('--month-pct', type=float, default=5.0)
    ap.add_argument('--prev-date', help='build date of PREV (YYYY-MM-DD); needed when PREV has neither window.to nor generated')
    ap.add_argument('--json-out', help='write {sanity, totals, previous, checks} for the run doc')
    a = ap.parse_args(argv)
    try:
        new = json.load(open(a.new))
        closed = tuple(m for m in a.closed_months.split(',') if m)
        if a.first_run:
            print('PASS first run, no baseline: checks skipped')
            if a.json_out:
                pn = payout_stores(new)
                json.dump({'sanity': 'pass', 'previous': None, 'checks': [],
                           'totals': {'q3_earned': month_sum(pn, closed), 'current_month_earned': month_sum(pn, [a.today[:7]]),
                                      'alltime_earned': alltime(new), 'payout_stores': len(pn)}}, open(a.json_out, 'w'), indent=1)
            return 0
        if not a.prev:
            print('ERROR: PREV is required unless --first-run is given', file=sys.stderr)
            return 2
        prev = json.load(open(a.prev))
        checks, totals, previous, info = run_checks(new, prev, a.today, closed, a.payout_stores_pct, a.closed_quarter_pct,
                                                    a.alltime_drop_pct, a.month_pct, a.prev_date)
    except (OSError, ValueError, KeyError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 2
    for c in checks:
        print(f'{"PASS" if c["ok"] else "FAIL"} {c["id"]} {c["name"]}: new {_fmt(c["new"])} vs previous {_fmt(c["prev"])} ({c["detail"]})')
    print(f'INFO closed quarter of {info["new_payout_stores"]} new payout stores (not checked): {_fmt(info["q3_new_payout_stores"])}')
    print(f'INFO current month earned: new {_fmt(totals["current_month_earned"])} previous {_fmt(previous["current_month_earned"])}')
    failed = [c for c in checks if not c['ok']]
    verdict = 'pass' if not failed else 'fail: ' + '; '.join(f'check {c["id"]} {c["name"]}, previous {_fmt(c["prev"])}, now {_fmt(c["new"])}' for c in failed)
    if a.json_out:
        json.dump({'sanity': verdict, 'totals': totals, 'previous': previous, 'checks': checks, 'info': info}, open(a.json_out, 'w'), indent=1)
    print('SANITY ' + ('PASS' if not failed else 'FAIL'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
