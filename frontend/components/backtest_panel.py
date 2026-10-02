"""Explicit, user-triggered backtest: historical estimates, not live orders."""

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from backend.analytics.backtest import run_backtest
from backend.services.stock_fetcher import _download_daily, StockDataUnavailableError


def _queue_backtest(ticker):
    # Persist the submitted settings before price loading or auto-refresh.
    values = st.session_state
    prefix = f"backtest_{ticker}_"
    values[f"pending_backtest_{ticker}"] = {
        "initial_cash": values[prefix + "capital"], "test_days": values[prefix + "days"],
        "max_hold_days": int(values[prefix + "hold"]), "stop_loss_pct": values[prefix + "stop"] / 100,
        "commission": values[prefix + "fee"] / 100, "sell_tax": values[prefix + "tax"] / 100,
        "minimum_fee": values[prefix + "minimum"], "slippage": values[prefix + "slip"] / 100,
    }


def render_backtest_panel(ticker):
    with st.expander("歷史回測：技術評分規則"):
        st.caption("前日收盤分數 ≥45 隔日開盤進場；低於 36、收盤觸發停損或達持有期限，隔日開盤退場。一次一筆、使用全部試算資金，期末扣成本結算。法人未計分。")
        with st.form(f"backtest_form_{ticker}"):
            prefix = f"backtest_{ticker}_"
            st.number_input("試算資金（元）", min_value=1000.0, value=100000.0, step=10000.0, key=prefix + "capital")
            st.selectbox("回測交易日數", [126, 252, 504], index=1, key=prefix + "days")
            st.number_input("最長持有交易日數", min_value=1, max_value=120, value=20, key=prefix + "hold")
            st.number_input("收盤停損幅度（%）", min_value=0.1, max_value=50.0, value=8.0, key=prefix + "stop")
            st.number_input("單邊手續費（%）", min_value=0.0, max_value=5.0, value=0.1425, format="%.4f", key=prefix + "fee")
            st.number_input("賣出交易稅（%，依商品調整）", min_value=0.0, max_value=5.0, value=0.3, format="%.3f", key=prefix + "tax")
            st.number_input("每筆最低手續費（元）", min_value=0.0, value=0.0, key=prefix + "minimum")
            st.number_input("單邊滑價假設（%）", min_value=0.0, max_value=5.0, value=0.05, format="%.3f", key=prefix + "slip")
            st.form_submit_button("執行歷史回測", on_click=_queue_backtest, args=(ticker,))
        st.caption("預設交易稅為一般股票賣出 0.3%；ETF 等商品請依適用稅率調整。手續費與最低費用請依券商契約設定。")
        st.markdown("[交易成本參考：證交所投資指南](https://www.twse.com.tw/zh/about/company/guide.html)")
        pending_key = f"pending_backtest_{ticker}"
        if pending_key in st.session_state:
            parameters = st.session_state[pending_key]
            try:
                with st.spinner("讀取真實歷史行情並逐日回測…"):
                    frame, source = _download_daily(ticker, "2y")
                    now = datetime.now(ZoneInfo("Asia/Taipei"))
                    if now.hour * 60 + now.minute < 810:
                        frame = frame[frame["Date"].dt.date < now.date()].copy()
                    result = run_backtest(frame, **parameters)
                st.session_state[f"backtest_result_{ticker}"] = (result, source)
            except (ValueError, StockDataUnavailableError) as exc:
                st.session_state.pop(f"backtest_result_{ticker}", None)
                st.warning(str(exc))
            st.session_state.pop(pending_key, None)
        stored = st.session_state.get(f"backtest_result_{ticker}")
        if stored:
            result, source = stored
            st.caption(f"{result['start_date']} ～ {result['end_date']}，實際 {result['trading_days']} 個交易日；規則 {result['strategy_version']}；資料來源：{source}")
            st.caption("以下是上次提交參數的結果；修改參數後需重新執行。")
            settings = result["parameters"]
            st.caption(f"本次參數：資金 {settings['initial_cash']:,.0f} 元；最長持有 {settings['max_hold_days']} 日；停損 {settings['stop_loss_pct'] * 100:.1f}%；手續費 {settings['commission'] * 100:.4f}%；賣出稅 {settings['sell_tax'] * 100:.3f}%；滑價 {settings['slippage'] * 100:.3f}%；最低手續費 {settings['minimum_fee']:.2f} 元。")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("策略淨報酬", f"{result['return_pct']:+.2f}%")
            c2.metric("買進持有淨報酬", f"{result['benchmark_return_pct']:+.2f}%")
            c3.metric("最大回撤（日收盤）", f"{result['max_drawdown_pct']:.2f}%")
            c4.metric("完成交易次數", result["trade_count"])
            win = result["win_rate_pct"]
            st.write(f"完成交易勝率：{win:.1f}%" if win is not None else "完成交易勝率：無交易，無法計算。")
            st.write(f"策略成本（手續費、交易稅及滑價）：{result['total_cost']:,.2f} 元")
            if result["return_pct"] < 0:
                st.warning("本次回測扣除成本後虧損；目前規則仍需驗證。")
            if result["return_pct"] < result["benchmark_return_pct"]:
                gap = result["benchmark_return_pct"] - result["return_pct"]
                st.info(f"此期間策略落後買進持有 {gap:.2f} 個百分點。")
            st.line_chart(result["equity"].rename(columns={"strategy": "策略資產", "benchmark": "買進持有資產"}))
            if result["trades"]:
                st.dataframe(result["trades"], hide_index=True)
            if result["unfilled_entries"]:
                st.info(f"有 {result['unfilled_entries']} 次進場訊號因資金不足買入 1 股而未成交。")
            st.caption("這是還原股價的歷史試算，未模擬漲跌停、成交量限制、零股撮合或實際除權息現金流；停損遇跳空可能超過設定幅度。勝率僅描述此股票、此期間的已完成交易，未做跨股票或樣本外驗證。")
