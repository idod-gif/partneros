"""Synthetic page for the Ask Ido revenue and leads tools: template.html with invented snapshot, trend and registry seeds, plus a test hook
that exposes the page's closure (ST, floT, lbGet and friends) as window.__T. Invented names and numbers only; nothing here is real data."""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11']
TREND_MONTHS = ['2025-10', '2025-11', '2025-12'] + MONTHS
PAYOUT_CANARY = 31337.11     # a payout figure planted on the partner and store rows; it must never reach a tool result

PARTNERS = [('Alpha Agency', 'Alpha', ['Alpha']), ('Beta Labs', None, []), ('Delta Co', 'Delta', []), ('Epsilon Works', None, [])]


def store(i, partner, base, scale=1.0, scope='payout', lifetime=None):
    bm = {m: round(base * scale * (1 + 0.1 * k) + 0.37, 2) for k, m in enumerate(MONTHS) if (k + i) % 5 != 4}
    lt = lifetime if lifetime is not None else round(sum(bm.values()) + 1000.13, 2)
    return {'lead_id': 1000 + i, 'hs_id': 'h%d' % i, 'partner': partner, 'store': 'store-%d.example' % i, 'scope': scope, 'by_month': bm,
            'paying_since': '2026-02-01', 'no_commission': i == 3, 'payout_due': PAYOUT_CANARY,
            'lifetime': {'stripe': lt, 'shopify': 200.5 if i % 2 == 0 else 0, 'total': lt + (200.5 if i % 2 == 0 else 0), 'products': {'alerts': lt}}}


def dash():
    stores = [store(1, 'Alpha Agency', 900), store(2, 'Alpha Agency', 400, 1.3), store(3, 'Beta Labs', 700),
              store(4, 'Delta Co', 250), store(5, 'Delta Co', 120, scope='program'), store(6, 'Epsilon Works', 60)]
    q = lambda k, lab, ms, st: {'key': k, 'label': lab, 'months': ms, 'status': st, 'spend_basis': 'actual' if st == 'paid' else 'estimate'}
    return {'generated': '2026-11-11T08:00:00Z', 'window': {'from': '2026-01-01', 'to': '2026-11-11'}, 'months': MONTHS,
            'periods': [q('q1', 'Q1', MONTHS[0:3], 'paid'), q('q2', 'Q2', MONTHS[3:6], 'paid'), q('q3', 'Q3', MONTHS[6:9], 'paid'), q('oct', 'Oct to date', MONTHS[9:], 'open')],
            'defaults': {'rate': 25, 'spend_basis': 'gm'}, 'alltime': {'pre_q2_by_partner': {}},
            'partners': [{'name': n, 'display_name': d, 'q2_actual_payout': PAYOUT_CANARY, 'no_commission': n == 'Epsilon Works'} for n, d, _ in PARTNERS if n != 'Epsilon Works'] + [{'name': 'Epsilon Works', 'no_commission': True}],
            'stores': stores}


def trend():
    n = len(TREND_MONTHS)
    return {'months': TREND_MONTHS, 'earned': [1000.0 + 10 * i for i in range(n)], 'stores': {'h%d' % i: [50.0 + i + k for k in range(n)] for i in range(1, 7)}}


def registry(with_orphan=True):
    parts = [{'id': 'r%d' % i, 'name': n, 'aliases': al, 'agreement': {'status': 'active'}, 'stores': ['%d' % (1000 + j) for j in ([1, 2], [3], [4, 5], [6])[i]],
              'payouts_summary': {'q3_final': PAYOUT_CANARY}} for i, (n, d, al) in enumerate(PARTNERS)]
    if with_orphan:
        parts.append({'id': 'r9', 'name': 'Gamma Partners', 'aliases': ['Gamma'], 'agreement': {'status': 'active'}, 'stores': []})
    return {'partners': parts, 'built': '2026-11-04', 'meta': {}}


HOOK = ('window.__T={ST,FLO,LBS,floT,floTools,floSystem,floSend,floMount,lbGet,renderLB,renderLeads,leadRows,leadStats,rowsAll,ovCompute,netNewEarned,storeEarned,setSection,todayLocal};\n')


def build(out_path, snapshot=True):
    with open(os.path.join(HERE, 'template.html'), encoding='utf-8') as f:
        src = f.read()
    js = lambda o: json.dumps(o) if o is not None else 'null'
    for tok, val in (('/*__DASH_JSON__*/', js(dash() if snapshot else None)), ('/*__TREND_JSON__*/', js(trend() if snapshot else None)), ('/*__REG_JSON__*/', js(registry())),
                     ('/*__FEATURES_JSON__*/', '{}'), ('/*__MAP_HTML__*/', '""'), ('/*__EVENTS_JSON__*/', 'null'), ('/*__FLO_VOICE_JSON__*/', '{}')):
        assert src.count(tok) == 1, tok
        src = src.replace(tok, val)
    end = 'floBoot();hsBoot();\n})();'
    assert src.count(end) == 1
    src = src.replace(end, 'floBoot();hsBoot();\n' + HOOK + '})();')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(src)
    return out_path
