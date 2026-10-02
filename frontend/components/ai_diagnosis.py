# -*- coding: utf-8 -*-
"""Verified daily-price technical score: trend 30 + momentum 30.

Chip inputs are excluded until real historical feeds are connected. Missing,
unverified, stale or insufficient prices never produce scores. Score is a rule
index, not a calibrated probability. Legacy backup modules are not called.
"""

import sys

import numpy as np
import pandas as pd
import streamlit as st

try:
    from backend.services.stock_master import lookup_stock_name
except ModuleNotFoundError:
    # 允許以 `python frontend/components/ai_diagnosis.py` 單檔執行 Debug 綠燈掃描
    import pathlib

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from backend.services.stock_master import lookup_stock_name


def _debug_print(msg: str) -> None:
    """Debug 輸出：Windows 主控台（cp950）無法編碼 emoji 時以取代字元降級，避免中斷評分。"""
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or "utf-8"
            sys.stdout.write(msg.encode(enc, "replace").decode(enc, "replace") + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    return rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)


def compute_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """計算 MACD（DIF、MACD 柱狀體、訊號線）。"""
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    macd_bar = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - macd_bar
    return dif, macd_bar, hist



# ────────────────────── 短線爆量訊號判定 ──────────────────────


def decide_action_advice(
    df: pd.DataFrame, chip: dict, total_score: int, debug_log: bool = True
) -> tuple[str, str, str]:
    """雙情境行動指引決策（評分 + 短線訊號綜合判定）。

    邏輯層級：
      1. total_score < 36（低分格局）→ 依短線是否爆量反彈區分：
         is_short_term_burst → 暫不追高 / 逢高減碼
         否                → 觀望為宜 / 嚴設停損
      2. 36 <= total_score < 45（中性轉強）→ 少量試驗 / 觀察續抱
      3. total_score >= 45（高分強勢）→ 積極關注 / 強勢續抱

    回傳：(advice_no_position, advice_has_position, note_text)
    """
    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])
    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last

    # ── 均線排列：多頭判定（文案避矛盾）──
    is_ma_bullish = last > ma20 > ma60

    # ── 短線訊號：量能 vs 動能分開判定 ──
    vol_ma20 = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else float(df["Volume"].mean())
    vol_ratio = float(df["Volume"].iloc[-1]) / vol_ma20 if vol_ma20 > 0 else 1.0
    rsi_series = compute_rsi(closes, 14)
    rsi_14 = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    _, _, hist = compute_macd(closes)
    macd_hist = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0

    has_volume_burst = vol_ratio >= 1.5
    has_momentum = (rsi_14 > 50) or (macd_hist > 0)
    is_short_term_burst = has_volume_burst or has_momentum

    if debug_log:
        _debug_print(
        f"[Debug Advice] 總分:{total_score}, 短線:{is_short_term_burst}"
        f"（量比={vol_ratio:.2f}{'✓爆量' if has_volume_burst else ''}, "
        f"RSI={rsi_14:.1f}, MACD柱={macd_hist:+.2f}）"
        f"｜MA多頭:{is_ma_bullish}"
    )

    # ── 低分格局 ──
    if total_score < 36:
        if is_short_term_burst:
            advice_no = "🟡 暫不追高（短線反彈訊號，但中長線趨勢尚未扭轉）"
            advice_has = "⚠️ 逢高減碼（趁反彈分批落袋，嚴設 20MA 防守）"
            if has_volume_burst:
                burst_desc = "短線爆量反彈"
            else:
                burst_desc = "短線動能反彈"
            if is_ma_bullish:
                note = (
                    f"💡 系統診斷：目前屬於「{burst_desc}」，股價雖維持多頭排列，"
                    "但技術動能仍不足（MACD偏空），建議暫不追高。"
                )
            else:
                note = (
                    f"💡 系統診斷：目前屬於「{burst_desc}，但中長線仍受制於均線壓力」，"
                    "宜防範解套賣壓。"
                )
        else:
            advice_no = "🔴 觀望為宜（趨勢偏弱，靜待打底）"
            advice_has = "🚨 嚴設停損（技術面偏弱，注意下行風險）"
            if is_ma_bullish:
                note = (
                    "💡 系統診斷：股價雖維持多頭排列，但技術動能仍不足"
                    "（MACD偏空），建議暫不追高。"
                )
            else:
                note = (
                    "💡 系統診斷：均線架構偏弱且動能不足，"
                    "上方面臨均線反壓，宜保持觀望。"
                )
    # ── 中性轉強格局 ──
    elif total_score < 45:
        advice_no = "🟡 少量試驗 / 觀望拉回（觀察 20MA 支撐）"
        advice_has = "📈 觀察續抱（沿 20MA 操作，跌破離場）"
        note = "💡 系統診斷：個股處於區間震盪或轉強過渡期，可密切注意突破機會。"
    # ── 高分強勢格局 ──
    else:
        advice_no = "🟢 積極關注（多頭強勢，可尋找買點）"
        advice_has = "🚀 強勢續抱（多頭排列，沿 5 日線移動停利）"
        note = "💡 系統診斷：技術指標偏強，但尚未驗證籌碼與基本面，需等待進場條件確認。"

    return advice_no, advice_has, note


def validate_scoring_frame(df):
    if df.attrs.get("price_source") != "yfinance" or df.attrs.get("is_stale"):
        raise ValueError("評分需要可驗證來源、未過期的真實行情。")
    if len(df) < 120:
        raise ValueError("至少需要 120 根真實且已完成的日 K 才能評分。")
    price_values = df[["Open", "High", "Low", "Close", "Volume"]].to_numpy(dtype=float)
    if not np.isfinite(price_values).all() or (price_values[:, :4] <= 0).any() or (price_values[:, 4] < 0).any():
        raise ValueError("行情資料不完整，暫不評分。")


def calculate_precise_ai_score(
    df: pd.DataFrame,
    chip: dict,
    fund: dict,
    ticker: str = "",
    debug_log: bool = True,
    _compute_delta: bool = True,
) -> dict:
    """Score actual OHLCV only, with a maximum of 60 and no chip defaults."""

    validate_scoring_frame(df)
    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])

    # ── 均線 ──
    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last

    # ══════════════════════════════════════════════════════════
    #  維度一：60日波段趨勢 — 權重 30 分 (30%)
    # ══════════════════════════════════════════════════════════
    trend_score = 0.0
    t_align = t_slope = t_support = 0.0

    d60 = df.tail(60)
    n60 = len(d60)
    closes_60 = d60["Close"].astype(float)
    ma20_series_60 = closes_60.rolling(20).mean()

    # (a) 均線多頭排列判定 — 最高 15 分
    if last > ma20 > ma60:
        t_align = 15.0
    elif last > ma20:
        t_align = 8.0
    elif last > ma60:
        t_align = 4.0
    trend_score += t_align

    # (b) MA20 斜率 — 近 10 日 MA20 向上代表中期趨勢健康 — 最高 8 分
    if len(ma20_series_60.dropna()) >= 10:
        ma20_recent = ma20_series_60.dropna().tail(10).values
        ma20_slope = ma20_recent[-1] - ma20_recent[0]
        if ma20_slope > 0:
            slope_ratio = min(ma20_slope / (last * 0.01 + 1e-9), 1.0)
            t_slope = 8 * slope_ratio
        else:
            t_slope = max(0.0, 3 + 5 * (ma20_slope / (last * 0.01 + 1e-9)))
    trend_score += t_slope

    # (c) 支撐力道：近 60 日內回測 MA60 不破的次數 — 最高 7 分
    #     【BUG 修正】原實作在僅 60 列的視窗上做 rolling(60)，只有最後 1 列有值，
    #     迴圈永遠不會執行 → 此子分數恆為 0。改用全歷史 MA60（df["MA60"]，
    #     無此欄位則現算）與近 60 日 K 逐日對齊比對。
    support_touches = 0
    if "MA60" in df.columns:
        ma60_full = pd.to_numeric(df["MA60"], errors="coerce")
    else:
        ma60_full = df["Close"].astype(float).rolling(60, min_periods=1).mean()
    ma60_60d = ma60_full.tail(n60).to_numpy(dtype=float)
    lows_60 = d60["Low"].astype(float).to_numpy(dtype=float)
    closes_arr = closes_60.to_numpy(dtype=float)
    for i in range(1, n60):
        curr_ma60 = ma60_60d[i]
        if np.isnan(curr_ma60) or curr_ma60 <= 0:
            continue
        prev_close = float(closes_arr[i - 1])
        curr_low = float(lows_60[i])
        # 回測 MA60 附近（±2%）且前一日未實質跌破 → 支撐有效
        if abs(curr_low - curr_ma60) / (curr_ma60 + 1e-9) < 0.02 and prev_close > curr_ma60 * 0.98:
            support_touches += 1
    t_support = min(7.0, support_touches * 1.5)
    trend_score += t_support

    trend_score = min(30.0, trend_score)

    # Chip scores are disabled until real historical chip feeds are connected.

    # Short-term momentum, calculated exclusively from actual OHLCV.
    momentum_score = 0.0
    m_vol = m_price = m_break = m_rsi_macd = 0.0
    weighted_vol_score = 0.0

    d7 = df.tail(7)
    closes_7 = d7["Close"].astype(float)
    volumes_7 = d7["Volume"].astype(float)
    vol_ma20 = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else float(df["Volume"].mean())

    # 近鮮度權重：最舊=1 ... 今日=7
    raw_w = np.arange(1, 8, dtype=float)
    norm_w = raw_w / raw_w.sum()

    # (a) 近鮮度加權量能爆發 — 最高 12 分
    if vol_ma20 > 0:
        weighted_vol_score = float(np.dot((volumes_7.values / vol_ma20).astype(float), norm_w))
        if weighted_vol_score > 1.5:
            m_vol = min(12.0, (weighted_vol_score - 1.0) * 12)
        elif weighted_vol_score > 1.1:
            m_vol = (weighted_vol_score - 1.0) * 10
        elif weighted_vol_score > 0.8:
            m_vol = (weighted_vol_score - 0.8) * 5
    momentum_score += m_vol

    # (b) 7日價格動量（近鮮度加權漲幅）— 最高 9 分
    #     【BUG 修正】pct_change 後共 6 筆日報酬，原寫法 weights[:6] 使「今日」
    #     只拿到權重 6；改取權重陣列最重的尾段 [2..7] 對齊。
    if len(closes_7) >= 2:
        pct_changes_7 = closes_7.pct_change().dropna().values
        if len(pct_changes_7) > 0:
            seg = raw_w[-len(pct_changes_7):]
            price_weights = seg / seg.sum()
            weighted_return = float(np.dot(pct_changes_7, price_weights))
            if weighted_return > 0.02:
                m_price = min(9.0, weighted_return * 150)
            elif weighted_return > 0:
                m_price = weighted_return * 120
            elif weighted_return > -0.02:
                m_price = max(0.0, 2 + weighted_return * 80)
    momentum_score += m_price

    # (c) 突破信號：今日收盤創近 20 日新高 — 最高 4 分
    recent_20_high = float(df["High"].tail(20).max()) if len(df) >= 20 else last
    if last >= recent_20_high * 0.99:
        m_break = 4.0
    elif last >= recent_20_high * 0.97:
        m_break = 2.0
    momentum_score += m_break

    # (d) RSI / MACD 動能確認 — 最高 5 分（新增子維度）
    rsi_series_m = compute_rsi(closes, 14)
    rsi_now = float(rsi_series_m.iloc[-1]) if not pd.isna(rsi_series_m.iloc[-1]) else 50.0
    _, _, hist_m = compute_macd(closes)
    hist_now = float(hist_m.iloc[-1]) if not pd.isna(hist_m.iloc[-1]) else 0.0
    if rsi_now >= 55 and hist_now > 0:
        m_rsi_macd = 5.0
    elif rsi_now > 50 and hist_now > 0:
        m_rsi_macd = 4.0
    elif rsi_now > 50 or hist_now > 0:
        m_rsi_macd = 2.0
    momentum_score += m_rsi_macd

    momentum_score = float(np.clip(momentum_score, 0, 30))

    # ══════════════════════════════════════════════════════════
    #  總分 = 趨勢(30) + 動能(30)；籌碼不計分
    # ══════════════════════════════════════════════════════════
    total = int(round(float(trend_score + momentum_score)))
    total = int(np.clip(total, 0, 60))

    # 【門檻調整】放寬黃燈區間：>=75 綠燈、60~74 黃燈、<60 紅燈
    if total >= 45:
        signal_type = "🟢技術趨勢偏強"
    elif total >= 36:
        signal_type = "🟡技術趨勢中性"
    else:
        signal_type = "🔴技術趨勢偏弱"

    # ══════════════════════════════════════════════════════════
    # Compare technical scores using actual prior candles only.
    # ══════════════════════════════════════════════════════════
    score_delta = None
    chip_turn = None
    if _compute_delta and len(df) >= 121:
        try:
            prev_result = calculate_precise_ai_score(
                df.iloc[:-1],
                {},
                fund,
                ticker=ticker,
                debug_log=False,
                _compute_delta=False,
            )
            score_delta = int(total - prev_result["total_score"])
        except Exception:
            score_delta = None

    # ── 雙情境行動指引：評分 + 短線訊號綜合判定 ──
    advice_no_position, advice_has_position, note_text = decide_action_advice(df, {}, total, debug_log=debug_log)
    action_advice = advice_no_position  # 相容舊欄位：預設顯示未持股視角

    # ── Debug Log：輸出各子項目得分，便於確認是資料傳錯還是算法過苛 ──
    if debug_log:
        _debug_print(f"[Technical Score] {ticker} {total}/60; chip data excluded")

    return {
        "total_score": total,
        "score_max": 60,
        "model_version": "technical-real-v1",
        "coverage": "僅技術面；籌碼及基本面未計分",

        "signal_type": signal_type,
        "action_advice": action_advice,
        "advice_no_position": advice_no_position,
        "advice_has_position": advice_has_position,
        "note_text": note_text,
        "score_breakdown": {
            "trend_60d": round(float(trend_score), 2),
            "chip_20d": None,
            "momentum_7d": round(float(momentum_score), 2),
        },
        "score_delta": score_delta,
        "chip_turn": chip_turn,
    }


def get_global_precise_diagnosis(
    ticker: str,
    df: pd.DataFrame,
    chip: dict,
    fund: dict,
    debug_log: bool = True,
) -> dict:
    """全站唯一的權威 AI 診斷函式，同時輸出分數、燈號與各維度解析文字。"""
    score_result = calculate_precise_ai_score(df, chip, fund, ticker=ticker, debug_log=debug_log)
    score = score_result["total_score"]
    signal_type = score_result["signal_type"]
    action_advice = score_result["action_advice"]
    advice_no_position = score_result["advice_no_position"]
    advice_has_position = score_result["advice_has_position"]
    note_text = score_result["note_text"]
    score_breakdown = score_result["score_breakdown"]
    score_delta = score_result.get("score_delta")
    chip_turn = score_result.get("chip_turn")

    code = ticker.split(".")[0]
    name = lookup_stock_name(ticker)
    if name == code:
        name = fund.get("industry", code)

    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])
    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last
    rsi_series = compute_rsi(closes, 14)
    rsi_now = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    _, _, hist = compute_macd(closes)
    hist_now = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0
    vol_now = float(df["Volume"].iloc[-1])
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ma20 = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else vol_ma5

    # ── 基本面診斷文字 ──
    eps = fund.get("trailing_eps")
    pe = fund.get("pe_ratio")
    roe = fund.get("roe")
    div_yield = fund.get("dividend_yield")
    eps_desc = f"EPS = {eps:.2f}" if eps is not None else "EPS 暫無資料"
    pe_desc = f"P/E = {pe:.1f}倍" if pe is not None and 10 <= pe <= 25 else \
              f"P/E = {pe:.1f}倍" if pe is not None and pe > 25 else \
              f"P/E = {pe:.1f}倍" if pe is not None else "P/E 暫無資料"
    roe_desc = f"ROE = {roe * 100:.1f}%" if roe is not None else "ROE 暫無資料"
    # 【BUG 修正】yfinance 的 dividendYield 已是「百分點」數值（1.18 = 1.18%），
    # 舊碼誤再乘 100 造成「殖利率 = 118.00%」；並對不可能的異常值（>50%）防呆不顯示。
    if div_yield is not None and 0 < div_yield <= 50:
        div_desc = f"殖利率 = {div_yield:.2f}%"
    else:
        div_desc = ""
    fundamental_text = f"{eps_desc}｜{pe_desc}｜{roe_desc}" + (f"｜{div_desc}" if div_desc else "")

    # ── 技術面診斷文字 ──
    if last > ma20 and ma20 > ma60:
        ma_text = f"多頭排列：股價 ({last:.2f}) 站穩 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之上"
    elif last > ma20 and ma20 <= ma60:
        ma_text = f"反彈格局：股價 ({last:.2f}) 站上 20MA ({ma20:.2f})，但 20MA 仍低於 60MA ({ma60:.2f})"
    elif last < ma20 and last < ma60:
        ma_text = f"空頭排列：股價 ({last:.2f}) 跌破 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之下"
    else:
        ma_text = f"均線糾結：股價 ({last:.2f}) 在 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之間震盪"

    rsi_text = f"RSI(14) = {rsi_now:.1f}" + (
        "，處於 70 以上過熱區" if rsi_now >= 70 else
        "，處於 50~70 偏多區" if rsi_now > 50 else
        "，處於 40~50 觀望中性區" if rsi_now >= 40 else
        "，處於 40 以下偏空區"
    )

    if hist_now > 0:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，多方動能"
    else:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，空方動能"

    # ── 量能與籌碼文字 ──
    vol_ratio_20 = vol_now / vol_ma20 if vol_ma20 else 1.0
    if vol_ratio_20 >= 1.5:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能放大（無法據此判定大戶進場）"
    elif vol_ratio_20 >= 1.2:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能溫和放大"
    elif vol_ratio_20 <= 0.6:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，明顯縮量"
    else:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能平穩"

    market_cap = fund.get("market_cap")
    if market_cap is not None and market_cap >= 200_000_000_000:
        cap_text = f"市值 {market_cap / 1e12:.1f} 兆，屬大型市值股票"
    elif market_cap is not None:
        cap_text = f"市值 {market_cap / 1e9:.0f} 億"
    else:
        cap_text = ""
    if cap_text:
        vol_text += f"｜{cap_text}"

    return {
        "ticker": ticker,
        "code": code,
        "name": name,
        "score": score,
        "score_max": score_result["score_max"],
        "model_version": score_result["model_version"],
        "coverage": score_result["coverage"],
        "signal_type": signal_type,
        "action_advice": action_advice,
        "advice_no_position": advice_no_position,
        "advice_has_position": advice_has_position,
        "note_text": note_text,
        "score_breakdown": score_breakdown,
        "score_delta": score_delta,
        "chip_turn": chip_turn,
        "fundamental_text": fundamental_text,
        "ma_text": ma_text,
        "rsi_text": rsi_text,
        "macd_text": macd_text,
        "vol_text": vol_text,
    }


def get_cached_ai_diagnosis(ticker: str, df: pd.DataFrame, chip: dict, fund: dict) -> dict:
    # DataFrame attrs may not be included in Streamlit's hash. Validate before
    # reading the cache, and explicitly include provenance in its key.
    validate_scoring_frame(df)
    provenance = (df.attrs.get("price_source"), df.attrs.get("is_stale"), df.attrs.get("fetched_at"))
    return _cached_verified_diagnosis(ticker, df, chip, fund, provenance)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_verified_diagnosis(ticker, df, chip, fund, provenance):
    return get_global_precise_diagnosis(ticker, df, chip, fund)


def render_ultimate_diagnosis_card(diag: dict) -> None:
    """渲染 AI 多重週期滑動評分診斷卡片。"""
    score = diag["score"]
    signal_type = diag.get("signal_type", "")
    score_breakdown = diag.get("score_breakdown", {})

    icon = '🟢' if score >= 45 else ('🟡' if score >= 36 else '🔴')

    # 【門檻調整】卡片配色同步放寬：>=75 綠、>=60 黃、其餘紅
    if score >= 45:
        color = "#10B981"
        glow = "rgba(16,185,129,0.40)"
    elif score >= 36:
        color = "#F59E0B"
        glow = "rgba(245,158,11,0.35)"
    else:
        color = "#EF4444"
        glow = "rgba(239,68,68,0.40)"
    status_color = color

    trend_60d = score_breakdown.get("trend_60d", 0)
    momentum_7d = score_breakdown.get("momentum_7d", 0)

    # ── 與前一日比較：分數變化（+ 綠字 / - 紅字 / 0 或無資料 灰字）──
    score_delta = diag.get("score_delta")
    if score_delta is None:
        delta_html = '<span style="color:#9CA3AF;">較前日 --</span>'
    elif score_delta > 0:
        delta_html = f'<span style="color:#10B981;font-weight:800;">較前日 +{score_delta} 分</span>'
    elif score_delta < 0:
        delta_html = f'<span style="color:#EF4444;font-weight:800;">較前日 {score_delta} 分</span>'
    else:
        delta_html = '<span style="color:#9CA3AF;">較前日 ±0 分</span>'

    # ── 主力籌碼轉折標籤 Badge ──
    chip_turn = diag.get("chip_turn")
    if chip_turn:
        turn_color = "#10B981" if "由賣轉買" in chip_turn else "#F59E0B"
        turn_html = (
            f'<span style="background:{turn_color}26;color:{turn_color};'
            f'padding:2px 10px;border-radius:999px;font-size:0.76rem;'
            f'font-weight:800;margin-left:8px;white-space:nowrap;">{chip_turn}</span>'
        )
    else:
        turn_html = ""

    st.markdown(
        f"""
<div class="ai-diagnosis-card" style="border-left:5px solid {color};
    box-shadow:0 0 22px {glow}, 0 6px 18px rgba(0,0,0,0.30);">

  <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
    <span style="font-size:1.8rem;">{icon}</span>
    <div>
      <div style="font-size:1.35rem;font-weight:900;color:{color};letter-spacing:1px;">真實行情技術評分：{signal_type}</div>
      <div style="color:#9CA3AF;font-size:0.82rem;margin-top:2px;">技術評分 {score} / 60（不是勝率）　｜　{delta_html}{turn_html}</div>
      <div style="color:#9CA3AF;font-size:0.82rem;margin-top:2px;">60日趨勢({trend_60d:.0f}/30) + 7日動能({momentum_7d:.0f}/30)；籌碼與基本面未計分</div>
    </div>
  </div>

  <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px;">
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #10B981;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📈 60日波段趨勢 ({trend_60d:.0f}/30)</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("ma_text", "均線數據解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #3B82F6;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📦 真實成交量（不代表法人或主力）</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("vol_text", "成交量解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #F59E0B;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">🚀 7日爆發動能 ({momentum_7d:.0f}/30)</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("rsi_text", "RSI 指標解析中...")}｜{diag.get("macd_text", "MACD 動能解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #A78BFA;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📊 基本面輔助</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("fundamental_text", "基本面數據解析中...")}</div>
    </div>
  </div>

  <div style="color:#6B7280;font-size:0.75rem;margin-top:10px;text-align:right;">
    技術評分：60日趨勢(30) + 7日動能(30)；尚未回測校準　|　僅供參考
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown(f"""
<div style="background-color: rgba(255, 255, 255, 0.05); padding: 12px 16px; border-radius: 8px; margin-top: 12px; border-left: 4px solid {status_color};">
    <div style="font-size: 15px; font-weight: 600; color: #ffffff; margin-bottom: 6px;">
        🛒 <span style="color: #a0a0a0; font-weight: 400;">未持股建議：</span>{diag.get('advice_no_position', diag.get('action_advice'))}
    </div>
    <div style="font-size: 15px; font-weight: 600; color: #ffffff;">
        💰 <span style="color: #a0a0a0; font-weight: 400;">已有持股建議：</span>{diag.get('advice_has_position', '無建議')}
    </div>
</div>
""", unsafe_allow_html=True)

    note_text = diag.get("note_text")
    if note_text:
        st.markdown(f"""
<div style="background:rgba(59,130,246,0.08);border-radius:8px;padding:10px 14px;margin-top:10px;border-left:3px solid #3B82F6;">
  <div style="font-size:0.88rem;color:#93C5FD;line-height:1.55;">{note_text}</div>
</div>
""", unsafe_allow_html=True)

    # ── 數據剖析展開區 ──
    with st.expander("📊 診斷原因與數據剖析", expanded=False):
        expander_css = (
            "background:rgba(255,255,255,0.06);border-radius:8px;"
            "padding:10px 12px;border-left:3px solid {border_c};"
            "margin-bottom:8px;"
        )
        detail_items = [
            ("📊 基本面體質", "#3B82F6", diag.get("fundamental_text", "基本面數據解析中...")),
            ("📈 均線排列 (MA)", "#60A5FA", diag.get("ma_text", "均線數據解析中...")),
            ("💪 RSI 動能指標", "#A78BFA", diag.get("rsi_text", "RSI 指標解析中...")),
            ("📊 MACD 柱狀體", "#34D399", diag.get("macd_text", "MACD 動能解析中...")),
            ("📦 成交量", "#FBBF24", diag.get("vol_text", "成交量解析中...")),
        ]
        grid_html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">'
        for title, border_c, body in detail_items:
            grid_html += (
                f'<div style="{expander_css.format(border_c=border_c)}">'
                f'<div style="font-size:0.78rem;color:{border_c};font-weight:700;margin-bottom:4px;">{title}</div>'
                f'<div style="font-size:0.85rem;color:#E5E7EB;line-height:1.55;">{body}</div>'
                f'</div>'
            )
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)


# ====================== 【Debug】找尋綠燈股票掃描 ======================
_DEBUG_WATCHLIST: list[tuple[str, str]] = [
    ("台積電", "2330.TW"),
    ("鴻海", "2317.TW"),
    ("聯發科", "2454.TW"),
    ("廣達", "2382.TW"),
    ("緯創", "3231.TW"),
]


def debug_find_green_light_stocks() -> list[dict]:
    """【Debug】掃描熱門標的最新總分與燈號，輸出至終端機並回傳結果清單。

    綠燈標準：技術分數 >= 45 / 60。
    單檔執行：python frontend/components/ai_diagnosis.py
    """
    from backend.services.stock_fetcher import fetch_stock_data, get_chip_data
    from backend.services.stock_master import get_fundamental

    results: list[dict] = []
    _debug_print("=" * 64)
    _debug_print("[GreenLight] 開始掃描熱門標的燈號（綠燈標準 >= 75 分）...")
    for name, ticker in _DEBUG_WATCHLIST:
        try:
            df, _source = fetch_stock_data(ticker)
            if df is None or len(df) < 30:
                _debug_print(f"[GreenLight] ⚠️ {name} ({ticker})　資料不足，略過")
                continue
            chip = get_chip_data(ticker)
            fund = get_fundamental(ticker)
            diag = get_global_precise_diagnosis(ticker, df, chip, fund, debug_log=False)
            results.append(diag)
            mark = "🟢" if diag["score"] >= 45 else ("🟡" if diag["score"] >= 36 else "🔴")
            _debug_print(
                f"[GreenLight] {mark} {name} ({diag['code']})　技術分數 {diag['score']}/60　"
                f"{diag['signal_type']}｜未持股 {diag['advice_no_position']}｜持股 {diag['advice_has_position']}"
            )
        except Exception as exc:
            _debug_print(f"[GreenLight] ⚠️ {name} ({ticker})　計算失敗：{exc}")

    greens = [r for r in results if r["score"] >= 75]
    if greens:
        summary = "、".join(f"{r['name']}({r['code']}) {r['score']}分" for r in greens)
        _debug_print(f"[GreenLight] 🟢 達到綠燈標準共 {len(greens)} 支：{summary}")
    else:
        _debug_print("[GreenLight] 目前沒有達到綠燈標準（>=45 / 60 分）的熱門標的")
    return results


if __name__ == "__main__":
    debug_find_green_light_stocks()
