"""sanity.py against synthetic snapshots: a passing baseline, one failing case per check, boundaries, like-for-like matching,
and the command line (exit codes, first run, json out)."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest

import sanity

Q3 = ('2026-07', '2026-08', '2026-09')


def mk(n=10, per_month=100.0, months=('2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10'),
       lifetime=1000.0, to='2026-11-04', prefix='h'):
    stores = []
    for i in range(n):
        stores.append({'scope': 'payout', 'hs_id': f'{prefix}{i}', 'lead_id': f'L{i}', 'by_month': {m: per_month for m in months},
                       'lifetime': {'total': lifetime}})
    return {'window': {'to': to}, 'stores': stores}


def run(new, prev, today='2026-11-18', **kw):
    return sanity.run_checks(new, prev, today, **kw)


def by_id(checks):
    return {c['id']: c for c in checks}


class Passing(unittest.TestCase):
    def test_identical_snapshots_pass_all_four(self):
        checks, totals, previous, _ = run(mk(), mk())
        self.assertEqual([c['ok'] for c in checks], [True] * 4)
        self.assertEqual(totals, previous)
        self.assertEqual(totals['q3_earned'], 3000.0)
        self.assertEqual(totals['alltime_earned'], 10000.0)
        self.assertEqual(totals['payout_stores'], 10)

    def test_current_month_is_reported(self):
        new = mk()
        for s in new['stores']:
            s['by_month']['2026-11'] = 7.0
        _, totals, previous, _ = run(new, mk())
        self.assertEqual(totals['current_month_earned'], 70.0)
        self.assertEqual(previous['current_month_earned'], 0.0)

    def test_program_scope_stores_do_not_count_as_payout(self):
        new = mk()
        new['stores'].append({'scope': 'program', 'hs_id': 'p1', 'by_month': {m: 999.0 for m in Q3}, 'lifetime': {'total': 5.0}})
        _, totals, _, _ = run(new, mk())
        self.assertEqual(totals['payout_stores'], 10)
        self.assertEqual(totals['q3_earned'], 3000.0)
        self.assertEqual(totals['alltime_earned'], 10005.0)   # all stores count for all time

    def test_null_lifetime_and_missing_month_count_as_zero(self):
        new = mk(n=1)
        new['stores'][0]['lifetime'] = None
        del new['stores'][0]['by_month']['2026-07']
        _, totals, _, _ = run(new, mk(n=1))
        self.assertEqual(totals['alltime_earned'], 0.0)
        self.assertEqual(totals['q3_earned'], 200.0)


class Check1PayoutStores(unittest.TestCase):
    def test_boundary_inclusive(self):
        self.assertTrue(by_id(run(mk(n=12), mk(n=10))[0])[1]['ok'])     # +20.0 percent
        self.assertTrue(by_id(run(mk(n=8), mk(n=10))[0])[1]['ok'])      # -20.0 percent

    def test_fail_over_twenty_percent(self):
        c = by_id(run(mk(n=13), mk(n=10))[0])[1]
        self.assertFalse(c['ok'])
        self.assertEqual((c['new'], c['prev']), (13, 10))

    def test_fail_on_drop(self):
        self.assertFalse(by_id(run(mk(n=7), mk(n=10))[0])[1]['ok'])

    def test_previous_zero_with_new_nonzero_fails_closed(self):
        self.assertFalse(by_id(run(mk(n=3), mk(n=0))[0])[1]['ok'])

    def test_both_zero_passes(self):
        self.assertTrue(by_id(run(mk(n=0), mk(n=0))[0])[1]['ok'])


class Check2ClosedQuarter(unittest.TestCase):
    def test_boundary_two_percent_passes(self):
        new = mk()
        for s in new['stores']:
            s['by_month']['2026-07'] = 106.0        # +6 per store on 300 per store = exactly 2.00 percent
        self.assertTrue(by_id(run(new, mk())[0])[2]['ok'])

    def test_fail_over_two_percent(self):
        new = mk()
        for s in new['stores']:
            s['by_month']['2026-07'] = 107.0        # 2.33 percent
        c = by_id(run(new, mk())[0])[2]
        self.assertFalse(c['ok'])
        self.assertEqual((c['new'], c['prev']), (3070.0, 3000.0))

    def test_new_payout_store_is_reported_not_failed(self):
        new = mk()
        new['stores'].append({'scope': 'payout', 'hs_id': 'extra', 'lead_id': 'LX', 'by_month': {m: 500.0 for m in Q3}, 'lifetime': {'total': 0}})
        checks, _, _, info = run(new, mk(), payout_pct=50.0)
        self.assertTrue(by_id(checks)[2]['ok'])
        self.assertEqual(info['q3_new_payout_stores'], 1500.0)
        self.assertEqual(info['new_payout_stores'], 1)

    def test_like_for_like_ignores_a_store_that_left(self):
        prev = mk()
        new = mk(n=9)                               # one store dropped out of the payout list
        c = by_id(run(new, prev)[0])[2]
        self.assertTrue(c['ok'])
        self.assertEqual((c['new'], c['prev']), (2700.0, 2700.0))

    def test_match_by_lead_id_when_no_hs_id(self):
        new, prev = mk(n=2), mk(n=2)
        for snap in (new, prev):
            for s in snap['stores']:
                s['hs_id'] = ''
        for s in new['stores']:
            s['by_month']['2026-08'] = 1000.0
        c = by_id(run(new, prev)[0])[2]
        self.assertFalse(c['ok'])
        self.assertEqual(c['prev'], 600.0)

    def test_stores_with_no_ids_are_never_common(self):
        new, prev = mk(n=2), mk(n=2)
        for snap in (new, prev):
            for s in snap['stores']:
                s['hs_id'] = ''
                s['lead_id'] = ''
        c = by_id(run(new, prev)[0])[2]
        self.assertEqual((c['new'], c['prev']), (0.0, 0.0))    # nothing to compare: passes with zero evidence

    def test_closed_months_argument(self):
        new = mk()
        for s in new['stores']:
            s['by_month']['2026-05'] = 200.0
        self.assertFalse(by_id(run(new, mk(), closed_months=('2026-05',))[0])[2]['ok'])


class Check3AllTime(unittest.TestCase):
    def test_drop_over_one_percent_fails(self):
        new = mk(lifetime=980.0)                    # -2 percent
        c = by_id(run(new, mk())[0])[3]
        self.assertFalse(c['ok'])
        self.assertEqual((c['new'], c['prev']), (9800.0, 10000.0))

    def test_boundary_one_percent_passes(self):
        self.assertTrue(by_id(run(mk(lifetime=990.0), mk())[0])[3]['ok'])

    def test_growth_passes_and_is_unbounded(self):
        self.assertTrue(by_id(run(mk(lifetime=5000.0), mk())[0])[3]['ok'])

    def test_alltime_is_not_like_for_like(self):
        new = mk(n=10)
        new['stores'] = new['stores'][:8]
        self.assertFalse(by_id(run(new, mk())[0])[3]['ok'])


class Check4EarlierMonths(unittest.TestCase):
    def test_previous_last_full_month(self):
        self.assertEqual(sanity.prev_last_full_month({'window': {'to': '2026-11-04'}}), '2026-10')
        self.assertEqual(sanity.prev_last_full_month({'window': {'to': '2027-01-02'}}), '2026-12')

    def test_restated_early_month_fails(self):
        new = mk()
        for s in new['stores']:
            s['by_month']['2026-03'] = 110.0        # +10 percent on a month well before October
        c = by_id(run(new, mk())[0])[4]
        self.assertFalse(c['ok'])
        self.assertIn('2026-03', c['detail'])
        self.assertEqual((c['new'], c['prev']), (1100.0, 1000.0))

    def test_boundary_five_percent_passes(self):
        new = mk()
        for s in new['stores']:
            s['by_month']['2026-03'] = 105.0
        self.assertTrue(by_id(run(new, mk())[0])[4]['ok'])

    def test_last_full_month_itself_is_excluded(self):
        # the runbook says "before the previous snapshot's last full month": 2026-10 here is that month, so it is not checked
        new = mk()
        for s in new['stores']:
            s['by_month']['2026-10'] = 500.0
        self.assertTrue(by_id(run(new, mk())[0])[4]['ok'])

    def test_month_with_zero_previous_and_revenue_now_fails(self):
        new = mk()
        for s in new['stores']:
            s['by_month']['2025-12'] = 10.0
        self.assertFalse(by_id(run(new, mk())[0])[4]['ok'])

    def test_empty_early_months_do_not_fail(self):
        new, prev = mk(), mk()
        for snap in (new, prev):
            for s in snap['stores']:
                s['by_month']['2025-12'] = 0.0
        self.assertTrue(by_id(run(new, prev)[0])[4]['ok'])

    def test_missing_prev_window_is_an_error(self):
        prev = mk()
        del prev['window']
        with self.assertRaises(ValueError):
            run(mk(), prev)

    def test_joined_shard_file_needs_prev_date_and_then_works(self):
        prev = {'stores': mk()['stores']}          # what refresh-run.md step 0.3 leaves when latest is sharded
        with self.assertRaises(ValueError):
            run(mk(), prev)
        checks, _, _, info = run(mk(), prev, prev_date='2026-11-02')
        self.assertTrue(all(c['ok'] for c in checks))
        self.assertEqual(info['last_full_month_prev'], '2026-10')

    def test_generated_is_used_when_window_is_absent(self):
        prev = mk()
        del prev['window']
        prev['generated'] = '2026-11-02T09:46:52Z'
        self.assertEqual(run(mk(), prev)[3]['last_full_month_prev'], '2026-10')

    def test_new_store_does_not_disturb_the_like_for_like_months(self):
        new = mk()
        new['stores'].append({'scope': 'payout', 'hs_id': 'extra', 'lead_id': 'LX', 'by_month': {'2026-03': 9999.0}, 'lifetime': {'total': 0}})
        self.assertTrue(by_id(run(new, mk(), payout_pct=50.0)[0])[4]['ok'])


class Cli(unittest.TestCase):
    def _go(self, new, prev=None, *args):
        with tempfile.TemporaryDirectory() as d:
            argv = []
            p = os.path.join(d, 'new.json')
            json.dump(new, open(p, 'w'))
            argv.append(p)
            if prev is not None:
                q = os.path.join(d, 'prev.json')
                json.dump(prev, open(q, 'w'))
                argv.append(q)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = sanity.main(argv + ['--today', '2026-11-18', '--json-out', os.path.join(d, 'o.json')] + list(args))
            js = json.load(open(os.path.join(d, 'o.json'))) if os.path.exists(os.path.join(d, 'o.json')) else None
            return code, out.getvalue(), err.getvalue(), js

    def test_pass_exit_zero_and_one_line_per_check(self):
        code, out, _, js = self._go(mk(), mk())
        self.assertEqual(code, 0)
        self.assertEqual([l[:6] for l in out.splitlines() if l.startswith(('PASS', 'FAIL'))], ['PASS 1', 'PASS 2', 'PASS 3', 'PASS 4'])
        self.assertEqual(js['sanity'], 'pass')
        self.assertEqual(js['totals']['q3_earned'], 3000.0)

    def test_failure_exit_one_names_check_and_numbers(self):
        code, out, _, js = self._go(mk(lifetime=500.0), mk())
        self.assertEqual(code, 1)
        self.assertIn('FAIL 3', out)
        self.assertTrue(js['sanity'].startswith('fail: check 3'))
        self.assertIn('5000'[0] + ',000.00', js['sanity'])

    def test_first_run_passes_without_checks(self):
        code, out, _, js = self._go(mk(), None, '--first-run')
        self.assertEqual(code, 0)
        self.assertIn('first run, no baseline', out)
        self.assertIsNone(js['previous'])

    def test_missing_prev_without_first_run_is_an_error(self):
        code, _, err, _ = self._go(mk(), None)
        self.assertEqual(code, 2)
        self.assertIn('PREV is required', err)

    def test_threshold_arguments_apply(self):
        code, _, _, _ = self._go(mk(n=13), mk(n=10), '--payout-stores-pct', '40')
        self.assertEqual(code, 0)

    def test_unreadable_input_exit_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sanity.main(['/nonexistent/new.json', '/nonexistent/prev.json', '--today', '2026-11-18']), 2)

    def test_does_not_mutate_inputs(self):
        new, prev = mk(), mk()
        a, b = copy.deepcopy(new), copy.deepcopy(prev)
        run(new, prev)
        self.assertEqual((new, prev), (a, b))


if __name__ == '__main__':
    unittest.main()
