# -*- coding: utf-8 -*-
"""
主畫面總組裝（Frontend — Main View）
右側主畫面總組裝。依序呼叫 scroll_script → 股票標題 → kline_chart → data_tables。
"""

import streamlit as st

from frontend.components.kline_chart import get_kline_chart, render_kline_chart, _build_kline_figure
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
    get_chip_data,
    StockDataUnavailableError,
    resample_ohlc,
    resample_kline,
    _month_rule,
)
from backend.services.stock_master import (
    STOCK_INFO,
    get_daily_trending_stocks,
    validate_stock_input,
    build_search_error_message,
    get_fundamental,
    lookup_stock_name,
)
from frontend.components.sidebar import _return_home
from backend.services.analysis_repository import save_score_snapshot, load_score_history
from frontend.components.backtest_panel import render_backtest_panel
from frontend.components.research_panel import render_research_panel


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



_FORCE_SCROLL_TOP_JS = """<script>
function forceScrollTop() {
    var topElem = window.parent.document.getElementById('page-top');
    if (topElem) {
        topElem.scrollIntoView({behavior: 'instant', block: 'start'});
    }
    var mainContainer = window.parent.document.querySelector('.main')
        || window.parent.document.querySelector('[data-testid="stMainBlockContainer"]');
    if (mainContainer) { mainContainer.scrollTop = 0; }
    window.parent.scrollTo(0, 0);
}
forceScrollTop();
setTimeout(forceScrollTop, 50);
setTimeout(forceScrollTop, 150);
</script>"""


def _render_home_page():
    """首頁模式：歡迎畫面與熱門推薦。"""
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
        '於左側側邊欄輸入台股代號或名稱，或直接點選下方熱門標的，'
        '查看真實 K 線與技術評分；法人、營收與市場資訊作為輔助。</div>'
        '</div>',
        unsafe_allow_html=True,
    )

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
                st.session_state.pop("pending_stock_query", None)
                st.session_state.pop("search_error", None)
                st.session_state["target_ticker"] = code
                st.session_state["loading_new"] = True
                st.rerun()

    st.divider()
    st.caption("於左側側邊欄輸入股票代號 / 名稱後按「開始 AI 診斷」，或點選上方熱門標的，即可進入完整分析。")


def _render_stock_dashboard(ticker: str, status: dict):
    """完整分析儀表板（Phase 2：資料抓取 + 渲染 UI）。"""
    import streamlit.components.v1 as _comp
    _comp.html(_FORCE_SCROLL_TOP_JS, height=0)

    info = STOCK_INFO.get(ticker, {})
    stock_code = ticker.rsplit(".", 1)[0]
    stock_name = lookup_stock_name(ticker)

    # ②③ 載入提示 + API 資料抓取
    with st.spinner("💰 財神爺正在讀取真實 K 線與基本面資料，請稍候..."):
        fund = get_fundamental(ticker)
        chip = get_chip_data(ticker)
        try:
            if status["is_open"]:
                df, source = _download_daily(ticker)
                live = fetch_live_price(ticker)
                if live is not None:
                    source += "（1 分鐘級報價，可能延遲）"
            else:
                df, source = fetch_stock_data(ticker)
                live = None
        except StockDataUnavailableError as exc:
            st.error(str(exc))
            st.info("本專案已停用模擬行情。請確認網路後，再次提交搜尋。")
            return

    with st.container(key=f"main_content_{ticker}"):
        # ── 頂部概覽 ──
        header_left, header_right = st.columns([6, 1])
        with header_left:
            st.markdown(f"# {stock_name} ({stock_code})")
        with header_right:
            st.button("🏠 回到首頁", key="btn_back_home", use_container_width=True,
                      on_click=_return_home)
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

        # ── 核心區塊（最上方）：AI 多重週期籌碼評分與建議（快取 1 小時，重複檢視 0 秒讀取）──
        st.subheader("🤖 真實行情技術評分")
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_tw = datetime.now(ZoneInfo("Asia/Taipei"))
        score_df = df
        if now_tw.hour * 60 + now_tw.minute < 13 * 60 + 30:
            score_df = df[df["Date"].dt.date < now_tw.date()].copy()
        st.caption("僅使用已完成日 K 的趨勢、成交量、RSI 與 MACD；籌碼及基本面未計分，分數不是勝率。")
        if not score_df.empty:
            st.caption(f"評分行情截至：{score_df['Date'].iloc[-1]:%Y-%m-%d}")
        score_ready = False
        try:
            with st.spinner("正在計算真實行情指標…"):
                diag = get_cached_ai_diagnosis(ticker, score_df, {}, fund)
            render_ultimate_diagnosis_card(diag)
            score_ready = True
            try:
                inserted = save_score_snapshot(ticker, score_df, diag)
                st.caption("本次評分已存入資料庫。" if inserted else "相同行情與診斷已記錄，未重複新增。")
            except Exception:
                st.warning("評分已完成，但紀錄儲存失敗，請檢查資料庫或初始化資料表。")
            with st.expander("評分歷史紀錄"):
                if st.button("查看最近 10 筆評分", key=f"score_history_{ticker}"):
                    try:
                        history = load_score_history(ticker)
                        if history:
                            st.dataframe(history, hide_index=True)
                        else:
                            st.info("尚無評分紀錄。")
                    except Exception:
                        st.warning("暫時無法讀取評分紀錄。")
        except ValueError as exc:
            st.warning(str(exc))
            st.info("暫不提供評分及操作建議，以下僅顯示可取得的歷史行情。")

        st.divider()

        render_backtest_panel(ticker)
        render_research_panel(ticker, df)

        # ── 次要區塊：技術面 K 線與型態診斷 ──
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

        try:
            fig_kline = get_kline_chart(ticker, kline_period)
        except StockDataUnavailableError:
            st.warning("此週期的長期行情暫時無法取得，改用目前已取得的真實日 K 彙整。")
            fig_kline = _build_kline_figure(kdf, kline_period)
        render_kline_chart(fig_kline, ticker, view_lock=view_lock, uirevision=uirevision, period=kline_period)

        render_timeframe_cards(df, resample_ohlc(df, "W-FRI"), resample_ohlc(df, _month_rule()))

        if not score_ready:
            return

        # ── 關鍵支撐 / 壓力 / 風控指標 ──
        st.subheader("關鍵價位與風控防線")
        render_key_support_resistance(df, close_now)

        levels["resistance"] = round(float(df["High"].tail(60).max()), 1)
        render_price_dashboard(levels)

        st.divider()

        # ── AI 價位策略與風險評估 ──
        strategy = compute_price_strategy(score_df, float(score_df["Close"].iloc[-1]))
        st.subheader("🎯 AI 價位策略與風險評估")
        st.caption("依據近期 20 / 60 日 K 線動態計算；僅供教學與研究參考，不構成投資建議。")

        with st.container(border=True):
            p1, p2, p3, p4 = st.columns(4)
            with p1:
                _strategy_block(
                    "🟢 最佳低風險進場區",
                    f"{strategy['buy_lo']:,.2f} ~ {strategy['buy_hi']:,.2f} 元",
                    "依近期支撐計算的參考區間，尚未回測",
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
                st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，達到系統設定門檻，未代表交易勝率或安全程度。")
            elif rr >= 1.5:
                st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，未達系統設定門檻，需繼續觀察。")
            else:
                st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，尚未達標、追價風險偏高。")

            if close_now > strategy["buy_hi"] * 1.05:
                st.warning("⚠️ 目前股價離支撐區較遠，追高風險較大，建議耐心等待回檔至低風險區附近再分批佈局。")
            elif strategy["buy_lo"] * 0.98 <= close_now <= strategy["buy_hi"] * 1.03:
                st.info(f"✅ 當前價格位於相對低風險區間，且風報比達 {rr:.1f}，仍需確認趨勢與風險，不能僅憑此區間進場。")
            else:
                st.info(f"📊 目前股價位於低風險區上緣附近，可等待拉回 {strategy['buy_hi']:,.2f} 元以下再分批佈局。")

        st.divider()

        st.subheader("真實法人買賣超")
        with st.spinner("正在讀取近 20 個交易日的官方法人資料…"):
            chip = get_chip_data(ticker, [stamp.date() for stamp in score_df["Date"].tail(20)])
        if chip["available"]:
            st.caption(f"來源：{chip['source']}；單位：股；資料截至 {chip['latest_date']}；取得 {len(chip['rows'])}/{chip['expected_days']} 個交易日。")
            latest = chip["rows"][-1]
            foreign_col, trust_col, dealer_col = st.columns(3)
            for col, label, key in [(foreign_col, "外資（含外資自營商）", "foreign_net"),
                                     (trust_col, "投信", "trust_net"),
                                     (dealer_col, "自營商", "dealer_net")]:
                with col:
                    st.metric(f"{label}最新日買賣超", f"{latest[key]:+,} 股")
                    total = sum(row[key] for row in chip["rows"])
                    st.caption(f"已取得 {len(chip['rows'])} 日累計：{total:+,} 股")
            if not chip["complete"]:
                st.warning("法人日期有缺漏，上述累計僅涵蓋已取得日期，不能解讀為完整 20 日累計。")
            if not chip["stored"]:
                st.warning("官方資料已取得，但本次儲存失敗。")
            with st.expander("法人每日明細"):
                st.dataframe(chip["rows"], hide_index=True)
        st.info(chip["reason"])

        st.subheader("基本面：Yahoo Finance 查詢值")
        st.caption("輔助資訊，未納入技術評分；缺少的欄位不以預設值代替。")
        for label, key, percent in [
            ("EPS", "trailing_eps", False), ("本益比", "pe_ratio", False),
            ("ROE", "roe", True), ("Beta", "beta", False),
        ]:
            value = fund.get(key)
            if value is None:
                st.write(f"{label}：暫無資料")
            else:
                st.write(f"{label}：{value * 100 if percent else value:.2f}{'%' if percent else ''}")
        st.caption("產業景氣、缺貨題材、庫存與資本支出尚無可驗證資料，暫不判斷。")
        st.caption("規則已提供歷史回測工具，尚未完成跨股票與樣本外校準；請確認資料日期與條件後再作判斷。")


def render_main_view():
    """Resolve a persisted search request, then render its dashboard in this run."""
    st.session_state.setdefault("target_ticker", None)
    st.session_state.setdefault("loading_new", False)

    st.markdown(_RESPONSIVE_CSS, unsafe_allow_html=True)
    st.markdown('<div id="page-top"></div>', unsafe_allow_html=True)
    status = get_market_status()

    # Leave the request pending until validation finishes. An interrupted run
    # can resume it without asking the user to click the submit button again.
    if "pending_stock_query" in st.session_state:
        query = st.session_state["pending_stock_query"]
        with st.spinner(f"正在確認股票：{query or '（尚未輸入）'}…"):
            valid, ticker, _ = validate_stock_input(query)
        if valid and ticker:
            st.session_state["target_ticker"] = ticker
            st.session_state["loading_new"] = True
            st.session_state.pop("search_error", None)
        else:
            st.session_state["search_error"] = build_search_error_message(query)
        st.session_state.pop("pending_stock_query", None)

    target = st.session_state.get("target_ticker")
    selected = st.session_state.get("selected_ticker")
    if target and (st.session_state["loading_new"] or target != selected):
        # Search has already been validated; quick picks supply ticker codes.
        # The dashboard fetches data once and shows its own loading spinner.
        st.session_state["selected_ticker"] = target
        st.session_state["loading_new"] = False
        selected = target

    if st.session_state.get("search_error"):
        st.sidebar.error(st.session_state["search_error"])

    if selected:
        _render_stock_dashboard(selected, status)
        # Enable periodic updates only after the requested page has rendered.
        setup_autorun(status["is_open"])
    else:
        _render_home_page()
