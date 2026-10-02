"""Deterministic synthetic OHLCV for tests only; never used by the app."""

import numpy as np
import pandas as pd


def market_frame(ticker="2330.TW", days=180):
    index = np.arange(days, dtype=float)
    close = 100 + index * 0.1 + np.sin(index / 5)
    frame = pd.DataFrame({
        "Date": pd.bdate_range(end="2026-09-30", periods=days),
        "Open": close - 0.1, "High": close + 1, "Low": close - 1,
        "Close": close, "Volume": (10000 + index * 10).astype(int),
    })
    for period in (5, 20, 60):
        frame[f"MA{period}"] = frame["Close"].rolling(period).mean()
    return frame
