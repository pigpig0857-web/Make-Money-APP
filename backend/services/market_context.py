"""Official TPEx monthly index history for Yahoo's unavailable OTC series."""

from datetime import date
import math

import pandas as pd
import requests
import streamlit as st

from backend.services.database import database_connection


def parse_tpex_index(payload, month):
    if str(payload.get('stat', '')).lower() != 'ok':
        raise ValueError('櫃買指數報表未成功。')
    tables = payload.get('tables', [])
    if not tables:
        raise ValueError('櫃買指數報表缺資料。')
    table = tables[0]
    if table.get('fields', [])[0:1] != ['日期'] or len(table.get('fields', [])) != 6 or table['fields'][4] != '櫃買指數':
        raise ValueError('櫃買指數欄位變更，暫不比較。')
    if str(payload.get('date', ''))[:6] != month.strftime('%Y%m'):
        raise ValueError('櫃買指數月份不符。')
    rows = []
    for raw in table.get('data', []):
        if not isinstance(raw, list) or len(raw) != 6:
            raise ValueError('櫃買指數格式不符。')
        parts = str(raw[0]).split('/')
        if len(parts) != 3:
            raise ValueError('櫃買指數格式不符。')
        day = date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
        close = float(str(raw[4]).replace(',', ''))
        if day.replace(day=1) != month or not math.isfinite(close) or close <= 0:
            raise ValueError('櫃買指數日期或價格無效。')
        rows.append((day, close))
    if not rows or len({r[0] for r in rows}) != len(rows):
        raise ValueError('櫃買指數沒有唯一有效日期。')
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tpex_month(month):
    response = requests.get('https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex',
                            params={'date': month.strftime('%Y/%m/%d'), 'response': 'json'}, timeout=12)
    response.raise_for_status()
    return parse_tpex_index(response.json(), month)


def load_tpex_benchmark(start, end):
    rows, offline, stored = [], False, True
    try:
        for month in pd.period_range(start, end, freq='M'):
            rows.extend(fetch_tpex_month(month.start_time.date()))
        try:
            with database_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany('''INSERT INTO money_app.market_index_closes
                        (symbol, trade_date, close, source) VALUES ('TPEX', %s, %s, 'TPEx')
                        ON CONFLICT (symbol, trade_date) DO UPDATE SET close = EXCLUDED.close,
                        retrieved_at = CURRENT_TIMESTAMP''', rows)
        except Exception:
            stored = False
    except (requests.RequestException, ValueError):
        offline = True
        try:
            with database_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('''SELECT trade_date, close FROM money_app.market_index_closes
                        WHERE symbol = 'TPEX' AND trade_date BETWEEN %s AND %s ORDER BY trade_date''', (start, end))
                    rows = cursor.fetchall()
        except Exception:
            rows = []
    if not rows:
        raise ValueError('官方櫃買指數暫無可用歷史資料。')
    frame = pd.DataFrame(rows, columns=['Date', 'Close']).sort_values('Date')
    frame['Date'] = pd.to_datetime(frame['Date'])
    frame.attrs.update(price_source='TPEx', is_stale=offline)
    source = 'TPEx 官方日成交量值指數（離線歷史）' if offline else 'TPEx 官方日成交量值指數'
    source += '（已存入資料庫）' if stored else '（資料庫儲存失敗）'
    return frame, source
