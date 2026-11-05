#!/usr/bin/env python3
"""Events module sync: tracker.html (SEED + autolog blocks + optional export JSON) -> PartnerOS db manifests.

Read-only on tracker.html. Never writes to a db; produces ArtifactData batch manifests only.

  python3 events_sync.py [--tracker PATH] [--export PATH] [--out DIR] [--db-dir READBACK] [--now ISO]

--db-dir points at a readback of the live db (DIR/contacts/*.json, DIR/events/*.json); local data is merged onto it
(newer updated_at wins scalar fields, status never downgrades, statusLog/interactions/checks unioned).
Output: DIR/docs/{events,contacts}/<doc_id>.json and DIR/batch-NN.json (50 `set` entries each).
PII: contacts intentionally carry names and LinkedIn URLs (Ido, 2026-10-18); shared.pii_hits is NOT run here."""
import argparse, datetime, json, os, re, sys, collections

ROOT = '/Users/idod/dev'
TRACKER = f'{ROOT}/docs/recurring/events/tracker.html'
EXPORT = f'{ROOT}/docs/finance/partneros/events/tracker-export.json'
OUT = f'{ROOT}/docs/finance/partneros/events'
MAX_SHARD = 950
STATUSES = ['not-started', 'requested', 'replied', 'booked', 'met', 'followed-up', 'won', 'dead']
ORD = {s: i for i, s in enumerate(STATUSES)}
CONNECT_NOTE = 'Swapcard connect + Calendly invite'
PARTNER_NOTE = 'Partner program invite (partners.chargeflow.io)'
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def doc_id(i):
    return re.sub(r'[^a-z0-9_-]', '_', i.replace('|', '__').lower())


def iso_now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


# ---------- tracker parsing ----------
def bracket_json(text, start):
    """Decode the JSON value that begins at text[start] (a '[' or '{') with raw_decode."""
    return json.JSONDecoder().raw_decode(text[start:])[0]


def read_tracker(path):
    t = open(path, encoding='utf-8').read()
    m = re.search(r'^const SEED = ', t, re.M)
    seed = bracket_json(t, m.end())
    # script blocks: split on <script ...> ... </script>
    blocks = re.findall(r'<script([^>]*)>(.*?)</script>', t, re.S)
    auto = {'patch': [], 'IDS': [], 'CIDS': [], 'BIDS': [], 'PIDS': [], 'EV': []}
    for attrs, body in blocks:
        if 'cf-ase-autolog' in attrs:
            for k in ('IDS', 'CIDS', 'BIDS', 'PIDS'):
                mm = re.search(r'var %s=\[(.*?)\];' % k, body, re.S)
                auto[k] = re.findall(r'"([^"\n]+)"', re.sub(r'/\*.*?\*/', '', mm.group(1), flags=re.S)) if mm else []
        elif 'cf-manual-dm-autolog' in attrs:
            mm = re.search(r'var EV=\[(.*?)\];', body, re.S)
            for line in mm.group(1).splitlines():
                line = line.strip().rstrip(',')
                if line.startswith('{'):
                    auto['EV'].append(json.loads(line))
        elif 'DotDev connection DMs' in body:
            mm = re.search(r'var IDS = \[(.*?)\];', body, re.S)
            txt = '\n'.join(l for l in mm.group(1).splitlines() if not l.strip().startswith('//'))
            auto['patch'] = re.findall(r'"([^"\n]+)"', txt)
    ev = re.search(r'^const SEED_EVENTS = \[(.*?)^\];', t, re.M | re.S).group(1)
    events = []
    for em in re.finditer(r"\{id:'([^']+)', name:'([^']+)', dates:'([^']+)', checklist:\[(.*?)\]\},?", ev, re.S):
        items = [{'id': i.group(1), 'label': i.group(2), 'bumpsTo': None if i.group(3) == 'null' else i.group(3).strip("'"),
                  'outreach': i.group(4) == 'true'}
                 for i in re.finditer(r"\{id:'([^']+)', label:'([^']+)', bumpsTo:(null|'[^']+'), outreach:(true|false)\}", em.group(4))]
        events.append({'id': em.group(1), 'name': em.group(2), 'dates': em.group(3), 'checklist_items': items})
    # sanity: enums copied into the schema must match what the file says
    st = re.search(r'const STATUSES = \[(.*?)\];', t, re.S).group(1)
    if re.findall(r"id:'([^']+)'", st) != STATUSES:
        sys.exit('STATUSES in tracker.html differ from this script; update events_schema.md and events_sync.py')
    return seed, events, auto, os.path.getmtime(path)


# ---------- overlay logic (same rules as the tracker) ----------
def tsof(it):
    return (it or {}).get('ts') or (it or {}).get('at') or ''


def norm_overlay(o):
    o = json.loads(json.dumps(o or {}))
    ints = []
    for it in o.get('interactions') or []:
        if not isinstance(it, dict):
            continue
        n = {'ts': tsof(it), 'type': it.get('type') or 'Other', 'note': it.get('note') or ''}
        ints.append(n)
    o['interactions'] = ints
    o['statusLog'] = [s for s in (o.get('statusLog') or []) if isinstance(s, dict)]
    o['checks'] = dict(o.get('checks') or {})
    o['prevStatus'] = dict(o.get('prevStatus') or {})
    return o


def raise_status(o, to, only_from=None):
    cur = o.get('status')
    if only_from is not None:
        if not cur or cur in only_from:
            o['status'] = to
            return True
        return False
    if not cur or ORD[to] > ORD[cur]:
        o['status'] = to
        return True
    return False


def apply_autolog(ovs, auto, now, ckey):
    """Mutates ovs (id -> overlay). Returns set of ids whose status autolog set or raised."""
    got = set()
    def O(i):
        return ovs.setdefault(i, norm_overlay({}))
    for i in auto['patch']:
        o = O(i)
        if not o.get('status') or o['status'] == 'not-started':
            o['status'] = 'requested'; got.add(i)
    for i in auto['IDS']:
        o = O(i)
        if not o['checks'].get(ckey):
            o['checks'][ckey] = now
        if not o.get('status') or o['status'] == 'not-started':
            o['status'] = 'requested'; got.add(i)
    for i in auto['CIDS']:
        o = O(i)
        if not any(x['note'] == CONNECT_NOTE for x in o['interactions']):
            o['interactions'].append({'ts': now, 'type': 'Other', 'note': CONNECT_NOTE})
        if not o.get('status') or o['status'] == 'not-started':
            o['status'] = 'requested'; got.add(i)
    for i in auto['BIDS']:
        o = O(i)
        if not o.get('status') or o['status'] in ('not-started', 'requested', 'replied'):
            o['status'] = 'booked'; got.add(i)
    for i in auto['PIDS']:
        o = O(i)
        if not any(x['note'] == PARTNER_NOTE for x in o['interactions']):
            o['interactions'].append({'ts': now, 'type': 'Other', 'note': PARTNER_NOTE})
    for e in auto['EV']:
        o = O(e['id'])
        if raise_status(o, e['status']):
            got.add(e['id'])
        if not any(x['ts'] == e['ts'] and x['type'] == e['type'] for x in o['interactions']):
            o['interactions'].append({'ts': e['ts'], 'type': e['type'], 'note': e['note']})
    return got


# ---------- derived fields ----------
def derive(c):
    s = c.get('sheetNotes') or ''
    attending = None
    m = re.search(r'\[Attending: ([^\]]+)\]', s)
    if m:
        v = m.group(1).split(' - ')[0].strip().lower()
        attending = v
    typ = None
    m = re.match(r'\[(agency|media|network|super-affiliate) · tier', s)
    if m:
        typ = m.group(1)
    else:
        m = re.search(r'\[(agency|media|network|super-affiliate) · tier', s)
        if m:
            typ = m.group(1)
    li = re.search(r'https?://(?:www\.)?linkedin\.com/in/[^\s|\]\)"]+', s)
    return attending, typ, li.group(0) if li else None


def build_contact(c, o, shard_tier, now, seed_at):
    attending, typ, li = derive(c)
    status = o.get('status') or ('requested' if c.get('requested') else 'not-started')
    tss = [x['ts'] for x in o['interactions']] + [x.get('ts', '') for x in o['statusLog']] + list(o['checks'].values())
    tss = [t for t in tss if t and t != now and t <= now]
    return {
        'id': c['id'], 'event': c['event'], 'shard': f"{c['event']}__{c['tier']}",
        'name': c['name'], 'company': c['company'], 'title': c['title'], 'tier': c['tier'],
        'priority': bool(c.get('priority')), 'type': typ, 'angle': c.get('angle') or '',
        'sheetNotes': c.get('sheetNotes') or '', 'linkedin': li, 'attending': attending,
        'requested': bool(c.get('requested')), 'status': status,
        'statusLog': sorted(o['statusLog'], key=lambda s: s.get('ts', '')),
        'checks': o['checks'], 'prevStatus': o['prevStatus'],
        'interactions': sorted(o['interactions'], key=lambda x: x['ts']),
        'nextAction': o.get('nextAction') or '', 'nextDue': o.get('nextDue') or '', 'myNotes': o.get('myNotes') or '',
        'custom': bool(c.get('custom')), 'imported_at': now,
        'updated_at': max(tss + [seed_at]), 'history': [],
    }


def merge_db(local, db):
    """Merge a freshly built local doc onto the db readback doc."""
    if not db:
        return local
    out = dict(local)
    newer_db = (db.get('updated_at') or '') > local['updated_at']
    if newer_db:
        for k in ('name', 'company', 'title', 'tier', 'priority', 'type', 'angle', 'sheetNotes', 'linkedin', 'attending',
                  'nextAction', 'nextDue', 'myNotes'):
            if k in db:
                out[k] = db[k]
        if db.get('shard'):
            out['shard'] = db['shard']
    st_l, st_d = local['status'], db.get('status') or 'not-started'
    out['status'] = st_l if ORD[st_l] >= ORD.get(st_d, 0) else st_d
    sl = {(s.get('ts'), s.get('from'), s.get('to')): s for s in (db.get('statusLog') or []) + local['statusLog']}
    out['statusLog'] = sorted(sl.values(), key=lambda s: s.get('ts') or '')
    seen = {}
    for x in (db.get('interactions') or []) + local['interactions']:
        k = (x.get('ts'), x.get('type'), x.get('note'))
        seen.setdefault(k, {'ts': k[0], 'type': k[1], 'note': k[2]})
    ints = sorted(seen.values(), key=lambda x: x['ts'] or '')
    # autolog notes are stamped at import time; keep one per note like the tracker does
    done = set()
    keep = []
    for x in ints:
        if x['note'] in (CONNECT_NOTE, PARTNER_NOTE):
            if x['note'] in done:
                continue
            done.add(x['note'])
        keep.append(x)
    out['interactions'] = keep
    ch = dict(local['checks'])
    for k, v in (db.get('checks') or {}).items():
        ch[k] = min(v, ch[k]) if ch.get(k) else v
    out['checks'] = ch
    out['prevStatus'] = {**local['prevStatus'], **(db.get('prevStatus') or {})}
    out['history'] = db.get('history') or []
    out['imported_at'] = db.get('imported_at') or local['imported_at']
    out['updated_at'] = max(db.get('updated_at') or '', local['updated_at'])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tracker', default=TRACKER)
    ap.add_argument('--export', default=EXPORT)
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--db-dir')
    ap.add_argument('--now')
    a = ap.parse_args()
    now = a.now or iso_now()
    seed, events, auto, mtime = read_tracker(a.tracker)
    seed_at = datetime.datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%dT%H:%M:%SZ')
    ev_map = {e['id']: e for e in events}
    ckey = ev_map['ase-2026']['checklist_items'][0]['id']

    export = None
    if os.path.exists(a.export):
        export = json.load(open(a.export))
        export = export.get('state') or export
        print(f'export: FOUND {a.export}')
    else:
        print(f'export: NOT FOUND ({a.export}); proceeding without it. Custom contacts/events and in-app overlays are NOT included.')

    ovs = {}
    if export:
        for i, o in (export.get('overlays') or {}).items():
            ovs[i] = norm_overlay(o)
    base_status = {i: o.get('status') for i, o in ovs.items()}
    got = apply_autolog(ovs, auto, now, ckey)
    # contacts that got a status from autolog = autolog set/raised it (differs from export/no-overlay baseline)
    from_autolog = {i for i in got if ovs[i].get('status') != base_status.get(i)}
    seed_ids = {c['id'] for c in seed}
    from_autolog_seed = from_autolog & seed_ids

    contacts = list(seed)
    ids = {c['id'] for c in contacts}
    for c in (export or {}).get('customContacts') or []:
        if c['id'] not in ids:
            contacts.append(c); ids.add(c['id'])
    # autolog ids with no contact
    orphan = sorted(i for i in ovs if i not in ids)

    db_c, db_e = {}, {}
    if a.db_dir:
        for sub, dest in (('contacts', db_c), ('events', db_e)):
            d = f'{a.db_dir}/{sub}'
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith('.json'):
                        dest[fn[:-5]] = json.load(open(f'{d}/{fn}'))
        print(f'db readback: {len(db_c)} contacts, {len(db_e)} events')

    docs = {}
    for c in contacts:
        loc = build_contact(c, ovs.get(c['id']) or norm_overlay({}), None, now, seed_at)
        did = doc_id(c['id'])
        if did in docs:
            sys.exit(f'doc id collision: {did}')
        docs[did] = merge_db(loc, db_c.get(did))

    # events
    all_events = list(events)
    cc = (export or {}).get('customChecklist') or {}
    for e in (export or {}).get('customEvents') or []:
        if e['id'] not in ev_map:
            all_events.append({'id': e['id'], 'name': e.get('name', e['id']), 'dates': e.get('dates', ''),
                               'checklist_items': e.get('checklist') or []})
    ev_docs = {}
    for e in all_events:
        mine = [d for d in docs.values() if d['event'] == e['id']]
        items = e['checklist_items'] + cc.get(e['id'], [])
        ev_docs[e['id']] = {
            'id': e['id'], 'name': e['name'], 'dates': e['dates'], 'venue': None, 'city': None,
            'checklist': [i['id'] for i in items], 'checklist_items': items,
            'counts': {
                'total': len(mine),
                'by_tier': {t: sum(1 for d in mine if d['tier'] == t) for t in 'ABC'},
                'by_status': {s: sum(1 for d in mine if d['status'] == s) for s in STATUSES},
                'priority': sum(1 for d in mine if d['priority']),
                'attending_confirmed': sum(1 for d in mine if d['attending'] == 'confirmed')},
            'generated_at': now}

    shards = collections.Counter(d['shard'] for d in docs.values())
    for s, n in sorted(shards.items()):
        print(f'shard {s}: {n}')
    if any(n > MAX_SHARD for n in shards.values()):
        sys.exit(f'FAIL: a shard exceeds {MAX_SHARD}')

    # write docs + manifests
    out = a.out
    for sub in ('events', 'contacts'):
        os.makedirs(f'{out}/docs/{sub}', exist_ok=True)
    writes = []
    for eid, d in sorted(ev_docs.items()):
        p = f'{out}/docs/events/{doc_id(eid)}.json'
        open(p, 'w').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))
        writes.append({'op': 'set', 'collection': 'events', 'doc_id': doc_id(eid), 'file_path': p})
    for did, d in sorted(docs.items(), key=lambda kv: (kv[1]['shard'], kv[0])):
        p = f'{out}/docs/contacts/{did}.json'
        open(p, 'w').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))
        writes.append({'op': 'set', 'collection': 'contacts', 'doc_id': did, 'file_path': p})
    for f in os.listdir(out):
        if re.fullmatch(r'batch-\d+\.json', f):
            os.remove(f'{out}/{f}')
    for b in range(0, len(writes), 50):
        json.dump(writes[b:b + 50], open(f'{out}/batch-{b // 50 + 1:02d}.json', 'w'))

    n_li = sum(1 for d in docs.values() if d['linkedin'])
    def textof(d):
        return ' '.join([d['sheetNotes'], d['myNotes'], d['nextAction']] + [x['note'] for x in d['interactions']])
    n_em = sum(1 for d in docs.values() if EMAIL.search(textof(d)))
    n_em_angle = sum(1 for d in docs.values() if EMAIL.search(d['angle']))
    print(json.dumps({
        'total_docs': len(writes), 'events': len(ev_docs), 'contacts': len(docs), 'batches': (len(writes) + 49) // 50,
        'seed_per_event': dict(collections.Counter(c['event'] for c in seed)),
        'status_from_autolog_seed_contacts': len(from_autolog_seed), 'status_from_autolog_all_ids': len(from_autolog),
        'autolog_ids_without_contact': orphan[:10] + (['...'] if len(orphan) > 10 else []), 'autolog_orphans': len(orphan),
        'docs_with_linkedin': n_li, 'docs_with_email_in_notes': n_em, 'docs_with_email_in_angle': n_em_angle,
        'status_counts': dict(collections.Counter(d['status'] for d in docs.values())),
        'export_used': bool(export), 'out': out}, indent=1))


if __name__ == '__main__':
    main()
