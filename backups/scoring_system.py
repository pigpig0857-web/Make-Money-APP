# -*- coding: utf-8 -*-
"""
AI 綜合評分與排行榜系統（備份）
原始碼位於 app.py，此為完整備份。
待測試結束後可隨時插回 app.py。
"""

import re
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import streamlit as st

from modules.data_fetcher import (
    fetch_stock_data,
    generate_chip_data,
    get_fundamental,
    lookup_stock_name,
)

def _seed(key: str):
    return np.random.default_rng(zlib.crc32(key.encode("utf-8")))


def timeframe_pattern(closes: pd.Series):
    """以均線排列粗略判斷各時間框架型態。"""
    closes = closes.dropna()
    if len(closes) < 5:
        return "資料不足", "warning"
    ma_s = float(closes.rolling(5).mean().iloc[-1])
    ma_m = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else float(closes.mean())
    ma_l = float(closes.rolling(60).mean().iloc[-1]) if len(closes) >= 60 else np.nan
    last = float(closes.iloc[-1])

    if not np.isnan(ma_l):
        if ma_s > ma_m > ma_l:
            return "多頭排列 / 趨勢強勁", "success"
        if ma_s < ma_m < ma_l:
            return "空頭排列 / 趨勢弱勢", "error"
        return "區間震盪 / 高檔洗盤", "warning"
    if last > ma_m:
        return "多頭初升 / 中繼續強", "success"
    return "走勢轉弱 / 觀望", "warning"


def compute_levels(close: float):
    """建議操作價位（相對收盤價換算，僅供參考）。"""
    return {
        "short_low": (round(close * 0.97, 1), round(close * 1.01, 1)),
        "swing_def": (round(close * 0.90, 1), round(close * 0.93, 1)),
        "stop": round(close * 0.88, 1),
    }


def compute_price_strategy(df: pd.DataFrame, close_now: float) -> dict:
    """依近期 20 / 60 日 K 線動態計算買賣價位策略。

    回傳進場區（支撐附近）、建議入場價、目標止盈價、嚴格停損價
    與風報比（盈虧比，理想值需 ≥ 2.0）。
    """
    d20 = df.tail(20)
    d60 = df.tail(60)

    support20 = float(d20["Low"].min()) if len(d20) else close_now
    resistance20 = float(d20["High"].max()) if len(d20) else close_now
    ma20 = float(d20["Close"].mean()) if len(d20) >= 5 else close_now

    # 最低風險進場區：近 20 日支撐位附近 [支撐價位 ~ 支撐價位 * 1.02]
    buy_lo = round(support20, 2)
    buy_hi = round(support20 * 1.02, 2)

    # 建議入場價：拉回至支撐或 MA20 附近分批佈局（限制於進場區內）
    entry = round(max(min(ma20, support20 * 1.02), support20), 2)

    # 嚴格停損價：跌破近 20 日最低點並下浮 3%~5%
    stop_loss = round(support20 * 0.96, 2)

    # 目標止盈價：近 20 日壓力位 或 黃金切割位（取較高者）
    spread = max(resistance20 - support20, 0.01)
    target = round(max(resistance20, support20 + spread * 0.618), 2)

    if target <= entry:
        target = round(entry * 1.08, 2)
    if stop_loss >= entry:
        stop_loss = round(entry * 0.95, 2)

    risk = entry - stop_loss
    reward = target - entry
    rr = reward / risk if risk > 0 else 0.0

    return {
        "buy_lo": buy_lo,
        "buy_hi": buy_hi,
        "entry": entry,
        "target": target,
        "stop_loss": stop_loss,
        "rr": round(rr, 2),
        "reward_pct": reward / entry * 100 if entry else 0.0,
        "risk_pct": risk / entry * 100 if entry else 0.0,
    }


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> tuple:
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    return k, d


def compute_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """計算 MACD（DIF、MACD 柱狀體、訊號線）。"""
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    macd_bar = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - macd_bar
    return dif, macd_bar, hist


def compute_ultimate_diagnosis(df: pd.DataFrame) -> dict:
    """雙維度矩陣：中長線趨勢 × 短線動能 → 四大綜合診斷結論。

    維度一 — 中長線趨勢 (trend)：判斷 20MA、60MA 與當前股價排列。
    維度二 — 短線動能 (momentum)：判斷 RSI(14) 位階與 MACD 柱狀體狀態。

    回傳 dict 含：
      trend / momentum / verdict / emoji / label / color / advice /
      ma20 / ma60 / rsi / macd_hist / vol_ratio / vol_change_pct
    """
    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])

    # ---- 均線 ----
    ma5 = float(df["MA5"].iloc[-1]) if not pd.isna(df["MA5"].iloc[-1]) else last
    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last

    # ---- RSI ----
    rsi_series = compute_rsi(closes, 14)
    rsi_now = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

    # ---- MACD ----
    dif, macd_bar, hist = compute_macd(closes)
    dif_now = float(dif.iloc[-1]) if not pd.isna(dif.iloc[-1]) else 0.0
    dif_prev = float(dif.iloc[-2]) if len(dif) >= 2 and not pd.isna(dif.iloc[-2]) else dif_now
    bar_now = float(macd_bar.iloc[-1]) if not pd.isna(macd_bar.iloc[-1]) else 0.0
    bar_prev = float(macd_bar.iloc[-2]) if len(macd_bar) >= 2 and not pd.isna(macd_bar.iloc[-2]) else bar_now
    hist_now = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0
    hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 and not pd.isna(hist.iloc[-2]) else hist_now

    golden_cross = dif_prev <= bar_prev and dif_now > bar_now
    death_cross = dif_prev >= bar_prev and dif_now < bar_now

    # ---- 成交量 ----
    vol_now = float(df["Volume"].iloc[-1])
    vol_prev = float(df["Volume"].iloc[-2]) if len(df) >= 2 else vol_now
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ratio = vol_now / vol_ma5 if vol_ma5 else 1.0
    vol_change_pct = (vol_now / vol_prev - 1) * 100 if vol_prev else 0.0

    # ================================================================
    #  維度一：中長線趨勢
    # ================================================================
    above_ma20 = last > ma20
    ma20_above_ma60 = ma20 > ma60
    below_ma20 = last < ma20
    ma20_below_ma60 = ma20 < ma60

    if above_ma20 and ma20_above_ma60:
        trend = "bull"
        trend_label = "多頭趨勢"
    elif below_ma20 and ma20_below_ma60:
        trend = "bear"
        trend_label = "空頭趨勢"
    else:
        trend = "neutral"
        trend_label = "震盪格局"

    # ================================================================
    #  維度二：短線動能
    # ================================================================
    rsi_overbought = rsi_now >= 70
    rsi_bullish = 50 < rsi_now < 70
    rsi_neutral = 40 <= rsi_now <= 50
    rsi_bearish = rsi_now < 40
    hist_positive = hist_now > 0
    hist_increasing = hist_now > hist_prev

    if (rsi_bullish or rsi_overbought) and hist_positive and (golden_cross or hist_increasing):
        momentum = "bull"
        momentum_label = "偏多動能"
    elif rsi_bearish and (death_cross or (not hist_positive and not hist_increasing)):
        momentum = "bear"
        momentum_label = "偏空動能"
    else:
        momentum = "neutral"
        momentum_label = "觀望中性"

    # ================================================================
    #  四大綜合診斷矩陣
    # ================================================================
    if trend == "bull" and momentum == "bull":
        verdict = "bull_bull"
        emoji = "🟢"
        label = "多頭主升段"
        advice = "中短線方向一致，建議順勢持股續抱或逢拉回分批佈局。"
        color = "#10B981"
    elif trend == "bull" and momentum != "bull":
        verdict = "bull_other"
        emoji = "🟡"
        label = "中多短洗（高檔震盪）"
        advice = "中長線趨勢保護短線，當前屬高檔洗盤，建議靜待拉回支撐位再行觀望佈局。"
        color = "#F59E0B"
    elif trend == "bear" and momentum == "bull":
        verdict = "bear_bull"
        emoji = "🟠"
        label = "弱勢反彈"
        advice = "中長線趨勢仍偏空，短線屬技術性反彈，留意上方均線反壓，不宜盲目追高。"
        color = "#F97316"
    else:
        verdict = "bear_bear"
        emoji = "🔴"
        label = "空頭防守"
        advice = "趨勢與動能雙弱，建議空手觀望為主，並嚴格執行停損防線。"
        color = "#EF4444"

    # ---- 均線排列文字 ----
    if above_ma20 and ma20_above_ma60:
        ma_text = f"多頭排列：股價 ({last:.2f}) 站穩 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之上"
    elif below_ma20 and ma20_below_ma60:
        ma_text = f"空頭排列：股價 ({last:.2f}) 跌破 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之下"
    elif above_ma20 and not ma20_above_ma60:
        ma_text = f"股價 ({last:.2f}) 站上 20MA ({ma20:.2f})，但 20MA 仍低於 60MA ({ma60:.2f})，趨勢未完全轉多"
    elif not above_ma20 and ma20_above_ma60:
        ma_text = f"股價 ({last:.2f}) 跌破 20MA ({ma20:.2f})，但 20MA 仍在 60MA ({ma60:.2f}) 之上，留意回測支撐"
    else:
        ma_text = f"均線糾結：股價 ({last:.2f}) 在 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之間震盪"

    # ---- RSI 文字 ----
    if rsi_overbought:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 70 以上過熱區，短線慎防拉回修正"
    elif rsi_bullish:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 50~70 偏多區，短線多方動能尚存"
    elif rsi_neutral:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 40~50 觀望中性區"
    elif rsi_bearish:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 40 以下偏空區，短線動能偏弱"
    else:
        rsi_text = f"RSI(14) = {rsi_now:.1f}"

    # ---- MACD 文字 ----
    if golden_cross:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，剛出現黃金交叉，短線動能轉多"
    elif death_cross:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，剛出現死亡交叉，短線動能轉空"
    elif hist_now > 0 and hist_increasing:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體翻正且放大，多頭動能增強"
    elif hist_now > 0:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體為正但縮小，多頭動能趨緩"
    elif hist_now < 0 and hist_increasing:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體為負但收斂，空頭動能減弱"
    elif hist_now < 0:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體為負且擴大，空頭動能增強"
    else:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}"

    # ---- 成交量文字 ----
    if vol_ratio >= 1.5:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，屬於放量表態"
    elif vol_ratio >= 1.1:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，量能溫和放大"
    elif vol_ratio <= 0.6:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，屬於明顯縮量"
    elif vol_ratio <= 0.9:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，屬於縮量震盪"
    else:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，量能平穩"

    return {
        "trend": trend,
        "trend_label": trend_label,
        "momentum": momentum,
        "momentum_label": momentum_label,
        "verdict": verdict,
        "emoji": emoji,
        "label": label,
        "color": color,
        "advice": advice,
        "last": last,
        "ma20": ma20,
        "ma60": ma60,
        "rsi": rsi_now,
        "macd_hist": hist_now,
        "macd_cross": "golden" if golden_cross else ("death" if death_cross else "none"),
        "vol_ratio": vol_ratio,
        "vol_change_pct": vol_change_pct,
        "ma_text": ma_text,
        "rsi_text": rsi_text,
        "macd_text": macd_text,
        "vol_text": vol_text,
    }


def render_ultimate_diagnosis_card(diag: dict, df: pd.DataFrame) -> None:
    """渲染 AI 三維度綜合診斷卡片：基本面 + 技術面 + 籌碼關注度。"""
    score = diag["score"]
    icon = diag["icon"]
    status = diag["status"]

    if score >= 80:
        color = "#10B981"
        glow = "rgba(16,185,129,0.40)"
    elif score >= 50:
        color = "#F59E0B"
        glow = "rgba(245,158,11,0.35)"
    else:
        color = "#EF4444"
        glow = "rgba(239,68,68,0.40)"

    st.markdown(
        f"""
<div class="ai-diagnosis-card" style="border-left:5px solid {color};
    box-shadow:0 0 22px {glow}, 0 6px 18px rgba(0,0,0,0.30);">

  <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
    <span style="font-size:1.8rem;">{icon}</span>
    <div>
      <div style="font-size:1.35rem;font-weight:900;color:{color};letter-spacing:1px;">AI 三維度綜合診斷：{status}</div>
      <div style="color:#9CA3AF;font-size:0.82rem;margin-top:2px;">綜合評分 {score} / 100　｜　基本面(30) + 技術面(50) + 籌碼關注度(20)</div>
    </div>
  </div>

  <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px;">
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #3B82F6;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📊 基本面體質</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("fundamental_text", "基本面數據解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #60A5FA;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📈 技術面趨勢</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("ma_text", "均線數據解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #A78BFA;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">💪 動能與指標</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("rsi_text", "RSI 指標解析中...")}｜{diag.get("macd_text", "MACD 動能解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #FBBF24;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📦 量能與籌碼</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("vol_text", "成交量解析中...")}</div>
    </div>
  </div>

  <div style="color:#6B7280;font-size:0.75rem;margin-top:10px;text-align:right;">
    三維度加權評分：基本面(30) + 技術面(50) + 籌碼關注度(20)　|　僅供參考
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

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
            ("📦 成交量與籌碼", "#FBBF24", diag.get("vol_text", "成交量解析中...")),
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


def calculate_precise_ai_score(df: pd.DataFrame, chip: dict, fund: dict) -> tuple[int, str, str]:
    """三維度全方位綜合評分（總分 100 分）：基本面(30) + 技術面(50) + 籌碼關注度(20)。
    回傳 (score, icon, status)。
    """
    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])
    vol_now = float(df["Volume"].iloc[-1])
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ma20 = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else vol_ma5

    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last
    rsi_series = compute_rsi(closes, 14)
    rsi_now = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    _, _, hist = compute_macd(closes)
    hist_now = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0

    # ══════════════════════════════════════════════════════════
    #  維度一：基本面 (Fundamental) — 權重 30 分
    # ══════════════════════════════════════════════════════════
    fund_score = 0

    # EPS & 獲利能力 (10分)
    eps = fund.get("trailing_eps")
    if eps is not None and eps > 0:
        fund_score += 10

    # 本益比 P/E Ratio (10分)
    pe = fund.get("pe_ratio")
    if pe is not None and 10 <= pe <= 25:
        fund_score += 10

    # ROE / 殖利率 (10分)
    roe = fund.get("roe")
    div_yield = fund.get("dividend_yield")
    roe_ok = roe is not None and roe > 0.10
    div_ok = div_yield is not None and div_yield > 0.04
    if roe_ok or div_ok:
        fund_score += 10

    # ══════════════════════════════════════════════════════════
    #  維度二：技術面 (Technical) — 權重 50 分
    # ══════════════════════════════════════════════════════════
    tech_score = 0

    # MA 趨勢 (20分)
    if last > ma20 and ma20 > ma60:
        tech_score += 20
    elif last > ma20 and ma20 <= ma60:
        tech_score += 10
    elif last < ma20 and last < ma60:
        tech_score -= 10

    # 動能指標 (20分)
    if hist_now > 0:
        tech_score += 10
    if 50 < rsi_now < 70:
        tech_score += 10
    elif rsi_now > 70:
        tech_score += 5

    # 成交量與支撐壓力 (10分)
    support = float(df["Close"].tail(20).min())
    vol_above_ma5 = vol_now > vol_ma5
    above_support = last > support
    if vol_above_ma5 and above_support:
        tech_score += 10
    elif above_support:
        tech_score += 5

    # ══════════════════════════════════════════════════════════
    #  維度三：籌碼與關注度 (Flow & Interest) — 權重 20 分
    # ══════════════════════════════════════════════════════════
    flow_score = 0

    # 量能爆發度 (10分)：當日成交量 > 20日均量 1.5 倍
    if vol_ma20 > 0 and vol_now > vol_ma20 * 1.5:
        flow_score += 10
    elif vol_ma20 > 0 and vol_now > vol_ma20 * 1.2:
        flow_score += 5

    # 市值大戶保護力 (10分)：大型權值股/核心 ETF
    market_cap = fund.get("market_cap")
    if market_cap is not None and market_cap >= 200_000_000_000:
        flow_score += 10

    # ══════════════════════════════════════════════════════════
    #  總分 = 基本面 + 技術面 + 籌碼關注度
    # ══════════════════════════════════════════════════════════
    total = fund_score + tech_score + flow_score
    total = int(np.clip(total, 0, 100))

    if total >= 80:
        return total, "🟢", "多頭主升 (基本面+技術面極佳)"
    elif total >= 50:
        return total, "🟡", "震盪整理 (指標分歧/觀望)"
    else:
        return total, "🔴", "空頭防守 (趨勢偏弱)"


# ====================== 權威統一診斷（全站唯一來源） ======================
def get_global_precise_diagnosis(ticker: str, df: pd.DataFrame, chip: dict, fund: dict) -> dict:
    """全站唯一的權威 AI 診斷函式，同時輸出分數、燈號、狀態標籤。
    左邊排行榜與右邊詳細診斷卡片，一律呼叫此函式，確保 100% 同步。
    """
    score, icon, status = calculate_precise_ai_score(df, chip, fund)
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
    vol_ratio = vol_now / vol_ma5 if vol_ma5 else 1.0
    vol_prev = float(df["Volume"].iloc[-2]) if len(df) >= 2 else vol_now
    vol_change_pct = (vol_now / vol_prev - 1) * 100 if vol_prev else 0.0

    # ── 基本面診斷文字 ──
    eps = fund.get("trailing_eps")
    pe = fund.get("pe_ratio")
    roe = fund.get("roe")
    div_yield = fund.get("dividend_yield")
    eps_desc = f"EPS = {eps:.2f}" if eps is not None else "EPS 暫無資料"
    pe_desc = f"P/E = {pe:.1f}倍（合理）" if pe is not None and 10 <= pe <= 25 else \
              f"P/E = {pe:.1f}倍（偏高）" if pe is not None and pe > 25 else \
              f"P/E = {pe:.1f}倍" if pe is not None else "P/E 暫無資料"
    roe_desc = f"ROE = {roe * 100:.1f}%" if roe is not None else "ROE 暫無資料"
    div_desc = f"殖利率 = {div_yield * 100:.2f}%" if div_yield is not None else ""
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
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能爆發（大戶進場關照）"
    elif vol_ratio_20 >= 1.2:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能溫和放大"
    elif vol_ratio_20 <= 0.6:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，明顯縮量"
    else:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能平穩"

    market_cap = fund.get("market_cap")
    if market_cap is not None and market_cap >= 200_000_000_000:
        cap_text = f"市值 {market_cap / 1e12:.1f} 兆，屬大型權值股（大戶保護力強）"
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
        "icon": icon,
        "status": status,
        "fundamental_text": fundamental_text,
        "ma_text": ma_text,
        "rsi_text": rsi_text,
        "macd_text": macd_text,
        "vol_text": vol_text,
    }


# ====================== AI 高分飆股排行榜（100 檔精選池） ======================
_TOP100_TICKERS: list[str] = [
    # ── 半導體 ──
    "2330.TW", "2303.TW", "2454.TW", "3711.TW", "2379.TW", "3034.TW",
    "2408.TW", "2344.TW", "2337.TW", "3443.TW", "6770.TW", "8046.TW",
    "6239.TW", "3661.TW", "5269.TW", "5274.TW", "6547.TW", "3653.TW",
    "6669.TW", "3037.TW", "2327.TW", "3017.TW", "2368.TW", "6182.TW",
    "3293.TWO", "8069.TWO", "6223.TWO", "3533.TW",
    # ── AI 伺服器 / 散熱 / ODM ──
    "2382.TW", "3231.TW", "2376.TW", "2357.TW", "2356.TW", "2353.TW",
    "4938.TW", "2301.TW", "2377.TW", "2395.TW", "3044.TW", "3596.TW",
    "2345.TW", "2388.TW", "3293.TWO", "3045.TW", "4958.TW",
    # ── 面板 / 光電 ──
    "3481.TW", "2409.TW", "6116.TW", "3008.TW", "3406.TW", "2474.TW",
    # ── 電子零組件 / 連接器 ──
    "2324.TW", "2308.TW", "2327.TW", "2308.TW", "2301.TW", "2449.TW",
    "2458.TW", "3702.TW", "8112.TW", "6271.TW", "6285.TW",
    # ── 金融 ──
    "2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW", "2892.TW",
    "2880.TW", "2885.TW", "2890.TW", "2887.TW", "2883.TW", "2801.TW",
    "5880.TW", "5876.TW", "2892.TW", "2888.TW",
    # ── 航運 ──
    "2603.TW", "2609.TW", "2615.TW", "2618.TW", "2610.TW", "2637.TW",
    # ── 傳產 / 重電 / 水泥 / 鋼鐵 ──
    "1101.TW", "1301.TW", "1303.TW", "2002.TW", "1504.TW", "1605.TW",
    "9904.TW", "9910.TW", "9921.TW", "2207.TW", "1216.TW",
    # ── 電信 / 軟體 ──
    "2412.TW", "4904.TW", "3045.TW",
    # ── 高股息 ETF ──
    "0050.TW", "0056.TW", "00878.TW", "00919.TW", "00929.TW",
    "00940.TW", "006208.TW", "00692.TW", "00881.TW", "00713.TW",
]
# 去重
_TOP100_TICKERS = list(dict.fromkeys(_TOP100_TICKERS))


def _compute_one_stock(ticker: str) -> dict | None:
    """為單一股票計算統一的 AI 診斷（分數 + 燈號 + 診斷）。"""
    try:
        df, _ = fetch_stock_data(ticker)
        if df is None or len(df) < 30:
            return None
        chip = generate_chip_data(ticker)
        fund = get_fundamental(ticker)
        return get_global_precise_diagnosis(ticker, df, chip, fund)
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
@st.cache_data(ttl=86400, show_spinner=False)
def get_top_ranked_stocks() -> list[dict]:
    """並行計算 100 檔精選台股的 AI 綜合評分，回傳由高至低排序的排行榜（快取 24 小時）。"""
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_compute_one_stock, t): t for t in _TOP100_TICKERS}
        for future in as_completed(futures):
            r = future.result()
            if r is not None:
                results.append(r)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def render_ai_scoreboard() -> None:
    """在側邊欄渲染 AI 高分飆股排行榜（前 15 名）。"""
    st.markdown("**🏆 AI 高分選股排行榜**")

    if st.button("🔄 立即更新排行榜", key="btn_refresh_rank", use_container_width=True):
        get_top_ranked_stocks.clear()
        st.rerun()

    scoreboard = get_top_ranked_stocks()
    if not scoreboard:
        st.caption("暫無排行榜資料")
        return
    for item in scoreboard[:15]:
        label = f"【{item['score']}分 {item['icon']}】{item['name']} ({item['code']})"
        if st.button(label, key=f"rank_{item['ticker']}", use_container_width=True):
            st.session_state["selected_stock"] = item["ticker"]
            st.session_state["sidebar_key"] += 1
            force_scroll_to_top()
            st.rerun()


def streak_text(n: int, who: str) -> str:
    if n > 0:
        return f"{who} 連買 {n} 日"
    if n < 0:
        return f"{who} 連賣 {-n} 日"
    return f"{who} 買賣持平"


def render_pill(text: str, level: str):
    bg = {"success": "#dcfce7", "warning": "#fef3c7", "error": "#fee2e2"}[level]
    fg = {"success": "#15803d", "warning": "#b45309", "error": "#b91c1c"}[level]
    st.markdown(
        f'<span style="background:{bg};color:{fg};padding:4px 12px;border-radius:999px;'
        f'font-weight:700;">{text}</span>',
        unsafe_allow_html=True,
    )


def cycle_level(cycle: str) -> str:
    if cycle in ("谷底復甦", "擴張成長"):
        return "success"
    if cycle in ("暴衝期", "高峰期"):
        return "warning"
    return "error"
