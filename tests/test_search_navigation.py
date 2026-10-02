"""Regression tests for first-submit navigation without external market APIs."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest


class SearchNavigationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.events = []
        self.stack.enter_context(patch(
            "frontend.components.sidebar.get_daily_trending_stocks",
            return_value=[("台積電", "2330.TW")],
        ))
        self.validate = self.stack.enter_context(patch(
            "frontend.main_view.validate_stock_input",
            side_effect=self.validate_query,
        ))
        self.stack.enter_context(patch(
            "frontend.main_view.get_market_status", return_value={"is_open": True},
        ))
        self.stack.enter_context(patch(
            "frontend.main_view._render_home_page", side_effect=lambda: st.title("首頁"),
        ))
        self.stack.enter_context(patch(
            "frontend.main_view._render_stock_dashboard", side_effect=self.render_dashboard,
        ))
        self.autorun = self.stack.enter_context(patch(
            "frontend.main_view.setup_autorun",
            side_effect=lambda _: self.events.append("autorun"),
        ))
        self.app = AppTest.from_string('''
from frontend.components.sidebar import render_sidebar
from frontend.main_view import render_main_view
render_sidebar()
render_main_view()
''').run()
        self.assertEqual(len(self.app.exception), 0)

    def validate_query(self, query):
        self.events.append("validate")
        codes = {"6770": "6770.TW", "2330": "2330.TW", "台積電": "2330.TW"}
        ticker = codes.get(query)
        return bool(ticker), ticker, None

    def render_dashboard(self, ticker, status):
        self.events.append("dashboard")
        st.title(ticker)

    def submit(self, query):
        self.app.text_input(key="stock_search_input").set_value(query)
        button = next(b for b in self.app.button if "開始 AI 診斷" in b.label)
        button.click().run()
        self.assertEqual(len(self.app.exception), 0)

    def test_first_submit_and_switch_stock(self):
        self.autorun.assert_not_called()
        self.submit("6770")
        self.assertEqual(self.app.title[0].value, "6770.TW")
        self.validate.assert_called_once_with("6770")
        self.assertEqual(self.events, ["validate", "dashboard", "autorun"])
        self.submit("台積電")
        self.assertEqual(self.app.title[0].value, "2330.TW")

    def test_interrupted_validation_keeps_first_submission(self):
        interrupted = False

        def validate_with_rerun(query):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                st.rerun()
            return self.validate_query(query)

        self.validate.side_effect = validate_with_rerun
        self.submit("6770")
        self.assertEqual(self.app.title[0].value, "6770.TW")
        self.assertNotIn("pending_stock_query", self.app.session_state)

    def test_invalid_and_empty_search_show_error(self):
        self.submit("不存在的股票")
        self.assertEqual(self.app.title[0].value, "首頁")
        self.assertIn("找不到", self.app.error[0].value)
        self.submit("")
        self.assertIn("請輸入", self.app.error[0].value)
        self.submit("2330")
        self.assertEqual(len(self.app.error), 0)

    def test_home_reset_and_quick_pick(self):
        self.submit("6770")
        self.app.button(key="btn_home_sidebar").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.app.title[0].value, "首頁")
        self.assertEqual(self.app.text_input[0].value, "")
        self.app.button(key="quick_2330.TW").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.app.title[0].value, "2330.TW")


if __name__ == "__main__":
    unittest.main()
