"""Persistent desktop watchlist and visible, resumable catch-up batches."""

import streamlit as st

from backend.services.watchlist_repository import add_watch, remove_watch, list_watches
from backend.services.stock_master import validate_stock_input
from backend.services.backfill import new_job, run_step


def _queue_add():
    st.session_state['pending_watch_add'] = st.session_state.get('watch_query', '').strip()


def _queue_current(ticker):
    st.session_state['pending_watch_add'] = ticker


def _queue_batch(tickers):
    st.session_state['backfill_job'] = new_job(tickers,
        days=st.session_state.get('backfill_days', 60),
        period=st.session_state.get('backfill_period', '2y'))


def _cancel_batch():
    job = st.session_state.pop('backfill_job', None)
    if job:
        st.session_state['backfill_last_results'] = job['results']
    st.session_state['watch_notice'] = '補齊已停止；已寫入的資料保留，再次執行會檢查缺漏。'


def render_watchlist_panel():
    """Return True while busy, so the main dashboard does not refresh sources."""
    job = st.session_state.get('backfill_job')
    busy = bool(job and not job['done'])
    with st.sidebar.expander('追蹤清單與補齊資料', expanded=busy):
        st.caption('清單保存在本機資料庫；電腦開機後按補齊即可更新，不需要每天登入。')
        if 'pending_watch_add' in st.session_state:
            query = st.session_state['pending_watch_add']
            try:
                with st.spinner('確認追蹤股票…'):
                    valid, ticker, _ = validate_stock_input(query)
                if valid and ticker:
                    add_watch(ticker)
                    st.session_state['watch_notice'] = f'已加入 {ticker}。'
                else:
                    st.session_state['watch_notice'] = '找不到股票，請確認代號或名稱。'
            except Exception:
                st.session_state['watch_notice'] = '無法加入，請確認股票來源、資料庫連線及資料表初始化。'
            st.session_state.pop('pending_watch_add', None)
        if st.session_state.get('watch_notice'):
            st.info(st.session_state['watch_notice'])
        try:
            watches = list_watches()
        except Exception:
            st.info('請先連接資料庫並初始化專案資料表，才能建立追蹤清單。')
            return False
        with st.form('watch_add_form'):
            st.text_input('加入追蹤：股票代號或名稱', key='watch_query', disabled=busy)
            st.form_submit_button('加入追蹤清單', on_click=_queue_add, disabled=busy)
        selected = st.session_state.get('selected_ticker')
        if selected:
            st.button(f'追蹤目前股票 {selected}', key='watch_current',
                      on_click=_queue_current, args=(selected,), disabled=busy)
        for ticker, name in watches:
            left, right = st.columns([3, 1])
            left.write(f'{ticker} {name}')
            if right.button('移除', key=f'remove_watch_{ticker}', disabled=busy):
                try:
                    remove_watch(ticker)
                    st.rerun()
                except Exception:
                    st.error('移除失敗，請確認資料庫連線。')
        st.selectbox('日 K 下載範圍', ['1y', '2y', '5y'], index=1, key='backfill_period', disabled=busy)
        st.selectbox('法人補齊範圍（最近交易日）', [20, 60, 120], index=1, key='backfill_days', disabled=busy)
        st.caption('每批最多請求 60 份法人日報，超過可再次按補齊續補。未公布或取得失敗會列出缺漏；月營收歷史及歷史每日評分尚不補算。')
        st.button('補齊追蹤股票資料', key='start_backfill', on_click=_queue_batch,
                  args=([ticker for ticker, _ in watches],), disabled=busy or not watches)
        if busy:
            st.button('停止補齊', key='stop_backfill', on_click=_cancel_batch)
        elif not watches:
            st.info('先加入股票，再按補齊。')

    if busy:
        st.subheader('正在補齊追蹤股票資料')
        ticker = job['tickers'][job['index']]
        labels = {'prices': '下載並儲存真實日 K', 'institutions': '檢查並補入官方法人日報', 'outcomes': '更新實際評分後績效'}
        st.progress(job['index'] / len(job['tickers']), text=f"{job['index'] + 1}/{len(job['tickers'])} · {ticker} · {labels[job['stage']]}")
        st.caption(f"本批法人日報請求：{job['report_requests']}/60。重新整理後可繼續；關閉程式後再執行會依資料庫重新檢查。")
        run_step(job)
        st.session_state['backfill_job'] = job
        if job['done']:
            st.session_state['backfill_last_results'] = job['results']
            from backend.services.stock_fetcher import fetch_stock_data
            from frontend.components.kline_chart import get_kline_chart
            fetch_stock_data.clear()
            get_kline_chart.clear()
            for ticker in job['tickers']:
                st.session_state.pop(f'research_outcomes_result_{ticker}', None)
                st.session_state.pop(f'research_context_result_{ticker}', None)
        st.rerun()
    results = st.session_state.get('backfill_last_results')
    if results:
        with st.expander('最近一次補齊結果', expanded=True):
            rows = []
            for r in results:
                rows.append({'股票': r['ticker'], '狀態': {'complete': '所選範圍已完成', 'partial': '部分完成', 'failed': '失敗'}[r['status']],
                             '日 K 範圍': f"{r.get('first_date', '')} ～ {r.get('last_date', '')}",
                             '日 K 已儲存筆數': r.get('price_rows', 0),
                             '法人已具備／需具備': f"{r.get('institution_rows', 0)}/{len(r.get('dates', []))}",
                             '缺漏法人日期': ', '.join(map(str, r.get('missing_dates', []))),
                             '失敗階段': r.get('error_stage', ''), '錯誤代碼': r.get('error_type', ''),
                             '已到期績效筆數': r.get('outcomes', 0), '說明': '；'.join(r['notes'])})
            st.dataframe(rows, hide_index=True, width='stretch')
            st.caption('日 K 筆數是本次保存範圍的總筆數；採更新方式，不代表全部都是新增資料。沒有事後建立假冒當日建議的評分。')
    return False
