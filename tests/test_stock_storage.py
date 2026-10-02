"""Verify cache/fallback behavior without a network or database dependency."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from backend.services.stock_fetcher import _download_daily, StockDataUnavailableError
from backend.services.stock_repository import save_daily_prices
from market_fixtures import market_frame


class StockStorageTests(unittest.TestCase):
    def setUp(self):
        self.frame = market_frame("2330.TW", days=90)
        self.refreshed = datetime.now(timezone.utc)
        self.raw = self.frame.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]

    def test_fresh_database_prices_skip_network(self):
        with patch("backend.services.stock_fetcher.load_daily_prices", return_value=(self.frame, self.refreshed)), patch("yfinance.download") as download:
            frame, source = _download_daily("2330.TW", "5y")
        download.assert_not_called()
        self.assertIs(frame, self.frame)
        self.assertIn("PostgreSQL", source)

    def test_real_download_is_saved_with_requested_history(self):
        with patch("backend.services.stock_fetcher.load_daily_prices", return_value=None), patch("yfinance.download", return_value=self.raw), patch("backend.services.stock_fetcher.save_daily_prices") as save:
            frame, source = _download_daily("2330.TW", "2y")
        self.assertEqual(len(frame), 90)
        self.assertEqual(save.call_args.args[:2], ("2330.TW", "2y"))
        self.assertEqual(save.call_args.kwargs["source"], "yfinance")
        self.assertIn("已存入", source)

    def test_failed_network_reads_labelled_stale_history(self):
        with patch("backend.services.stock_fetcher.load_daily_prices", side_effect=[None, (self.frame, self.refreshed)]) as load, patch("yfinance.download", side_effect=RuntimeError("offline")), patch("backend.services.stock_fetcher.save_daily_prices") as save:
            frame, source = _download_daily("2330.TW")
        self.assertIs(frame, self.frame)
        self.assertIn("非最新行情", source)
        self.assertIsNone(load.call_args.kwargs["max_age_seconds"])
        save.assert_not_called()

    def test_mock_prices_are_never_saved(self):
        with patch("backend.services.stock_fetcher.load_daily_prices", return_value=None), patch("yfinance.download", return_value=pd.DataFrame()), patch("backend.services.stock_fetcher.save_daily_prices") as save:
            with self.assertRaises(StockDataUnavailableError):
                _download_daily("2330.TW")
        save.assert_not_called()
        with self.assertRaises(ValueError):
            save_daily_prices("2330.TW", "1y", self.frame, source="mock")

    def test_database_outage_keeps_real_analysis_available(self):
        with patch("backend.services.stock_fetcher.load_daily_prices", side_effect=RuntimeError("unavailable")), patch("yfinance.download", return_value=self.raw), patch("backend.services.stock_fetcher.save_daily_prices", side_effect=RuntimeError("unavailable")):
            frame, source = _download_daily("2330.TW")
        self.assertEqual(len(frame), 90)
        self.assertIn("儲存失敗", source)

    def test_incomplete_download_is_not_saved(self):
        with patch("backend.services.stock_fetcher.load_daily_prices", return_value=None), patch("yfinance.download", return_value=self.raw.iloc[:10]), patch("backend.services.stock_fetcher.save_daily_prices") as save:
            with self.assertRaises(StockDataUnavailableError):
                _download_daily("2330.TW")
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
