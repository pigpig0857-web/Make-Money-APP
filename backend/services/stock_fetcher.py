# -*- coding: utf-8 -*-
"""
股票數據抓取模組（Backend — Stock Fetcher）
僅使用 yfinance 與 PostgreSQL 真實行情；不產生模擬行情或籌碼。
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

from backend.services.stock_master import STOCK_INFO, TW_STOCK_NAMES
from backend.services.stock_repository import load_daily_prices, save_daily_prices


# ────────────────────── K 線週期歷史跨度 ──────────────────────

_PERIOD_HISTORY = {
    "日 K": "2y",
    "週 K": "5y",
    "月 K": "max",
}


# ────────────────────── 無真實資料時停止分析 ──────────────────────

class StockDataUnavailableError(Exception):
    """No genuine market data is available for this stock."""


# ────────────────────── 日 K 數據下載 ──────────────────────

def _download_daily(ticker: str, history_period: str = "1y", *, force_refresh=False):
    """讀取近期資料庫行情；過期才下載，失敗時保留已存行情。"""
    database_available = True
    try:
        stored = load_daily_prices(ticker, history_period)
        if stored is not None and not force_refresh:
            frame, refreshed = stored
            return frame, f"PostgreSQL（Yahoo Finance；更新 {refreshed.astimezone(ZoneInfo('Asia/Taipei')):%Y-%m-%d %H:%M}）"
    except Exception:
        database_available = False

    source = "Yahoo Finance 日 K 資料"
    df = None
    try:
        import yfinance as yf

        raw = yf.download(ticker, period=history_period, interval="1d", progress=False, auto_adjust=True)
        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw = raw.reset_index()
            cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
            df = raw[cols].dropna().copy()
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").reset_index(drop=True)
            if len(df) < 30:
                df = None
            else:
                df["MA5"] = df["Close"].rolling(5).mean()
                df["MA20"] = df["Close"].rolling(20).mean()
                df["MA60"] = df["Close"].rolling(60).mean()
                df.attrs["price_source"] = "yfinance"
                df.attrs["is_stale"] = False
                source = "Yahoo Finance 日 K 資料"
    except Exception:
        df = None

    if df is not None:
        try:
            name = STOCK_INFO.get(ticker, {}).get("name") or TW_STOCK_NAMES.get(ticker.split(".")[0], "")
            save_daily_prices(ticker, history_period, df, name, source="yfinance")
            source += "（已存入 PostgreSQL）"
        except Exception:
            source += "（資料庫儲存失敗，本次仍可分析）"
    else:
        if database_available:
            try:
                stored = load_daily_prices(ticker, history_period, max_age_seconds=None)
                if stored is not None:
                    frame, refreshed = stored
                    frame.attrs["is_stale"] = True
                    return frame, f"PostgreSQL 離線歷史資料（更新 {refreshed.astimezone(ZoneInfo('Asia/Taipei')):%Y-%m-%d %H:%M}；非最新行情）"
            except Exception:
                pass
        raise StockDataUnavailableError("目前無法取得真實行情；未產生評分，請稍後重試。")
    return df, source


@st.cache_data(ttl=300, show_spinner=False)
def fetch_stock_data(ticker: str, price_policy_version="real-only-v1"):
    """5-minute cache; policy key excludes prices cached by the old mock policy."""
    return _download_daily(ticker)


# ────────────────────── 即時報價 ──────────────────────

TAIWAN_TZ = ZoneInfo("Asia/Taipei")


def _is_tw_session_now() -> bool:
    """台股開盤時段：週一至週五 09:00-13:30（Asia/Taipei）。"""
    now = datetime.now(TAIWAN_TZ)
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 <= minutes <= 13 * 60 + 30


@st.cache_data(ttl=300, show_spinner=False)
def _previous_close(ticker: str):
    """取得昨日收盤價（剔除今日盤中未完成日 K 後的最後一筆收盤）。"""
    import yfinance as yf

    hist = yf.Ticker(ticker).history(period="5d", interval="1d")
    if hist is None or hist.empty:
        return None
    today = datetime.now(TAIWAN_TZ).date()
    hist = hist[[ts.date() < today for ts in hist.index]]
    closes = hist["Close"].dropna()
    if closes.empty:
        return None
    return float(closes.iloc[-1])


@st.cache_data(ttl=15, show_spinner=False)
def fetch_live_price(ticker: str):
    """開盤期間抓取 1 分鐘等級最新成交資料（漲跌基準統一為昨日收盤價）。

    開盤防呆：台股開盤時段（09:00-13:30）若取得的現價與昨日收盤價相同
    （yfinance 資料延遲），改呼叫 ticker.history(period='1d', interval='1m')
    取最後一筆 1 分鐘 K 線的 Close 作為即時現價。
    """
    try:
        import yfinance as yf

        raw = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        close = raw["Close"].dropna()
        if close.empty:
            return None
        price = float(close.iloc[-1])
        ts = close.index[-1]

        try:
            prev_close = _previous_close(ticker)
        except Exception:
            prev_close = None

        # 開盤防呆：盤中現價仍等於昨日收盤 → 以 Ticker.history 1 分 K 最後一筆重取
        if (
            _is_tw_session_now()
            and prev_close
            and np.isclose(price, prev_close, rtol=0.0, atol=1e-6)
        ):
            try:
                hist_1m = yf.Ticker(ticker).history(period="1d", interval="1m")
                if hist_1m is not None and not hist_1m.empty:
                    last_bar = hist_1m["Close"].dropna()
                    if not last_bar.empty:
                        price = float(last_bar.iloc[-1])
                        ts = last_bar.index[-1]
            except Exception:
                pass

        if not prev_close or prev_close <= 0:
            return {"price": price, "change": 0.0, "change_pct": 0.0, "ts": ts}

        # 統一漲跌計算：漲跌金額 = 最新現價 - 昨日收盤價；漲跌幅 % = 差額 / 昨收 * 100
        change = price - prev_close
        change_pct = (price - prev_close) / prev_close * 100
        return {"price": price, "change": change, "change_pct": change_pct, "ts": ts}
    except Exception:
        return None


# ────────────────────── 重抽樣工具 ──────────────────────

def _month_rule() -> str:
    try:
        ver = tuple(int(x) for x in re.findall(r"\d+", pd.__version__)[:3]) or (1, 0, 0)
    except Exception:
        ver = (1, 0, 0)
    return "ME" if ver >= (2, 2, 0) else "M"


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    d = df.set_index("Date")
    out = (
        d.resample(rule)
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
    )
    return out


def resample_kline(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """將日 K 資料轉為指定週期（日 K / 週 K / 月 K）之 K 線 DataFrame。"""
    if period == "日 K":
        out = df.copy()
    else:
        rule = "W-FRI" if period == "週 K" else _month_rule()
        out = resample_ohlc(df, rule).reset_index()
        out.columns = [str(c) for c in out.columns]
    close = out["Close"]
    out["MA5"] = close.rolling(5, min_periods=1).mean()
    out["MA20"] = close.rolling(20, min_periods=1).mean()
    out["MA60"] = close.rolling(60, min_periods=1).mean()
    return out.reset_index(drop=True)


# Real chip feeds have not been connected yet. Missing values stay missing.
def get_chip_data(ticker: str, trade_dates=None) -> dict:
    from backend.services.institutional_fetcher import get_institutional_data
    return get_institutional_data(ticker, trade_dates)
