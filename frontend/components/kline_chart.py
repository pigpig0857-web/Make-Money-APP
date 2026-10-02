# -*- coding: utf-8 -*-
"""
K 線圖元件（Frontend — K-line Chart）
僅放置 Plotly K 線圖繪製邏輯、快取機制與渲染配置。
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from backend.services.stock_fetcher import _PERIOD_HISTORY, _download_daily, resample_kline


def is_mobile_request() -> bool:
    """以瀏覽器 User-Agent 偵測手機 / 平板。"""
    try:
        headers = st.context.headers
        ua = headers.get("User-Agent", "") if hasattr(headers, "get") else ""
        if not ua and hasattr(headers, "to_dict"):
            ua = headers.to_dict().get("User-Agent", "")
    except Exception:
        return False
    ua = (ua or "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone", "ipad"))


def _build_kline_figure(df: pd.DataFrame, period: str = "日 K") -> go.Figure:
    """純函式：將指定週期 K 線資料轉為 Plotly K 線 + 成交量 Figure。"""
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
            # 台股標準配色：紅 K 上漲 / 綠 K 下跌
            increasing_line_color="#ef5350",
            increasing_fillcolor="#ef5350",
            decreasing_line_color="#26a69a",
            decreasing_fillcolor="#26a69a",
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

    # ── 成交量整備：股 → 張（台股 1 張 = 1000 股），並壓制極端值避免量能圖被單筆異常拉扁 ──
    vol_lots = df["Volume"].astype(float) / 1000.0
    vol_cap = float(np.nanpercentile(vol_lots, 99.9)) if len(vol_lots) else 0.0
    vol_plot = vol_lots.clip(upper=max(vol_cap, 0.0))

    # 台股配色對齊 K 線：收 >= 開（紅 K）→ 紅量；收 < 開（綠 K）→ 綠量
    vol_colors = ["#ef5350" if c >= o else "#26a69a" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(
        go.Bar(
            x=df["Date"],
            y=vol_plot,
            name="成交量",
            marker_color=vol_colors,
            marker_line_width=0,
            customdata=vol_lots.to_numpy(),
            hovertemplate="成交量 %{customdata[0]:,.0f} 張<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.add_hline(y=resistance, line_dash="dash", line_color="#dc2626", line_width=1.6, row=1, col=1)
    fig.add_hline(y=support, line_dash="dash", line_color="#16a34a", line_width=1.6, row=1, col=1)
    fig.add_annotation(
        x=df["Date"].iloc[-1],
        y=resistance,
        text=f"壓力: {resistance:.1f}",
        showarrow=False,
        xanchor="right",
        yshift=10,
        font=dict(color="#dc2626", size=14, family="Microsoft JhengHei"),
    )
    fig.add_annotation(
        x=df["Date"].iloc[-1],
        y=support,
        text=f"支撐: {support:.1f}",
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
        dragmode="pan",
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
def get_kline_chart(stock_id: str, period: str = "日 K", price_policy_version="real-only-v1") -> go.Figure:
    """快取真實 K 線；政策版本避免沿用舊版模擬圖表。"""
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
    """渲染 K 線圖：在快取的 Figure 上疊加視角狀態後輸出。"""
    layout_update = {"uirevision": (uirevision if uirevision is not None else ticker)}
    # 行動裝置手勢保證：強制 pan 模式——單指左右滑動平移 K 線、雙指捏合縮放，
    # 與電腦版滑鼠滾輪縮放效果一致；於渲染層設定可不受舊快取 Figure 影響。
    layout_update["dragmode"] = "pan"
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
        modebar_remove.append("toImage")
    # 觸控裝置優化：啟用滾動/手勢縮放與平移，並常駐工具列供手機操作
    # （電腦版行為不變——原本即顯示工具列、scrollZoom 已開啟）
    config = {
        "displaylogo": False,
        "responsive": True,
        "scrollZoom": True,  # 允許單指/雙指滾動與手勢縮放
        "displayModeBar": True,  # 顯示上方工具列（含縮放 / 平移控制，手機亦可見）
        "showTips": False,
        "showAxisDragHandles": True,
        "modeBarButtonsToRemove": modebar_remove,
    }
    st.plotly_chart(fig, config=config, use_container_width=True, key=f"kline_{ticker}_{period}")
