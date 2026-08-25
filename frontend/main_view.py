# -*- coding: utf-8 -*-
"""
主畫面總組裝（Frontend — Main View）
右側主畫面總組裝。依序呼叫 scroll_script → 股票標題 → kline_chart → data_tables。
"""

import streamlit as st
import streamlit.components.v1 as components

from frontend.components.kline_chart import get_kline_chart, render_kline_chart
from frontend.components.ai_diagnosis import (
    get_cached_ai_diagnosis,
    render_ultimate_diagnosis_card,
)
from frontend.components.data_tables import (
    get_market_status,
    setup_autorun,
    render_market_badge,
    compute_levels,
    compute_price_strategy,
    render_timeframe_cards,
    render_key_support_resistance,
    render_price_dashboard,
    _strategy_block,
    streak_text,
    render_pill,
    cycle_level,
)
from backend.services.stock_fetcher import (
    _download_daily,
    fetch_stock_data,
    fetch_live_price,
    generate_chip_data,
    resample_ohlc,
    resample_kline,
    _month_rule,
)
from backend.services.stock_master import (
    STOCK_INFO,
    get_daily_trending_stocks,
    resolve_ticker,
    build_search_error_message,
    validate_stock_input,
    get_fundamental,
    lookup_stock_name,
)
from frontend.components.scroll_script import force_scroll_to_top


# ====================== 手機版響應式 CSS ======================
_RESPONSIVE_CSS = """
<style>
.ai-diagnosis-card {
    background: linear-gradient(135deg, #1E222D 0%, #2A2E39 100%);
    border-radius: 14px;
    padding: 20px 22px;
    margin-bottom: 16px;
    color: #F9FAFB;
    font-family: 'Microsoft JhengHei', Arial, sans-serif;
    line-height: 1.65;
}
@media (max-width: 768px) {
    .js-plotly-plot, .plot-container {
        max-height: 400px !important;
    }
    .price-card-container {
        flex-direction: column !important;
        gap: 8px !important;
    }
    .main .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
    }
    .stButton > button {
        width: 100% !important;
        font-size: 14px !important;
    }
    .stButton > button,
    .stDownloadButton > button,
    .stTextInput input,
    .stSelectbox [data-baseweb="select"] > div,
    .stRadio > div[role="radiogroup"] > label {
        min-height: 44px !important;
    }
    .stButton > button {
        padding-top: 8px !important;
        padding-bottom: 8px !important;
    }
    .stButton > button,
    .stTextInput input,
    .stSelectbox [data-baseweb="select"] > div,
    [data-testid="stHorizontalBlock"] > div {
        user-select: none !important;
        -webkit-user-select: none !important;
        -webkit-tap-highlight-color: transparent;
    }
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    [data-testid="stHorizontalBlock"] > div {
        min-width: 45% !important;
        flex: 1 1 auto !important;
    }
    .ai-diagnosis-card {
        background: linear-gradient(135deg, #1E222D 0%, #2A2E39 100%);
        border-radius: 14px;
        padding: 20px 22px;
        margin-bottom: 16px;
        color: #F9FAFB;
        font-family: 'Microsoft JhengHei', Arial, sans-serif;
        line-height: 1.65;
    }
    .ai-diagnosis-card summary::-webkit-details-marker { display: none; }
    .ai-diagnosis-card summary::marker { display: none; content: ''; }
    .ai-diagnosis-card details[open] summary > span:first-child { transform: rotate(90deg); }
    @media (max-width: 768px) {
        .ai-diagnosis-card {
            padding: 14px 12px !important;
            border-radius: 10px !important;
            font-size: 0.9rem !important;
        }
        .ai-diagnosis-card > div:first-child span[style*="font-size:1.8rem"] {
            font-size: 1.3rem !important;
        }
        .ai-diagnosis-card > div:first-child div span[style*="font-size:1.35rem"] {
            font-size: 1.05rem !important;
        }
        .ai-diagnosis-card details > div > div[style*="grid-template-columns"] {
            grid-template-columns: 1fr !important;
        }
        .ai-diagnosis-card > div[style*="display:flex"][style*="gap:14px"] {
            flex-direction: column !important;
        }
    }
}
</style>
"""


def _signed_number(value: float, digits: int = 2, suffix: str = "") -> str:
    """正值補 +、負值補 -，零值不帶符號（例如 0.00%）。"""
    if value > 0:
        return f"+{value:.{digits}f}{suffix}"
    if value < 0:
        return f"-{abs(value):.{digits}f}{suffix}"
    return f"{value:.{digits}f}{suffix}"


def _tw_color(value: float) -> str:
    """台股慣例顏色：漲亮紅 / 跌亮綠 / 平盤淡灰。"""
    if value > 0:
        return "#ff4d4f"
    if value < 0:
        return "#00e676"
    return "#a0a0a0"


def _tw_badge(value: float, text: str) -> str:
    """台股漲跌 Badge：>0 半透明紅底 ↑、<0 半透明綠底 ↓、=0 淡灰無箭頭。"""
    if value > 0:
        arrow, bg = "↑", "rgba(255, 77, 79, 0.15)"
    elif value < 0:
        arrow, bg = "↓", "rgba(0, 230, 118, 0.15)"
    else:
        arrow, bg = "", "rgba(160, 160, 160, 0.15)"
    fg = _tw_color(value)
    inner = f"{arrow} {text}".strip()
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};padding:3px 12px;'
        f'border-radius:999px;font-weight:800;font-size:0.88rem;">{inner}</span>'
    )


def _price_metric_card(label: str, value: str, badge_html: str) -> None:
    """以 HTML/CSS 渲染指標卡片：標頭 + 主數字 + 漲跌 Badge。"""
    st.markdown(
        '<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;'
        'box-shadow:0 1px 3px rgba(0,0,0,0.06);height:100%;">'
        f'<div style="color:#6b7280;font-size:0.86rem;font-weight:700;margin-bottom:4px;">{label}</div>'
        f'<div style="font-weight:800;font-size:1.55rem;color:#ffffff !important;line-height:1.35;">{value}</div>'
        f'<div style="margin-top:8px;">{badge_html}</div></div>',
        unsafe_allow_html=True,
    )


def _render_home_page():
    """首頁模式：熱門推薦與搜尋引導。"""
    if "home_search_sync" in st.session_state:
        st.session_state["home_search"] = st.session_state.pop("home_search_sync")

    st.markdown(
        '<div style="text-align:center;padding:48px 20px 8px;">'
        '<div style="display:flex;align-items:center;justify-content:center;gap:15px;margin-bottom:5px;">'
        '<img src="https://img.icons8.com/color/96/gold-bars.png" width="45" height="45" style="object-fit:contain;">'
        '<div style="font-size:42px;font-weight:900;letter-spacing:2px;color:#FFD700;'
        'text-shadow:0 3px 6px rgba(180,120,0,0.40);">財神爺 AI 智股通</div>'
        '<img src="https://img.icons8.com/color/96/gold-bars.png" width="45" height="45" style="object-fit:contain;"></div>'
        '<div style="color:#b45309;font-size:0.98rem;font-weight:700;margin-top:14px;">'
        '「富貴雙收·點石成金｜AI 籌碼診斷與技術分析」</div>'
        '<div style="color:#6b7280;font-size:1.02rem;margin-top:10px;">'
        '輸入台股代號或名稱，或直接點選下方熱門標的，'
        '立即取得技術面 K 線、籌碼面、基本面與 AI 綜合診斷。</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.form(key="home_search_form"):
        st.text_input(
            "🔍 股票代號 / 名稱",
            placeholder="例如：2330 或 台積電",
            key="home_search",
        )
        home_submitted = st.form_submit_button("🔍 開始 AI 診斷", use_container_width=True, key="home_submit_btn")
    if home_submitted:
        valid, ticker_code, _ = validate_stock_input(st.session_state["home_search"])
        if valid and ticker_code:
            st.session_state["current_ticker"] = ticker_code
            st.session_state["sidebar_key"] += 1
            force_scroll_to_top()
            st.rerun()
        else:
            st.session_state["search_error"] = build_search_error_message(st.session_state["home_search"])
            st.rerun()

    st.markdown("### 🔥 熱門推薦標的 Quick Pick")
    hot_trending = get_daily_trending_stocks()
    hot_columns = st.columns(len(hot_trending))
    for col, (name, code) in zip(hot_columns, hot_trending):
        with col:
            if st.button(
                f"**{name}**\n\n{code.replace('.TW', '')}",
                key=f"hot_pick_{code}",
                use_container_width=True,
            ):
                st.session_state["current_ticker"] = code
                st.session_state["home_search_sync"] = name
                st.session_state["sidebar_key"] += 1
                force_scroll_to_top()
                st.rerun()

    st.divider()
    st.caption("點擊任一熱門標的，或於左側搜尋欄輸入股票代號 / 名稱後按「開始 AI 診斷」，即可進入完整分析。")
    st.stop()


def _render_stock_dashboard(ticker: str, status: dict):
    """完整分析儀表板：標題 → 指標 → K 線 → 數據表格。"""
    info = STOCK_INFO.get(ticker, {})
    stock_code = ticker.rsplit(".", 1)[0]
    stock_name = lookup_stock_name(ticker)

    with st.spinner("💰 財神爺正在讀取基本面、籌碼面與 K 線資料，請稍候..."):
        fund = get_fundamental(ticker)
        chip = generate_chip_data(ticker)
        if status["is_open"]:
            df, source = _download_daily(ticker)
            live = fetch_live_price(ticker)
            if live is not None:
                source += "（1 分鐘級即時報價）"
        else:
            df, source = fetch_stock_data(ticker)
            live = None

    with st.container(key=f"main_content_{ticker}"):
        # ── 頂部概覽 ──
        header_left, header_right = st.columns([6, 1])
        with header_left:
            st.markdown(f"# {stock_name} ({stock_code})")
        with header_right:
            if st.button("🏠 回到首頁", key="btn_back_home", use_container_width=True):
                st.session_state["current_ticker"] = None
                st.session_state["sidebar_key"] += 1
                force_scroll_to_top()
                st.rerun()
        st.caption(f"產業：{fund['industry']}　|　資料來源：{source}")
        render_market_badge(status)

        close_now = float(df["Close"].iloc[-1])
        close_prev = float(df["Close"].iloc[-2])
        chg = close_now - close_prev
        chg_pct = chg / close_prev * 100
        vol_now = float(df["Volume"].iloc[-1])
        vol_prev = float(df["Volume"].iloc[-2])
        vol_pct = (vol_now / vol_prev - 1) * 100 if vol_prev else 0.0

        if live is not None:
            close_now = live["price"]
            chg = live["change"]
            chg_pct = live["change_pct"]

        levels = compute_levels(close_now)

        c1, c2, c3 = st.columns(3)
        with c1:
            _price_metric_card(
                "目前股價 (NT$)",
                f"{close_now:,.2f}",
                _tw_badge(chg_pct, f"{_signed_number(chg_pct)}%"),
            )
        with c2:
            _price_metric_card(
                "當日漲跌幅",
                f'<span style="color:{_tw_color(chg_pct)};">{_signed_number(chg_pct)}%</span>',
                _tw_badge(chg, f"{_signed_number(chg)} 元"),
            )
        with c3:
            _price_metric_card(
                "成交量",
                f"{vol_now / 1000:,.1f} 仟張",
                _tw_badge(vol_pct, f"較昨日 {_signed_number(vol_pct, digits=1)}%"),
            )

        st.divider()

        # ── 技術面 K 線與型態診斷 ──
        st.subheader("技術面：K 線與型態診斷")

        if "chart_view" not in st.session_state:
            st.session_state["chart_view"] = {}
        if "chart_uirev" not in st.session_state:
            st.session_state["chart_uirev"] = 0

        kline_period = st.radio(
            "📊 選擇 K 線週期：",
            ["日 K", "週 K", "月 K"],
            horizontal=True,
            key="kline_period",
        )

        kdf = resample_kline(df, kline_period)
        n_show = 80
        window = kdf.tail(n_show)
        price_min, price_max = float(window["Low"].min()), float(window["High"].max())
        pad = (price_max - price_min) * 0.05
        default_view = {
            "xaxis_range": [window["Date"].iloc[0], window["Date"].iloc[-1]],
            "yaxis_range": [price_min - pad, price_max + pad],
        }

        b_left, b_right = st.columns([1, 1])
        with b_left:
            if st.button(
                "🔄 更新 K 線數據",
                key=f"btn_kline_refresh_{ticker}",
                help="重新抓取最新日 K 資料並重繪（開盤期間自動刷新不會觸發）",
            ):
                get_kline_chart.clear()
        with b_right:
            reset_clicked = st.button(
                "🔄 復原全圖視角",
                key=f"btn_chart_reset_{ticker}",
                help=f"回復到最新 80 根{kline_period.replace(' K', '')} K 的預設視角",
            )

        view_key = f"{ticker}|{kline_period}"
        apply_default = reset_clicked or (view_key not in st.session_state["chart_view"])

        if apply_default:
            st.session_state["chart_view"][view_key] = default_view
            st.session_state["chart_uirev"] += 1

        view_lock = st.session_state["chart_view"].get(view_key) if apply_default else None
        uirevision = f"{ticker}|{kline_period}-v{st.session_state['chart_uirev']}"

        fig_kline = get_kline_chart(ticker, kline_period)
        render_kline_chart(fig_kline, ticker, view_lock=view_lock, uirevision=uirevision, period=kline_period)

        render_timeframe_cards(df, resample_ohlc(df, "W-FRI"), resample_ohlc(df, _month_rule()))

        # ── AI 多重週期籌碼評分（快取 1 小時，重複檢視 0 秒讀取） ──
        st.subheader("🤖 AI 多重週期籌碼評分")
        with st.spinner("🤖 AI 多重週期籌碼分析中..."):
            diag = get_cached_ai_diagnosis(ticker, df, chip, fund)
        render_ultimate_diagnosis_card(diag)

        # ── 關鍵支撐 / 壓力 / 風控指標 ──
        st.subheader("關鍵價位與風控防線")
        render_key_support_resistance(df, close_now)

        levels["resistance"] = round(float(df["High"].tail(60).max()), 1)
        render_price_dashboard(levels)

        st.divider()

        # ── AI 價位策略與風險評估 ──
        strategy = compute_price_strategy(df, close_now)
        st.subheader("🎯 AI 價位策略與風險評估")
        st.caption("依據近期 20 / 60 日 K 線動態計算；僅供教學與研究參考，不構成投資建議。")

        with st.container(border=True):
            p1, p2, p3, p4 = st.columns(4)
            with p1:
                _strategy_block(
                    "🟢 最佳低風險進場區",
                    f"{strategy['buy_lo']:,.2f} ~ {strategy['buy_hi']:,.2f} 元",
                    "此區間接近支撐，盈虧比最佳、風險最小",
                    "#00FF7F",
                )
            with p2:
                _strategy_block(
                    "🔵 建議入場價",
                    f"{strategy['entry']:,.2f} 元",
                    "拉回至支撐 / MA20 附近分批佈局",
                    "#00BFFF",
                )
            with p3:
                _strategy_block(
                    "🎯 目標止盈價",
                    f"{strategy['target']:,.2f} 元",
                    f"潛在報酬 +{strategy['reward_pct']:.1f}%",
                    "#FF6B6B",
                )
            with p4:
                _strategy_block(
                    "🛑 嚴格停損價",
                    f"{strategy['stop_loss']:,.2f} 元",
                    f"防守風險 -{strategy['risk_pct']:.1f}%",
                    "#FFA500",
                )

            rr = strategy["rr"]
            if rr >= 2.0:
                st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，目前達標、風險相對可控。")
            elif rr >= 1.5:
                st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，接近達標、可小量試單。")
            else:
                st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，尚未達標、追價風險偏高。")

            if close_now > strategy["buy_hi"] * 1.05:
                st.warning("⚠️ 目前股價離支撐區較遠，追高風險較大，建議耐心等待回檔至低風險區附近再分批佈局。")
            elif strategy["buy_lo"] * 0.98 <= close_now <= strategy["buy_hi"] * 1.03:
                st.info(f"✅ 當前價格位於相對低風險區間，且風報比達 {rr:.1f}，適合分批建立基本倉位。")
            else:
                st.info(f"📊 目前股價位於低風險區上緣附近，可等待拉回 {strategy['buy_hi']:,.2f} 元以下再分批佈局。")

        st.divider()

        # ── 籌碼面四大指標 ──
        st.subheader("籌碼面：法人 / 大戶 / 主力 / 融資融券")

        cc1, cc2, cc3, cc4 = st.columns(4)
        with cc1:
            st.markdown("**法人動向**")
            st.metric("外資", f"{chip['foreign']:+d} 日", streak_text(chip["foreign"], "外資"))
            st.metric("投信", f"{chip['it']:+d} 日", streak_text(chip["it"], "投信"))
            st.metric("自營商", f"{chip['dealer']:+d} 日", streak_text(chip["dealer"], "自營商"))
            if chip["foreign"] > 0 and chip["it"] > 0:
                st.success("外資與投信同步偏多，籌碼面助漲。")
            elif chip["foreign"] < 0 and chip["it"] < 0:
                st.warning("外資與投信同步賣超，籌碼面有壓。")
            else:
                st.info("法人多空分歧，以區間應對。")

        with cc2:
            st.markdown("**集保大戶 vs 散戶集中度**")
            st.metric("大戶持股比重", f"{chip['large_holder']}%", f"{chip['large_delta']:+.1f} 百分點")
            st.metric("散戶持股比重", f"{chip['retail']}%")
            if chip["large_delta"] > 0:
                st.success("大戶持股集中度上升，籌碼趨向集中。")
            else:
                st.warning("大戶持股鬆動、散戶進場，留意反轉風險。")

        with cc3:
            st.markdown("**主力買賣家數差**")
            st.metric("買賣家數差", f"{chip['main_diff']:+d} 家")
            if chip["main_diff"] > 0:
                st.success("買超家數較多，短線主力偏多。")
            elif chip["main_diff"] < 0:
                st.warning("賣超家數較多，短線主力偏空。")
            else:
                st.info("主力買賣家數大致平衡。")

        with cc4:
            st.markdown("**融資融券氣象**")
            st.metric("融資餘額變動", f"{chip['margin_chg']:+.1f}%")
            if chip["margin_chg"] > 8:
                st.warning("融資暴增警示：人氣過熱，高檔恐有多殺多風險。")
            elif chip["margin_chg"] < -5:
                st.success("融資大幅減碼，浮額清洗相對乾淨。")
            else:
                st.info("融資餘額變動溫和，籌碼相對穩定。")

        st.divider()

        # ── 基本面與缺貨題材 ──
        st.subheader("基本面：景氣週期與缺貨漲價題材")

        f1, f2 = st.columns(2)
        with f1:
            st.markdown("**產業景氣週期位階**")
            render_pill(fund["cycle"], cycle_level(fund["cycle"]))
            st.caption(
                "谷底復甦 = 低基期轉折；暴衝 / 高峰期 = 獲利與股價風險同步放大；修正期 = 獲利下修進行式。"
            )
        with f2:
            st.markdown("**產品缺貨與漲價效應**")
            st.write(fund["shortage"])

        st.divider()

        # ── 風險管理 ──
        st.subheader("風險管理：Check List")

        r1, r2, r3 = st.columns(3)
        with r1:
            st.markdown("**庫存天數與去化速度**")
            if fund["inventory"] == "偏高":
                st.warning("庫存水位偏高，需留意下游去化放緩與毛利率壓力。")
            elif fund["inventory"] == "偏低":
                st.success("庫存水位偏低，供需相對健康。")
            else:
                st.success("庫存水位正常。")

        with r2:
            st.markdown("**資本支出 / 折舊警訊**")
            if fund["capex"]:
                st.warning("資本支出與折舊負擔較重，新品量產前可能侵蝕獲利。")
            else:
                st.success("資本支出與折舊負擔相對可控。")

        with r3:
            st.markdown("**大盤系統性風險連動度**")
            st.metric("Beta（大盤連動度）", f"{fund['beta']:.2f}")
            if fund["beta"] >= 1.2:
                st.warning("Beta 偏高，市場修正時個股跌幅恐放大，需留意停損紀律。")
            else:
                st.success("Beta 低於 1，相對抗跌，系統性風險影響較小。")

        st.divider()

        st.caption(
            "本 App 僅供教學與研究用途，所有數據（尤其 Mock Data）與診斷結果均不構成投資建議；"
            "投資有風險，決策前請自行審慎評估。"
        )


def render_main_view():
    """主畫面總組裝入口。"""
    st.markdown(_RESPONSIVE_CSS, unsafe_allow_html=True)

    status = get_market_status()
    setup_autorun(status["is_open"])

    ticker = st.session_state.get("current_ticker")

    if ticker is not None and resolve_ticker(ticker) is None:
        st.session_state["search_error"] = build_search_error_message(ticker)
        st.session_state["current_ticker"] = None
        ticker = None

    if "search_error" in st.session_state:
        st.error(st.session_state.pop("search_error"), icon="⚠️")

    # 每次切換股票重新繪製 main_view 時，強制將主視窗區塊捲回最上方
    components.html(
        """
        <script>
            setTimeout(function() {
                try {
                    var mainContainer = window.parent.document.querySelector('section.main');
                    if (mainContainer) {
                        mainContainer.scrollTop = 0;
                        mainContainer.scrollTo({ top: 0, behavior: 'instant' });
                    }
                    window.parent.scrollTo(0, 0);
                } catch(e) {
                    console.error('Scroll error:', e);
                }
            }, 100);
        </script>
        """,
        height=0,
        width=0
    )

    container_key = f"main_content_holder_{ticker or 'home'}"
    with st.container(key=container_key):
        st.markdown('<div id="main-top"></div>', unsafe_allow_html=True)

        if ticker is None:
            _render_home_page()

        _render_stock_dashboard(ticker, status)
