"""Official institutional history and immutable, reproducible score records."""

import hashlib
import json

from psycopg.types.json import Jsonb

from backend.services.database import database_connection


def save_institutional_flows(ticker, rows):
    if not rows:
        return
    if any(row["source"] not in ("TWSE", "TPEx") or
           row["total_net"] != row["foreign_net"] + row["trust_net"] + row["dealer_net"]
           for row in rows):
        raise ValueError("Unverified institutional records.")
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO money_app.stocks (ticker) VALUES (%s) ON CONFLICT DO NOTHING", (ticker,))
            cursor.executemany("""
                INSERT INTO money_app.institutional_flows
                    (ticker, trade_date, foreign_net, trust_net, dealer_net, total_net, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    foreign_net = EXCLUDED.foreign_net, trust_net = EXCLUDED.trust_net,
                    dealer_net = EXCLUDED.dealer_net, total_net = EXCLUDED.total_net,
                    source = EXCLUDED.source, retrieved_at = CURRENT_TIMESTAMP
            """, [(ticker, r["trade_date"], r["foreign_net"], r["trust_net"],
                    r["dealer_net"], r["total_net"], r["source"]) for r in rows])


def load_institutional_flows(ticker, dates):
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT trade_date, foreign_net, trust_net, dealer_net, total_net, source
                FROM money_app.institutional_flows
                WHERE ticker = %s AND trade_date = ANY(%s) ORDER BY trade_date
            """, (ticker, dates))
            return [dict(zip(("trade_date", "foreign_net", "trust_net", "dealer_net", "total_net", "source"), row))
                    for row in cursor.fetchall()]


def save_score_snapshot(ticker, frame, diagnosis):
    from frontend.components.ai_diagnosis import validate_scoring_frame
    validate_scoring_frame(frame)
    # Save the exact price inputs: later split adjustments must not overwrite
    # evidence of what the rule actually saw when it made this recommendation.
    prices = [[date.date().isoformat(), float(o), float(h), float(l), float(c), int(v)]
              for date, o, h, l, c, v in frame[["Date", "Open", "High", "Low", "Close", "Volume"]].itertuples(index=False, name=None)]
    payload = {"price_source": "yfinance", "prices": prices, "diagnosis": diagnosis}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO money_app.stocks (ticker) VALUES (%s) ON CONFLICT DO NOTHING", (ticker,))
            cursor.execute("""
                INSERT INTO money_app.score_snapshots
                    (ticker, price_date, model_version, score, score_max, input_hash, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, model_version, input_hash) DO NOTHING
                RETURNING id
            """, (ticker, frame["Date"].iloc[-1].date(), diagnosis["model_version"],
                    diagnosis["score"], diagnosis["score_max"], fingerprint, Jsonb(payload)))
            return cursor.fetchone() is not None


def load_score_history(ticker, limit=10):
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT price_date, score, score_max, model_version, recorded_at
                FROM money_app.score_snapshots WHERE ticker = %s
                ORDER BY recorded_at DESC, id DESC LIMIT %s
            """, (ticker, limit))
            return [dict(zip(("行情日期", "技術分數", "滿分", "規則版本", "記錄時間"), row))
                    for row in cursor.fetchall()]


def get_analysis_counts():
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT (SELECT COUNT(*) FROM money_app.institutional_flows), (SELECT COUNT(*) FROM money_app.score_snapshots)")
            return cursor.fetchone()
