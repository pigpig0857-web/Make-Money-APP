"""User-triggered connection check; no credentials appear in the UI."""

import streamlit as st

from backend.services.database import (
    DatabaseConfigurationError,
    check_database_connection,
)
from backend.services.stock_repository import get_storage_summary, initialize_stock_storage
from backend.services.analysis_repository import get_analysis_counts
from backend.services.research_repository import get_research_counts


def render_database_status():
    with st.sidebar.expander("資料庫連線"):
        st.caption("PostgreSQL · Make Money APP")
        if st.button("檢查資料庫連線", key="check_database_connection"):
            try:
                with st.spinner("正在連線…"):
                    database, user = check_database_connection()
                st.success(f"連線成功：{database}（帳號：{user}）")
            except DatabaseConfigurationError as exc:
                st.info(str(exc))
            except ImportError:
                st.error("缺少資料庫套件，請使用啟動 App 的 Python 執行 pip install -r requirements.txt。")
            except Exception:
                # Avoid echoing connection settings or credentials in errors.
                st.error("連線失敗。請檢查主機、埠、資料庫名稱、帳號與密碼，以及 PostgreSQL 是否運行。")
        st.caption("搜尋股票後，真實日 K 資料會自動儲存；模擬行情不會寫入。")
        if st.button("查看已儲存資料", key="check_stored_prices"):
            try:
                stocks, prices, updated = get_storage_summary()
                st.write(f"已登錄標的：{stocks}；日 K：{prices:,} 筆")
                institutions, snapshots = get_analysis_counts()
                st.write(f"法人每日資料：{institutions:,} 筆；評分紀錄：{snapshots:,} 筆")
                revenues, outcomes, price_tickers = get_research_counts()
                st.write(f"月營收版本：{revenues:,} 筆；已完成績效：{outcomes:,} 筆")
                st.caption(f"有日 K 的標的：{price_tickers}。登錄標的包含營收報表公司與大盤指數，不代表全部已下載行情。")
                if updated:
                    from zoneinfo import ZoneInfo
                    st.caption(f"最後寫入：{updated.astimezone(ZoneInfo('Asia/Taipei')):%Y-%m-%d %H:%M:%S}")
                else:
                    st.info("尚無行情，請搜尋股票並等待分析完成。")
            except Exception:
                st.error("無法讀取儲存統計，請檢查連線並確認資料表已初始化。")
        if st.button("初始化專案資料表", key="initialize_stock_storage"):
            try:
                initialize_stock_storage()
                st.success("股價、法人、月營收、評分與績效追蹤資料表已就緒。")
            except Exception:
                st.error("初始化失敗，請檢查連線及建立資料表權限。")
