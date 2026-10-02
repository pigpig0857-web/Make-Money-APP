"""Catch-up date gaps, bounded retries and resumable UI batches."""

import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

from backend.services.backfill import new_job, run_step
from market_fixtures import market_frame


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.frame = market_frame('2330.TW', days=140)
        self.frame.attrs.update(price_source='yfinance', is_stale=False)
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.stored = {}
        self.download = self.stack.enter_context(patch('backend.services.backfill._download_daily', return_value=(self.frame, 'verified test fixture')))
        self.save_prices = self.stack.enter_context(patch('backend.services.backfill.save_daily_prices', return_value=len(self.frame)))
        self.stack.enter_context(patch('backend.services.backfill.load_daily_prices', return_value=(self.frame, self.now)))
        self.stack.enter_context(patch('backend.services.backfill.load_tracking_snapshots', return_value=[]))
        self.stack.enter_context(patch('backend.services.backfill.save_outcomes'))
        self.stack.enter_context(patch('backend.services.backfill.load_institutional_flows', side_effect=lambda ticker, dates: [self.stored[d] for d in dates if d in self.stored]))
        self.stack.enter_context(patch('backend.services.backfill.save_institutional_flows', side_effect=lambda ticker, rows: self.stored.update({r['trade_date']: r for r in rows})))
        self.fetch = self.stack.enter_context(patch('backend.services.backfill.fetch_daily_report', side_effect=self.report))

    def report(self, market, day):
        return {'2330': {'trade_date': day, 'source': market, 'foreign_net': 1,
                         'trust_net': 0, 'dealer_net': 0, 'total_net': 1}}

    def finish(self, job):
        for _ in range(300):
            if job['done']:
                return job['results']
            run_step(job, self.now)
        self.fail('Batch did not finish')

    def test_fills_interior_gap_and_second_run_skips_stored_dates(self):
        dates = [d.date() for d in self.frame['Date'].tail(20)]
        self.stored = {d: self.report('TWSE', d)['2330'] for d in dates if d != dates[7]}
        results = self.finish(new_job(['2330.TW'], 20))
        self.fetch.assert_called_once_with('TWSE', dates[7])
        self.assertEqual(results[0]['status'], 'complete')
        self.assertEqual(results[0]['missing_dates'], [])
        self.download.assert_called_with('2330.TW', '2y', force_refresh=True)
        self.fetch.reset_mock()
        self.finish(new_job(['2330.TW'], 20))
        self.fetch.assert_not_called()

    def test_network_failure_is_partial_and_next_batch_can_retry(self):
        self.fetch.side_effect = RuntimeError('offline')
        result = self.finish(new_job(['2330.TW'], 20))[0]
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['missing_dates']), 20)
        self.fetch.assert_called_once()
        self.fetch.side_effect = self.report
        self.assertEqual(self.finish(new_job(['2330.TW'], 20))[0]['status'], 'complete')

    def test_empty_report_is_not_stored_as_zero(self):
        self.fetch.return_value = {}
        self.fetch.side_effect = None
        result = self.finish(new_job(['2330.TW'], 20))[0]
        self.assertEqual(result['institution_rows'], 0)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.stored, {})

    def test_budget_partial_then_continues_without_duplicates(self):
        job = new_job(['2330.TW'], 120)
        result = self.finish(job)[0]
        self.assertEqual(self.fetch.call_count, 60)
        self.assertEqual(len(result['missing_dates']), 60)
        self.assertEqual(result['status'], 'partial')
        self.fetch.reset_mock()
        result = self.finish(new_job(['2330.TW'], 120))[0]
        self.assertEqual(self.fetch.call_count, 60)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.stored), 120)

    def test_stale_prices_not_declared_updated(self):
        self.frame.attrs['is_stale'] = True
        result = self.finish(new_job(['2330.TW'], 20))[0]
        self.assertEqual(result['status'], 'failed')
        self.save_prices.assert_not_called()
        self.fetch.assert_not_called()

    def test_failed_stock_does_not_block_next_stock(self):
        self.download.side_effect = [RuntimeError('offline'), (self.frame, 'verified')]
        results = self.finish(new_job(['2308.TW', '2330.TW'], 20))
        self.assertEqual([r['status'] for r in results], ['failed', 'complete'])

    def test_resuming_after_committed_price_step(self):
        job = new_job(['2330.TW'], 20)
        run_step(job, self.now)
        self.assertEqual(job['stage'], 'institutions')
        self.finish(job)
        self.download.assert_called_once()

    def test_old_download_signature_is_actionable(self):
        self.download.side_effect = TypeError("_download_daily() got an unexpected keyword argument 'force_refresh'")
        result = self.finish(new_job(['2330.TW'], 20))[0]
        self.assertEqual(result['error_stage'], '行情下載')
        self.assertEqual(result['error_type'], 'TypeError')
        self.assertIn('重新啟動 Streamlit', result['notes'][0])

    def test_storage_failure_does_not_leak_exception_details(self):
        self.save_prices.side_effect = RuntimeError('password=do-not-display')
        result = self.finish(new_job(['2330.TW'], 20))[0]
        self.assertEqual(result['error_stage'], '日 K 資料庫寫入')
        self.assertEqual(result['error_type'], 'RuntimeError')
        self.assertNotIn('do-not-display', str(result))


class WatchlistUITests(unittest.TestCase):
    def test_first_click_runs_all_steps_and_keeps_results(self):
        from streamlit.testing.v1 import AppTest
        def step(job):
            if job['stage'] == 'prices':
                job['stage'] = 'outcomes'
            else:
                job['done'] = True
                job['results'] = [{'ticker': '2330.TW', 'status': 'complete', 'notes': []}]
        with patch('frontend.components.watchlist_panel.list_watches', return_value=[('2330.TW', '台積電')]), patch('frontend.components.watchlist_panel.run_step', side_effect=step) as worker:
            app = AppTest.from_string('''
from frontend.components.watchlist_panel import render_watchlist_panel
render_watchlist_panel()
''').run()
            app.button(key='start_backfill').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(worker.call_count, 2)
        self.assertTrue(app.session_state['backfill_job']['done'])
        self.assertEqual(len(app.dataframe), 1)


if __name__ == '__main__':
    unittest.main()
