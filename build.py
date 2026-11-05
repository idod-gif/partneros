#!/usr/bin/env python3
"""Build the PartnerOS page: inline the seed data into template.html and write partneros.html.

Seeds (the page prefers the artifact db when its docs are newer or present):
- /*__DASH_JSON__*/  dashboard.json through slim.slim_snapshot() (slim.py, shared with refresh.py --slim-out), slimmed to what PartnerOS reads (periods, window, stores' by_month; since build 4 also
                     the fields the ported leadership dashboard reads: partners, defaults, alltime, product and gm splits). PII-checked.
- /*__TREND_JSON__*/ trend.json, as is
- /*__REG_JSON__*/   {built, meta, partners}: the contact-free registry docs (shared.load_stripped)
- /*__EVENTS_JSON__*/ [{id,title,start,end,role}] from the same map's .stop elements, for the Overview countdown (null when the map is missing)
- /*__FLO_VOICE_JSON__*/ flo-voice.json as a whole ({voice, about, openers, placeholders}): the default openers behind Copy opener.
                     Required: the build exits 1 when the file is missing, unreadable or fails the PII check.
- /*__MAP_HTML__*/   the 2027 partner-events journey map (CF_EVENTS_MAP, default docs/projects/affiliate-summits-2027/map/index.html)
                     as one srcdoc string for Events > 2027 plan: font URLs pointed at the PartnerOS woff2 files, the map's own
                     lockup and eyebrow hidden, a height reporter appended. The map file is never edited; rebuild after it changes.

Inputs: CF_DASH_DIR (default docs/finance/payouts/2026-q3/dashboard), CF_REGISTRY_ROOT (default <dash dir>/registry).
Output: CF_PARTNEROS_OUT (default docs/finance/partneros/partneros.html), plus fonts/*.woff2 copied next to it.
Fonts are files (scripts/partneros/fonts/), referenced by relative URL in template.html and published as supporting files;
the build never inlines them and exits 1 if a data: font appears. docs/finance is gitignored; the output
carries revenue figures and is never committed. Private URLs and the like come from local-data.json (shared.load_local).

Usage: python3 build.py            writes the page, prints counts
       python3 build.py check      prints counts and the PII check, writes nothing
Exit 1 when the registry payload fails the PII check or a placeholder is missing."""
import json, os, re, sys, datetime, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared

DASH_DIR = os.environ.get('CF_DASH_DIR') or '/Users/idod/dev/docs/finance/payouts/2026-q3/dashboard'
OUT = os.environ.get('CF_PARTNEROS_OUT') or '/Users/idod/dev/docs/finance/partneros/partneros.html'
TEMPLATE = os.path.join(HERE, 'template.html')
PH = {'dash': '/*__DASH_JSON__*/', 'trend': '/*__TREND_JSON__*/', 'reg': '/*__REG_JSON__*/', 'feat': '/*__FEATURES_JSON__*/', 'map': '/*__MAP_HTML__*/', 'events': '/*__EVENTS_JSON__*/',
      'flo': '/*__FLO_VOICE_JSON__*/'}
FLO_VOICE = os.environ.get('CF_FLO_VOICE') or os.path.join(HERE, 'flo-voice.json')  # default openers for the Contacted guard (build 2)
FONTS = os.path.join(HERE, 'fonts')  # woff2 files, published as supporting files at fonts/<name>.woff2 (never inlined)
FEATURE_MAP = os.environ.get('CF_FEATURE_MAP') or '/Users/idod/dev/docs/projects/partneros/feature-map.md'
EVENTS_MAP = os.environ.get('CF_EVENTS_MAP') or '/Users/idod/dev/docs/projects/affiliate-summits-2027/map/index.html'
# the map's font files -> the PartnerOS woff2 files that replace them inside the embed (same faces, all published under fonts/)
MAP_FONTS = {'DegularDisplay-Medium.otf': ('Degular Display', '500', 'degular-display-500.woff2'),
             'DegularDisplay-Semibold.otf': ('Degular Display', '600', 'degular-display-600.woff2'),
             'DegularDisplay-Bold.otf': ('Degular Display', '700', 'degular-display-700.woff2'),
             'Helvetica-Now-Var.ttf': ('Helvetica Now Var', '100 900', 'helvetica-now-var.woff2')}
# injected into the srcdoc only: PartnerOS already shows the section title, so the map's lockup row and eyebrow go and the
# h1 drops to a section-heading size; side padding stays 16px (the map sits on its own light panel in dark mode)
MAP_EMBED_CSS = (
    'html,body{overflow:hidden!important;background:#f6f8fa}'
    '.page>header>.top,.page>header>.eyebrow{display:none!important}'
    '.page{padding-block:12px 24px}'
    '.page>header>h1{font-size:30px;line-height:1.1;max-width:none}'
    '.page>header>.sub{margin-top:8px;font-size:14px}'
    '@media (max-width:560px){.page>header>h1{font-size:26px}}')
# posts the document height to PartnerOS so the iframe has no inner scrollbar
MAP_EMBED_JS = (
    '(function(){var last=0;function send(){var b=document.body;if(!b)return;'
    'var h=Math.ceil(Math.max(b.getBoundingClientRect().height,b.scrollHeight));'
    'if(Math.abs(h-last)<2)return;last=h;try{parent.postMessage({type:"partneros-map-h",h:h},"*");}catch(e){}}'
    'if(window.ResizeObserver)new ResizeObserver(send).observe(document.body);'
    'window.addEventListener("load",send);window.addEventListener("resize",send);'
    'if(document.fonts&&document.fonts.ready)document.fonts.ready.then(send);send();setTimeout(send,600);setTimeout(send,2000);})();')
# stub section key -> heading number in feature-map.md ("## N. <Section>")
STUBS = {'outreach': 4, 'payouts': 5, 'leads': 6, 'settings': 7}
# the snapshot shape (KEEP lists, slim_dash, compact, split) lives in slim.py, shared with refresh.py --slim-out
from slim import STORE_KEEP, TOP_KEEP, PARTNER_KEEP, LIFETIME_KEEP, GM_KEEP, ALLTIME_KEEP, PRE_Q1_KEEP, slim_dash, slim_snapshot, js  # noqa: F401


def load(name):
    p = os.path.join(DASH_DIR, name)
    if not os.path.exists(p):
        print(f'MISSING {p}: seed skipped')
        return None
    return json.load(open(p, encoding='utf-8'))


def parse_features(path=FEATURE_MAP):
    """Parse the feature map: {stub key: [{feature, readiness, step}]}. Sections are matched by their number in
    the '## N. <Section>' heading. A missing file or heading prints a warning and leaves the key out, so the
    page falls back to its built-in stub text."""
    out = {}
    if not os.path.exists(path):
        print(f'WARNING: feature map not found at {path}; stubs use the built-in text')
        return out
    sections, cur = {}, None
    for line in open(path, encoding='utf-8').read().splitlines():
        m = re.match(r'##\s+(\d+)\.\s', line)
        if m:
            cur = int(m.group(1)); sections[cur] = []
        elif line.startswith('## '):
            cur = None
        elif cur is not None and line.startswith('|'):
            sections[cur].append(line)
    for key, num in STUBS.items():
        rows = []
        for line in sections.get(num, []):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) < 4 or cells[0].lower() == 'feature' or set(cells[0]) <= set('-: '):
                continue
            rows.append({'feature': cells[0].replace('`', ''), 'readiness': cells[-2], 'step': cells[-1]})
        if rows:
            out[key] = rows
        else:
            print(f'WARNING: no feature rows for section {num} ({key}) in the feature map; stub uses the built-in text')
    return out


MON3 = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}


def _txt(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html or '')).strip()


def _day_range(text):
    """First day-precise date or range in free text: '17-18 Feb 2027', '9-11 Mar 2027', '27 JUL 2026'.
    Returns (start, end) as YYYY-MM-DD, or None when the text only names a month or a season."""
    m = re.search(r'\b(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b', text or '')
    mo = MON3.get(m.group(3).lower()) if m else None
    if not mo:
        return None
    try:
        start = datetime.date(int(m.group(4)), mo, int(m.group(1)))
        end = datetime.date(int(m.group(4)), mo, int(m.group(2))) if m.group(2) else start
    except ValueError:
        return None
    return start.isoformat(), end.isoformat()


def parse_events(path=EVENTS_MAP):
    """The map's stops as [{id,title,start,end,role}], sorted by start, for the Overview countdown. A stop's date comes from
    data-start/data-end when the map carries them, else the first day-precise date in its .where line, else its stamp date.
    Stops with only a month or a season are left out (a countdown needs a day). None when the map file is missing."""
    if not os.path.exists(path):
        return None
    src = open(path, encoding='utf-8').read()
    out, skipped = [], []
    for part in re.split(r'(?=<div class="stop[\s"])', src)[1:]:
        tag = re.match(r'<div[^>]*>', part).group(0)
        attr = lambda k: (re.search(k + r'="([^"]*)"', tag) or [None, None])[1]
        sid, role = attr('data-id'), attr('data-role') or ''
        body = part[:8000]
        nm = re.search(r'class="nm">(.*?)</span>', body, re.S)
        title = _txt(nm.group(1)) if nm else ''
        where = re.search(r'class="where">(.*?)</p>', body, re.S)
        start, end = attr('data-start'), attr('data-end')
        if not start:
            rng = _day_range(_txt(where.group(1)) if where else '')
            if not rng:
                st = re.search(r'>(\d{1,2} [A-Z]{3} \d{4})<', body)
                rng = _day_range(st.group(1)) if st else None
            if rng:
                start, end = rng
        if not sid or not title or not start or role == 'none':
            skipped.append(sid or '?')
            continue
        out.append({'id': sid, 'title': title, 'start': start[:10], 'end': (end or start)[:10], 'role': role})
    out.sort(key=lambda e: e['start'])
    print(f'events: {len(out)} dated stops from the map, {len(skipped)} without a day-precise date left out ({", ".join(skipped)})')
    return out


def map_srcdoc(path=EVENTS_MAP):
    """The map page as one srcdoc string, or None when the file is missing (the tab then says so).
    Never edits the map file. Exits 1 when the map references a font file PartnerOS does not ship."""
    if not os.path.exists(path):
        print(f'WARNING: events map not found at {path}; Events > 2027 plan shows a notice')
        return None
    src = open(path, encoding='utf-8').read()
    faces = re.findall(r'@font-face\s*\{[^}]*\}', src)
    refs = set(re.findall(r'url\(["\']?(?:\./)?fonts/([^"\')]+)["\']?\)', ''.join(faces)))
    unknown = sorted(refs - set(MAP_FONTS))
    if unknown:
        print(f'ERROR: the events map uses font files PartnerOS does not map or ship: {", ".join(unknown)}. Add them to MAP_FONTS and fonts/.')
        sys.exit(1)
    for n in sorted({v[2] for v in MAP_FONTS.values()}):
        if not os.path.exists(os.path.join(FONTS, n)):
            print(f'ERROR: fonts/{n} is missing'); sys.exit(1)
    for f in faces:
        src = src.replace(f, '', 1)
    ff = ''.join(f'@font-face{{font-family:"{fam}";src:url(fonts/{w2}) format("woff2");font-weight:{wt};font-style:normal;font-display:swap}}'
                 for fam, wt, w2 in MAP_FONTS.values())
    head = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    if re.match(r'\s*<!doctype', src, re.I):
        print('WARNING: the events map now carries its own doctype; the embed still prepends one, check the result')
    tail = f'<style>{MAP_EMBED_CSS}</style><script>{MAP_EMBED_JS}</script>'
    out = f'{head}<style>{ff}</style></head><body>{src}{tail}</body></html>'
    print(f'events map: {len(src):,} bytes from {path}, {len(faces)} font faces remapped to woff2')
    return out


def main():
    check_only = len(sys.argv) > 1 and sys.argv[1] == 'check'
    dash = slim_snapshot(load('dashboard.json'))
    trend = load('trend.json')
    docs = shared.load_stripped()
    reg = {'built': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'meta': shared.load_meta(), 'partners': docs}
    reg_txt = js(reg)
    hits = shared.pii_hits(reg_txt)
    active = sum(1 for x in docs if (x.get('agreement') or {}).get('status') == 'active')
    print(f'registry: {len(docs)} partners, {active} active, {sum(x["has_contact"] for x in docs)} with a contact on file, {len(reg_txt):,} bytes')
    print(f'PII check (registry seed): {"PASS, 0 hits" if not hits else f"FAIL, {len(hits)} hits"}')
    if dash:
        dash_hits = shared.pii_hits(js(dash))
        print(f'PII check (dash seed): {"PASS, 0 hits" if not dash_hits else f"FAIL, {len(dash_hits)} hits"}')
        if dash_hits:
            for k, v in dash_hits[:10]:
                print(f'  {k}: {v[:3]}...')
            sys.exit(1)
        print(f'snapshot: generated {dash.get("generated")}, {len(dash["stores"])} stores, {len(js(dash)):,} bytes slimmed')
    if trend:
        print(f'trend: generated {trend.get("generated")}, {len(trend.get("months", []))} months')
    if not os.path.exists(FLO_VOICE):
        print(f'ERROR: {FLO_VOICE} is missing; the Contacted guard needs its default openers'); sys.exit(1)
    try:
        flo = json.load(open(FLO_VOICE, encoding='utf-8'))
    except ValueError as e:
        print(f'ERROR: {FLO_VOICE} is not valid JSON: {e}'); sys.exit(1)
    flo_hits = shared.pii_hits(js(flo))
    print(f'PII check (flo-voice): {"PASS, 0 hits" if not flo_hits else f"FAIL, {len(flo_hits)} hits"}')
    if flo_hits:
        sys.exit(1)
    feats = parse_features()
    mapdoc = map_srcdoc()
    events = parse_events()
    if events and shared.pii_hits(js(events)):
        print('PII check (events seed): FAIL'); sys.exit(1)
    print('features: ' + ', '.join(f'{k} {len(v)}' for k, v in feats.items()))
    if hits:
        for k, v in hits[:10]:
            print(f'  {k}: {v[:3]}...')
        sys.exit(1)
    if check_only:
        return
    tpl = open(TEMPLATE, encoding='utf-8').read()
    for k, ph in PH.items():
        if tpl.count(ph) != 1:
            print(f'ERROR: placeholder {ph} found {tpl.count(ph)} times in the template')
            sys.exit(1)
    html = tpl.replace(PH['dash'], js(dash) if dash else 'null')
    html = html.replace(PH['trend'], js(trend) if trend else 'null')
    html = html.replace(PH['reg'], reg_txt)
    html = html.replace(PH['feat'], js(feats))
    # '<' escaped so no '<!--', '<script' or '</script' from the map can reach the page's script parser
    html = html.replace(PH['map'], js(mapdoc).replace('<', '\\u003c') if mapdoc else 'null')
    html = html.replace(PH['events'], js(events) if events else 'null')
    html = html.replace(PH['flo'], js(flo))
    # private URLs live in local-data.json, never in the template
    for tok, key in (('__REFRESH_ROUTINE_URL__', 'refresh_routine_url'), ('__NEXT_URL__', 'next_tracker_url')):
        if html.count(tok) != 1:
            print(f'ERROR: token {tok} found {html.count(tok)} times in the template'); sys.exit(1)
        html = html.replace(tok, shared.load_local()['links']['template_links'][key])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if 'base64,' in html and '@font-face' in html.split('</style>')[0] and re.search(r'@font-face\{[^}]*data:font', html):
        print('ERROR: the template inlines a font as a data URI; fonts ship as files under fonts/')
        sys.exit(1)
    open(OUT, 'w', encoding='utf-8').write(html)
    print(f'wrote {OUT} ({len(html.encode()):,} bytes)')
    # copy the font files next to the page so a local preview resolves fonts/<name>.woff2 like the published artifact
    dst = os.path.join(os.path.dirname(OUT), 'fonts')
    os.makedirs(dst, exist_ok=True)
    for n in sorted(os.listdir(FONTS)):
        if n.endswith('.woff2'):
            shutil.copy2(os.path.join(FONTS, n), os.path.join(dst, n))
            print(f'font {n}: {os.path.getsize(os.path.join(FONTS, n)):,} bytes')


if __name__ == '__main__':
    main()
