"""Persistent, adjusted daily Yahoo prices; simulated prices never enter this store."""

from pathlib import Path

import numpy as np
import pandas as pd

from backend.services.database import database_connection


def initialize_stock_storage():
    sql = (Path(__file__).resolve().parents[2] / "database" / "schema.sql").read_text(encoding="utf-8")
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)


def save_daily_prices(ticker, history_period, frame, name="", *, source):
    if source != "yfinance":
        raise ValueError("Only genuine yfinance prices may be stored.")
    data = frame[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    data["Date"] = pd.to_datetime(data["Date"]).dt.date
    data = data.sort_values("Date").drop_duplicates("Date", keep="last")
    values = data[["Open", "High", "Low", "Close", "Volume"]].to_numpy(dtype=float)
    if len(data) < 30 or data["Date"].isna().any() or not np.isfinite(values).all():
        raise ValueError("Incomplete daily prices.")
    if (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError("Invalid prices or volume.")
    rows = [
        (ticker, date, float(o), float(h), float(l), float(c), int(v))
        for date, o, h, l, c, v in data.itertuples(index=False, name=None)
    ]
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO money_app.stocks (ticker, name) VALUES (%s, %s)
                ON CONFLICT (ticker) DO UPDATE SET
                    name = CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE stocks.name END,
                    updated_at = CURRENT_TIMESTAMP
            """, (ticker, name))
            cursor.executemany("""
                INSERT INTO money_app.daily_prices
                    (ticker, trade_date, open, high, low, close, volume, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'yfinance')
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    close = EXCLUDED.close, volume = EXCLUDED.volume,
                    source = EXCLUDED.source, updated_at = CURRENT_TIMESTAMP
            """, rows)
            cursor.execute("""
                INSERT INTO money_app.price_downloads
                    (ticker, history_period, first_date, last_date, row_count)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ticker, history_period) DO UPDATE SET
                    first_date = EXCLUDED.first_date, last_date = EXCLUDED.last_date,
                    row_count = EXCLUDED.row_count, fetched_at = CURRENT_TIMESTAMP
            """, (ticker, history_period, rows[0][1], rows[-1][1], len(rows)))
    return len(rows)


def load_daily_prices(ticker, history_period, max_age_seconds=300):
    """Return (frame, refreshed time), or None for missing/expired data.

    Set max_age_seconds=None only for an explicitly labelled offline fallback.
    """
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT first_date, last_date, row_count, fetched_at,
                       EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - fetched_at))
                FROM money_app.price_downloads WHERE ticker = %s AND history_period = %s
            """, (ticker, history_period))
            metadata = cursor.fetchone()
            if metadata is None:
                return None
            first_date, last_date, expected, fetched_at, age = metadata
            if max_age_seconds is not None and float(age) > max_age_seconds:
                return None
            cursor.execute("""
                SELECT trade_date, open, high, low, close, volume
                FROM money_app.daily_prices
                WHERE ticker = %s AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date
            """, (ticker, first_date, last_date))
            rows = cursor.fetchall()
    if len(rows) != expected or len(rows) < 30:
        return None
    frame = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    frame["Date"] = pd.to_datetime(frame["Date"])
    for days in (5, 20, 60):
        frame[f"MA{days}"] = frame["Close"].rolling(days).mean()
    frame.attrs["price_source"] = "yfinance"
    frame.attrs["is_stale"] = False
    frame.attrs["fetched_at"] = fetched_at.isoformat()
    return frame, fetched_at


def get_storage_summary():
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM money_app.stocks")
            stocks = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*), MAX(updated_at) FROM money_app.daily_prices")
            prices, updated_at = cursor.fetchone()
    return stocks, prices, updated_at


if __name__ == "__main__":
    initialize_stock_storage()
    print("Initialized money_app stock storage.")
