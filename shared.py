"""Contact-free partner docs for the PartnerOS db and page seed.

Same whitelist as scripts/partner-registry/export_shared.py, plus:
- has_contact: true when the local doc holds an email, LinkedIn URL or phone for a person (the value never leaves the local file)
- agreement.contract_url and agreement.terms_version, null until contracts are ingested
Reads local registry files only."""
import json, os, re

REG = os.environ.get('CF_REGISTRY_ROOT') or '/Users/idod/dev/docs/finance/payouts/2026-q3/dashboard/registry'
LOCAL_PATH = os.environ.get('CF_PARTNEROS_LOCAL') or '/Users/idod/dev/docs/finance/partneros/local-data.json'
_LOCAL = None


def load_local():
    """The data that stays out of the repo: paid dates and notes, payout quarter specs, links, source labels, aliases,
    limits text, test leads, guard terms. One local gitignored file; a missing file is a hard error, never defaults."""
    global _LOCAL
    if _LOCAL is None:
        if not os.path.exists(LOCAL_PATH):
            raise SystemExit(f'ERROR: local data file not found: {LOCAL_PATH} (set CF_PARTNEROS_LOCAL or restore it)')
        _LOCAL = json.load(open(LOCAL_PATH, encoding='utf-8'))
    return _LOCAL


def paid_on():
    """{partner name: transfer date} for the final quarter; single source for enrich.py and step4_build.py."""
    return load_local()['paid_on']


def paid_note():
    return load_local()['paid_note']


def paid_on_quarter():
    """Quarter-level paid_on on the payouts doc: the latest transfer date."""
    return max(paid_on().values())


KEEP = ['name', 'aliases', 'partner_type', 'partner_type_confidence', 'tier', 'agreement', 'hubspot_company_id',
        'stores', 'last_touch', 'owed', 'flags', 'history', 'updated_at']

# PII patterns: any of these in a payload fails the build or the seed
PII = [('email', re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')), ('at-sign', re.compile(r'@')),
       ('linkedin', re.compile(r'linkedin\.com', re.I)), ('phone', re.compile(r'\+?\d[\d\s().-]{8,}\d'))]


def strip(d, fname):
    s = {'id': d.get('partnerio_id', int(fname[:-5]))}
    s.update({k: d.get(k) for k in KEEP})
    a = dict(s.get('agreement') or {})
    a.setdefault('contract_url', None)
    a.setdefault('terms_version', None)
    s['agreement'] = a
    c = d.get('contacts') or {}
    s['has_contact'] = bool(isinstance(c, dict) and any(c.get(k) for k in ('email', 'linkedin', 'phone')))
    for k in ('payouts_summary', 'page', 'connect', 'portal_agreement'):
        if k in d:
            s[k] = d[k]
    s['contacts_local'] = True
    return s


def load_stripped():
    out = []
    pdir = f'{REG}/partners'
    for f in sorted(os.listdir(pdir)):
        if f.endswith('.json'):
            out.append(strip(json.load(open(f'{pdir}/{f}')), f))
    return out


def load_meta():
    p = f'{REG}/meta.json'
    return json.load(open(p)) if os.path.exists(p) else None


def pii_hits(text):
    """Return [(kind, match)] for every PII pattern hit in text. Store ids and dates are digits without separators
    of phone shape only when 10+ digits run together, so the phone pattern skips ids joined by commas in JSON."""
    hits = []
    for kind, rx in PII:
        for m in rx.finditer(text):
            v = m.group(0)
            if kind == 'phone' and (re.fullmatch(r'\d+', v) or ',' in v or re.fullmatch(r'\d{4}-\d{2}-\d{2}.*', v)):
                continue
            hits.append((kind, v))
    return hits
