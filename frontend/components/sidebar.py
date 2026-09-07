# -*- coding: utf-8 -*-
"""
側邊欄元件（Frontend — Sidebar）
僅放置側邊欄 UI：股票代號/名稱搜尋輸入框、熱門推薦 Quick Pick 按鈕。
"""

import streamlit as st

from backend.services.stock_master import (
    build_search_error_message,
    get_daily_trending_stocks,
    validate_stock_input,
)


def render_sidebar():
    """渲染側邊欄：品牌標題、搜尋框、熱門推薦、回首頁按鈕。"""
    # ── 防禦式初始化：確保無論何種入口，首次按鈕點擊前已完成 Session State ──
    if "target_ticker" not in st.session_state:
        st.session_state["target_ticker"] = None
    if "loading_new" not in st.session_state:
        st.session_state["loading_new"] = False
    if "stock_search_input" not in st.session_state:
        st.session_state["stock_search_input"] = ""

    with st.sidebar:
        st.markdown('<div id="sidebar-top"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:1.4rem;font-weight:900;letter-spacing:1px;'
            'background:linear-gradient(90deg,#FFD700 0%,#FFA500 100%);'
            '-webkit-background-clip:text;background-clip:text;'
            '-webkit-text-fill-color:transparent;color:transparent;'
            'filter:drop-shadow(0 2px 3px rgba(180,120,0,0.30));">'
            '💰 財神爺 AI 智股通</div>',
            unsafe_allow_html=True,
        )
        st.caption("台股 AI 操盤與籌碼分析助理（教學用途）")

        # ── 搜尋：st.form 一次性同步取值 + 觸發 Phase 1 ──
        with st.form(key="search_form", clear_on_submit=False):
            st.text_input("股票代號/名稱", key="stock_search_input")
            search_submitted = st.form_submit_button("🚀 開始 AI 診斷", use_container_width=True)

        if search_submitted:
            query = (st.session_state.get("stock_search_input") or "").strip()
            if query:
                valid, ticker_code, _ = validate_stock_input(query)
                if valid and ticker_code:
                    st.session_state["target_ticker"] = ticker_code
                    st.session_state["loading_new"] = True
                    st.rerun()
                else:
                    st.session_state["search_error"] = build_search_error_message(query)

        # 隱藏輸入框右側的 Press Enter 提示
        st.markdown(
            """
            <style>
            div[data-testid="InputInstructions"] { display: none !important; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("**🔥 熱門推薦 Quick Pick**")
        for name, code in get_daily_trending_stocks():
            if st.button(f"{name}　{code.replace('.TW', '')}", key=f"quick_{code}", use_container_width=True):
                st.session_state["target_ticker"] = code
                st.session_state["loading_new"] = True
                st.rerun()

        st.divider()
        if st.button("🏠 回到首頁 / 重新搜尋", key="btn_home_sidebar", use_container_width=True):
            st.session_state["selected_ticker"] = None
            st.session_state["target_ticker"] = None
            st.session_state["loading_new"] = False
            st.session_state["stock_search_input"] = ""
            st.rerun()

        st.caption("資料來源：優先使用 Yahoo Finance，離線或延遲時自動以 Mock Data 展示。")
        st.caption("交易時段（週一至五 09:00–13:30）將自動每 15 秒刷新頁面，呈現即時價格浮動。")