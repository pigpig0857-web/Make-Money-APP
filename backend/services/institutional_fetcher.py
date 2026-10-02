"""Official TWSE/TPEx reports; absent dates and values remain missing."""

from datetime import date
import re

import requests
import streamlit as st

from backend.services.analysis_repository import load_institutional_flows, save_institutional_flows

TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"


def _integer(value):
    text = str(value).strip().replace(",", "").replace("−", "-")
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError("Missing institutional value")
    return int(text)


def parse_report(payload, market, requested_date):
    """Validate official date/layout and net totals before accepting a row."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid official report")
    if str(payload.get("stat", "")).upper() != "OK":
        return {}
    if market == "TWSE":
        if payload.get("date") != requested_date.strftime("%Y%m%d"):
            raise ValueError("Report date mismatch")
        table = payload
        fields = table.get("fields", [])
        if len(fields) != 19 or fields[0] != "證券代號" or fields[10] != "投信買賣超股數" or fields[11] != "自營商買賣超股數":
            raise ValueError("TWSE report layout changed")
    elif market == "TPEx":
        tables = payload.get("tables", [])
        if not tables:
            return {}
        table = tables[0]
        report_date = f"{requested_date.year - 1911}/{requested_date.month:02d}/{requested_date.day:02d}"
        if table.get("date") != report_date:
            raise ValueError("Report date mismatch")
        fields = table.get("fields", [])
        if len(fields) != 24 or fields[:2] != ["代號", "名稱"] or fields[23] != "三大法人買賣超股數合計":
            raise ValueError("TPEx report layout changed")
    else:
        raise ValueError("Unsupported market")
    records = {}
    for row in table.get("data", []):
        try:
            if len(row) != len(fields):
                continue
            if market == "TWSE":
                foreign = _integer(row[4]) + _integer(row[7])
                trust, dealer, total = map(_integer, (row[10], row[11], row[18]))
            else:
                foreign, trust, dealer, total = map(_integer, (row[10], row[13], row[22], row[23]))
                if foreign != _integer(row[4]) + _integer(row[7]) or dealer != _integer(row[16]) + _integer(row[19]):
                    continue
            if total != foreign + trust + dealer:
                continue
            records[str(row[0]).strip()] = {
                "trade_date": requested_date, "foreign_net": foreign,
                "trust_net": trust, "dealer_net": dealer, "total_net": total,
                "source": market,
            }
        except (ValueError, IndexError):
            continue
    return records


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_daily_report(market, trade_date):
    if market == "TWSE":
        url = TWSE_URL
        params = {"date": trade_date.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"}
    else:
        url = TPEX_URL
        params = {"date": trade_date.strftime("%Y/%m/%d"), "type": "Daily", "response": "json"}
    response = requests.get(url, params=params, timeout=6,
                            headers={"User-Agent": "MakeMoneyApp/1.0"})
    response.raise_for_status()
    return parse_report(response.json(), market, trade_date)


def get_institutional_data(ticker, trade_dates=None):
    missing = {"available": False, "source": None, "rows": [],
               "reason": "法人資料尚未取得；大戶、分點與融資融券仍未接入，不納入技術評分。"}
    if not trade_dates:
        return missing
    market = "TPEx" if ticker.endswith(".TWO") else "TWSE" if ticker.endswith(".TW") else None
    if market is None:
        return missing
    dates = sorted(set(trade_dates))[-20:]
    try:
        stored = load_institutional_flows(ticker, dates)
    except Exception:
        stored = []
    by_date = {row["trade_date"]: row for row in stored}
    downloaded = []
    # Newest first; stop on network failure rather than blocking on 20 timeouts.
    for day in reversed(dates):
        if day in by_date:
            continue
        try:
            record = fetch_daily_report(market, day).get(ticker.split(".")[0])
            if record:
                by_date[day] = record
                downloaded.append(record)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            break
    storage_ok = True
    if downloaded:
        try:
            save_institutional_flows(ticker, downloaded)
        except Exception:
            storage_ok = False
    rows = [by_date[day] for day in dates if day in by_date]
    if not rows:
        return missing
    return {"available": True, "source": market, "rows": rows,
            "expected_days": len(dates), "complete": len(rows) == len(dates),
            "latest_date": rows[-1]["trade_date"], "stored": storage_ok,
            "reason": "僅法人資料，未涵蓋大戶、分點、融資融券；未納入技術評分。"}
