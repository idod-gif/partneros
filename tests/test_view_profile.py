"""View profiles (template.html, "build 19"): who gets the Finance view, matched case-insensitively on id and email, from the server field
or settings/view_profiles, with retries and a console warning on failure, no full-nav flash, and the admin "Preview the Finance view".
Runs tests/vp_browser.js on the synthetic page from tests/flo_fixture.py. Invented identities only."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

import flo_fixture
from test_flo_tools import _ready

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, 'vp_browser.js')
FIN = ['finance', 'dashboard', 'partners', 'payouts']
AT = chr(64)  # invented identities, built at run time
V1, V2, ADM = 'viewer.one' + AT + 'example.test', 'someone.else' + AT + 'example.test', 'admin' + AT + 'example.test'
DOC = {'profiles': {V1: 'finance'}}
RUNS = {
    'server': {'me': {'id': V1, 'viewProfile': 'finance', 'viewProfileStatus': 'ok'}},
    'server-none-wins': {'me': {'id': V1, 'viewProfile': None, 'viewProfileStatus': 'ok'}, 'settings': DOC},
    'client-exact': {'me': {'id': V1}, 'settings': DOC},
    'client-case-space': {'me': {'id': ' ' + V1.title() + ' '}, 'settings': {'profiles': {'  ' + V1.upper() + ' ': ' Finance '}}},
    'client-email-field': {'me': {'id': 'u-123', 'email': V1}, 'settings': DOC},
    'client-wrapped': {'me': {'id': V1}, 'settings': {'data': DOC}},
    'other-viewer': {'me': {'id': V2}, 'settings': DOC},
    'missing-doc': {'me': {'id': V1}, 'settings': None},
    'wrong-shape': {'me': {'id': V1}, 'settings': {'profiles': [V1]}},
    'read-error': {'me': {'id': V1}, 'settingsError': True, 'wait': 5500},
    'admin-card': {'me': {'id': ADM, 'viewProfile': None, 'viewProfileStatus': 'ok'}, 'canEdit': True, 'settings': DOC},
    'admin-preview': {'me': {'id': ADM, 'viewProfile': None, 'viewProfileStatus': 'ok'}, 'canEdit': True, 'settings': DOC, 'clickPreview': True},
    'viewer-no-preview': {'me': {'id': V2, 'viewProfile': None, 'viewProfileStatus': 'ok'}, 'viewas': 'finance', 'settings': DOC},
}


@unittest.skipUnless(_ready(), 'node, playwright-core or Chromium not found')
class ViewProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='vp_')
        cls.page = flo_fixture.build(os.path.join(cls.tmp, 'page.html'))

        def run(item):
            name, opts = item
            p = subprocess.run(['node', JS, cls.page, json.dumps(opts)], capture_output=True, text=True, timeout=300)
            try:
                return name, json.loads(p.stdout)
            except ValueError:
                return name, {'fatal': (p.stdout + p.stderr)[-800:]}
        with ThreadPoolExecutor(max_workers=4) as ex:
            cls.runs = dict(ex.map(run, RUNS.items()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def r(self, name):
        r = self.runs[name]
        self.assertNotIn('fatal', r, r.get('fatal'))
        self.assertEqual(r['errors'], [], name)
        self.assertFalse(r['waiting'], name + ': still hidden')
        return r

    def finance(self, name):
        r = self.r(name)
        self.assertEqual(r['nav'], FIN, name)
        self.assertEqual(r['sec'], 'p-finance', name)
        self.assertFalse(r['flash'], name + ': the full navigation was visible before the profile applied')

    def full(self, name):
        r = self.r(name)
        self.assertIn('outreach', r['nav'], name)
        return r

    def test_server_profile(self):
        self.finance('server')
        self.assertEqual(self.runs['server']['reads'], 0, 'the server field avoids the settings read')

    def test_server_answer_wins(self):
        self.full('server-none-wins')

    def test_client_matching(self):
        for n in ('client-exact', 'client-case-space', 'client-email-field', 'client-wrapped'):
            self.finance(n)

    def test_unmatched_and_bad_docs(self):
        for n in ('other-viewer', 'missing-doc', 'wrong-shape'):
            self.full(n)

    def test_read_error_retries_and_warns(self):
        r = self.full('read-error')
        self.assertEqual(r['reads'], 3)
        self.assertTrue(any('view profile could not be read' in w for w in r['warnings']), r['warnings'])

    def test_admin_card_lists_the_mapping(self):
        r = self.full('admin-card')
        self.assertIsNotNone(r['card'])
        self.assertIn(V1, r['card']['text'])
        self.assertTrue(r['card']['btn'])

    def test_admin_preview(self):
        r = self.r('admin-preview')
        self.assertEqual(r['nav'], FIN)
        self.assertTrue(r['preview'], 'the preview banner shows')

    def test_preview_needs_admin(self):
        r = self.full('viewer-no-preview')
        self.assertFalse(r['preview'])


if __name__ == '__main__':
    unittest.main()
