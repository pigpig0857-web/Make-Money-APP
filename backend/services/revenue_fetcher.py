"""Official latest revenue tables. Never pretend these are historical filings."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
import statistics

import requests
import streamlit as st

from backend.services.research_repository import save_revenues, load_revenues

REVENUE_URLS = {
    'TWSE': 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L',
    'TPEx': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O',
}


def roc_date(value, month_only=False):
    raw = str(value).strip()
    expected = 5 if month_only else 7
    if not raw.isdigit() or len(raw) != expected:
        raise ValueError('Unexpected ROC date.')
    return date(int(raw[:3]) + 1911, int(raw[3:5]), 1 if month_only else int(raw[5:7]))


def number(value):
    if value is None or str(value).strip() in ('', '-', '--', 'N/A'):
        return None
    try:
        result = Decimal(str(value).replace(',', '').strip())
    except InvalidOperation as exc:
        raise ValueError('Invalid revenue value.') from exc
    if not result.is_finite():
        raise ValueError('Nonfinite revenue value.')
    return result


def parse_revenues(payload, source):
    if source not in REVENUE_URLS or not isinstance(payload, list) or not payload:
        raise ValueError('Unexpected official revenue table.')
    required = {'出表日期', '資料年月', '公司代號', '公司名稱', '產業別',
                '營業收入-當月營收', '營業收入-去年同月增減(%)'}
    if not isinstance(payload[0], dict) or not required <= payload[0].keys():
        raise ValueError('Official revenue fields changed.')
    rows = []
    for raw in payload:
        try:
            code = str(raw['公司代號']).strip()
            if not re.fullmatch(r'\d{4,6}', code):
                continue
            month = roc_date(raw['資料年月'], True)
            report = roc_date(raw['出表日期'])
            amount = number(raw['營業收入-當月營收'])
            if amount is None or month > report.replace(day=1) or report > date.today():
                continue
            row = {'ticker': code + ('.TW' if source == 'TWSE' else '.TWO'),
                   'name': str(raw['公司名稱']).strip(), 'industry': str(raw['產業別']).strip(),
                   'revenue_month': month, 'report_date': report, 'revenue_thousand': amount,
                   'previous_month_thousand': number(raw.get('營業收入-上月營收')),
                   'previous_year_thousand': number(raw.get('營業收入-去年當月營收')),
                   'source': source, 'payload': raw}
            for key, field in [('yoy_pct', '營業收入-去年同月增減(%)'),
                               ('mom_pct', '營業收入-上月比較增減(%)'),
                               ('cumulative_yoy_pct', '累計營業收入-前期比較增減(%)')]:
                value = number(raw.get(field))
                row[key] = float(value) if value is not None else None
            rows.append(row)
        except (ValueError, KeyError, TypeError):
            continue  # One missing company must not become a fabricated zero.
    if not rows:
        raise ValueError('No valid official revenue rows.')
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_revenue_table(source):
    response = requests.get(REVENUE_URLS[source], timeout=12)
    response.raise_for_status()
    return parse_revenues(response.json(), source), datetime.now(timezone.utc)


def get_revenue_context(ticker):
    source = 'TPEx' if ticker.endswith('.TWO') else 'TWSE' if ticker.endswith('.TW') else None
    if source is None:
        return {'available': False, 'reason': '此商品不適用公司月營收。'}
    stored, offline, observed = True, False, None
    try:
        rows, observed = fetch_revenue_table(source)
        try:
            save_revenues(rows)
        except Exception:
            stored = False
    except (requests.RequestException, ValueError):
        offline = True
        try:
            rows = load_revenues(source)
        except Exception:
            rows = []
    candidates = [r for r in rows if r['ticker'] == ticker]
    if not candidates:
        return {'available': False, 'reason': '官方月營收尚無此股票資料；未使用預設值。'}
    latest = max(candidates, key=lambda r: r['revenue_month'])
    # Exclude target; compare same market, same industry, SAME reporting month.
    peers = {r['ticker']: r for r in rows if r['ticker'] != ticker and
             r['industry'] == latest['industry'] and r['revenue_month'] == latest['revenue_month']
             and r['yoy_pct'] is not None}
    values = [r['yoy_pct'] for r in peers.values()]
    return {'available': True, 'latest': latest, 'offline': offline, 'stored': stored,
            'observed_at': observed or latest.get('last_seen_at'), 'peer_count': len(values),
            'peer_median_yoy': statistics.median(values) if len(values) >= 3 else None,
            'peer_scope': '同市場、同產業、同月份；排除本股票',
            'reason': '月營收與同業比較只作輔助，尚未納入技術分數。'}
