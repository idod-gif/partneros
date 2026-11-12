"""Ask Ido's revenue and leads tools, in a headless browser on a synthetic page (tests/flo_fixture.py builds it from template.html; the page JS sits in
one closure, so the page gets a one-line test hook, window.__T). tests/flo_browser.js compares every tool result with what the Revenue and Leads sections
render for the same filter, then runs one scripted-model question through Ask Ido's tool loop. Skipped when node, playwright-core or Chromium is missing.
Invented data only: no real partner, store, lead, amount or contact appears here."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import flo_fixture

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, 'flo_browser.js')


def _ready():
    if not shutil.which('node'):
        return False
    pw = any(os.path.isdir(p) for p in ('/Users/idod/dev/dispute-desk/node_modules/playwright-core', '/Users/idod/dev/openscreen/node_modules/playwright-core')) or os.environ.get('FLO_PW')
    ch = any(os.path.exists(p) for p in ('/Users/idod/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium',
                                          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) or os.environ.get('FLO_CHROME')
    return bool(pw and ch)


@unittest.skipUnless(_ready(), 'node, playwright-core or Chromium not found')
class FloTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='flo_tools_')
        cls.page = flo_fixture.build(os.path.join(cls.tmp, 'page.html'))
        cls.nosnap = flo_fixture.build(os.path.join(cls.tmp, 'nosnap.html'), snapshot=False)
        cls.runs = {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_js(self, page, role, leads):
        key = (page, role, leads)
        if key not in self.runs:
            p = subprocess.run(['node', JS, page, role, leads], capture_output=True, text=True, timeout=180)
            self.assertTrue(p.stdout.strip(), p.stderr[-500:])
            self.runs[key] = json.loads(p.stdout)
        r = self.runs[key]
        self.assertNotIn('fatal', r, r.get('fatal'))
        return r

    def assert_all(self, r, tool, at_least):
        c = [x for x in r['checks'] if x['tool'] == tool]
        self.assertGreaterEqual(len(c), at_least, tool)
        bad = [x for x in c if not x['ok']]
        self.assertEqual(bad, [], tool)

    def test_tools_match_the_page_as_editor(self):
        r = self.run_js(self.page, 'editor', 'fixture')
        for tool in ('revenue_overview', 'revenue_by_partner', 'get_partner_revenue', 'leads_summary', 'search_leads'):
            self.assert_all(r, tool, 5)
        self.assertEqual(r['errors'], [])

    def test_viewer_sees_the_same(self):
        e, v = self.run_js(self.page, 'editor', 'fixture'), self.run_js(self.page, 'viewer', 'fixture')
        self.assertEqual([c for c in v['checks'] if not c['ok']], [])
        self.assertEqual(e['out']['keys'], v['out']['keys'])
        self.assertEqual(v['errors'], [])

    def test_end_to_end_loop(self):
        r = self.run_js(self.page, 'viewer', 'fixture')
        self.assert_all(r, 'e2e', 4)
        self.assertEqual(len(r['out']['e2e']['calls']), 2)

    def test_no_payout_fields_and_no_contact_data(self):
        r = self.run_js(self.page, 'viewer', 'fixture')
        banned = ('spend', 'payout', 'commission', 'owed', 'net', 'rate', 'no_comm', 'outside', 'email', 'phone', 'contact', 'linkedin', 'has_contact')
        keys = r['out']['keys']
        self.assertEqual([k for k in keys if any(b in k for b in banned)], [], keys)
        self.assertEqual(r['out']['hits'], {'email': False, 'canary': False, 'linkedin': False, 'phone': False})

    def test_lead_names_that_look_like_contact_data_are_withheld(self):
        o = self.run_js(self.page, 'viewer', 'fixture')['out']
        self.assertGreaterEqual(o['piiBlank'], 1)

    def test_result_sizes_stay_under_the_cap(self):
        o = self.run_js(self.page, 'viewer', 'fixture')['out']
        for k in ('ovLen', 'byLen', 'partLen', 'lsLen', 'slLen'):
            self.assertLess(o[k], 9500, k)

    def test_tools_leave_page_selection_alone(self):
        self.assertTrue(self.run_js(self.page, 'viewer', 'fixture')['out']['restored'])

    def test_gate_when_leads_not_loaded(self):
        r = self.run_js(self.page, 'viewer', 'none')
        self.assertFalse(r['out']['leadsOk'])
        self.assert_all(r, 'leads_summary', 1)
        self.assert_all(r, 'search_leads', 1)

    def test_gate_when_snapshot_not_loaded(self):
        r = self.run_js(self.nosnap, 'viewer', 'fixture')
        self.assertFalse(r['out']['snapOk'])
        for t in ('revenue_overview', 'revenue_by_partner', 'get_partner_revenue'):
            self.assert_all(r, t, 1)

    def test_stale_cost_line_is_host_neutral(self):
        with open(os.path.join(flo_fixture.HERE, 'template.html'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('one Claude call on your account', src)
        self.assertIn('one model call, charged by the host this page runs on', src)


if __name__ == '__main__':
    unittest.main()
