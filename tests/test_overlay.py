"""Characterization: ledger_overlay.apply_overlay and cmd_apply. Synthetic ledger and snapshot; skipped when openpyxl is not importable
(ledger_overlay imports it at module load)."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from tests._path import ROOT

try:
    import ledger_overlay as lo
except ImportError:   # pragma: no cover
    lo = None


def merchant(cf, lead, collected=0.0, shopify=0.0, invoiced=None):
    return {'chargeflow_id': cf, 'lead_id': lead, 'store': 'm-' + cf, 'partner': 'partner-a', 'stripe_collected_alltime': collected,
            'stripe_invoiced_alltime': collected if invoiced is None else invoiced, 'shopify_billed_alltime': shopify,
            'first_paid': None, 'last_paid': None, 'invoice_count': 1, 'paid_invoice_count': 1, 'shopify_charges': 0}


def ledger(*ms, alt=None):
    return {'asof': '2026-11-08', 'version': 'v2', 'label': 'ledger v2, 2026-11-08', 'merchants': {m['chargeflow_id']: m for m in ms},
            'lead_to_cfid': {m['lead_id']: m['chargeflow_id'] for m in ms}, 'alt_to_primary': alt or {}, 'skipped_uncounted': {}}


def snap_store(lead='L1', hs='1000001', cfid='', stripe=10.0, shopify=None, partner='partner-a', **kw):
    s = {'lead_id': lead, 'hs_id': hs, 'cfid': cfid, 'partner': partner, 'company': 'store-' + lead, 'scope': 'payout',
         'lifetime': {'stripe': stripe, 'shopify': shopify, 'total': stripe + (shopify or 0.0), 'first_paid_invoice': None}}
    s.update(kw)
    return s


@unittest.skipIf(lo is None, 'openpyxl not importable')
class ApplyOverlay(unittest.TestCase):
    def snap(self, *stores):
        return {'stores': list(stores), 'partners': [{'name': 'partner-a'}], 'limits': ['keep this'], 'alltime': {}}

    def test_match_by_lead_id_replaces_lifetime_with_ledger(self):
        sn = self.snap(snap_store(stripe=10.0, shopify=5.0))
        rep = lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=200.0, shopify=30.0)))
        lt = sn['stores'][0]['lifetime']
        self.assertEqual((lt['stripe'], lt['shopify'], lt['total'], lt['shopify_unsplit']), (200.0, 30.0, 230.0, 30.0))
        self.assertEqual(sn['stores'][0]['lifetime_prev'], {'stripe': 10.0, 'shopify': 5.0, 'total': 15.0})
        self.assertEqual(sn['stores'][0]['lifetime_source'], 'ledger')
        self.assertEqual(len(rep['matched']), 1)
        self.assertEqual(sn['alltime']['stripe_asof'], '2026-11-08')

    def test_v4_rails_use_collected_gross_and_carry_fees_and_net(self):
        sn = self.snap(snap_store(stripe=10.0, shopify=5.0), snap_store(lead='L9', hs='1000009', stripe=7.0))
        m = merchant('c1', 'L1', collected=300.0, shopify=40.0)
        m.update({'stripe_rail_collected': 200.0, 'shopify_gross_collected': 150.0, 'shopify_fees': 12.5, 'shopify_net_received': 137.5})
        led = ledger(m)
        led['rail_mode'] = 'collected'
        led['version'], led['label'] = 'v4', 'BI ledger v4, 2026-11-08'
        rep = lo.apply_overlay(sn, led)
        lt = sn['stores'][0]['lifetime']
        self.assertEqual((lt['stripe'], lt['shopify'], lt['total'], lt['shopify_unsplit']), (200.0, 150.0, 350.0, 150.0))
        self.assertEqual((lt['shopify_fees'], lt['shopify_net']), (12.5, 137.5))
        fb = sn['stores'][1]['lifetime']
        self.assertEqual((fb['stripe'], fb['shopify'], fb.get('shopify_fees')), (7.0, 0.0, None))
        self.assertEqual(sn['alltime']['shopify_basis'], 'collected gross (BI ledger)')
        self.assertEqual((sn['alltime']['shopify_fees'], sn['alltime']['shopify_net']), (12.5, 137.5))
        self.assertTrue(any('gross' in t for t in sn['limits']))
        n = len(sn['limits'])
        lo.apply_overlay(sn, led)
        self.assertEqual(len(sn['limits']), n)
        self.assertEqual(sn['stores'][0]['lifetime']['total'], 350.0)
        self.assertEqual(len(rep['matched']), 1)

    def test_no_ledger_merchant_keeps_hubspot_stripe_and_zero_shopify(self):
        sn = self.snap(snap_store(lead='L9', stripe=10.0, shopify=99.0))
        rep = lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=200.0)))
        lt = sn['stores'][0]['lifetime']
        self.assertEqual((lt['stripe'], lt['shopify'], lt['total']), (10.0, 0.0, 10.0))
        self.assertEqual(sn['stores'][0]['lifetime_source'], 'hubspot_fallback')
        self.assertEqual(len(rep['fallback']), 1)

    def test_match_by_cfid_and_alt_id(self):
        sn = self.snap(snap_store(lead='LX', cfid='alt1'))
        lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=70.0), alt={'alt1': 'c1'}))
        self.assertEqual(sn['stores'][0]['ledger_match'], 'cfid')
        self.assertEqual(sn['stores'][0]['lifetime']['stripe'], 70.0)

    def test_match_by_hs_id_through_leads_csv_rows(self):
        sn = self.snap(snap_store(lead='LX', hs='1000005'))
        lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=70.0)), [{'hubspot_company_id': '1000005', 'lead_id': 'L1'}])
        self.assertEqual(sn['stores'][0]['ledger_match'], 'hs_id')

    def test_hs_id_with_two_candidate_merchants_is_not_matched(self):
        sn = self.snap(snap_store(lead='LX', hs='1000005'))
        rows = [{'hubspot_company_id': '1000005', 'lead_id': 'L1'}, {'hubspot_company_id': '1000005', 'lead_id': 'L2'}]
        lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=1.0), merchant('c2', 'L2', collected=2.0)), rows)
        self.assertEqual(sn['stores'][0]['lifetime_source'], 'hubspot_fallback')

    def test_one_ledger_merchant_is_never_counted_on_two_stores(self):
        a, b = snap_store(lead='L1', hs='1000001'), snap_store(lead='LB', hs='1000002', cfid='c1')
        sn = self.snap(a, b)
        lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=200.0)))
        self.assertEqual(a['lifetime']['stripe'], 200.0)
        self.assertEqual(b['lifetime_source'], 'hubspot_fallback')
        self.assertEqual(b['ledger_match'], 'duplicate')

    def test_ledger_zero_but_hubspot_positive_is_reported_and_zeroed(self):
        sn = self.snap(snap_store(stripe=50.0))
        rep = lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=0.0)))
        self.assertEqual(sn['stores'][0]['lifetime']['stripe'], 0.0)
        self.assertEqual(len(rep['ledger_zero_hubspot_paid']), 1)

    def test_unmatched_billed_merchants_listed_and_zero_ones_not(self):
        sn = self.snap(snap_store(lead='L9'))
        rep = lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=200.0), merchant('c2', 'L2', collected=0.0)))
        self.assertEqual([e['chargeflow_id'] for e in rep['ledger_unmatched']], ['c1'])
        self.assertEqual(sn['alltime']['ledger_unmatched'][0]['stripe_collected_alltime'], 200.0)

    def test_program_adds_become_program_stores_matched_by_lead_id(self):
        sn = self.snap(snap_store(lead='L9', hs='1000009', stripe=7.0))
        adds = [{'partner': 'partner-a', 'company': 'new store', 'lead_id': 'L1', 'hs_id': '', 'cfid': 'c1'},
                {'partner': 'no-such-partner', 'company': 'orphan', 'lead_id': 'L2'},
                {'partner': 'partner-a', 'company': 'dup', 'lead_id': 'L9'}]
        led = ledger(merchant('c1', 'L1', collected=200.0, shopify=30.0), merchant('c2', 'L2', collected=5.0))
        rep = lo.apply_overlay(sn, led, program_adds=adds)
        self.assertEqual((rep['program_added'], rep['program_skipped']), (['L1'], ['L2']))
        new = [s for s in sn['stores'] if s['lead_id'] == 'L1'][0]
        self.assertEqual((new['scope'], new['lifetime']['stripe'], new['lifetime']['shopify'], new['ledger_match']), ('program', 200.0, 30.0, 'lead_id'))
        self.assertEqual([e['chargeflow_id'] for e in rep['ledger_unmatched']], ['c2'])
        self.assertEqual(len(sn['stores']), 2)
        lo.apply_overlay(sn, led, program_adds=adds)      # re-run adds nothing twice
        self.assertEqual(len(sn['stores']), 2)

    def test_idempotent_second_run_same_figures(self):
        sn = self.snap(snap_store(stripe=10.0), snap_store(lead='L9', hs='1000009', stripe=7.0))
        led = ledger(merchant('c1', 'L1', collected=200.0, shopify=30.0))
        lo.apply_overlay(sn, led)
        money = lambda: [{k: s['lifetime'][k] for k in ('stripe', 'shopify', 'total', 'products')} for s in sn['stores']]  # noqa: E731
        first = money()
        src1 = sn['stores'][0]['lifetime']['products_source']
        lo.apply_overlay(sn, led)
        self.assertEqual(money(), first)
        # the label drifts on a re-run (no config shares: none_as_other, then line_items, because the first run's split is reused as shares); amounts do not
        self.assertEqual((src1, sn['stores'][0]['lifetime']['products_source']), ('none_as_other', 'line_items'))
        self.assertEqual(sn['stores'][1]['lifetime']['stripe'], 7.0)       # fallback keeps the HubSpot figure through a re-run
        self.assertEqual(sn['stores'][0]['lifetime_prev']['stripe'], 10.0)  # first prev is not overwritten

    def test_product_split_sums_to_stripe_and_partner_rollup(self):
        sn = self.snap(snap_store(stripe=10.0))
        lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=100.0)), cfg_shares={'1000001': {'alerts': 0.5, 'automation': 0.25, 'prevent': 0.125, 'other': 0.125}})
        pr = sn['stores'][0]['lifetime']['products']
        self.assertEqual(round(sum(pr.values()), 2), 100.0)
        self.assertEqual(sn['partners'][0]['lifetime_products'], pr)

    def test_no_shares_puts_everything_in_other(self):
        sn = self.snap(snap_store(stripe=10.0))
        lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=100.0)))
        self.assertEqual(sn['stores'][0]['lifetime']['products'], {'alerts': 0.0, 'automation': 0.0, 'prevent': 0.0, 'other': 100.0})
        self.assertEqual(sn['stores'][0]['lifetime']['products_source'], 'none_as_other')

    def test_limits_line_replaced_not_duplicated(self):
        sn = self.snap(snap_store())
        sn['limits'] = ['keep this', 'All time: Stripe lifetime old text', 'after']
        led = ledger(merchant('c1', 'L1', collected=1.0))
        lo.apply_overlay(sn, led)
        n = len(sn['limits'])
        lo.apply_overlay(sn, led)
        self.assertEqual(len(sn['limits']), n)
        self.assertEqual(sn['limits'][0], 'keep this')
        self.assertIn('after', sn['limits'])

    def test_months_and_periods_untouched(self):
        s = snap_store(by_month={'2026-02': 9.0})
        sn = self.snap(s)
        sn['periods'] = [{'key': 'q1'}]
        lo.apply_overlay(sn, ledger(merchant('c1', 'L1', collected=1.0)))
        self.assertEqual(s['by_month'], {'2026-02': 9.0})
        self.assertEqual(sn['periods'], [{'key': 'q1'}])


@unittest.skipIf(lo is None, 'openpyxl not importable')
class CmdApplyShards(unittest.TestCase):
    def _run(self, d, extra=()):
        sn = os.path.join(d, 'snapshot.json')
        with open(sn, 'w') as f:
            json.dump({'stores': [snap_store()], 'partners': [{'name': 'partner-a'}], 'limits': [], 'alltime': {}, 'months': ['2026-01'],
                       'periods': [], 'window': {'from': '2026-01-01', 'to': '2026-01-31'}}, f)
        led = os.path.join(d, 'ledger.json')
        with open(led, 'w') as f:
            json.dump(ledger(merchant('c1', 'L1', collected=5.0)), f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lo.cmd_apply(['--snapshot', sn, '--ledger', led, '--out', sn, '--slim-out', os.path.join(d, 'slim.json'),
                          '--slim-dir', ROOT, *extra])
        return out.getvalue()

    def test_stale_shard_files_are_left_on_disk(self):
        # SUSPECT (money, stale data): cmd_apply writes shard files but never removes old slim-stores-<i>.json from an earlier, larger
        # run (refresh.write_slim does remove them). refresh-run.md step 5.1 sets every shard it finds, so a stale shard could be
        # written as snapshots/stores-<i> next to a fresh latest.
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, 'slim-stores-0.json')
            with open(stale, 'w') as f:
                f.write('{"stale":true}')
            self._run(d)
            self.assertTrue(os.path.exists(stale))
            self.assertEqual(open(stale).read(), '{"stale":true}')

    @unittest.expectedFailure
    def test_stale_shard_files_should_be_removed(self):
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, 'slim-stores-0.json')
            with open(stale, 'w') as f:
                f.write('{"stale":true}')
            self._run(d)
            self.assertFalse(os.path.exists(stale))

    def test_small_snapshot_writes_one_slim_file_and_no_shards(self):
        with tempfile.TemporaryDirectory() as d:
            out = self._run(d)
            self.assertTrue(os.path.exists(os.path.join(d, 'slim.json')))
            self.assertIn('0 shard(s)', out)
            self.assertFalse([f for f in os.listdir(d) if '-stores-' in f])


class RefreshWriteSlimRemovesStale(unittest.TestCase):
    def test_refresh_write_slim_removes_old_shards(self):
        import refresh
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, 'slim-stores-3.json')
            with open(stale, 'w') as f:
                f.write('x')
            data = {'generated': 'g', 'window': {}, 'months': [], 'periods': [], 'stores': [], 'partners': [], 'alltime': {}}
            with contextlib.redirect_stdout(io.StringIO()):
                refresh.write_slim(data, os.path.join(d, 'slim.json'))
            self.assertFalse(os.path.exists(stale))


if __name__ == '__main__':
    unittest.main()
