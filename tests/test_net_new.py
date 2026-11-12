"""Net-new revenue card rule, in a headless browser on the synthetic page (see tests/net_new_browser.js). Invented data only.
Skipped when node, playwright-core or Chromium is missing."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import flo_fixture
from test_flo_tools import _ready

JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'net_new_browser.js')


@unittest.skipUnless(_ready(), 'node, playwright-core or Chromium not found')
class NetNew(unittest.TestCase):
    def test_rule(self):
        tmp = tempfile.mkdtemp(prefix='net_new_')
        try:
            page = flo_fixture.build(os.path.join(tmp, 'page.html'))
            p = subprocess.run(['node', JS, page], capture_output=True, text=True, timeout=180)
            self.assertTrue(p.stdout.strip(), p.stderr[-500:])
            r = json.loads(p.stdout)
            self.assertNotIn('fatal', r, r.get('fatal'))
            self.assertEqual(r['errors'], [])
            self.assertGreaterEqual(len(r['checks']), 10)
            bad = [c for c in r['checks'] if not c['ok']]
            self.assertEqual(bad, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
