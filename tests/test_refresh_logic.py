"""Characterization: apply_gm, apply_registry, the test-lead rule, internal-domain lead drop, compute_forecast, periods and a small
end-to-end build() (refresh.py). All data synthetic. SUSPECT = looks wrong for money; asserted as-is, with an expectedFailure twin."""
import json
import os
import tempfile
import unittest
from unittest import mock

from tests._path import AT, INTERNAL_DOMAIN
import refresh

MONTHS = ['2026-01', '2026-02', '2026-03']


def store(hs='1000001', by_month=None, partner='partner-a', **kw):
    s = {'hs_id': hs, 'partner': partner, 'handle': 'store-a', 'company': 'store-a', 'lead_id': 'L1', 'by_month': by_month or {}}
    s.update(kw)
    return s


def ratio(share=0.4, margin=0.5, auto=0.3, prev=0.2, other=0.1):
    return {'alerts_share': share, 'alerts_margin': margin, 'automation_share': auto, 'prevent_share': prev, 'other_share': other}


class ApplyGm(unittest.TestCase):
    def test_line_items_month_commissionable(self):
        s = store(by_month={'2026-01': 100.0})
        cfg = {'gm': {'ratios': {'1000001': {'2026-01': ratio(share=0.4, margin=0.5)}}}}
        tot = refresh.apply_gm(cfg, [s], MONTHS)
        # paid x ((1 - share) + share x margin) = 100 x (0.6 + 0.2)
        self.assertEqual(s['commissionable_by_month']['2026-01'], 80.0)
        self.assertEqual(s['gm_by_month']['2026-01']['gm_source'], 'line_items')
        self.assertEqual(tot['line_months'], 1)
        self.assertAlmostEqual(tot['alerts_rev'], 40.0)
        self.assertAlmostEqual(tot['alerts_gm_rev'], 20.0)
        self.assertEqual(round(sum(s['product_by_month']['2026-01'].values()), 2), 100.0)

    def test_null_margin_is_treated_as_zero_margin(self):
        # SUSPECT (money, understates payout): an unknown alerts margin (None) counts as 0, so the alerts share of revenue drops out of
        # the commissionable base entirely. make-config only writes None when no alerts were billed (share 0), so it is latent
        # there, but a hand-edited or carried entry with a share and a None margin would zero the alerts revenue silently.
        s = store(by_month={'2026-01': 100.0})
        cfg = {'gm': {'ratios': {'1000001': {'2026-01': ratio(share=0.4, margin=None)}}}}
        tot = refresh.apply_gm(cfg, [s], MONTHS)
        self.assertEqual(s['commissionable_by_month']['2026-01'], 60.0)
        self.assertIsNone(s['gm_by_month']['2026-01']['alerts_margin'])
        self.assertEqual(tot['alerts_gm_rev'], 0.0)

    def test_carried_month_after_last_month(self):
        s = store(by_month={'2026-03': 100.0})
        cfg = {'gm': {'last_month': '2026-02', 'ratios': {}, 'carry': {'1000001': ratio(share=0.5, margin=0.2)}}}
        tot = refresh.apply_gm(cfg, [s], MONTHS)
        self.assertEqual(s['gm_by_month']['2026-03']['gm_source'], 'carried')
        self.assertAlmostEqual(s['commissionable_by_month']['2026-03'], 100 * (0.5 + 0.5 * 0.2))
        self.assertEqual(tot['carried_months'], 1)

    def test_carried_month_with_no_paid_has_no_gm_entry(self):
        s = store(by_month={})
        cfg = {'gm': {'last_month': '2026-02', 'ratios': {}, 'carry': {'1000001': ratio()}}}
        refresh.apply_gm(cfg, [s], MONTHS)
        self.assertNotIn('2026-03', s['gm_by_month'])
        self.assertEqual(s['commissionable_by_month']['2026-03'], 0.0)

    def test_no_ratio_means_full_revenue_commissionable(self):
        s = store(by_month={'2026-01': 100.0})
        tot = refresh.apply_gm({'gm': {}}, [s], MONTHS)
        self.assertEqual(s['commissionable_by_month']['2026-01'], 100.0)
        self.assertEqual(s['gm_by_month']['2026-01']['gm_source'], 'none')
        self.assertEqual(s['product_by_month']['2026-01'], {'alerts': 0.0, 'automation': 0.0, 'prevent': 0.0, 'other': 100.0})
        self.assertEqual(tot['none_months'], 1)

    def test_missing_line_item_month_inside_range_is_fully_commissionable(self):
        # SUSPECT (money, overstates payout): a store that has line items for other months but none for this paid month (at or
        # before last_month) falls to 'none': the whole month is commissionable, alerts included at no cost, instead of using the
        # carry ratio or flagging the gap. Only after last_month does the carry apply.
        s = store(by_month={'2026-01': 100.0})
        cfg = {'gm': {'last_month': '2026-03', 'ratios': {'1000001': {'2026-02': ratio(share=0.4, margin=0.0)}},
                      'carry': {'1000001': ratio(share=0.4, margin=0.0)}}}
        refresh.apply_gm(cfg, [s], MONTHS)
        self.assertEqual(s['commissionable_by_month']['2026-01'], 100.0)
        self.assertEqual(s['gm_by_month']['2026-01']['gm_source'], 'none')

    @unittest.expectedFailure
    def test_missing_line_item_month_inside_range_should_use_carry(self):
        s = store(by_month={'2026-01': 100.0})
        cfg = {'gm': {'last_month': '2026-03', 'ratios': {'1000001': {'2026-02': ratio(share=0.4, margin=0.0)}},
                      'carry': {'1000001': ratio(share=0.4, margin=0.0)}}}
        refresh.apply_gm(cfg, [s], MONTHS)
        self.assertEqual(s['commissionable_by_month']['2026-01'], 60.0)

    def test_pre_split_ratio_without_automation_share_is_alerts_and_other(self):
        s = store(by_month={'2026-01': 100.0})
        cfg = {'gm': {'ratios': {'1000001': {'2026-01': {'alerts_share': 0.25, 'alerts_margin': 1.0}}}}}
        refresh.apply_gm(cfg, [s], MONTHS)
        self.assertEqual(s['product_by_month']['2026-01'], {'alerts': 25.0, 'automation': 0.0, 'prevent': 0.0, 'other': 75.0})

    def test_store_without_hs_id_gets_full_commissionable(self):
        s = store(hs='', by_month={'2026-01': 40.0})
        refresh.apply_gm({'gm': {'ratios': {'': {'2026-01': ratio()}}}}, [s], MONTHS)
        # ratios.get('') matches a '' key: an empty hs_id would pick up a ratio filed under the empty string
        self.assertEqual(s['gm_by_month']['2026-01']['gm_source'], 'line_items')

    def test_line_shares_clamps_negative_and_records_violation(self):
        viol = []
        sh = refresh.line_shares({'alerts_billed': 120, 'automation_billed': -20, 'prevent_billed': 0, 'other_billed': 0}, viol, 'k')
        self.assertEqual(viol, ['k'])
        self.assertAlmostEqual(sum(sh), 1.0)
        self.assertEqual(sh[1], 0.0)

    def test_line_shares_no_billed_is_all_other(self):
        self.assertEqual(refresh.line_shares({}), (0.0, 0.0, 0.0, 1.0))

    def test_split_dollars_sum_to_paid(self):
        d = refresh.split_dollars(100.01, (1 / 3, 1 / 3, 1 / 3, 0.0))
        self.assertAlmostEqual(sum(d.values()), 100.01, places=2)


def reg_partner(name):
    return {'name': name, 'stores': 1}


class ApplyRegistry(unittest.TestCase):
    def setUp(self):
        self.stores = [store(partner='label-a', lead_id='L1', scope='payout')]
        self.partners = [reg_partner('label-a')]

    def _apply(self, docs):
        return refresh.apply_registry(docs, self.stores, self.partners, {})

    def test_matches_by_lead_id_and_sets_flags(self):
        docs = [{'name': 'Registry A', 'stores': ['L1'], 'agreement': {'status': 'active', 'rate': 30, 'signed_on': '2026-02-01'}}]
        summary, _ = self._apply(docs)
        p = self.partners[0]
        self.assertEqual(p['display_name'], 'Registry A')
        self.assertEqual(p['rate_override'], 30)
        self.assertEqual(p['signed_on'], '2026-02-01')
        self.assertNotIn('no_commission', p)
        self.assertEqual(summary['active'], 1)
        self.assertEqual(summary['missing'], [])

    def test_default_rate_gives_no_override(self):
        self._apply([{'name': 'Registry A', 'stores': ['L1'], 'agreement': {'status': 'active', 'rate': 25}}])
        self.assertIsNone(self.partners[0].get('rate_override'))

    def test_inactive_or_missing_agreement_is_no_commission(self):
        self._apply([{'name': 'Registry A', 'stores': ['L1'], 'agreement': {'status': 'terminated'}}])
        self.assertTrue(self.partners[0]['no_commission'])
        self.assertTrue(self.stores[0]['no_commission'])
        self.assertIn('terminated', self.stores[0]['no_commission_reason'])

    def test_doc_without_agreement_is_no_commission(self):
        self._apply([{'name': 'Registry A', 'stores': ['L1']}])
        self.assertTrue(self.partners[0]['no_commission'])

    def test_flag_overrides_active_agreement(self):
        self._apply([{'name': 'Registry A', 'stores': ['L1'], 'flags': {'no_commission': True}, 'agreement': {'status': 'active'}}])
        self.assertTrue(self.partners[0]['no_commission'])

    def test_name_and_alias_match_is_case_insensitive(self):
        summary, lines = self._apply([{'name': 'LABEL-A', 'agreement': {'status': 'active'}}])
        self.assertEqual(summary['missing'], [])
        self.assertTrue(any('name or alias' in l for l in lines))

    def test_partner_without_doc_listed_missing_and_untouched(self):
        self.partners.append(reg_partner('label-b'))
        summary, _ = self._apply([{'name': 'Registry A', 'stores': ['L1'], 'agreement': {'status': 'active'}}])
        self.assertEqual(summary['missing'], ['label-b'])
        self.assertNotIn('display_name', self.partners[1])

    def test_migration_stamp_creation_date_is_dropped(self):
        self._apply([{'name': 'Registry A', 'stores': ['L1'], 'created_at': '2026-06-08T01:00:00Z', 'agreement': {'status': 'active'}}])
        self.assertIsNone(self.partners[0]['partnerio_created'])

    def test_second_doc_for_the_same_partner_is_silently_dropped(self):
        # SUSPECT (money, either direction): when two registry docs match one partner the first in list order wins and the
        # second is dropped without a trace (it is not even counted in "docs match no dashboard partner"). If the first is a
        # terminated doc and the second the active agreement, the partner is flagged no-commission (payout withheld); if the order
        # is reversed a terminated agreement keeps earning. The result depends on registry list order.
        terminated = {'name': 'Registry Old', 'stores': ['L1'], 'agreement': {'status': 'terminated'}}
        active = {'name': 'Registry New', 'stores': ['L1'], 'agreement': {'status': 'active'}}
        summary, lines = self._apply([terminated, active])
        self.assertEqual(self.partners[0]['display_name'], 'Registry Old')
        self.assertTrue(self.partners[0]['no_commission'])
        self.assertTrue(any('0 docs match no dashboard partner' in l for l in lines))   # the dropped doc is not reported
        self.assertEqual(summary['loaded'], 2)

    @unittest.expectedFailure
    def test_second_doc_for_the_same_partner_should_not_flip_payout_by_list_order(self):
        for docs in ([{'name': 'Registry Old', 'stores': ['L1'], 'agreement': {'status': 'terminated'}},
                      {'name': 'Registry New', 'stores': ['L1'], 'agreement': {'status': 'active'}}],):
            self.stores[0].pop('no_commission', None)
            self.partners[0].pop('no_commission', None)
            self._apply(docs)
            self.assertNotIn('no_commission', self.partners[0])   # expected: the active agreement wins whatever the order


class TestLeadRule(unittest.TestCase):
    def test_whole_word_test_is_dropped(self):
        for name in ('Test', 'test store', 'My Test Store', 'store (TEST)', 'test-shop', 'shop.test'):
            self.assertTrue(refresh.is_test(name), name)

    def test_substring_test_is_kept(self):
        # the code matches the whole word only
        for name in ('Latest Goods', 'Contest Co', 'Testimonial Labs', 'Tester Shop', 'Attest', 'protest-wear'):
            self.assertFalse(refresh.is_test(name), name)

    def test_underscore_joined_test_is_kept(self):
        # underscore is a word character, so test_shop and my_test_store are NOT dropped
        self.assertFalse(refresh.is_test('test_shop'))
        self.assertFalse(refresh.is_test('my_test_store'))

    def test_runbook_substring_rule_differs_from_code(self):
        # SUSPECT (money, understates if the runbook is followed literally): refresh-run.md step 1 tells the agent to drop "any
        # name containing test (case-insensitive)", a substring rule, while refresh.py drops only the whole word. A real store such
        # as "Latest Goods" is dropped by the agent's hand filter but would survive in the code.
        self.assertFalse(refresh.is_test('Latest Goods'))

    def test_checks_company_or_handle_and_none_safe(self):
        self.assertTrue(refresh.is_test('store-a', 'test'))
        self.assertTrue(refresh.is_test(None, 'Test'))
        self.assertFalse(refresh.is_test(None, None))

    def test_test_rule_applies_to_additions_only(self):
        cfg = {'no_commission_partners': [], 'stores_cut': [store(hs='1000001', company='Test Store', lead_id='L1')],
               'no_commission_reason': 'x', 'match_rows': []}
        leads = [{'lead_id': 'L2', 'status': 'approved', 'hs_id': '1000002', 'company': 'Test Store', 'partner': 'p'},
                 {'lead_id': 'L3', 'status': 'approved', 'hs_id': '1000003', 'company': 'Latest Goods', 'partner': 'p'}]
        stores, adds, _, _, _, dropped = refresh.assemble_stores(cfg, leads)
        self.assertEqual([s['company'] for s in stores], ['Test Store'])          # a cut store named Test is NOT filtered
        self.assertEqual([a['company'] for a in adds], ['Latest Goods'])
        self.assertEqual(dropped, ['Test Store'])


class InternalLeadDrop(unittest.TestCase):
    def lead(self, email=None, contact=None):
        l = {'id': 'x'}
        if email is not None:
            l['data'] = {'email': email}
        if contact is not None:
            l['contacts'] = [{'fields': [{'value': contact}]}]
        return l

    def test_internal_domain_dropped_in_either_place(self):
        self.assertTrue(refresh.is_internal_test_lead(self.lead(email='someone' + INTERNAL_DOMAIN)))
        self.assertTrue(refresh.is_internal_test_lead(self.lead(contact='someone' + INTERNAL_DOMAIN)))

    def test_case_and_whitespace_ignored(self):
        self.assertTrue(refresh.is_internal_test_lead(self.lead(email='  Someone' + INTERNAL_DOMAIN.upper() + ' ')))

    def test_subdomain_and_lookalikes_kept(self):
        for e in ('a' + AT + 'mail.example.org', 'a' + '@sub.' + INTERNAL_DOMAIN[1:], 'a' + '@not' + INTERNAL_DOMAIN[1:],
                  'a' + INTERNAL_DOMAIN + '.example.org', 'a' + AT + 'example.org'):
            self.assertFalse(refresh.is_internal_test_lead(self.lead(email=e)), e)

    def test_malformed_shapes_do_not_crash(self):
        self.assertFalse(refresh.is_internal_test_lead({'data': 'x', 'contacts': 'y'}))
        self.assertFalse(refresh.is_internal_test_lead({'contacts': [None, {'fields': [None, {'value': 5}]}]}))

    def test_load_leads_drops_them_silently(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'leads.json')
            with open(p, 'w') as f:
                json.dump({'leads': [self.lead(email='a' + INTERNAL_DOMAIN), {'id': 'keep'}, 'junk']}, f)
            self.assertEqual(refresh.load_leads(p), [{'id': 'keep'}])


class Periods(unittest.TestCase):
    CFG = {}

    def _legacy(self, months, q3m, q3):
        """The hardcoded block build() had before the calendar change (source value stubbed)."""
        return [
            {'key': 'q1', 'label': 'Q1', 'months': [m for m in months if '2026-01' <= m <= '2026-03'], 'status': 'paid', 'spend_basis': 'actual'},
            {'key': 'q2', 'label': 'Q2', 'months': [m for m in months if '2026-04' <= m <= '2026-06'], 'status': 'paid', 'spend_basis': 'actual'},
            {'key': 'q3', 'label': 'Q3', 'months': [m for m in months if m in q3m],
             **({'status': 'paid', 'spend_basis': 'actual', 'source': 'src'} if q3 else {'status': 'in progress', 'spend_basis': 'estimate'})},
            {'key': 'oct', 'label': 'Oct to date', 'months': [m for m in months if m >= '2026-10'], 'status': 'open', 'spend_basis': 'estimate'}]

    def _new(self, today, q3):
        months = refresh.month_range('2026-01', today[:7])
        with mock.patch.object(refresh, 'load_local', return_value={'q3_period_source': 'src'}):
            return refresh.build_periods(months, today, ['2026-07', '2026-08', '2026-09'], q3, self.CFG)

    def test_same_as_legacy_for_october_to_december_2026(self):
        for today in ('2026-10-05', '2026-11-04', '2026-12-31'):
            for q3 in ({}, {'p': {}}):
                months = refresh.month_range('2026-01', today[:7])
                self.assertEqual(self._new(today, q3), self._legacy(months, ['2026-07', '2026-08', '2026-09'], q3), (today, bool(q3)))

    def test_november_run_has_q1_q2_q3_and_open_oct_bucket(self):
        per = self._new('2026-11-04', {'p': {}})
        self.assertEqual([p['key'] for p in per], ['q1', 'q2', 'q3', 'oct'])
        self.assertEqual(per[3]['months'], ['2026-10', '2026-11'])
        self.assertEqual(per[3]['label'], 'Oct to date')
        self.assertEqual(per[3]['status'], 'open')

    def test_q3_in_progress_without_actuals(self):
        self.assertEqual(self._new('2026-11-04', {})[2]['status'], 'in progress')

    def test_january_2027_gets_q4_and_a_new_open_period(self):
        per = self._new('2027-01-12', {'p': {}})
        self.assertEqual([p['key'] for p in per], ['q1', 'q2', 'q3', 'q4', 'jan'])
        self.assertEqual(per[3]['months'], ['2026-10', '2026-11', '2026-12'])
        self.assertEqual(per[3]['status'], 'in progress')   # closed but no actuals in config yet
        self.assertEqual(per[4], {'key': 'jan', 'label': 'Jan to date', 'months': ['2027-01'], 'status': 'open', 'spend_basis': 'estimate'})

    def test_closed_q4_is_paid_when_config_has_actuals(self):
        months = refresh.month_range('2026-01', '2027-01')
        per = refresh.build_periods(months, '2027-01-12', ['2026-07', '2026-08', '2026-09'], {'p': {}}, {'q4': {'p': {}}})
        self.assertEqual(per[3]['status'], 'paid')

    def test_every_month_lands_in_exactly_one_period(self):
        for today in ('2026-05-10', '2026-11-04', '2027-02-01', '2027-04-30'):
            months = refresh.month_range('2026-01', today[:7])
            with mock.patch.object(refresh, 'load_local', return_value={'q3_period_source': 'src'}):
                per = refresh.build_periods(months, today, ['2026-07', '2026-08', '2026-09'], {}, {})
            got = [m for p in per for m in p['months']]
            self.assertEqual(sorted(got), months, today)
            self.assertEqual(len(got), len(set(got)))

    def test_second_year_quarters_carry_the_year(self):
        per = self._new('2027-05-01', {})
        self.assertIn('q1_2027', [p['key'] for p in per])
        self.assertEqual([p['label'] for p in per if p['key'] == 'q1_2027'], ['Q1 2027'])

    def test_month_helpers(self):
        self.assertEqual(refresh.month_range('2026-11', '2027-02'), ['2026-11', '2026-12', '2027-01', '2027-02'])
        self.assertEqual(refresh.month_bounds('2028-02'), ('2028-02-01', '2028-02-29'))


def exp_series(n, start=1000.0, g=1.05):
    return [round(start * g ** i, 2) for i in range(n)]


class Forecast(unittest.TestCase):
    def trend(self, n=28, ser=None):
        months = refresh.month_range('2024-08', refresh._fc_add_months('2024-08', n - 1))
        ser = ser or exp_series(n)
        return {'months': months, 'earned': ser, 'earned_payout': ser}

    def test_exact_growth_series_projects_the_trend(self):
        t = self.trend(28)                                  # first to last month
        f = refresh.compute_forecast(t, [], 25, '2026-11-10', 'now')
        self.assertIsNone(f['warning'])
        self.assertEqual(f['months'], ['2026-12', '2027-01', '2027-02', '2027-03', '2027-04'])
        self.assertEqual(f['fitted_months'][0], '2025-11')
        self.assertEqual(f['fitted_months'][-1], '2026-10')
        self.assertAlmostEqual(f['growth_pct_monthly'], 5.0, places=1)
        last_full = t['earned_payout'][26]                  # 2026-10
        # the first forecast month is two steps past the last full month (the current month is skipped): damp = phi + phi^2
        expect_h1 = last_full * 1.05 ** (0.85 + 0.85 ** 2)
        self.assertAlmostEqual(f['earned']['base'][0] / expect_h1, 1.0, places=2)

    def test_ceiling_is_rate_times_earned_and_payout_gm_never_above_it(self):
        t = self.trend(28)
        stores = [{'scope': 'payout', 'product_by_month': {m: {'alerts': 50.0, 'automation': 30.0, 'prevent': 10.0, 'other': 10.0} for m in ('2026-08', '2026-09', '2026-10')},
                   'gm_by_month': {m: {'alerts_margin': 0.5} for m in ('2026-08', '2026-09', '2026-10')}}]
        f = refresh.compute_forecast(t, stores, 25, '2026-11-10', 'now')
        self.assertEqual(f['shares_used'], {'alerts_share': 0.5, 'alerts_margin': 0.5, 'automation_share': 0.3})
        for b, ce, gm in zip(f['earned']['base'], f['payout_ceiling'], f['payout_gm']):
            self.assertAlmostEqual(ce, round(0.25 * b), delta=1)
            self.assertAlmostEqual(gm, round(0.25 * b * (0.3 + 0.5 * 0.5)), delta=1)
            self.assertLessEqual(gm, ce)

    def test_program_scope_stores_do_not_feed_the_shares(self):
        t = self.trend(28)
        stores = [{'scope': 'program', 'product_by_month': {'2026-10': {'alerts': 100.0}}, 'gm_by_month': {}}]
        f = refresh.compute_forecast(t, stores, 25, '2026-11-10', 'now')
        self.assertEqual(f['shares_used'], {'alerts_share': 0.0, 'alerts_margin': 0.0, 'automation_share': 0.0})

    def test_null_alerts_margin_counts_as_zero_in_the_forecast_shares(self):
        # SUSPECT (money, understates the forecast payout): a null alerts margin is weighted as 0 margin (refresh.py line 672),
        # the same silent zero as apply_gm.
        t = self.trend(28)
        stores = [{'scope': 'payout', 'product_by_month': {'2026-10': {'alerts': 100.0, 'automation': 0.0, 'prevent': 0.0, 'other': 0.0}},
                   'gm_by_month': {'2026-10': {'alerts_margin': None}}}]
        f = refresh.compute_forecast(t, stores, 25, '2026-11-10', 'now')
        self.assertEqual(f['shares_used']['alerts_margin'], 0.0)

    def test_short_history_gives_a_warning_and_no_forecast(self):
        t = self.trend(10)
        f = refresh.compute_forecast(t, [], 25, '2026-06-10', 'now')
        self.assertIn('fewer than 12', f['warning'])
        self.assertNotIn('earned', f)

    def test_non_positive_month_in_window_blocks_the_forecast(self):
        ser = exp_series(28)
        ser[20] = 0.0
        f = refresh.compute_forecast(self.trend(28, ser), [], 25, '2026-11-10', 'now')
        self.assertIn('no forecast', f['warning'])

    def test_run_rate_for_the_current_month(self):
        t = self.trend(28)
        f = refresh.compute_forecast(t, [], 25, '2026-11-10', 'now')
        self.assertEqual(f['run_rate_current_month'], round(t['earned_payout'][27] * 30 / 10))

    def test_backtest_on_steady_growth_series_underforecasts(self):
        f = refresh.compute_forecast(self.trend(28), [], 25, '2026-11-10', 'now')
        bt = f['backtest']
        self.assertEqual(len(bt['per_month']), 5)
        # growth is damped, so a steady 5 percent a month series is always under-forecast
        self.assertTrue(all(r['forecast'] < r['actual'] for r in bt['per_month']))
        self.assertLess(bt['mape_pct'], 10.0)


def cfg_small():
    return {'from_month': '2026-01', 'no_commission_partners': ['partner-nc'], 'no_commission_reason': 'self-referral',
            'portal': '123', 'stores_cut': [
                {'partner': 'partner-a', 'company': 'store-a', 'handle': 'store-a', 'cfid': 'c1', 'hs_id': '1000001', 'lead_id': 'L1',
                 'lead_status': 'approved', 'q3_reference': None, 'added_since_cut': False, 'note': ''},
                {'partner': 'partner-nc', 'company': 'store-b', 'handle': 'store-b', 'cfid': 'c2', 'hs_id': '1000002', 'lead_id': 'L2',
                 'lead_status': 'approved', 'q3_reference': None, 'added_since_cut': False, 'note': ''}],
            'match_rows': [], 'ids_override': {}, 'alias': {}, 'additions_extra': [], 'q1': {}, 'q2': {}, 'q3': {},
            'q3_months': ['2026-07', '2026-08', '2026-09'], 'q1_recorded_paid': None, 'q2_recorded_paid': 0,
            'q2_realized_alerts_margin': 0.5, 'pre_q1': {'payouts': [], 'complete': False}, 'pre_q2': {'payouts': [], 'complete': False},
            'shopify_lifetime': {'1000001': 40.0}, 'shopify_asof': '2026-08-02', 'lifetime_products': {},
            'gm': {'last_month': None, 'ratios': {}, 'carry': {}}, 'alerts_unit_costs': {}, 'alerts_notion_url': 'x',
            'defaults': {'rate': 25, 'spend_basis': 'gm'}, 'limits': ['l'], 'limits_q3_final': ['l'], 'limits_alltime': ['a {asof}'],
            'limit_program': '{n} unlinked'}


class BuildSmall(unittest.TestCase):
    def assertSub(self, got, want):
        self.assertEqual({k: got.get(k) for k in want}, want)

    def build(self, pull, life, today='2026-11-10', leads=None, cfg=None):
        return refresh.build(cfg or cfg_small(), leads or [], pull, life, today)

    def test_by_month_periods_and_lifetime(self):
        pull = {('2026-02', '1000001'): 100.0, ('2026-08', '1000001'): 50.0, ('2026-10', '1000001'): 25.0, ('2026-11', '1000002'): 10.0}
        life = {'1000001': {'sum': 500.0, 'first': '2025-01-01T00:00:00Z'}}
        data, info = self.build(pull, life)
        a = next(s for s in data['stores'] if s['hs_id'] == '1000001')
        self.assertEqual(a['by_month']['2026-02'], 100.0)
        self.assertEqual(a['by_month']['2026-03'], 0.0)
        self.assertSub(a['lifetime'], {'stripe': 500.0, 'shopify': 40.0, 'total': 540.0, 'first_paid_invoice': '2025-01-01T00:00:00Z'})
        keys = {p['key']: p for p in data['periods']}
        self.assertEqual(keys['q1']['months'], ['2026-01', '2026-02', '2026-03'])
        self.assertEqual(keys['oct']['months'], ['2026-10', '2026-11'])
        total = sum(s['by_month'][m] for s in data['stores'] for p in data['periods'] for m in p['months'])
        self.assertEqual(total, 185.0)
        self.assertEqual(data['window']['to'], '2026-11-10')

    def test_no_commission_partner_flag_on_store(self):
        data, _ = self.build({('2026-02', '1000002'): 5.0}, {})
        b = next(s for s in data['stores'] if s['hs_id'] == '1000002')
        self.assertTrue(b['no_commission'])
        self.assertEqual(b['no_commission_reason'], 'self-referral')

    def test_revenue_without_lifetime_result_falls_to_window_floor(self):
        data, _ = self.build({('2026-02', '1000001'): 100.0, ('2026-03', '1000001'): 20.0}, {})
        a = next(s for s in data['stores'] if s['hs_id'] == '1000001')
        self.assertSub(a['lifetime'], {'stripe': 120.0, 'shopify': None, 'total': 120.0, 'first_paid_invoice': None})

    def test_paid_invoice_less_store_with_lifetime_entry_reads_zero(self):
        data, _ = self.build({}, {'1000001': {'sum': None, 'first': None}})
        a = next(s for s in data['stores'] if s['hs_id'] == '1000001')
        self.assertEqual(a['lifetime']['stripe'], 0.0)

    def test_addition_without_revenue_in_window_is_not_kept_as_payout_store(self):
        leads = [{'lead_id': 'L9', 'status': 'approved', 'hs_id': '1000009', 'company': 'store-new', 'partner': 'partner-a'}]
        data, info = self.build({}, {}, leads=leads)
        self.assertEqual(info['adds_considered'], 1)
        self.assertEqual(info['adds_kept'], 0)
        data2, info2 = self.build({('2026-05', '1000009'): 12.0}, {}, leads=leads)
        self.assertEqual(info2['adds_kept'], 1)
        self.assertEqual(next(s for s in data2['stores'] if s['hs_id'] == '1000009')['scope'], 'payout')

    def test_program_store_has_lifetime_but_no_months(self):
        leads = [{'lead_id': 'L8', 'status': 'approved', 'hs_id': '1000008', 'company': 'store-p', 'partner': 'partner-z'}]
        data, _ = self.build({('2026-02', '1000001'): 1.0}, {'1000008': {'sum': 75.0, 'first': None}, '1000001': {'sum': 1.0, 'first': None}}, leads=leads)
        p = next(s for s in data['stores'] if s['hs_id'] == '1000008')
        self.assertEqual((p['scope'], p['by_month'], p['lifetime']['total']), ('program', {}, 75.0))


if __name__ == '__main__':
    unittest.main()
