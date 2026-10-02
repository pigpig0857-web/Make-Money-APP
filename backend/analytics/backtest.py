"""Fixed-rule historical simulation with prior-close signals and next-open fills."""

import math

import numpy as np
import pandas as pd


def run_backtest(frame, *, initial_cash=100000.0, test_days=252,
                 entry_score=45, exit_score=36, max_hold_days=20,
                 stop_loss_pct=0.08, commission=0.001425, sell_tax=0.003,
                 slippage=0.0005, minimum_fee=0.0, score_function=None):
    """Long-only, integer shares, one position, no parameter optimization.

    Signals use only candles available before each day's open. A stop loss is
    checked at the prior close and executes at the next open, including gaps.
    Both strategy and buy-and-hold are liquidated at the final adjusted close.
    """
    if frame.attrs.get("price_source") != "yfinance":
        raise ValueError("回測只接受真實 Yahoo 行情。")
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("初始資金必須大於零。")
    if not 0 <= exit_score < entry_score <= 60:
        raise ValueError("進場門檻需高於退場門檻，且在 0 至 60 分之間。")
    if test_days < 1 or max_hold_days < 1:
        raise ValueError("回測天數與持有天數必須大於零。")
    if any(not math.isfinite(x) or not 0 <= x < 1 for x in (commission, sell_tax, slippage, stop_loss_pct)):
        raise ValueError("費率、滑價與停損比例必須介於 0 至 100% 之間。")
    if not math.isfinite(minimum_fee) or minimum_fee < 0:
        raise ValueError("最低手續費不得為負數。")
    data = frame.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    if data["Date"].isna().any() or data["Date"].duplicated().any() or not data["Date"].is_monotonic_increasing:
        raise ValueError("行情日期必須唯一且依時間排序。")
    values = data[["Open", "High", "Low", "Close", "Volume"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError("回測行情有缺漏或無效價格。")
    if len(data) < 121:
        raise ValueError("需要至少 120 根暖身日 K，加上 1 根可交易日 K。")
    # Recompute causal indicators rather than trusting externally supplied MAs.
    for days in (5, 20, 60):
        data[f"MA{days}"] = data["Close"].rolling(days).mean()
    if score_function is None:
        from frontend.components.ai_diagnosis import calculate_precise_ai_score

        def score_function(history):
            # A historical test can use a stored, old dataset. It never emits a
            # current-market recommendation or changes the source frame's attrs.
            history.attrs["is_stale"] = False
            return calculate_precise_ai_score(history, {}, {}, debug_log=False,
                                              _compute_delta=False)["total_score"]

    start = max(120, len(data) - int(test_days))
    cash = float(initial_cash)
    position = None
    trades = []
    curve = [{"Date": data["Date"].iloc[start - 1], "strategy": cash}]
    total_cost = 0.0
    unfilled_entries = 0

    def buy(cash_value, raw_price):
        price = raw_price * (1 + slippage)
        if cash_value <= minimum_fee:
            return 0, price, 0.0, 0.0
        shares = int(cash_value / (price * (1 + commission)))
        while shares > 0:
            value = shares * price
            fee = max(minimum_fee, value * commission)
            if value + fee <= cash_value:
                return shares, price, value + fee, fee + shares * (price - raw_price)
            shares -= 1
        return 0, price, 0.0, 0.0

    def sell(shares, raw_price):
        price = raw_price * (1 - slippage)
        value = shares * price
        fee = max(minimum_fee, value * commission) + value * sell_tax
        return price, value - fee, fee + shares * (raw_price - price)

    def close_position(index, raw_price, reason):
        nonlocal cash, position, total_cost
        price, proceeds, costs = sell(position["shares"], raw_price)
        cash += proceeds
        total_cost += costs
        trades.append({
            "進場日": position["date"], "出場日": data["Date"].iloc[index],
            "進場訊號日": position["signal_date"], "進場分數": position["score"],
            "股數": position["shares"], "進場價": position["price"], "出場價": price,
            "持有交易日": index - position["index"] + 1,
            "淨損益": proceeds - position["cost"],
            "淨報酬率(%)": (proceeds / position["cost"] - 1) * 100,
            "出場原因": reason,
        })
        position = None

    for index in range(start, len(data)):
        score = score_function(data.iloc[:index].copy())
        if not math.isfinite(float(score)) or not 0 <= score <= 60:
            raise ValueError("評分引擎回傳無效分數。")
        opening = float(data["Open"].iloc[index])
        exited = False
        if position is not None:
            previous_close = float(data["Close"].iloc[index - 1])
            reason = None
            if previous_close <= position["price"] * (1 - stop_loss_pct):
                reason = "前日收盤停損訊號"
            elif score < exit_score:
                reason = "技術分數低於退場門檻"
            elif index - position["index"] >= max_hold_days:
                reason = "達持有期限"
            if reason:
                close_position(index, opening, reason)
                exited = True
        if position is None and not exited and score >= entry_score:
            shares, price, cost, costs = buy(cash, opening)
            if shares:
                cash -= cost
                total_cost += costs
                position = {"shares": shares, "price": price, "cost": cost,
                            "date": data["Date"].iloc[index], "index": index,
                            "signal_date": data["Date"].iloc[index - 1], "score": score}
            else:
                unfilled_entries += 1
        equity = cash + (position["shares"] * float(data["Close"].iloc[index]) if position else 0)
        curve.append({"Date": data["Date"].iloc[index], "strategy": equity})
    if position:
        close_position(len(data) - 1, float(data["Close"].iloc[-1]), "期末收盤結算")
        curve[-1]["strategy"] = cash

    # Same date window, integer-share rule, costs and forced liquidation.
    shares, price, cost, _ = buy(initial_cash, float(data["Open"].iloc[start]))
    benchmark_cash = initial_cash - cost
    curve[0]["benchmark"] = initial_cash
    for offset, row in enumerate(curve[1:], start=start):
        row["benchmark"] = benchmark_cash + shares * float(data["Close"].iloc[offset])
    if shares:
        _, proceeds, _ = sell(shares, float(data["Close"].iloc[-1]))
        curve[-1]["benchmark"] = benchmark_cash + proceeds
    equity = pd.DataFrame(curve).set_index("Date")
    drawdowns = equity["strategy"] / equity["strategy"].cummax() - 1
    winners = sum(trade["淨損益"] > 0 for trade in trades)
    return {
        "return_pct": (cash / initial_cash - 1) * 100,
        "benchmark_return_pct": (curve[-1]["benchmark"] / initial_cash - 1) * 100,
        "max_drawdown_pct": float(drawdowns.min()) * 100,
        "trade_count": len(trades),
        "win_rate_pct": winners / len(trades) * 100 if trades else None,
        "total_cost": total_cost, "final_equity": cash,
        "unfilled_entries": unfilled_entries,
        "start_date": data["Date"].iloc[start].date(),
        "end_date": data["Date"].iloc[-1].date(),
        "trading_days": len(data) - start,
        "equity": equity, "trades": trades,
        "model_version": "technical-real-v1", "strategy_version": "score-next-open-v1",
        "parameters": {"initial_cash": initial_cash, "requested_days": test_days,
                       "entry_score": entry_score, "exit_score": exit_score,
                       "max_hold_days": max_hold_days, "stop_loss_pct": stop_loss_pct,
                       "commission": commission, "sell_tax": sell_tax,
                       "slippage": slippage, "minimum_fee": minimum_fee},
    }
