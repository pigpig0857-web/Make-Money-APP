"""Versioned official revenue observations and forward score measurements."""

import hashlib
import json

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.services.database import database_connection


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, default=str).encode()).hexdigest()


def save_revenues(rows):
    if not rows:
        return
    if any(r['source'] not in ('TWSE', 'TPEx') for r in rows):
        raise ValueError('Unverified revenue source.')
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany('''INSERT INTO money_app.stocks (ticker, name)
                VALUES (%s, %s) ON CONFLICT (ticker) DO UPDATE SET
                name = EXCLUDED.name''', [(r['ticker'], r['name']) for r in rows])
            cursor.executemany('''INSERT INTO money_app.monthly_revenues
                (ticker, revenue_month, report_date, industry, revenue_thousand,
                 previous_month_thousand, previous_year_thousand, yoy_pct, mom_pct,
                 cumulative_yoy_pct, source, input_hash, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, revenue_month, input_hash) DO UPDATE
                SET last_seen_at = CURRENT_TIMESTAMP''', [
                (r['ticker'], r['revenue_month'], r['report_date'], r['industry'],
                 r['revenue_thousand'], r['previous_month_thousand'], r['previous_year_thousand'],
                 r['yoy_pct'], r['mom_pct'], r['cumulative_yoy_pct'], r['source'],
                 fingerprint(r['payload']), Jsonb(r['payload'])) for r in rows])


def load_revenues(source):
    with database_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('''SELECT DISTINCT ON (r.ticker, r.revenue_month) r.*, s.name
                FROM money_app.monthly_revenues r JOIN money_app.stocks s ON s.ticker = r.ticker
                WHERE r.source = %s
                ORDER BY r.ticker, r.revenue_month DESC, r.first_seen_at DESC, r.input_hash''', (source,))
            return cursor.fetchall()


def load_tracking_snapshots(ticker, limit=100):
    with database_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('''SELECT id, ticker, price_date, score, score_max,
                model_version, recorded_at FROM money_app.score_snapshots
                WHERE ticker = %s ORDER BY recorded_at DESC, id DESC LIMIT %s''', (ticker, limit))
            return cursor.fetchall()


def save_outcomes(rows):
    completed = [r for r in rows if r['status'] == 'complete']
    if not completed:
        return
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany('''INSERT INTO money_app.score_outcomes
                (snapshot_id, horizon, calculation_version, entry_date, end_date,
                 return_pct, lowest_close_return_pct, price_source, input_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'yfinance', %s)
                ON CONFLICT (snapshot_id, horizon, calculation_version) DO UPDATE SET
                entry_date = EXCLUDED.entry_date, end_date = EXCLUDED.end_date,
                return_pct = EXCLUDED.return_pct,
                lowest_close_return_pct = EXCLUDED.lowest_close_return_pct,
                input_hash = EXCLUDED.input_hash, updated_at = CURRENT_TIMESTAMP''', [
                (r['snapshot_id'], r['horizon'], r['calculation_version'], r['entry_date'],
                 r['end_date'], r['return_pct'], r['lowest_close_return_pct'], r['input_hash'])
                for r in completed])


def get_research_counts():
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''SELECT (SELECT COUNT(*) FROM money_app.monthly_revenues),
                (SELECT COUNT(*) FROM money_app.score_outcomes),
                (SELECT COUNT(DISTINCT ticker) FROM money_app.daily_prices)''')
            return cursor.fetchone()
