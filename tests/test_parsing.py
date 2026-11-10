"""Characterization: parse_rows, read_monthly, read_lifetime, read_company_dates (refresh.py). SUSPECT tags mark behaviour
that looks wrong for money; the test asserts what the code does today and an expectedFailure twin asserts what it should do."""
import os
import tempfile
import unittest

from tests._path import COMMA, ID
import refresh


def parse(text):
    return refresh.parse_rows(text)


class ParseRows(unittest.TestCase):
    def test_tab_row_with_month_is_series(self):
        alltime, series, trunc = parse('2026-03-01\t100.50\t12345678901\n')
        self.assertEqual(alltime, [])
        self.assertEqual(series, [('2026-03-01 100.50 12345678901', '2026-03')])
        self.assertIsNone(trunc)

    def test_row_without_month_is_alltime(self):
        alltime, series, _ = parse(ID + '\t250.25\n')
        self.assertEqual(alltime, [('250.25 ' + ID, None)])
        self.assertEqual(series, [])

    def test_column_order_does_not_matter(self):
        _, a, _ = parse('2026-03-01\t100.5\t12345678901\n')
        _, b, _ = parse('12345678901\t2026-03-01\t100.5\n')
        self.assertEqual(a, b)

    def test_csv_and_double_space_separators(self):
        _, a, _ = parse('12345678901,2026-04-01,10.5\n')
        _, b, _ = parse('  '.join([ID, '2026-04-01', '10.5']) + '\n')
        self.assertEqual(a, b)
        self.assertEqual(a[0][1], '2026-04')

    def test_header_comment_blank_and_short_rows_skipped(self):
        text = 'hs_object_id\tmonth\tsum\n# note\n\n12345678901\nShowing 3 of 3\n'
        self.assertEqual(parse(text)[:2], ([], []))

    def test_id_shorter_than_six_digits_dropped(self):
        self.assertEqual(parse('2026-03-01\t10.5\t12345\n')[:2], ([], []))

    def test_negative_amount_kept(self):
        _, series, _ = parse('2026-03-01\t-40.00\t12345678901\n')
        self.assertEqual(series[0][0], '2026-03-01 -40.00 12345678901')

    def test_truncation_marker_detected(self):
        _, _, trunc = parse('Showing 500 of 640\n')
        self.assertEqual((trunc.group(1), trunc.group(2)), ('500', '640'))

    def test_thousands_comma_amount_is_split_and_wrong(self):
        # SUSPECT (money, understates): the parser splits on commas, so an amount written with a thousands comma becomes two
        # cells; the first numeric cell after the id is taken as the amount. a grouped 1250.50 is read as 1.
        amount = '1' + COMMA + '250.50'
        _, series, _ = parse(f'2026-03-01\t{amount}\t12345678901\n')
        self.assertEqual(series, [('2026-03-01 1 12345678901', '2026-03')])

    @unittest.expectedFailure
    def test_thousands_comma_amount_should_be_read_whole(self):
        amount = '1' + COMMA + '250.50'
        _, series, _ = parse(f'2026-03-01\t{amount}\t12345678901\n')
        self.assertEqual(series, [('2026-03-01 1250.50 12345678901', '2026-03')])

    def test_longest_integer_wins_even_when_it_is_not_the_id(self):
        # SUSPECT (money, misattributes): the id is the numeric cell with the longest integer part. A count or other id column
        # longer than the company id takes its place, and the revenue lands on a different company.
        _, series, _ = parse('2026-03-01\t100.50\t12345678901\t999999999999999\n')
        self.assertEqual(series[0][0], '2026-03-01 100.50 999999999999999')   # wrong id, real id is 12345678901

    @unittest.expectedFailure
    def test_longest_integer_should_not_replace_the_company_id(self):
        _, series, _ = parse('2026-03-01\t100.50\t12345678901\t999999999999999\n')
        self.assertEqual(series[0][0], '2026-03-01 100.50 12345678901')

    def test_equal_length_prefers_integer_over_decimal(self):
        # tie on integer-part length: the integer-looking cell is the id (key sorts '.' not in c higher)
        _, series, _ = parse('\t'.join(['2026-03-01', ID + '.50', ID]) + '\n')
        self.assertEqual(series[0][0], '2026-03-01 ' + ID + '.50 ' + ID)

    def test_only_first_extra_number_is_the_amount(self):
        # a third numeric cell (for example a count) is ignored silently; the first one is the amount
        _, series, _ = parse('2026-03-01\t100.50\t7\t12345678901\n')
        self.assertEqual(series[0][0], '2026-03-01 100.50 12345678901')


class ReadMonthly(unittest.TestCase):
    def _write(self, d, name, text):
        p = os.path.join(d, name)
        open(p, 'w').write(text)
        return p

    def test_reads_month_and_company(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, 'm1.txt', '2026-03-01\t100.50\t12345678901\n2026-04-01\t20\t12345678901\n')
            data, warns = refresh.read_monthly([p])
        self.assertEqual(data, {('2026-03', '12345678901'): 100.5, ('2026-04', '12345678901'): 20.0})
        self.assertEqual(warns, [])

    def test_truncation_warning_only_when_total_exceeds_shown(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, 'm1.txt', 'Showing 500 of 700\n2026-03-01\t1\t12345678901\n')
            q = self._write(d, 'm2.txt', 'Showing 500 of 500\n2026-03-01\t1\t12345678902\n')
            _, w1 = refresh.read_monthly([p])
            _, w2 = refresh.read_monthly([q])
        self.assertEqual(len(w1), 1)
        self.assertIn('TRUNCATED', w1[0])
        self.assertEqual(w2, [])

    def test_duplicate_month_and_company_last_row_wins_not_summed(self):
        # SUSPECT (money, understates): two rows for the same company and month (a batch split that overlaps, or a stray bucket)
        # overwrite each other instead of adding up.
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, 'm1.txt', '2026-03-01\t100\t12345678901\n2026-03-01\t50\t12345678901\n')
            data, _ = refresh.read_monthly([p])
        self.assertEqual(data[('2026-03', '12345678901')], 50.0)

    @unittest.expectedFailure
    def test_duplicate_month_and_company_should_not_silently_lose_a_row(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, 'm1.txt', '2026-03-01\t100\t12345678901\n2026-03-01\t50\t12345678901\n')
            data, _ = refresh.read_monthly([p])
        self.assertEqual(data[('2026-03', '12345678901')], 150.0)

    def test_money_strips_symbols(self):
        self.assertEqual(refresh.money('12.50'), 12.5)
        self.assertEqual(refresh.money(None), 0.0)
        self.assertEqual(refresh.money('abc'), 0.0)
        self.assertEqual(refresh.money('-3.25'), -3.25)


class ReadLifetime(unittest.TestCase):
    def _read(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'l1.txt')
            open(p, 'w').write(text)
            return refresh.read_lifetime([p])

    def test_sum_and_min_rows_merge_in_any_order(self):
        sum_row = '12345678901\t500.25\n'
        min_row = '12345678901\t2025-02-03T10:00:00.000Z\n'
        a, _ = self._read(sum_row + min_row)
        b, _ = self._read(min_row + sum_row)
        self.assertEqual(a, b)
        self.assertEqual(a['12345678901'], {'sum': 500.25, 'first': '2025-02-03T10:00:00.000Z'})

    def test_earliest_timestamp_kept(self):
        out, _ = self._read('12345678901\t2025-05-01T00:00:00Z\n12345678901\t2025-01-01T00:00:00Z\n')
        self.assertEqual(out['12345678901']['first'], '2025-01-01T00:00:00Z')
        self.assertIsNone(out['12345678901']['sum'])

    def test_company_with_only_a_min_row_has_no_sum(self):
        out, _ = self._read('12345678901\t2025-05-01T00:00:00Z\n')
        self.assertIsNone(out['12345678901']['sum'])

    def test_truncation_warning(self):
        _, warns = self._read('Showing 500 of 900\n12345678901\t5\n')
        self.assertEqual(len(warns), 1)

    def test_short_id_and_single_number_rows_ignored(self):
        out, _ = self._read('12345\t5\n12345678901\n')
        self.assertEqual(out, {})

    def test_thousands_comma_sum_is_split_and_wrong(self):
        # SUSPECT (money, understates all time): same comma split as parse_rows; a grouped 1250.50 is read as 1.
        out, _ = self._read('12345678901\t' + '1' + ',' + '250.50\n')
        self.assertEqual(out['12345678901']['sum'], 1.0)

    @unittest.expectedFailure
    def test_thousands_comma_sum_should_be_read_whole(self):
        out, _ = self._read('12345678901\t' + '1' + ',' + '250.50\n')
        self.assertEqual(out['12345678901']['sum'], 1250.5)

    def test_duplicate_sum_rows_last_wins(self):
        out, _ = self._read('12345678901\t10\n12345678901\t30\n')
        self.assertEqual(out['12345678901']['sum'], 30.0)


class ReadCompanyDates(unittest.TestCase):
    def test_tab_rows_and_json_records(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c1.txt')
            open(p, 'w').write('12345678901\t2025-03-04T10:00:00Z\n'
                               '{"objectTypeId":"0-2","properties":{"hs_object_id":"12345678902","createdate_iso":"2025-06-07T01:02:03Z"}}\n')
            out = refresh.read_company_dates([p])
        self.assertEqual(out, {'12345678901': '2025-03-04', '12345678902': '2025-06-07'})


if __name__ == '__main__':
    unittest.main()
