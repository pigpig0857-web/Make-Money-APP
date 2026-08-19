# -*- coding: utf-8 -*-
"""
財神爺選股 — AI 智慧選股與技術診斷
台股技術面 / 籌碼面 / 基本面 / 風險管理 / AI 綜合決策儀表板

主入口：僅負責頁面初始化、session_state 預設值、呼叫 Sidebar 與 Main View。
"""

import streamlit as st

from frontend.components.sidebar import render_sidebar
from frontend.main_view import render_main_view

# ====================== 頁面基本設定 ======================
st.set_page_config(
    page_title="財神爺選股｜AI 智慧選股與技術診斷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ====================== Session State 初始化 ======================
if "current_ticker" not in st.session_state:
    st.session_state["current_ticker"] = None
if "sidebar_key" not in st.session_state:
    st.session_state["sidebar_key"] = 0
if "scroll_to_top" not in st.session_state:
    st.session_state["scroll_to_top"] = False

# ====================== 渲染 ======================
render_sidebar()
render_main_view()
