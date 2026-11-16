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
    'center-1440': ('center', {'role': 'owner'}),
    'center-820-dark': ('center', {'role': 'viewer', 'width': 820, 'theme': 'dark'}),
    'center-390': ('center', {'role': 'owner', 'width': 390, 'height': 844}),
    'center-390-short': ('center', {'role': 'viewer', 'width': 390, 'height': 600}),
    'center-noask': ('center', {'role': 'editor', 'ask': False}),
    'center-rm': ('center', {'role': 'owner', 'rm': True}),
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
        self.assertEqual(self.ids('walk-owner'), ['dashboard', 'numbers', 'revenue', 'partners', 'outreach', 'events', 'payouts', 'leads',
                                                  'settings', 'search', 'ask', 'menu'])

    def test_viewer_gets_viewer_copy(self):
        self.ok('walk-viewer', 100)
        self.assertEqual(self.ids('walk-viewer'), self.ids('walk-owner'))
        self.assertEqual(len(self.ids('walk-owner')), 12)
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
        self.assertEqual(len(self.ids('walk-noask')), 11)

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

    def test_start_card_is_centred_at_every_width(self):
        for name in ('center-1440', 'center-820-dark', 'center-390', 'center-390-short'):
            o = self.ok(name, 25)
            for k in ('welcome', 'restart'):
                self.assertLessEqual(abs(o[k]['dx']), 3, (name, k))

    def test_restart_card_without_ask_ido(self):
        o = self.ok('center-noask', 25)
        self.assertFalse(o['restart']['av'])

    def test_glide_frames_and_reduced_motion(self):
        self.assertGreaterEqual(self.ok('center-1440', 25).get('frames', 0), 25)
        self.assertNotIn('frames', self.ok('center-rm', 20))

    def test_copy_rules_in_the_source(self):
        with open(TEMPLATE, encoding='utf-8') as f:
            src = f.read()
        a = src.index('/* ==== build 12: guided tour.')
        b = src.index('/* ---- boot: render the seed first')
        block = src[a:b]
        self.assertNotRegex(block, '[\\u2014\\u2013]')
        titles = re.findall(r'title:(?:c=>c\.narrow\?)?"([^"]+)"(?::"([^"]+)")?', block)
        flat = [t for pair in titles for t in pair if t]
        self.assertGreaterEqual(len(flat), 12)
        for t in flat:
            self.assertTrue(1 <= len(t.split()) <= 4 and t[0].isupper(), t)
        # the tour never calls the assistant or a connector, and writes only through tourMark
        self.assertNotRegex(block, r'floSend|floRunSkill|callTool|claude\.use|\.set\(|\.update\(')
        self.assertEqual(block.count('profPatch('), 1)


# ---- the finance tour (template block "build 20: the finance tour"), tests/tour_finance_browser.js, on the same synthetic page plus a
# variant with an invented Finance ledger series inlined (FIN_LEDGER), so the Revenue explorer steps render. Invented data only.
FJS = os.path.join(HERE, 'tour_finance_browser.js')


def fake_ledger():
    ms = flo_fixture.MONTHS
    merch = []
    for i in range(1, 7):
        rows = [[m, 100.0 + 10 * i + k, 40.0 + i, 5.0, 2.0] for k, m in enumerate(ms)]
        merch.append({'id': str(1000 + i), 'cf': 'cf%d' % i, 'n': 'Store %d' % i, 'p': ('Alpha Agency', 'Alpha Agency', 'Beta Labs', 'Delta Co', 'Delta Co', 'Epsilon Works')[i - 1],
                      'st': 'won', 'fi': ms[0], 'c': rows, 'b': rows, 'g': {'c': [0.5, 0.4], 'b': [0.5, 0.4]}, 'sf': [[m, 1.0, 0.5, 0, 0] for m in ms], 'leads': [], 'hs': ['h%d' % i]})
    return {'source': {'label': 'Test ledger v0', 'asof': '2026-11-10', 'first': ms[0], 'last': ms[-1]}, 'merch': merch}


FRUNS = {
    'f-walk-query': ('walk', {'view': 'query'}),
    'f-walk-mapped': ('walk', {'view': 'mapped'}),
    'f-walk-preview': ('walk', {'view': 'preview'}),
    'f-walk-nodb': ('walk', {'view': 'query', 'db': False, 'ask': False}),
    'f-walk-noled': ('walk', {'view': 'query', 'page': 'plain'}),
    'f-walk-820-dark': ('walk', {'view': 'query', 'width': 820, 'theme': 'dark'}),
    'f-walk-390': ('walk', {'view': 'query', 'width': 390, 'height': 844}),
    'f-walk-390-dark': ('walk', {'view': 'mapped', 'width': 390, 'height': 844, 'theme': 'dark'}),
    'f-walk-rm': ('walk', {'view': 'query', 'rm': True}),
    'f-entries': ('entries', {'view': 'mapped'}),
    'f-entries-390': ('entries', {'view': 'query', 'width': 390, 'height': 844}),
    'f-wait': ('wait', {'view': 'mapped', 'slowVp': 2500}),
    'f-seen-general': ('seen', {'view': 'mapped', 'lsGeneral': True, 'prof': 'role'}),
    'f-seen-finance': ('seen', {'view': 'mapped', 'lsFinance': True}),
    'f-missing': ('missing', {'view': 'query'}),
}


@unittest.skipUnless(_ready(), 'node, playwright-core or Chromium not found')
class FinanceTour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='tourf_')
        plain = flo_fixture.build(os.path.join(cls.tmp, 'plain.html'))
        with open(plain, encoding='utf-8') as f:
            src = f.read()
        tok = '/*__FIN_LEDGER_JSON__*/'
        assert src.count(tok) == 1
        led = os.path.join(cls.tmp, 'ledger.html')
        with open(led, 'w', encoding='utf-8') as f:
            f.write(src.replace(tok, json.dumps(fake_ledger())))
        pages = {'plain': plain, 'ledger': led}

        def run(item):
            name, (scen, opts) = item
            pg = pages[opts.get('page', 'ledger')]
            p = subprocess.run(['node', FJS, pg, scen, json.dumps(opts)], capture_output=True, text=True, timeout=300)
            try:
                return name, json.loads(p.stdout)
            except ValueError:
                return name, {'fatal': (p.stdout + p.stderr)[-800:], 'checks': [], 'errors': []}
        with ThreadPoolExecutor(max_workers=4) as ex:
            cls.runs = dict(ex.map(run, FRUNS.items()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    ok = Tour.ok

    def ids(self, name):
        return [s['id'] for s in self.runs[name]['out']['steps']]

    def test_finance_view_gets_the_finance_tour_at_first_run(self):
        for name in ('f-walk-query', 'f-walk-mapped', 'f-walk-preview'):
            self.ok(name, 60)
            self.assertEqual(self.ids(name), ['fin-summary', 'fin-sources', 'fin-explorer', 'fin-chart', 'fin-payouts', 'fin-partners', 'fin-ask', 'fin-menu'], name)

    def test_without_ask_ido_or_a_database(self):
        self.ok('f-walk-nodb', 50)
        self.assertNotIn('fin-ask', self.ids('f-walk-nodb'))

    def test_without_the_ledger_the_chart_step_drops_and_the_explorer_says_why(self):
        self.ok('f-walk-noled', 50)
        self.assertNotIn('fin-chart', self.ids('f-walk-noled'))
        body = next(s['body'] for s in self.runs['f-walk-noled']['out']['steps'] if s['id'] == 'fin-explorer')
        self.assertIn('not loaded', body)

    def test_narrow_widths_dark_and_reduced_motion(self):
        for name in ('f-walk-820-dark', 'f-walk-390', 'f-walk-390-dark', 'f-walk-rm'):
            self.ok(name, 50)
        self.assertEqual(next(s['title'] for s in self.runs['f-walk-390']['out']['steps'] if s['id'] == 'fin-menu'), 'Take it again')

    def test_every_entry_point_gives_the_finance_tour(self):
        self.ok('f-entries', 12)
        self.ok('f-entries-390', 8)

    def test_waits_for_the_view_profile(self):
        self.ok('f-wait', 2)

    def test_seen_flags_are_per_tour(self):
        self.ok('f-seen-general', 3)
        self.ok('f-seen-finance', 2)

    def test_missing_target_is_skipped(self):
        self.ok('f-missing', 20)

    def test_finance_copy_rules_in_the_source(self):
        with open(TEMPLATE, encoding='utf-8') as f:
            src = f.read()
        a = src.index('the finance tour. Tours are keyed by view')
        b = src.index('const tourTxt=')
        block = src[a:b]
        self.assertNotRegex(block, '[\\u2014\\u2013]')
        self.assertNotRegex(block, r'\$\d|\d+%')
        for t in re.findall(r'title:(?:c=>c\.narrow\?)?"([^"]+)"(?::"([^"]+)")?', block):
            for x in t:
                if x:
                    self.assertTrue(1 <= len(x.split()) <= 4 and x[0].isupper(), x)


if __name__ == '__main__':
    unittest.main()
