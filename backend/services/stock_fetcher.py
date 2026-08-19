# -*- coding: utf-8 -*-
"""
股票數據抓取模組（Backend — Stock Fetcher）
包含 yfinance 歷史數據下載、K 線圖數據處理、Mock Data 生成、
籌碼面模擬、即時報價等。
"""

import re
import zlib

import numpy as np
import pandas as pd
import streamlit as st

from backend.services.stock_master import STOCK_INFO


# ────────────────────── K 線週期歷史跨度 ──────────────────────

_PERIOD_HISTORY = {
    "日 K": "2y",
    "週 K": "5y",
    "月 K": "max",
}


# ────────────────────── 隨機種子與 Mock Data ──────────────────────

def _seed(key: str):
    return np.random.default_rng(zlib.crc32(key.encode("utf-8")))


def generate_mock_data(ticker: str, days: int = 180) -> pd.DataFrame:
    """產生可重現的離線模擬 K 線資料（含 5 / 20 / 60 日均線）。"""
    info = STOCK_INFO.get(ticker, {})
    base_price = float(info.get("base_price", 60.0))
    rng = _seed(ticker + "_kline")

    drift = float(rng.normal(0.0009, 0.0006))
    vol = float(rng.uniform(0.014, 0.024))
    theta = 0.05
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)

    x = 0.0
    xs = []
    for _ in range(days):
        x = x - theta * x + float(rng.normal(drift, vol))
        xs.append(x)
    xs = np.asarray(xs)
    close = base_price * np.exp(xs - xs[-1])
    open_ = close * (1 + rng.normal(0, 0.004, days))
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.006, days)) * close * 0.5
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.006, days)) * close * 0.5
    volume = (rng.integers(25000, 140000, days) * (1 + np.abs(np.diff(xs, prepend=0.0)) * 40)).astype(int)

    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }
    )
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    return df


# ────────────────────── 日 K 數據下載 ──────────────────────

def _download_daily(ticker: str, history_period: str = "1y"):
    """抓取日 K 級資料；失敗或延遲時自動切換 Mock Data。"""
    source = "Mock Data（離線模擬資料）"
    df = None
    try:
        import yfinance as yf

        raw = yf.download(ticker, period=history_period, interval="1d", progress=False, auto_adjust=True)
        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw = raw.reset_index()
            cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
            present = [c for c in cols if c in raw.columns]
            df = raw[present].copy()
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").reset_index(drop=True)
            if len(df) < 30:
                df = None
            else:
                df["MA5"] = df["Close"].rolling(5).mean()
                df["MA20"] = df["Close"].rolling(20).mean()
                df["MA60"] = df["Close"].rolling(60).mean()
                source = "Yahoo Finance 即時資料"
    except Exception:
        df = None

    if df is None:
        df = generate_mock_data(ticker)
    return df, source


@st.cache_data(ttl=300, show_spinner=False)
def fetch_stock_data(ticker: str):
    """閉盤時使用快取（5 分鐘），避免盤後過度請求 API。"""
    return _download_daily(ticker)


# ────────────────────── 即時報價 ──────────────────────

@st.cache_data(ttl=15, show_spinner=False)
def fetch_live_price(ticker: str):
    """開盤期間抓取 1 分鐘等級最新成交資料。"""
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
        last = float(close.iloc[-1])
        first = float(close.iloc[0])
        return {
            "price": last,
            "change": last - first,
            "change_pct": (last - first) / first * 100 if first else 0.0,
            "ts": close.index[-1],
        }
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


# ────────────────────── 籌碼面模擬 ──────────────────────

def generate_chip_data(ticker: str) -> dict:
    rng = _seed(ticker + "_chip")
    return {
        "foreign": int(rng.integers(-6, 9)),
        "it": int(rng.integers(-5, 7)),
        "dealer": int(rng.integers(-4, 7)),
        "large_holder": round(float(rng.uniform(58, 74)), 1),
        "large_delta": round(float(rng.uniform(-1.5, 3.0)), 1),
        "retail": round(float(rng.uniform(26, 42)), 1),
        "main_diff": int(rng.integers(-8, 12)),
        "margin_chg": round(float(rng.uniform(-5.0, 15.0)), 1),
    }
