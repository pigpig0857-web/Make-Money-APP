"""Real-data parsing, comparable cohorts, and non-backdated score outcomes."""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd
import requests

from backend.analytics.score_tracking import completed_prices, measure_snapshots, relative_return
from backend.services.revenue_fetcher import parse_revenues, get_revenue_context
from backend.services.market_context import parse_tpex_index
from backend.services.research_repository import save_outcomes


def revenue_payload():
    return {'出表日期': '1150917', '資料年月': '11508', '公司代號': '2330',
            '公司名稱': '台積電', '產業別': '半導體業',
            '營業收入-當月營收': '1,000', '營業收入-上月營收': '800',
            '營業收入-去年當月營收': '500', '營業收入-去年同月增減(%)': '100',
            '營業收入-上月比較增減(%)': '25', '累計營業收入-前期比較增減(%)': '-'}


def prices(days=65):
    frame = pd.DataFrame({'Date': pd.bdate_range('2026-06-01', periods=days),
                          'Open': [100.0] * days, 'Close': [110.0] * days})
    frame.attrs['price_source'] = 'yfinance'
    return frame


def snapshot():
    return {'id': 1, 'price_date': date(2026, 5, 1), 'score': 45, 'score_max': 60,
            'model_version': 'technical-real-v1',
            'recorded_at': datetime(2026, 6, 1, 8, tzinfo=timezone.utc)}


class RevenueTests(unittest.TestCase):
    def test_official_month_unit_and_missing_percent(self):
        for source, suffix in [('TWSE', '.TW'), ('TPEx', '.TWO')]:
            row = parse_revenues([revenue_payload()], source)[0]
            self.assertEqual(row['ticker'], '2330' + suffix)
            self.assertEqual(row['revenue_month'], date(2026, 8, 1))
            self.assertEqual(row['report_date'], date(2026, 9, 17))
            self.assertEqual(row['revenue_thousand'], 1000)
            self.assertIsNone(row['cumulative_yoy_pct'])

    def test_bad_fields_missing_amount_and_nonfinite_are_rejected(self):
        for field, value in [('營業收入-當月營收', '--'), ('資料年月', '11513'),
                             ('營業收入-去年同月增減(%)', 'NaN')]:
            payload = revenue_payload()
            payload[field] = value
            with self.assertRaises(ValueError):
                parse_revenues([payload], 'TWSE')
        with self.assertRaises(ValueError):
            parse_revenues([{'wrong': 'fields'}], 'TWSE')

    def test_peer_comparison_excludes_target_different_month_and_industry(self):
        rows = parse_revenues([revenue_payload()], 'TWSE')
        for code, yoy in [('2301.TW', 10), ('2302.TW', 20), ('2303.TW', 30)]:
            rows.append(dict(rows[0], ticker=code, yoy_pct=yoy))
        rows.append(dict(rows[0], ticker='1101.TW', industry='水泥工業', yoy_pct=999))
        rows.append(dict(rows[0], ticker='2304.TW', revenue_month=date(2026, 7, 1), yoy_pct=999))
        with patch('backend.services.revenue_fetcher.fetch_revenue_table', return_value=(rows, datetime.now(timezone.utc))), patch('backend.services.revenue_fetcher.save_revenues'):
            result = get_revenue_context('2330.TW')
        self.assertEqual(result['peer_count'], 3)
        self.assertEqual(result['peer_median_yoy'], 20)

    def test_offline_read_is_labelled_and_not_fabricated(self):
        rows = parse_revenues([revenue_payload()], 'TWSE')
        with patch('backend.services.revenue_fetcher.fetch_revenue_table', side_effect=requests.Timeout), patch('backend.services.revenue_fetcher.load_revenues', return_value=rows):
            result = get_revenue_context('2330.TW')
        self.assertTrue(result['offline'])
        self.assertIsNone(result['peer_median_yoy'])


class ScoreTrackingTests(unittest.TestCase):
    def test_starts_after_real_recording_not_old_price_date(self):
        rows = measure_snapshots([snapshot()], prices())
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row['entry_date'], date(2026, 6, 2))
            self.assertAlmostEqual(row['return_pct'], 10)
        self.assertEqual(rows[0]['end_date'], date(2026, 6, 8))

    def test_timezone_uses_taiwan_day_and_waits_for_full_horizon(self):
        record = snapshot()
        record['recorded_at'] = datetime(2026, 6, 1, 17, tzinfo=timezone.utc)  # June 2 TW
        rows = measure_snapshots([record], prices(6))
        self.assertTrue(all(row['status'] == 'pending' for row in rows))
        self.assertEqual(rows[0]['observed_days'], 4)
        self.assertNotIn('return_pct', rows[0])

    def test_truncated_history_does_not_shift_entry(self):
        rows = measure_snapshots([snapshot()], prices().iloc[10:])
        self.assertTrue(all(row['status'] == 'insufficient_history' for row in rows))

    def test_completed_measurements_ignore_later_prices(self):
        original = prices()
        changed = original.copy()
        changed.loc[changed.index[6:], ['Open', 'Close']] = 5000
        a = measure_snapshots([snapshot()], original)[0]
        b = measure_snapshots([snapshot()], changed)[0]
        self.assertEqual(a['return_pct'], b['return_pct'])
        self.assertEqual(a['input_hash'], b['input_hash'])

    def test_partial_today_excluded_and_future_bars_excluded(self):
        frame = prices()
        now = datetime(2026, 6, 2, 4, tzinfo=timezone.utc)  # noon TW
        self.assertEqual(len(completed_prices(frame, now)), 1)
        now = datetime(2026, 6, 2, 6, tzinfo=timezone.utc)  # after close
        self.assertEqual(len(completed_prices(frame, now)), 2)

    def test_relative_return_requires_identical_endpoint_dates(self):
        own, market = prices(25), prices(25)
        market['Close'] = 100
        result = relative_return(own, market)
        self.assertAlmostEqual(result['excess_pp'], 0)
        with self.assertRaises(ValueError):
            relative_return(own, market.iloc[:-1])

    def test_unverified_prices_and_naive_recording_time_rejected(self):
        data = prices()
        data.attrs['price_source'] = 'mock'
        with self.assertRaises(ValueError):
            measure_snapshots([snapshot()], data)
        record = snapshot()
        record['recorded_at'] = datetime(2026, 6, 1)
        with self.assertRaises(ValueError):
            measure_snapshots([record], prices())

    def test_only_matured_outcomes_are_written(self):
        rows = measure_snapshots([snapshot()], prices(7))
        with patch('backend.services.research_repository.database_connection') as connect:
            save_outcomes(rows)
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            statement, parameters = cursor.executemany.call_args.args
        self.assertIn('ON CONFLICT', statement)
        self.assertEqual(len(parameters), 1)
        self.assertEqual(parameters[0][:2], (1, 5))
        with patch('backend.services.research_repository.database_connection') as connect:
            save_outcomes(measure_snapshots([snapshot()], prices(2)))
            connect.assert_not_called()


class OfficialIndexTests(unittest.TestCase):
    def test_monthly_index_does_not_fabricate_ohlc(self):
        payload = {'stat': 'ok', 'date': '20260901', 'tables': [{
            'fields': ['日期', '成交張數', '金額（仟元）', '筆數', '櫃買指數', '漲/跌'],
            'data': [['115/09/01', '1', '1', '1', '410.77', '1']]}]}
        rows = parse_tpex_index(payload, date(2026, 9, 1))
        self.assertEqual(rows, [(date(2026, 9, 1), 410.77)])
        with self.assertRaises(ValueError):
            parse_tpex_index(payload, date(2026, 8, 1))
        payload['tables'][0]['data'][0][4] = '--'
        with self.assertRaises(ValueError):
            parse_tpex_index(payload, date(2026, 9, 1))

    def test_close_only_index_comparison_cannot_be_used_as_score_outcome(self):
        own = prices(25)
        market = own[['Date', 'Close']].copy()
        market.attrs['price_source'] = 'TPEx'
        self.assertAlmostEqual(relative_return(own, market)['excess_pp'], 0)
        with self.assertRaises(ValueError):
            measure_snapshots([snapshot()], market)


class ResearchPanelTests(unittest.TestCase):
    def test_first_submit_survives_interrupted_context_load(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        interrupted = False

        def load_context(_):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                st.rerun()
            return {'available': False, 'reason': '尚無營收'}

        app = AppTest.from_string('''
import pandas as pd
from frontend.components.research_panel import render_research_panel
frame = pd.DataFrame({'Date': pd.bdate_range('2026-06-01', periods=25), 'Open': 100., 'Close': 100.})
frame.attrs['price_source'] = 'yfinance'
render_research_panel('2330.TW', frame)
''')
        with patch('frontend.components.research_panel.get_revenue_context', side_effect=load_context), patch('frontend.components.research_panel._download_daily', return_value=(prices(25), 'test verified fixture')):
            app.run()
            app.button(key='research_context_2330.TW').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertNotIn('pending_research_2330.TW_context', app.session_state)
        self.assertIn('research_context_result_2330.TW', app.session_state)

    def test_new_score_waiting_is_rendered_without_zero_return(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
import pandas as pd
from frontend.components.research_panel import render_research_panel
frame = pd.DataFrame({'Date': pd.bdate_range('2026-06-01', periods=25), 'Open': 100., 'Close': 100.})
frame.attrs['price_source'] = 'yfinance'
render_research_panel('2330.TW', frame)
''')
        with patch('frontend.components.research_panel.load_tracking_snapshots', return_value=[snapshot()]), patch('frontend.components.research_panel._download_daily', return_value=(prices(2), 'test verified fixture')), patch('frontend.components.research_panel.save_outcomes'):
            app.run()
            app.button(key='research_outcomes_2330.TW').click().run()
        self.assertEqual(len(app.exception), 0)
        table = app.dataframe[0].value
        self.assertTrue(table['報酬（%）'].isna().all())
        self.assertTrue(table['狀態'].str.contains('等待行情').all())


if __name__ == '__main__':
    unittest.main()
