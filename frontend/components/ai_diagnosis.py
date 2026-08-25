# -*- coding: utf-8 -*-
"""
AI 多重週期滑動評分元件（Frontend — AI Diagnosis）
自 backups/scoring_system.py 安全移植至前端元件層，未更動備份檔。

評分架構（總分 100 分）：
  60日波段趨勢 (30) + 20日主力籌碼 (40) + 7日爆發動能 (30)

行動指引（雙情境：未持股 / 已有持股）：
  均線優先——站上月線（現價 >= 20MA）絕對不輸出紅燈，僅依主力買/賣超
  分「積極關注 / 暫不追高」；跌破月線且主力持續賣超才一票否決紅燈。
  不再使用綜合得分區間判斷。

效能：get_cached_ai_diagnosis 以 @st.cache_data(ttl=3600) 快取診斷結果，
同支股票 1 小時內重複檢視直接自記憶體讀取（0 秒回應）；
資料實際更新（K 線 / 籌碼 / 基本面變動）時自動重新計算。
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
    return 100 - 100 / (1 + rs)


def compute_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """計算 MACD（DIF、MACD 柱狀體、訊號線）。"""
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    macd_bar = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - macd_bar
    return dif, macd_bar, hist


# ────────────────────── 均線優先雙情境建議 ──────────────────────

_ADVICE_VETO_NO_POS = "🔴 嚴禁進場（趨勢偏弱/主力離場，保留現金）"
_ADVICE_VETO_HAS_POS = "🚨 建議果斷出場/避險（破位或主力出貨，提防擴大虧損）"
_ADVICE_ABOVE_BUY_NO_POS = "🟢 積極關注（多頭強勢，沿 5 日線操作）"
_ADVICE_ABOVE_BUY_HAS_POS = "🚀 強勢續抱（沿 5 日線移動停利）"
_ADVICE_ABOVE_SELL_NO_POS = "🟡 暫不追高（多頭但主力調節，觀望拉回）"
_ADVICE_ABOVE_SELL_HAS_POS = "⚠️ 逢高獲利入袋（分批拉高落袋）"
# 保守退路：跌破月線但主力未「持續」賣超（不構成紅燈條件）
_ADVICE_BELOW_CALM_NO_POS = "🟡 等待回穩（跌破月線但主力未連續調節，暫勿急進）"
_ADVICE_BELOW_CALM_HAS_POS = "⚠️ 收緊防守（以 20MA 為停損線，無力收復則減碼）"


def _main_force_signals(chip: dict) -> tuple[float, bool]:
    """組合主力動向代理指標（chip 籌碼欄位為連買賣天數與家數差）。

    回傳：
      main_20d : 主力近 20 日淨方向代理值（外資+投信+自營商連買賣天數合計，
                 正 = 買超、負 = 賣超）
      sell_3d  : 近 3 日主力持續賣超（三大法人同步連續賣超至少 3 日）
    """
    foreign_days = float(chip.get("foreign", 0))
    it_days = float(chip.get("it", 0))
    dealer_days = float(chip.get("dealer", 0))
    main_20d = foreign_days + it_days + dealer_days
    sell_3d = min(foreign_days, it_days, dealer_days) <= -3
    return main_20d, sell_3d


def _chip_prev_day(chip: dict) -> dict:
    """回推一日前的籌碼快照：連買/連賣天數計數各退 1 天（其餘欄位沿用今日）。"""
    def _shift(days: int) -> int:
        if days > 0:
            return days - 1
        if days < 0:
            return days + 1
        return 0

    prev = dict(chip)
    for key in ("foreign", "it", "dealer"):
        prev[key] = _shift(int(chip.get(key, 0)))
    return prev


def _chip_turn_label(chip: dict) -> str | None:
    """比對今昨主力動向，偵測籌碼轉折並回傳標籤（無轉折回傳 None）。

    昨日代理值：以連買賣天數各退 1 天估算後取三合一淨方向；
    昨日合計為 0（中性）時併入對側判定——今日淨買視同「由賣轉買」、
    今日淨賣視同「由買轉賣」，確保剛起漲 / 剛轉弱的個股也能被提醒。
    """
    main_now, _ = _main_force_signals(chip)
    main_prev, _ = _main_force_signals(_chip_prev_day(chip))
    if main_prev <= 0 < main_now:
        return "🔄 主力由賣轉買"
    if main_prev >= 0 > main_now:
        return "⚠️ 主力由買轉賣"
    return None


def decide_action_advice(df: pd.DataFrame, chip: dict) -> tuple[str, str, str]:
    """雙情境行動指引決策（均線優先版）。

    邏輯層級：
      1. 現價 >= 20MA（站上月線）→ 均線優先，絕對不輸出紅燈：
         主力買超 → 積極關注；主力賣超 → 暫不追高。
      2. 現價 < 20MA 且 主力/法人持續賣超 → 一票否決紅燈
         （嚴禁進場 / 果斷出場避險）。
      3. 跌破月線但主力未持續賣超 → 保守黃燈退路（不誤殺）。

    回傳：(advice_no_position, advice_has_position, decision_note)
    """
    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])
    ma20_val = df["MA20"].iloc[-1]
    ma20 = float(ma20_val) if not pd.isna(ma20_val) else last

    main_20d, sell_3d = _main_force_signals(chip)
    main_buying = main_20d >= 0

    # ── 除錯輸出：確保現價 / 月線 / 主力數據比對正確 ──
    _debug_print(
        f"[Debug Advice] 現價:{last}, 20MA:{ma20}, 主力:{main_20d}"
        + ("（近3日三大法人持續賣超）" if sell_3d else "")
    )

    # ── 第一順位：站上月線 → 均線優先，禁止紅燈 ──
    if last >= ma20:
        if main_buying:
            note = (
                f"月線之上＋主力買超（{main_20d:+.0f}，"
                f"現價 {last:.2f} >= 20MA {ma20:.2f}）→ 多頭強勢"
            )
            return _ADVICE_ABOVE_BUY_NO_POS, _ADVICE_ABOVE_BUY_HAS_POS, note
        note = (
            f"月線之上＋主力賣超（{main_20d:+.0f}，"
            f"現價 {last:.2f} >= 20MA {ma20:.2f}）→ 觀望主力調節"
        )
        return _ADVICE_ABOVE_SELL_NO_POS, _ADVICE_ABOVE_SELL_HAS_POS, note

    # ── 第二順位：跌破月線 → 僅在主力/法人持續賣超時紅燈 ──
    if (not main_buying) and sell_3d:
        note = (
            f"一票否決：現價 {last:.2f} < 20MA {ma20:.2f}"
            f"且主力淨賣超（{main_20d:+.0f}）、近3日持續出貨"
        )
        return _ADVICE_VETO_NO_POS, _ADVICE_VETO_HAS_POS, note

    # ── 保守退路：跌破月線但主力未持續出貨 → 不誤判紅燈 ──
    note = (
        f"跌破月線（現價 {last:.2f} < 20MA {ma20:.2f}）"
        f"但主力未持續賣超（{main_20d:+.0f}）→ 保守觀望"
    )
    return _ADVICE_BELOW_CALM_NO_POS, _ADVICE_BELOW_CALM_HAS_POS, note


def calculate_precise_ai_score(
    df: pd.DataFrame,
    chip: dict,
    fund: dict,
    ticker: str = "",
    debug_log: bool = True,
    _compute_delta: bool = True,
) -> dict:
    """多重週期滑動評分（總分 100 分）：
      60日波段趨勢 (30%) + 20日主力籌碼 (40%) + 7日爆發動能 (30%)

    7日爆發動能細部組成：量能爆發(12) + 價格動量(9) + 突破信號(4) + RSI/MACD 動能確認(5)

    debug_log=True 時 print 各子項目得分細節，便於確認是資料傳錯還是算法過苛。
    回傳 dict：
      total_score     : 0~100 綜合分數
      signal_type     : 燈號標籤
      action_advice   : 白話行動指引
      score_breakdown : { trend_60d, chip_20d, momentum_7d } 各自子分數
      score_delta     : 較前一日總分變化（int；資料不足或失敗時 None）
      chip_turn       : 主力籌碼轉折標籤（無轉折 None）
    """
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

    # ══════════════════════════════════════════════════════════
    #  維度二：20日主力籌碼 — 權重 40 分 (40%)
    #  過濾隔日沖與假買
    # ══════════════════════════════════════════════════════════
    chip_score = 0.0
    c_inst = c_main = c_ldelta = c_margin = c_holder = 0.0

    foreign_days = chip.get("foreign", 0)       # 外資連買/賣天數（正=買）
    it_days = chip.get("it", 0)                 # 投信連買/賣天數
    dealer_days = chip.get("dealer", 0)         # 自營商連買/賣天數
    main_diff = chip.get("main_diff", 0)        # 主力買賣家數差
    large_holder = chip.get("large_holder", 50) # 大戶持股比重 %
    large_delta = chip.get("large_delta", 0)    # 大戶持股比重變化
    margin_chg = chip.get("margin_chg", 0)      # 融資餘額變動 %

    # (a) 法人淨買天數綜合 — 最高 18 分
    #     【放寬】改以 10 日為滿買基準（原 20 日過苛，多頭股常低於 15 分）
    c_inst = min(10.0, max(foreign_days, 0) / 10.0 * 10) + min(8.0, max(it_days, 0) / 10.0 * 8)
    chip_score += c_inst

    # (b) 主力買賣家數差 — 最高 8 分
    #     【放寬】正差以 10 家為滿分基準（原 20 家）
    if main_diff > 0:
        c_main = min(8.0, (main_diff / 10.0) * 8)
    else:
        c_main = max(0.0, 3 + (main_diff / 20.0) * 3)
    chip_score += c_main

    # (c) 大戶持股集中度變化 — 最高 8 分
    if large_delta > 0:
        c_ldelta = min(8.0, large_delta * 2)
    elif large_delta < -1:
        c_ldelta = max(0.0, 3 + large_delta * 1.5)
    else:
        c_ldelta = 3.0
    chip_score += c_ldelta

    # (d) 融資融券過濾：融資暴增代表散戶追高（扣分）
    if margin_chg > 8:
        c_margin = -4.0
    elif margin_chg > 3:
        c_margin = -1.0
    elif margin_chg < -5:
        c_margin = 4.0
    elif margin_chg < -2:
        c_margin = 2.0
    chip_score += c_margin

    # (e) 大戶持股比重基準加分
    if large_holder >= 60:
        c_holder = 4.0
    elif large_holder >= 50:
        c_holder = 2.0
    chip_score += c_holder

    chip_score = float(np.clip(chip_score, 0, 40))

    # ══════════════════════════════════════════════════════════
    #  維度三：7日爆發動能 — 權重 30 分 (30%)
    #  量能爆發(12) + 價格動量(9) + 突破信號(4) + RSI/MACD 動能確認(5)
    #  【BUG 修正】原版完全未採計 RSI / MACD，導致「RSI>50 且 MACD>0」的
    #  多方個股動能分數近乎掛零；並修正價格動量權重的對位錯置。
    # ══════════════════════════════════════════════════════════
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
    #  總分 = 趨勢(30) + 籌碼(40) + 動能(30)
    # ══════════════════════════════════════════════════════════
    total = int(round(float(trend_score + chip_score + momentum_score)))
    total = int(np.clip(total, 0, 100))

    # 【門檻調整】放寬黃燈區間：>=75 綠燈、60~74 黃燈、<60 紅燈
    if total >= 75:
        signal_type = "75分以上: 🟢大戶鎖碼波段股（強勢）"
    elif total >= 60:
        signal_type = "60-74分: 🟡籌碼沉澱中"
    else:
        signal_type = "<60分: 🔴趨勢偏弱/觀望"

    # ══════════════════════════════════════════════════════════
    #  與前一日比較：總分變化與主力籌碼轉折
    #  以 df 去掉最後一根 K + 籌碼連買賣天數回推一日，
    #  重跑同一評分引擎取得「昨日總分」再相減。
    # ══════════════════════════════════════════════════════════
    score_delta = None
    chip_turn = _chip_turn_label(chip)
    if _compute_delta and len(df) >= 31:
        try:
            prev_result = calculate_precise_ai_score(
                df.iloc[:-1],
                _chip_prev_day(chip),
                fund,
                ticker=ticker,
                debug_log=False,
                _compute_delta=False,
            )
            score_delta = int(total - prev_result["total_score"])
        except Exception:
            score_delta = None

    # ── 雙情境行動指引：均線優先（月線之上禁止紅燈）──
    # 【重構】棄用原「綜合得分區間判斷」（>=75 / >=60 映射）。
    # 現價 >= 20MA 時絕不輸出紅燈，僅依主力買/賣超分「積極關注 / 暫不追高」；
    # 唯有跌破 20MA 且主力/法人持續賣超才一票否決輸出紅燈。
    advice_no_position, advice_has_position, advice_decision = decide_action_advice(df, chip)
    action_advice = advice_no_position  # 相容舊欄位：預設顯示未持股視角

    # ── Debug Log：輸出各子項目得分，便於確認是資料傳錯還是算法過苛 ──
    if debug_log:
        _debug_print("=" * 64)
        _debug_print(f"[AI Score] {ticker or '(未知標的)'}　收盤 {last:.2f}")
        _debug_print(
            f"[AI Score] 趨勢 {trend_score:.1f}/30 = 排列 {t_align:.0f} + MA20斜率 {t_slope:.1f}"
            f" + MA60支撐 {t_support:.1f}（回測 {support_touches} 次）"
        )
        _debug_print(
            f"[AI Score] 籌碼 {chip_score:.1f}/40 = 法人 {c_inst:.1f} + 主力差 {c_main:.1f}"
            f" + 大戶變化 {c_ldelta:.1f} + 融資調整 {c_margin:+.1f} + 大戶比重 {c_holder:.0f}"
        )
        _debug_print(
            f"[AI Score] 動能 {momentum_score:.1f}/30 = 量能加權 {m_vol:.1f} + 價格動量 {m_price:.1f}"
            f" + 突破 {m_break:.0f} + RSI/MACD確認 {m_rsi_macd:.0f}"
        )
        _debug_print(
            f"[AI Score] 輸入檢查：外資 {foreign_days}／投信 {it_days}／主力差 {main_diff}／"
            f"大戶 {large_holder}%（{large_delta:+.1f}）／融資 {margin_chg:+.1f}%｜"
            f"RSI={rsi_now:.1f}　MACD柱={hist_now:+.2f}　量能加權比={weighted_vol_score:.2f}"
        )
        _debug_print(f"[AI Advice] {advice_decision}")
        if score_delta is not None:
            delta_txt = f"較前日 {score_delta:+d} 分"
        else:
            delta_txt = "較前日 --（資料不足或計算失敗）"
        if chip_turn:
            delta_txt += f"｜{chip_turn}"
        _debug_print(f"[AI Delta] {delta_txt}")
        _debug_print(f"[AI Score] 總分 {total}/100 → {signal_type}｜未持股 {advice_no_position}｜持股 {advice_has_position}")

    return {
        "total_score": total,
        "signal_type": signal_type,
        "action_advice": action_advice,
        "advice_no_position": advice_no_position,
        "advice_has_position": advice_has_position,
        "score_breakdown": {
            "trend_60d": round(float(trend_score), 2),
            "chip_20d": round(float(chip_score), 2),
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
    pe_desc = f"P/E = {pe:.1f}倍（合理）" if pe is not None and 10 <= pe <= 25 else \
              f"P/E = {pe:.1f}倍（偏高）" if pe is not None and pe > 25 else \
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
        "signal_type": signal_type,
        "action_advice": action_advice,
        "advice_no_position": advice_no_position,
        "advice_has_position": advice_has_position,
        "score_breakdown": score_breakdown,
        "score_delta": score_delta,
        "chip_turn": chip_turn,
        "fundamental_text": fundamental_text,
        "ma_text": ma_text,
        "rsi_text": rsi_text,
        "macd_text": macd_text,
        "vol_text": vol_text,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_ai_diagnosis(ticker: str, df: pd.DataFrame, chip: dict, fund: dict) -> dict:
    """AI 診斷快取層（ttl = 3600 秒 = 1 小時）。

    同支股票在資料未變動的前提下，1 小時內重複檢視直接命中記憶體快取，
    評分計算 0 秒完成；K 線 / 籌碼 / 基本面任一數據變動時自動重算。
    """
    return get_global_precise_diagnosis(ticker, df, chip, fund)


def render_ultimate_diagnosis_card(diag: dict) -> None:
    """渲染 AI 多重週期滑動評分診斷卡片。"""
    score = diag["score"]
    signal_type = diag.get("signal_type", "")
    score_breakdown = diag.get("score_breakdown", {})

    icon = '🟢' if '大戶鎖碼' in signal_type else ('🟡' if '籌碼沉澱' in signal_type else '🔴')

    # 【門檻調整】卡片配色同步放寬：>=75 綠、>=60 黃、其餘紅
    if score >= 75:
        color = "#10B981"
        glow = "rgba(16,185,129,0.40)"
    elif score >= 60:
        color = "#F59E0B"
        glow = "rgba(245,158,11,0.35)"
    else:
        color = "#EF4444"
        glow = "rgba(239,68,68,0.40)"
    status_color = color

    trend_60d = score_breakdown.get("trend_60d", 0)
    chip_20d = score_breakdown.get("chip_20d", 0)
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
      <div style="font-size:1.35rem;font-weight:900;color:{color};letter-spacing:1px;">AI 多重週期滑動評分：{signal_type}</div>
      <div style="color:#9CA3AF;font-size:0.82rem;margin-top:2px;">綜合評分 {score} / 100　｜　{delta_html}{turn_html}</div>
      <div style="color:#9CA3AF;font-size:0.82rem;margin-top:2px;">60日趨勢({trend_60d:.0f}/30) + 20日籌碼({chip_20d:.0f}/40) + 7日動能({momentum_7d:.0f}/30)</div>
    </div>
  </div>

  <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px;">
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #10B981;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📈 60日波段趨勢 ({trend_60d:.0f}/30)</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("ma_text", "均線數據解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #3B82F6;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">💪 20日主力籌碼 ({chip_20d:.0f}/40)</div>
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
    多重週期滑動評分：60日趨勢(30) + 20日籌碼(40) + 7日動能(30)　|　僅供參考
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

    綠燈標準：total_score >= 75 分。
    單檔執行：python frontend/components/ai_diagnosis.py
    """
    from backend.services.stock_fetcher import fetch_stock_data, generate_chip_data
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
            chip = generate_chip_data(ticker)
            fund = get_fundamental(ticker)
            diag = get_global_precise_diagnosis(ticker, df, chip, fund, debug_log=False)
            results.append(diag)
            mark = "🟢" if diag["score"] >= 75 else ("🟡" if diag["score"] >= 60 else "🔴")
            _debug_print(
                f"[GreenLight] {mark} {name} ({diag['code']})　總分 {diag['score']}/100　"
                f"{diag['signal_type']}｜未持股 {diag['advice_no_position']}｜持股 {diag['advice_has_position']}"
            )
        except Exception as exc:
            _debug_print(f"[GreenLight] ⚠️ {name} ({ticker})　計算失敗：{exc}")

    greens = [r for r in results if r["score"] >= 75]
    if greens:
        summary = "、".join(f"{r['name']}({r['code']}) {r['score']}分" for r in greens)
        _debug_print(f"[GreenLight] 🟢 達到綠燈標準共 {len(greens)} 支：{summary}")
    else:
        _debug_print("[GreenLight] 目前沒有達到綠燈標準（>=75 分）的熱門標的")
    return results


if __name__ == "__main__":
    debug_find_green_light_stocks()
