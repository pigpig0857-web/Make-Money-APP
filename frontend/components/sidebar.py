# -*- coding: utf-8 -*-
"""
側邊欄元件（Frontend — Sidebar）
僅放置側邊欄 UI：股票代號/名稱搜尋輸入框、熱門推薦 Quick Pick 按鈕。
"""

import streamlit as st

from frontend.components.scroll_script import force_scroll_to_top
from backend.services.stock_master import (
    build_search_error_message,
    get_daily_trending_stocks,
    validate_stock_input,
)


def _handle_sidebar_search() -> None:
    """Callback：輸入框按 Enter 或點擊搜尋鈕時觸發（標準 Streamlit Callback 機制）。

    只做 Session State 純寫入——callback 與元件事件同步批次執行，
    直接讀取已提交的輸入框值，徹底消除「需按 2~3 次」的生命週期不同步問題；
    實際驗證與頁面跳轉由主流程統一處理（callback 內不可渲染元件）。
    """
    st.session_state["pending_search"] = (st.session_state.get("temp_stock_input") or "").strip()


def render_sidebar():
    """渲染側邊欄：品牌標題、搜尋框、熱門推薦、回首頁按鈕。"""
    with st.sidebar:
        with st.container(key=f"sidebar_{st.session_state['sidebar_key']}"):
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

            # ── 搜尋：標準 Callback 機制（輸入完按 Enter、或點擊按鈕，一次即觸發）──
            st.text_input(
                "股票代號 / 名稱",
                placeholder="例如：2330.TW 或 台積電",
                help="支援台股 4 碼代號（如 2330）或中文名（如 台積電）。",
                key="temp_stock_input",
                on_change=_handle_sidebar_search,
            )
            st.button(
                "🔍 開始 AI 診斷",
                type="primary",
                use_container_width=True,
                on_click=_handle_sidebar_search,
            )

            # Callback 觸發後於主流程驗證並跳轉（一次操作立即 rerun 生效）
            if "pending_search" in st.session_state:
                query = st.session_state.pop("pending_search")
                valid, ticker_code, _ = validate_stock_input(query)
                if valid and ticker_code:
                    st.session_state["current_ticker"] = ticker_code
                    st.session_state["sidebar_key"] += 1
                    force_scroll_to_top()
                    st.rerun()  # 強制立即重新渲染畫面
                else:
                    st.session_state["search_error"] = build_search_error_message(query)
                    st.rerun()

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
                    st.session_state["current_ticker"] = code
                    st.session_state["sidebar_key"] += 1
                    force_scroll_to_top()
                    st.rerun()

            st.divider()
            if st.button("🏠 回到首頁 / 重新搜尋", key="btn_home_sidebar", use_container_width=True):
                st.session_state["current_ticker"] = None
                st.session_state["sidebar_key"] += 1
                force_scroll_to_top()
                st.rerun()

            st.caption("資料來源：優先使用 Yahoo Finance，離線或延遲時自動以 Mock Data 展示。")
            st.caption("交易時段（週一至五 09:00–13:30）將自動每 15 秒刷新頁面，呈現即時價格浮動。")
