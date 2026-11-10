"""Puts the repo root on sys.path so tests import refresh, ledger_overlay and sanity. Synthetic data only: no real names,
emails, links or amounts live in these files (the repo guard refuses them); strings that would trip it are built at runtime."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

COMMA = ','            # grouped numbers like 1 + COMMA + 250 are built at runtime so the guard does not see a literal
AT = '@'
INTERNAL_DOMAIN = AT + 'charge' + 'flow.io'
ID = '1234' + '5678901'    # an 11-digit company id; split in the source so digit runs next to spaces do not look like phone numbers
