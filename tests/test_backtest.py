"""Accounting and execution timing invariants; no live market dependency."""

import unittest

import pandas as pd

from backend.analytics.backtest import run_backtest
from market_fixtures import market_frame


class BacktestTests(unittest.TestCase):
    def frame(self, days=130):
        data = market_frame(days=days)
        for field in ("Open", "High", "Low", "Close"):
            data[field] = 100.0
        data.attrs.update(price_source="yfinance", is_stale=False)
        return data

    def test_previous_close_signal_executes_at_next_open_and_pays_costs(self):
        data = self.frame(days=124)
        data.loc[121, ["Open", "High", "Low", "Close"]] = 120
        result = run_backtest(data, initial_cash=1000, commission=0.01,
                              sell_tax=0.03, slippage=0,
                              score_function=lambda history: 45 if len(history) == 120 else 35)
        self.assertEqual(result["trade_count"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["進場訊號日"], data["Date"].iloc[119])
        self.assertEqual(trade["進場日"], data["Date"].iloc[120])
        self.assertEqual(trade["出場日"], data["Date"].iloc[121])
        self.assertEqual(trade["股數"], 9)
        self.assertAlmostEqual(result["final_equity"], 1127.8)
        self.assertAlmostEqual(result["total_cost"], 52.2)

    def test_stop_loss_gap_executes_at_open_not_imagined_stop_price(self):
        data = self.frame()
        data.loc[120, ["Close", "Low"]] = 90
        data.loc[121, ["Open", "Low"]] = 70
        result = run_backtest(data, initial_cash=1000, commission=0,
                              sell_tax=0, slippage=0, score_function=lambda _: 45)
        trade = result["trades"][0]
        self.assertEqual(trade["出場原因"], "前日收盤停損訊號")
        self.assertEqual(trade["出場價"], 70)
        self.assertAlmostEqual(trade["淨報酬率(%)"], -30)
        self.assertLessEqual(result["max_drawdown_pct"], -30)

    def test_future_prices_and_poisoned_indicators_do_not_change_prior_equity(self):
        data = self.frame()
        data["MA20"] = 999999

        def score(history):
            self.assertAlmostEqual(history["MA20"].iloc[-1], history["Close"].tail(20).mean())
            return 45

        first = run_backtest(data, commission=0, sell_tax=0, slippage=0, score_function=score)
        modified = data.copy()
        modified.loc[125:, ["Open", "High", "Low", "Close"]] = 300
        second = run_backtest(modified, commission=0, sell_tax=0, slippage=0, score_function=score)
        pd.testing.assert_frame_equal(first["equity"].iloc[:6], second["equity"].iloc[:6])

    def test_no_trades_have_no_invented_win_rate(self):
        result = run_backtest(self.frame(), score_function=lambda _: 0)
        self.assertEqual(result["return_pct"], 0)
        self.assertEqual(result["trade_count"], 0)
        self.assertIsNone(result["win_rate_pct"])

    def test_costs_and_slippage_reduce_flat_price_performance(self):
        clean = run_backtest(self.frame(), commission=0, sell_tax=0, slippage=0, score_function=lambda _: 45)
        costs = run_backtest(self.frame(), minimum_fee=20, score_function=lambda _: 45)
        self.assertAlmostEqual(clean["return_pct"], 0)
        self.assertLess(costs["return_pct"], clean["return_pct"])
        self.assertLess(costs["benchmark_return_pct"], 0)
        self.assertAlmostEqual(costs["return_pct"], costs["benchmark_return_pct"])

    def test_insufficient_cash_does_not_create_fractional_or_borrowed_shares(self):
        result = run_backtest(self.frame(), initial_cash=10, score_function=lambda _: 45)
        self.assertEqual(result["trade_count"], 0)
        self.assertGreater(result["unfilled_entries"], 0)
        self.assertEqual(result["final_equity"], 10)

    def test_unverified_source_and_short_history_are_rejected(self):
        data = self.frame()
        data.attrs["price_source"] = "mock"
        with self.assertRaises(ValueError):
            run_backtest(data)
        with self.assertRaises(ValueError):
            run_backtest(self.frame(days=120))


class BacktestPanelTests(unittest.TestCase):
    def test_first_submit_survives_interrupted_price_load(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        data = market_frame(days=180)
        data.attrs.update(price_source="yfinance", is_stale=False)
        interrupted = False

        def load(*args):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                st.rerun()
            return data, "測試行情"

        with patch("frontend.components.backtest_panel._download_daily", side_effect=load):
            app = AppTest.from_string('from frontend.components.backtest_panel import render_backtest_panel\nrender_backtest_panel("2330.TW")', default_timeout=20).run()
            next(button for button in app.button if button.label == "執行歷史回測").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertIn("backtest_result_2330.TW", app.session_state)
            self.assertNotIn("pending_backtest_2330.TW", app.session_state)
            self.assertTrue(any(metric.label == "策略淨報酬" for metric in app.metric))


if __name__ == "__main__":
    unittest.main()
