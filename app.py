# -*- coding: utf-8 -*-
"""
財神爺 AI 智股通 — AI 籌碼診斷與技術分析
台股技術面 / 籌碼面 / 基本面 / 風險管理 / AI 綜合決策儀表板

主入口：僅負責頁面初始化、session_state 預設值、呼叫 Sidebar 與 Main View。
"""

import streamlit as st

from frontend.components.sidebar import render_sidebar
from frontend.main_view import render_main_view

# ====================== 頁面基本設定 ======================
st.set_page_config(
    page_title="財神爺 AI 智股通｜AI 籌碼診斷與技術分析",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ====================== Session State 初始化 ======================
if "selected_ticker" not in st.session_state:
    st.session_state["selected_ticker"] = None
if "target_ticker" not in st.session_state:
    st.session_state["target_ticker"] = None
if "loading_new" not in st.session_state:
    st.session_state["loading_new"] = False
if "stock_search_input" not in st.session_state:
    st.session_state["stock_search_input"] = ""

# ====================== 渲染 ======================
render_sidebar()
render_main_view()

from frontend.components.scroll_script import render_scroll_script
render_scroll_script()
