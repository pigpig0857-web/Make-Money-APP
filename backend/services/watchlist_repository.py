"""Local database watchlist; removing a watch never deletes historical data."""

import re
from backend.services.database import database_connection


def add_watch(ticker, name=''):
    if not re.fullmatch(r'\d{4,6}\.(TW|TWO)', ticker):
        raise ValueError('只支援上市／上櫃股票代號。')
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''INSERT INTO money_app.stocks (ticker, name) VALUES (%s, %s)
                ON CONFLICT (ticker) DO NOTHING''', (ticker, name))
            cursor.execute('''INSERT INTO money_app.watchlist (ticker) VALUES (%s)
                ON CONFLICT DO NOTHING''', (ticker,))


def list_watches():
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''SELECT w.ticker, s.name FROM money_app.watchlist w
                JOIN money_app.stocks s ON s.ticker = w.ticker ORDER BY w.added_at, w.ticker''')
            return cursor.fetchall()


def remove_watch(ticker):
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM money_app.watchlist WHERE ticker = %s', (ticker,))
