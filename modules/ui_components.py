# -*- coding: utf-8 -*-
"""
UI 元件模組
包含 K 線圖繪製、技術指標計算、市場狀態判斷、置頂腳本注入、
側邊搜尋與熱門推薦按鈕元件等。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from modules.data_fetcher import _download_daily, resample_kline


def is_mobile_request() -> bool:
    """以瀏覽器 User-Agent 偵測手機 / 平板，供手機端 UI 最佳化使用。

    偵測不到（bare mode / 舊版）時一律回傳 False，確保電腦端行為不變。
    """
    try:
        headers = st.context.headers
        ua = headers.get("User-Agent", "") if hasattr(headers, "get") else ""
        if not ua and hasattr(headers, "to_dict"):
            ua = headers.to_dict().get("User-Agent", "")
    except Exception:
        return False
    ua = (ua or "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone", "ipad"))


TAIWAN_TZ = ZoneInfo("Asia/Taipei")
AUTORUN_INTERVAL_SECONDS = 15


def force_scroll_to_top():
    """標記需要強制置頂，由主內容區注入點執行 MutationObserver 滾動重置。"""
    st.session_state["scroll_to_top"] = True


def get_market_status() -> dict:
    """判斷台股盤態：週一至週五 09:00-13:30（Asia/Taipei）為開盤中。"""
    now = datetime.now(TAIWAN_TZ)
    is_weekday = now.weekday() < 5
    minutes = now.hour * 60 + now.minute
    in_session = 9 * 60 <= minutes <= 13 * 60 + 30
    return {
        "is_open": is_weekday and in_session,
        "now": now,
        "is_weekday": is_weekday,
        "in_session": in_session,
    }


def setup_autorun(is_open: bool) -> None:
    """開盤期間自動刷新頁面；閉盤或假日停用，避免不必要的 API 請求與效能消耗。

    註：PyPI 上並無 `streamlit-autorun` 套件，此處改用社群標準的
    `streamlit-autorefresh`（interval 單位為毫秒）；若元件載入失敗則退回
    HTML meta refresh 方案。
    """
    if not is_open:
        return
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=AUTORUN_INTERVAL_SECONDS * 1000, key="market_autorun")
    except Exception:
        st.markdown(
            f'<meta http-equiv="refresh" content="{AUTORUN_INTERVAL_SECONDS}">',
            unsafe_allow_html=True,
        )


def render_market_badge(status: dict) -> None:
    now = status["now"]
    if status["is_open"]:
        st.markdown(
            '<div style="background:#dcfce7;color:#15803d;padding:10px 16px;border-radius:10px;'
            'font-weight:800;text-align:center;font-size:1.05rem;">'
            "🟢 市場狀態：台股開盤中（即時價格更新）</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"台灣時間 {now:%Y-%m-%d %H:%M:%S}　|　自動刷新：每 {AUTORUN_INTERVAL_SECONDS} 秒"
            "（yfinance 1 分鐘級即時報價 + 日 K 動態更新）"
        )
    else:
        st.markdown(
            '<div style="background:#e5e7eb;color:#374151;padding:10px 16px;border-radius:10px;'
            'font-weight:800;text-align:center;font-size:1.05rem;">'
            "🔴 市場狀態：已收盤（顯示盤後最終數據）</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"台灣時間 {now:%Y-%m-%d %H:%M:%S}　|　自動刷新：已停用（非交易時段，節省 API 請求）"
        )


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


def render_key_support_resistance(df: pd.DataFrame, close_now: float) -> None:
    """計算並展示 4 個關鍵支撐 / 壓力 / 風控指標。"""
    d20 = df.tail(20)
    d5 = df.tail(5)

    resistance = float(d20["High"].max()) if len(d20) else close_now
    support = float(d20["Low"].min()) if len(d20) else close_now
    recent_5d_low = float(d5["Low"].min()) if len(d5) else support
    stop_loss = max(support * 0.97, recent_5d_low)

    upside = ((resistance - close_now) / close_now * 100) if close_now else 0
    downside = ((close_now - stop_loss) / close_now * 100) if close_now else 0
    rr_ratio = upside / downside if downside > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("近期壓力位 (Resistance)", f"{resistance:,.1f} 元", f"近 20 日最高價")
    c2.metric("近期支撐位 (Support)", f"{support:,.1f} 元", f"近 20 日最低價")
    c3.metric("建議參考停損價", f"{stop_loss:,.1f} 元", f"支撐位 -3% / 近 5 日低點")
    c4.metric("潛在風險報酬比", f"{rr_ratio:.2f}", f"上行 {upside:+.1f}% / 下行 -{downside:.1f}%", delta_color="off")

    if rr_ratio >= 2.0:
        st.success(f"風報比 {rr_ratio:.2f} 達標（≥ 2.0），從技術面看進場風險相對可控。")
    elif rr_ratio >= 1.0:
        st.info(f"風報比 {rr_ratio:.2f} 尚可（≥ 1.0），需搭配其他訊號綜合判斷。")
    else:
        st.warning(f"風報比 {rr_ratio:.2f} 偏低（< 1.0），短線追高風險較大，建議等待更好進場點。")


def _build_kline_figure(df: pd.DataFrame, period: str = "日 K") -> go.Figure:
    """純函式：將指定週期 K 線資料（df）轉為 Plotly K 線 + 成交量 Figure。

    不含 uirevision / 視角鎖定，這兩者會依呼叫端狀態在「渲染前」再疊加，
    因此本函式可安全地被 @st.cache_data 快取——開盤自動刷新不會重建圖表，
    使用者縮放 / 平移的視角與檢視區域可 100% 定格。
    period：日 K / 週 K / 月 K，用於標題、均線名稱與週末空窗設定。
    """
    closes = df["Close"]
    last = float(closes.iloc[-1])
    chg_pct_series = closes.diff() / closes.shift(1) * 100

    unit = "日" if period == "日 K" else ("週" if period == "週 K" else "月")
    if period == "日 K":
        ma_label = {"MA5": "5日線", "MA20": "20日線(月線)", "MA60": "60日線(季線)"}
    else:
        ma_label = {"MA5": f"5{unit}線", "MA20": f"20{unit}線", "MA60": f"60{unit}線"}

    resistance = float(df["High"].tail(60).max())
    support = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last * 0.95

    customdata = np.column_stack(
        [
            df["Open"].values,
            df["High"].values,
            df["Low"].values,
            df["Close"].values,
            chg_pct_series.fillna(0).values,
            df["MA5"].values,
            df["MA20"].values,
            df["MA60"].values,
        ]
    )

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.7, 0.3],
        subplot_titles=(f"{period} 股價走勢（K 線 + 5 / 20 / 60 均線）", "成交量"),
    )
    fig.update_annotations(font=dict(size=13, color="#374151", family="Microsoft JhengHei"))

    fig.add_trace(
        go.Candlestick(
            x=df["Date"],
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="K 線",
            customdata=customdata,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "開 %{customdata[0]:.2f}　高 %{customdata[1]:.2f}<br>"
                "低 %{customdata[2]:.2f}　收 %{customdata[3]:.2f}<br>"
                "漲跌幅 %{customdata[4]:+.2f}%<br>"
                "MA5 %{customdata[5]:.2f}　MA20 %{customdata[6]:.2f}　MA60 %{customdata[7]:.2f}"
                "<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )
    for col, color in (
        ("MA5", "#f59e0b"),
        ("MA20", "#3b82f6"),
        ("MA60", "#ef4444"),
    ):
        fig.add_trace(
            go.Scatter(
                x=df["Date"],
                y=df[col],
                mode="lines",
                name=ma_label[col],
                line=dict(width=1.4, color=color),
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )

    vol_colors = ["#22c55e" if c >= o else "#ef4444" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(
        go.Bar(
            x=df["Date"],
            y=df["Volume"],
            name="成交量",
            marker_color=vol_colors,
            marker_line_width=0,
            hovertemplate="成交量 %{y:,.0f} 張<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.add_hline(y=resistance, line_dash="dash", line_color="#dc2626", line_width=1.6, row=1, col=1)
    fig.add_hline(y=support, line_dash="dash", line_color="#16a34a", line_width=1.6, row=1, col=1)
    fig.add_annotation(
        x=df["Date"].iloc[-1],
        y=resistance,
        text=f"壓力位 {resistance:.1f}",
        showarrow=False,
        xanchor="right",
        yshift=10,
        font=dict(color="#dc2626", size=14, family="Microsoft JhengHei"),
    )
    fig.add_annotation(
        x=df["Date"].iloc[-1],
        y=support,
        text=f"支撐位 {support:.1f}",
        showarrow=False,
        xanchor="right",
        yshift=-10,
        font=dict(color="#16a34a", size=14, family="Microsoft JhengHei"),
    )

    fig.update_layout(
        height=640,
        hovermode="x",
        hoverlabel=dict(
            bgcolor="white",
            bordercolor="#cbd5e1",
            font=dict(size=12.5, color="#111827", family="Microsoft JhengHei"),
        ),
        xaxis_rangeslider_visible=False,
        dragmode="pan",  # 滑鼠預設互動模式 = 平移（手掌抓取），而非框選縮放
        legend=dict(
            orientation="h",
            x=0,
            xanchor="left",
            y=1.01,
            yanchor="bottom",
            font=dict(size=12.5, color="#1f2937"),
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#d1d5db",
            borderwidth=1,
        ),
        modebar=dict(
            bgcolor="rgba(17,24,39,0.9)",
            color="rgba(249,250,251,0.95)",
            activecolor="#f59e0b",
            orientation="h",
        ),
        margin=dict(l=10, r=10, t=70, b=10),
        template="plotly_white",
    )
    # 週期標題移到圖例正上方的留白區（y=1.0 + yshift 像素定位），與 Legend 垂直堆疊、絕不重疊
    if fig.layout.annotations:
        fig.layout.annotations[0].update(
            y=1.0,
            yanchor="bottom",
            yshift=50,
            bgcolor=None,
            bordercolor=None,
            borderwidth=None,
            borderpad=None,
        )
    fig.update_yaxes(title_text="股價 (NT$)", row=1, col=1, autorange=True)
    fig.update_yaxes(title_text="成交量 (張)", row=2, col=1)
    if period == "日 K":
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], row=1, col=1)
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1, spikecolor="#9ca3af")
    fig.update_yaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1, spikecolor="#9ca3af")
    return fig


@st.cache_data(ttl=300, show_spinner=False)
def get_kline_chart(stock_id: str, period: str = "日 K") -> go.Figure:
    """快取 K 線圖 Figure（快取鍵 = stock_id + period）。

    - 觸發重建條件：stock_id / period 變更、伺服器重啟、或使用者手動按「🔄 更新 K 線數據」
      （內部呼叫 get_kline_chart.clear() 清空全部週期快取）。
    - 開盤期間的自動刷新（每 15 秒）不會重建此圖，因此使用者縮放 / 平移的視角
      與檢視區域 100% 定格，絕不會因即時價格更新而被重置。
    """
    df, _ = _download_daily(stock_id, _PERIOD_HISTORY.get(period, "2y"))
    kdf = resample_kline(df, period)
    return _build_kline_figure(kdf, period)


def render_kline_chart(
    fig: go.Figure,
    ticker: str,
    view_lock: dict | None = None,
    uirevision: str | None = None,
    period: str = "日 K",
) -> None:
    """渲染 K 線圖：在快取的 Figure 上疊加視角狀態後輸出。

    - uirevision（預設 = ticker）：資料刷新時 100% 保留使用者縮放 / 平移視角；
      「切換股票 / 按復原按鈕」時由呼叫端改值，強制 Plotly 重新套用預設視角。
    - view_lock：Session State 雙向座標鎖定。非 None 時強制重送 x/y 軸範圍
      （僅「首次載入 / 切換股票 / 使用者按復原」成立），並同時關閉 autorange；
      為 None 時完全不送 range，純靠 uirevision 保留使用者已調整的視角。
    - 深色高對比水平 Modebar：只保留截圖、區域放大、平移、放大、縮小、重置等常用工具。
    """
    layout_update = {"uirevision": (uirevision if uirevision is not None else ticker)}
    if view_lock is not None:
        layout_update["xaxis_range"] = view_lock["xaxis_range"]
        layout_update["yaxis_range"] = view_lock["yaxis_range"]
    fig.update_layout(**layout_update)
    fig.update_yaxes(autorange=(view_lock is None), row=1, col=1)

    st.markdown(
        """
        <style>
        .js-plotly-plot .modebar {
            background: rgba(17, 24, 39, 0.92) !important;
            border-radius: 8px;
            padding: 3px 5px !important;
        }
        .js-plotly-plot .modebar-btn {
            padding: 4px !important;
        }
        .js-plotly-plot .modebar-btn svg {
            width: 17px !important;
            height: 17px !important;
        }
        .js-plotly-plot .modebar-btn path {
            fill: rgba(249, 250, 251, 0.95) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    mobile = is_mobile_request()
    modebar_remove = [
        "lasso2d",
        "select2d",
        "autoScale2d",
        "toggleSpikelines",
        "hoverClosestCartesian",
        "hoverCompareCartesian",
    ]
    if mobile:
        # 手機端只保留縮放 / 平移 / 重置，移除易誤觸的截圖與其餘工具
        modebar_remove.append("toImage")
    config = {
        "displaylogo": False,
        "responsive": True,
        "scrollZoom": True,
        "displayModeBar": False if mobile else True,
        "showAxisDragHandles": True,
        "modeBarButtonsToRemove": modebar_remove,
    }
    st.plotly_chart(fig, config=config, use_container_width=True, key=f"kline_{ticker}_{period}")


def render_timeframe_cards(df: pd.DataFrame, weekly: pd.DataFrame, monthly: pd.DataFrame) -> None:
    """三時態（天圖 / 週圖 / 月圖）精細化診斷卡片。"""
    closes = df["Close"]
    last = float(closes.iloc[-1])

    # ---- 日線指標 ----
    rsi = compute_rsi(closes)
    kd_k, kd_d = compute_kd(df["High"], df["Low"], closes)
    kd_j = 3 * kd_k - 2 * kd_d
    rsi_now = float(rsi.iloc[-1])
    k_now, d_now, j_now = float(kd_k.iloc[-1]), float(kd_d.iloc[-1]), float(kd_j.iloc[-1])
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ratio = float(df["Volume"].iloc[-1]) / vol_ma5 if vol_ma5 else 1.0
    deduct = float(closes.iloc[-20]) if len(closes) >= 20 else float(closes.mean())

    rsi_label = "過熱(短線慎追高)" if rsi_now >= 70 else ("超賣(留意反彈)" if rsi_now <= 30 else "中性")
    kd_label = "K>D 黃金交叉" if k_now >= d_now else "K<D 死亡交叉"
    j_label = "J 值過熱" if j_now >= 80 else ("J 值超賣" if j_now <= 20 else "J 值正常")
    vol_text = "放量上攻" if vol_ratio >= 1.3 else ("量能萎縮" if vol_ratio <= 0.7 else "量能平穩")

    pattern_daily, lvl_daily = timeframe_pattern(closes)
    pattern_weekly, lvl_weekly = timeframe_pattern(weekly["Close"])
    pattern_monthly, lvl_monthly = timeframe_pattern(monthly["Close"])

    # ---- 週線指標 ----
    wc = weekly["Close"].astype(float)
    wma12 = wc.rolling(12).mean()
    wma12_now = float(wma12.iloc[-1]) if not pd.isna(wma12.iloc[-1]) else float(wc.mean())
    w_bias = (float(wc.iloc[-1]) / wma12_now - 1) * 100 if wma12_now else 0.0
    w_vol5 = float(weekly["Volume"].tail(5).mean())
    w_ratio = float(weekly["Volume"].iloc[-1]) / w_vol5 if w_vol5 else 1.0
    w_turn = float(wc.max()) if len(wc) >= 3 else float(wc.iloc[-1])

    # ---- 月線指標 ----
    mc = monthly["Close"].astype(float)
    n_m = len(mc)
    if n_m >= 12:
        mom = (float(mc.iloc[-1]) / float(mc.iloc[-12]) - 1) * 100
        mom_span = 12
    elif n_m >= 6:
        mom = (float(mc.iloc[-1]) / float(mc.iloc[-6]) - 1) * 100
        mom_span = 6
    elif n_m >= 3:
        mom = (float(mc.iloc[-1]) / float(mc.iloc[0]) - 1) * 100
        mom_span = n_m - 1
    else:
        mom, mom_span = None, None
    m_ma = mc.rolling(12).mean().iloc[-1]
    m_defense = float(m_ma) if not pd.isna(m_ma) else float(mc.mean())
    if mom is None:
        cycle_text = "月線資料不足，僅約略評估"
    elif mom >= 30:
        cycle_text = "超級循環 / 大紅棒期"
    elif mom <= -10:
        cycle_text = "谷底打底期（低基期轉折）"
    else:
        cycle_text = "循環中段 / 區間築底"
    safety = {
        "success": "長線多頭架構完好，長線持股安全度較高。",
        "warning": "長線結構中性，長線持股需留意區間風險。",
        "error": "長線架構偏弱，長線持股安全度較低。",
    }[lvl_monthly]

    d1, d2, d3 = st.columns(3)
    with d1:
        st.markdown("**日線（天圖）診斷卡**")
        st.metric("RSI(14)", f"{rsi_now:.1f}", rsi_label, delta_color="off")
        st.metric("KD(9,3,3)", f"K {k_now:.1f} / D {d_now:.1f} / J {j_now:.1f}", f"{kd_label}・{j_label}", delta_color="off")
        st.markdown("**型態解讀**")
        render_pill(pattern_daily, lvl_daily)
        st.markdown("**觀察重點**")
        st.caption(f"今日量能為 5 日均量 {vol_ratio:.2f} 倍（{vol_text}），短線留意量價配合。")
        st.caption(
            f"MA20 明日扣抵值 {deduct:.1f}，今收{'高於' if last > deduct else '低於'}扣抵值，"
            f"月線短線將{'走升' if last > deduct else '走降'}；若後續數日扣抵高檔，留意月線下壓預警。"
        )

    with d2:
        st.markdown("**週線（週圖）診斷卡**")
        st.metric("週 MA12 乖離率", f"{w_bias:+.1f}%", "乖離過大留意拉回" if abs(w_bias) >= 15 else "乖離尚屬合理", delta_color="off")
        st.metric("週成交量倍數", f"{w_ratio:.2f} 倍", "爆量換手" if w_ratio >= 1.8 else ("量縮整理" if w_ratio <= 0.6 else "量能正常"), delta_color="off")
        st.markdown("**型態解讀**")
        render_pill(pattern_weekly, lvl_weekly)
        st.markdown("**觀察重點**")
        st.caption(f"中期主力防守區參考週 MA12 = {wma12_now:.1f}，回測不破則中線結構健康。")
        st.caption(f"留意週 K 是否帶量突破前波高點 {w_turn:.1f}（轉折確認訊號），否則僅屬高檔震盪。")

    with d3:
        st.markdown("**月線（月圖）診斷卡**")
        mom_value = f"{mom:+.1f}%" if mom is not None else "—"
        mom_label = f"近 {mom_span} 個月累計報酬" if mom is not None else "月線資料不足"
        st.metric(mom_label, mom_value, cycle_text, delta_color="off")
        st.metric("月 MA12 防守價", f"{m_defense:.1f}", "", delta_color="off")
        st.markdown("**型態解讀**")
        render_pill(pattern_monthly, lvl_monthly)
        st.markdown("**觀察重點**")
        st.caption(safety)
        st.caption(f"長線防守價設於月 MA12 = {m_defense:.1f}，長線持股以此為下檔依歸。")


def _price_block(label: str, value: str, note: str, color: str) -> None:
    st.markdown(
        f'<div style="border-top:3px solid {color};border-radius:8px;padding:8px 6px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.08);height:100%;">'
        f'<div style="font-weight:800;font-size:0.9rem;color:{color};">{label}</div>'
        f'<div style="font-weight:800;font-size:1.3rem;margin:6px 0;color:#111827;">{value}</div>'
        f'<div style="color:#6b7280;font-size:0.78rem;">{note}</div></div>',
        unsafe_allow_html=True,
    )


def _strategy_block(label: str, value: str, note: str, color: str) -> None:
    """價位策略卡片：深色底 + 亮白加粗大數字，提高對比度與可讀性。"""
    st.markdown(
        f'<div style="background-color:#1e222d;border-top:3px solid {color};border-radius:8px;'
        f'padding:12px 10px;height:100%;">'
        f'<span style="color:{color};font-weight:bold;font-size:0.95rem;">{label}</span><br>'
        f'<span style="color:#FFFFFF;font-size:26px;font-weight:bold;line-height:1.4;">{value}</span><br>'
        f'<span style="color:{color};font-size:12px;">{note}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_price_dashboard(levels: dict) -> None:
    """關鍵價格防禦儀表板（高對比實心卡片 + 超大亮色發光數字）。"""
    low_buy = levels["short_low"]
    core = levels["swing_def"]
    stop = levels["stop"]
    resistance = levels["resistance"]
    with st.container(border=True):
        st.markdown("**關鍵價格防禦儀表板**")
        st.caption("以當前股價與技術位階推算，僅供參考；實際操作請依盤勢與個人風險承受度調整。")
        st.markdown(
            f"""
<style>
.price-card {{
    background-color: #262c36;
    border-radius: 10px;
    padding: 16px;
    margin: 5px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.3);
}}
.price-title {{ font-size: 15px; font-weight: bold; margin-bottom: 8px; }}
.price-num {{ font-size: 28px; font-weight: 900; color: #FFD700; text-shadow: 0 0 10px rgba(255,215,0,0.3); margin: 6px 0; }}
.price-desc {{ font-size: 13px; color: #D1D5DB; }}
</style>

<div class="price-card-container" style="display: flex; gap: 10px; justify-content: space-between;">
    <div class="price-card" style="flex: 1; border-left: 5px solid #10B981;">
        <div class="price-title" style="color: #34D399;">🎯 短線低吸進場區</div>
        <div class="price-num">{low_buy[0]} ~ {low_buy[1]} 元</div>
        <div class="price-desc">回測區間分批佈局，不追高</div>
    </div>
    <div class="price-card" style="flex: 1; border-left: 5px solid #3B82F6;">
        <div class="price-title" style="color: #60A5FA;">🛡️ 波段核心防禦區</div>
        <div class="price-num">{core[0]} ~ {core[1]} 元</div>
        <div class="price-desc">跌破代表波段走勢轉弱</div>
    </div>
    <div class="price-card" style="flex: 1; border-left: 5px solid #EF4444;">
        <div class="price-title" style="color: #F87171;">🚨 關鍵停損防守位</div>
        <div class="price-num">{stop} 元</div>
        <div class="price-desc">跌破應紀律停損，嚴控風險</div>
    </div>
    <div class="price-card" style="flex: 1; border-left: 5px solid #A855F7;">
        <div class="price-title" style="color: #C084FC;">🚀 上檔第一阻力位</div>
        <div class="price-num">{resistance} 元</div>
        <div class="price-desc">帶量突破可望打開上行空間</div>
    </div>
</div>
""",
            unsafe_allow_html=True,
        )


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
