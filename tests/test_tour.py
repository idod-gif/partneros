"""The guided tour (template.html, block "build 12: guided tour"), in a headless browser on the synthetic page from tests/flo_fixture.py.
tests/tour_browser.js drives the page through the DOM only (offer card, Take the tour, user menu, palette, keys) with a stubbed window.claude
that records every db write. Skipped when node, playwright-core or Chromium is missing. Invented data only."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

import flo_fixture
from test_flo_tools import _ready

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, 'tour_browser.js')
TEMPLATE = os.path.join(flo_fixture.HERE, 'template.html')

# scenario name -> options; every run is one headless browser
RUNS = {
    'walk-owner': ('walk', {'role': 'owner'}),
    'walk-viewer': ('walk', {'role': 'viewer'}),
    'walk-noask': ('walk', {'role': 'editor', 'ask': False}),
    'walk-nodb': ('walk', {'role': 'none'}),
    'walk-phone': ('walk', {'role': 'owner', 'width': 390, 'height': 844}),
    'walk-tablet-dark': ('walk', {'role': 'viewer', 'width': 820, 'theme': 'dark'}),
    'walk-rm': ('walk', {'role': 'owner', 'rm': True}),
    'later': ('later', {'role': 'owner'}),
    'escoffer': ('escoffer', {'role': 'viewer'}),
    'skip': ('skip', {'role': 'owner'}),
    'restart': ('restart', {'role': 'owner'}),
    'restart-phone': ('restart', {'role': 'viewer', 'width': 390, 'height': 844}),
    'returning-role': ('returning', {'role': 'owner', 'prof': 'role'}),
    'returning-seen': ('returning', {'role': 'viewer', 'prof': 'seen'}),
    'missing': ('missing', {'role': 'owner'}),
    'keys': ('keys', {'role': 'owner'}),
}


@unittest.skipUnless(_ready(), 'node, playwright-core or Chromium not found')
class Tour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='tour_')
        cls.page = flo_fixture.build(os.path.join(cls.tmp, 'page.html'))

        def run(item):
            name, (scen, opts) = item
            p = subprocess.run(['node', JS, cls.page, scen, json.dumps(opts)], capture_output=True, text=True, timeout=300)
            try:
                return name, json.loads(p.stdout)
            except ValueError:
                return name, {'fatal': (p.stdout + p.stderr)[-800:], 'checks': [], 'errors': []}
        with ThreadPoolExecutor(max_workers=4) as ex:
            cls.runs = dict(ex.map(run, RUNS.items()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ok(self, name, at_least=1):
        r = self.runs[name]
        self.assertNotIn('fatal', r, r.get('fatal'))
        self.assertGreaterEqual(len(r['checks']), at_least, name)
        self.assertEqual([c['name'] + ': ' + c['detail'] for c in r['checks'] if not c['ok']], [], name)
        self.assertEqual(r['errors'], [], name)
        return r['out']

    def ids(self, name):
        return [s['id'] for s in self.runs[name]['out']['steps']]

    def body(self, name, sid):
        return next(s['body'] for s in self.runs[name]['out']['steps'] if s['id'] == sid)

    def test_owner_walk_forward_back_done(self):
        self.ok('walk-owner', 100)
        self.assertEqual(self.ids('walk-owner'), ['dashboard', 'attention', 'numbers', 'revenue', 'partners', 'outreach', 'events', 'payouts', 'leads',
                                                  'settings', 'search', 'ask', 'menu'])

    def test_viewer_gets_viewer_copy(self):
        self.ok('walk-viewer', 100)
        self.assertEqual(self.ids('walk-viewer'), self.ids('walk-owner'))
        self.assertIn('read its details', self.body('walk-viewer', 'partners'))
        self.assertIn('you can edit', self.body('walk-owner', 'partners'))
        self.assertIn('needs edit access', self.body('walk-viewer', 'ask'))
        self.assertIn('press Confirm', self.body('walk-owner', 'ask'))
        self.assertIn('record a payment', self.body('walk-owner', 'payouts'))
        self.assertNotIn('record', self.body('walk-viewer', 'payouts'))
        self.assertIn('need edit access', self.body('walk-viewer', 'menu'))

    def test_no_ask_ido_step_without_the_assistant(self):
        self.ok('walk-noask', 90)
        self.assertNotIn('ask', self.ids('walk-noask'))
        self.assertEqual(len(self.ids('walk-noask')), 12)

    def test_local_preview_without_a_database(self):
        self.ok('walk-nodb', 90)

    def test_phone_and_tablet_widths(self):
        self.ok('walk-phone', 90)
        self.ok('walk-tablet-dark', 90)
        self.assertIn('menu at the top', self.body('walk-phone', 'dashboard'))
        self.assertEqual(next(s['title'] for s in self.runs['walk-phone']['out']['steps'] if s['id'] == 'menu'), 'Take it again')

    def test_reduced_motion(self):
        self.ok('walk-rm', 90)

    def test_not_now_and_escape_on_the_offer_record_seen(self):
        self.ok('later', 3)
        self.ok('escoffer', 2)

    def test_skip_restores_section_scroll_and_focus(self):
        self.ok('skip', 6)

    def test_restart_from_sidebar_user_menu_and_palette(self):
        self.ok('restart', 8)
        self.ok('restart-phone', 5)

    def test_returning_users_get_no_offer(self):
        self.ok('returning-role', 2)
        self.ok('returning-seen', 2)

    def test_missing_target_is_skipped(self):
        self.ok('missing', 2)

    def test_keys_clicks_scroll_resize(self):
        self.ok('keys', 6)

    def test_copy_rules_in_the_source(self):
        with open(TEMPLATE, encoding='utf-8') as f:
            src = f.read()
        a = src.index('/* ==== build 12: guided tour.')
        b = src.index('/* ---- boot: render the seed first')
        block = src[a:b]
        self.assertNotRegex(block, '[—–]')
        titles = re.findall(r'title:(?:c=>c\.narrow\?)?"([^"]+)"(?::"([^"]+)")?', block)
        flat = [t for pair in titles for t in pair if t]
        self.assertGreaterEqual(len(flat), 13)
        for t in flat:
            self.assertTrue(1 <= len(t.split()) <= 4 and t[0].isupper(), t)
        # the tour never calls the assistant or a connector, and writes only through tourMark
        self.assertNotRegex(block, r'floSend|floRunSkill|callTool|claude\.use|\.set\(|\.update\(')
        self.assertEqual(block.count('profPatch('), 1)


if __name__ == '__main__':
    unittest.main()
