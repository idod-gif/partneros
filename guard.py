#!/usr/bin/env python3
"""Keeps data out of the PartnerOS code repo. Stdlib only.

    python3 guard.py FILE...     check the named files
    python3 guard.py --all       walk this directory (skips fonts/, _qa/, __pycache__, .git)

Fails (exit 1) on: emails, LinkedIn URLs, phone shapes (shared.PII), `$` followed by digits, comma-grouped numbers
(not in template.html), any .woff2 path, claude.ai artifact or routine URLs, and any term in
local-data.json `guard_terms` (case-insensitive, word boundary) when that file is present (a warning on stderr
when it is absent). template.html is checked for guard terms, emails and artifact URLs only.
The local-data path is CF_PARTNEROS_LOCAL or the default in shared.py."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared  # noqa: E402

SKIP_DIRS = {'fonts', '_qa', '__pycache__', '.git'}
SKIP_FILES = {'.DS_Store'}
EMAIL = next(rx for k, rx in shared.PII if k == 'email')
ARTIFACT = re.compile(r'claude\.ai/(artifact/|code/)', re.I)
DOLLAR = re.compile(r'\$\d')
BIGNUM = re.compile(r'(?<![\d,.])\d{1,3}(?:,\d{3})+(?![\d,])')


def load_terms():
    p = shared.LOCAL_PATH
    if not os.path.exists(p):
        print(f'WARNING guard: {p} not found; guard_terms NOT checked', file=sys.stderr)
        return []
    return [t for t in json.load(open(p, encoding='utf-8')).get('guard_terms') or [] if t]


def check(path, terms):
    out = []
    base = os.path.basename(path)
    if path.lower().endswith('.woff2'):
        return [(0, 'woff2 file', path)]
    try:
        text = open(path, encoding='utf-8').read()
    except (UnicodeDecodeError, IsADirectoryError):
        return [(0, 'unreadable or binary file', path)]
    tpl = base == 'template.html'
    rxs = [('email', EMAIL), ('artifact-url', ARTIFACT)]
    if not tpl:
        rxs += [('linkedin', re.compile(r'linkedin\.com', re.I)), ('dollar', DOLLAR), ('number', BIGNUM)]
    term_rx = [(t, re.compile(r'(?<!\w)' + re.escape(t) + r'(?!\w)', re.I)) for t in terms]
    for n, line in enumerate(text.splitlines(), 1):
        for kind, rx in rxs:
            for m in rx.finditer(line):
                out.append((n, kind, m.group(0)))
        if not tpl:
            for kind, v in shared.pii_hits(line):
                if kind == 'phone':
                    out.append((n, kind, v))
        for t, rx in term_rx:
            if rx.search(line):
                out.append((n, 'guard-term', t))
    return out


def walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if f not in SKIP_FILES:
                yield os.path.join(d, f)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return 2
    files = list(walk(HERE)) if args == ['--all'] else args
    terms = load_terms()
    bad = 0
    for f in files:
        if os.path.isdir(f) or not os.path.exists(f):
            continue
        for n, kind, v in check(f, terms):
            bad += 1
            shown = '<term>' if kind == 'guard-term' else v[:40]   # never echo a guard term: the output may be pasted
            print(f'{os.path.relpath(f, HERE)}:{n}: {kind}: {shown}')
    print(f'guard: {len(files)} files, {bad} findings')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
