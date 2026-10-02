"""Explainable real-data context and explicitly triggered forward tracking."""

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from backend.analytics.score_tracking import completed_prices, measure_snapshots, relative_return
from backend.services.revenue_fetcher import get_revenue_context
from backend.services.research_repository import load_tracking_snapshots, save_outcomes
from backend.services.stock_fetcher import _download_daily, StockDataUnavailableError
from backend.services.market_context import load_tpex_benchmark


def _queue_research(ticker, task):
    st.session_state[f'pending_research_{ticker}_{task}'] = True


def render_research_panel(ticker, frame):
    with st.expander('營運與市場：真實資料診斷依據'):
        st.caption('月營收與大盤比較為輔助資訊，尚未改變 60 分技術評分。產業比較衡量營收成長，不是產業股價指數。')
        st.button('更新月營收與大盤比較', key=f'research_context_{ticker}',
                  on_click=_queue_research, args=(ticker, 'context'))
        pending = f'pending_research_{ticker}_context'
        result_key = f'research_context_result_{ticker}'
        if st.session_state.get(pending):
            with st.spinner('讀取官方月營收及真實市場行情…'):
                revenue = get_revenue_context(ticker)
                market = None
                market_error = None
                label = '櫃買指數' if ticker.endswith('.TWO') else '加權指數'
                try:
                    now = datetime.now(ZoneInfo('Asia/Taipei'))
                    own = completed_prices(frame, now)
                    if len(own) <= 20:
                        raise ValueError('股票行情不足以比較。')
                    if ticker.endswith('.TWO'):
                        benchmark, source = load_tpex_benchmark(own['Date'].iloc[-21].date(), own['Date'].iloc[-1].date())
                    else:
                        benchmark, source = _download_daily('^TWII', '1y')
                    market = relative_return(own, completed_prices(benchmark, now))
                    market.update(label=label, source=source, offline=bool(benchmark.attrs.get('is_stale') or frame.attrs.get('is_stale')))
                except (ValueError, StockDataUnavailableError) as exc:
                    market_error = str(exc)
                st.session_state[result_key] = {'revenue': revenue, 'market': market, 'market_error': market_error}
            st.session_state.pop(pending, None)
        result = st.session_state.get(result_key)
        if result:
            revenue = result['revenue']
            if revenue['available']:
                r = revenue['latest']
                st.subheader(f"月營收：{r['name']} · {r['revenue_month']:%Y-%m}")
                st.caption(f"來源：{r['source']}；官方產業：{r['industry']}；出表日期：{r['report_date']}；本次資料取得時間：{revenue['observed_at']}")
                st.caption('出表日期是報表日期，不能當作這家公司實際公告時間。只累積實際取得的月份，未補造歷史資料。')
                c1, c2, c3 = st.columns(3)
                c1.metric('當月營收（新臺幣千元）', f"{r['revenue_thousand']:,.0f}")
                c2.metric('營收年增率', f"{r['yoy_pct']:+.2f}%" if r['yoy_pct'] is not None else '缺資料')
                c3.metric('營收月增率', f"{r['mom_pct']:+.2f}%" if r['mom_pct'] is not None else '缺資料')
                cumulative = r['cumulative_yoy_pct']
                st.write(f"年初至今累計營收年增率：{cumulative:+.2f}%" if cumulative is not None else '累計營收年增率：缺資料')
                median = revenue['peer_median_yoy']
                if median is not None:
                    st.write(f"同業營收年增率中位數：{median:+.2f}%（{revenue['peer_count']} 家）")
                    if r['yoy_pct'] is not None:
                        st.write(f"本股票高於／低於同業中位數：{r['yoy_pct'] - median:+.2f} 個百分點")
                else:
                    st.info(f"同月份可比較同業只有 {revenue['peer_count']} 家；不足 3 家，暫不計算同業中位數。")
                st.caption(revenue['peer_scope'] + '；營收成長不等於獲利成長或股價上漲。')
                if revenue['offline']:
                    st.warning('官方來源暫時無法取得，以上為資料庫離線營收，請查看月份與取得時間。')
                if not revenue['stored']:
                    st.warning('營收已取得，但資料庫儲存失敗，請初始化資料表並確認連線。')
                st.info(revenue['reason'])
            else:
                st.info(revenue['reason'])
            market = result['market']
            if market:
                st.subheader('近 20 個股票交易日：與市場比較')
                st.caption(f"{market['start_date']} ～ {market['end_date']}；比較基準：{market['label']}；{market['source']}")
                c1, c2, c3 = st.columns(3)
                c1.metric('股票還原價格報酬', f"{market['stock_return_pct']:+.2f}%")
                c2.metric('市場指數報酬', f"{market['market_return_pct']:+.2f}%")
                c3.metric('領先／落後市場', f"{market['excess_pp']:+.2f} 個百分點")
                st.caption('相同起訖日期；指數不是可直接成交的商品。未扣交易成本，還原價格與價格指數的股利處理不同。')
                if market['offline']:
                    st.warning('比較含離線歷史行情，不代表最新市場狀況。')
            elif result['market_error']:
                st.info('市場比較暫不可用：' + result['market_error'])
            st.caption('以上為上次按鈕取得的結果；切換其他控制項不會重新抓取。')
        else:
            st.info('按更新按鈕後，會儲存該市場官方月營收報表與大盤日 K。')

    with st.expander('評分後的結果：5／20／60 交易日追蹤'):
        st.caption('從評分實際記錄日之後的第一個交易日開盤起算；第 5／20／60 個交易日收盤觀察報酬。這是持有觀察，不是買賣策略回測。')
        st.button('更新評分後績效', key=f'research_outcomes_{ticker}',
                  on_click=_queue_research, args=(ticker, 'outcomes'))
        pending = f'pending_research_{ticker}_outcomes'
        result_key = f'research_outcomes_result_{ticker}'
        if st.session_state.get(pending):
            try:
                with st.spinner('比對實際評分時間與後續行情…'):
                    snapshots = load_tracking_snapshots(ticker)
                    if snapshots:
                        prices, source = _download_daily(ticker, '5y')
                        prices = completed_prices(prices, datetime.now(ZoneInfo('Asia/Taipei')))
                        rows = measure_snapshots(snapshots, prices)
                        save_outcomes(rows)
                        st.session_state[result_key] = {'rows': rows, 'source': source}
                    else:
                        st.session_state[result_key] = {'rows': [], 'source': ''}
            except (ValueError, StockDataUnavailableError) as exc:
                st.warning(str(exc))
                st.session_state.pop(result_key, None)
            except Exception:
                st.warning('無法讀取或儲存績效，請確認資料庫連線並初始化專案資料表。')
                st.session_state.pop(result_key, None)
            st.session_state.pop(pending, None)
        result = st.session_state.get(result_key)
        if result:
            display = []
            for r in result['rows']:
                display.append({'評分記錄時間': r['recorded_at'], '規則版本': r['model_version'],
                                '分數': f"{r['score']}/{r['score_max']}", '觀察交易日數': r['horizon'],
                                '狀態': {'complete': '已完成', 'pending': f"等待行情（已取得 {r['observed_days']} 日）",
                                         'insufficient_history': '歷史行情不足'}[r['status']],
                                '起算日': r.get('entry_date'), '結束日': r.get('end_date'),
                                '報酬（%）': r.get('return_pct'),
                                '期間最低收盤相對起算開盤（%）': r.get('lowest_close_return_pct')})
            if display:
                st.dataframe(display, hide_index=True, width='stretch')
                st.caption(f"最近最多 100 筆實際評分；{result['source']}；已完成結果存入資料庫。")
            else:
                st.info('尚無評分紀錄。先搜尋股票完成評分，再更新追蹤。')
        st.caption('使用 Yahoo 還原價格，不扣手續費、稅與滑價；未另計股利現金流。期間最低收盤報酬不是最大回撤，也不包含盤中跌幅。')
        st.caption('剛建立的評分需要等待交易日經過；尚未到期不顯示 0% 或假造結果。同一日多筆評分不是獨立樣本，不能直接當作勝率。')
