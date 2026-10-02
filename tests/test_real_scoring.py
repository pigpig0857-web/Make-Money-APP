"""Scoring must reject missing provenance and never infer unavailable chips."""

import unittest
from unittest.mock import patch, MagicMock

from frontend.components.ai_diagnosis import calculate_precise_ai_score
from backend.services.stock_fetcher import get_chip_data
from market_fixtures import market_frame
from contextlib import ExitStack
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from streamlit.testing.v1 import AppTest


class RealScoringTests(unittest.TestCase):
    def setUp(self):
        self.frame = market_frame(days=180)
        self.frame.attrs.update(price_source="yfinance", is_stale=False)

    def test_no_default_chip_bonus_or_chip_claims(self):
        empty = calculate_precise_ai_score(self.frame, {}, {}, debug_log=False)
        invented = calculate_precise_ai_score(self.frame, {
            "foreign": 100, "it": 100, "large_holder": 90,
            "main_diff": 100, "large_delta": 20,
        }, {}, debug_log=False)
        self.assertEqual(empty["total_score"], invented["total_score"])
        self.assertEqual(empty["score_max"], 60)
        self.assertIsNone(empty["score_breakdown"]["chip_20d"])
        self.assertIsNone(empty["chip_turn"])
        self.assertNotIn("籌碼面皆", empty["note_text"])
        self.assertFalse(get_chip_data("2330.TW")["available"])

    def test_unverified_and_stale_prices_do_not_score(self):
        for attrs in ({}, {"price_source": "mock"}, {"price_source": "yfinance", "is_stale": True}):
            self.frame.attrs = attrs
            with self.assertRaises(ValueError):
                calculate_precise_ai_score(self.frame, {}, {}, debug_log=False)

    def test_short_or_invalid_history_does_not_score(self):
        with self.assertRaises(ValueError):
            calculate_precise_ai_score(self.frame.iloc[:60], {}, {}, debug_log=False)
        self.frame.loc[0, "Close"] = float("nan")
        with self.assertRaises(ValueError):
            calculate_precise_ai_score(self.frame, {}, {}, debug_log=False)

    def test_rising_only_prices_have_rsi_100(self):
        import pandas as pd
        from frontend.components.ai_diagnosis import compute_rsi
        self.assertEqual(compute_rsi(pd.Series(range(1, 50))).iloc[-1], 100)

    def test_fundamental_has_no_static_risk_defaults(self):
        from backend.services.stock_master import get_fundamental
        get_fundamental.clear()
        ticker = MagicMock()
        ticker.info = {"trailingEps": 12.5, "beta": 1.3}
        with patch("yfinance.Ticker", return_value=ticker):
            fund = get_fundamental("2330.TW")
        self.assertEqual(fund["trailing_eps"], 12.5)
        self.assertEqual(fund["beta"], 1.3)
        for key in ("cycle", "inventory", "capex", "shortage"):
            self.assertIsNone(fund[key])
        get_fundamental.clear()


class RealDashboardTests(unittest.TestCase):
    def run_dashboard(self, stale=False, unavailable=False, chip=None):
        from backend.services.stock_fetcher import StockDataUnavailableError, resample_kline
        from frontend.components.kline_chart import _build_kline_figure
        frame = market_frame(days=180)
        frame.attrs.update(price_source="yfinance", is_stale=stale)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target, value in [
            ("frontend.components.sidebar.get_daily_trending_stocks", [("台積電", "2330.TW")]),
            ("frontend.main_view.get_daily_trending_stocks", [("台積電", "2330.TW")]),
            ("frontend.main_view.validate_stock_input", (True, "2330.TW", "台積電")),
            ("frontend.main_view.lookup_stock_name", "台積電"),
            ("frontend.main_view.get_market_status", {"is_open": False, "now": datetime.now(ZoneInfo("Asia/Taipei"))}),
            ("frontend.main_view.get_fundamental", {"industry": "半導體業"}),
            ("frontend.components.ai_diagnosis.lookup_stock_name", "台積電"),
            ("frontend.components.ai_diagnosis._debug_print", None),
            ("frontend.main_view.save_score_snapshot", False),
            ("frontend.main_view.get_chip_data", chip or {"available": False, "reason": "尚未接入真實測試法人資料"}),
        ]:
            stack.enter_context(patch(target, return_value=value))
        stack.enter_context(patch("frontend.main_view.fetch_stock_data",
                                  side_effect=StockDataUnavailableError("無真實行情") if unavailable else None,
                                  return_value=(frame, "測試真實資料流程")))
        stack.enter_context(patch("frontend.main_view.get_kline_chart",
                                  side_effect=lambda ticker, period: _build_kline_figure(resample_kline(frame, period), period)))
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        app.text_input(key="stock_search_input").set_value("2330")
        next(button for button in app.button if "開始 AI 診斷" in button.label).click().run()
        self.assertEqual(len(app.exception), 0)
        return app

    def test_real_dashboard_renders_60_point_score_and_missing_chips(self):
        app = self.run_dashboard()
        markup = "\n".join(element.value for element in app.markdown)
        self.assertIn("/ 60（不是勝率）", markup)
        self.assertNotIn("大戶鎖碼", markup)
        self.assertTrue(any("尚未接入真實" in element.value for element in app.info))

    def test_stale_history_has_no_score_or_strategy(self):
        app = self.run_dashboard(stale=True)
        self.assertTrue(any("未過期" in element.value for element in app.warning))
        self.assertFalse(any("價位策略" in element.value for element in app.subheader))
        self.assertFalse(any("/ 60（不是勝率）" in element.value for element in app.markdown))

    def test_no_prices_stops_without_mock_dashboard(self):
        app = self.run_dashboard(unavailable=True)
        self.assertEqual(app.error[0].value, "無真實行情")
        self.assertEqual(len(app.radio), 0)

    def test_institutional_panel_labels_partial_history_in_shares(self):
        from datetime import date
        day = date(2026, 9, 30)
        chip = {"available": True, "source": "TWSE", "latest_date": day,
                "expected_days": 20, "complete": False, "stored": True,
                "reason": "法人作輔助，未納入技術評分。", "rows": [
                    {"trade_date": day, "foreign_net": 1000, "trust_net": -200,
                     "dealer_net": 50, "total_net": 850, "source": "TWSE"}]}
        app = self.run_dashboard(chip=chip)
        self.assertTrue(any("日期有缺漏" in element.value for element in app.warning))
        self.assertTrue(any(element.value == "+1,000 股" for element in app.metric))


if __name__ == "__main__":
    unittest.main()
