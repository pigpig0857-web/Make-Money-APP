# -*- coding: utf-8 -*-
"""
股票主檔與名稱解析模組（Backend — Stock Master）
包含 TWSE/TPEx 股票主檔、全台股中文/代號對照、別名映射、
五層降級解析、熱門推薦抓取、基本面數據查詢等。
"""

import re
import json
import math

import requests
import streamlit as st
from bs4 import BeautifulSoup


# ────────────────────── 熱門推薦 ──────────────────────

_FALLBACK_HOT_STOCKS = [
    ("台積電", "2330.TW"),
    ("鴻海", "2317.TW"),
    ("聯發科", "2454.TW"),
    ("台達電", "2308.TW"),
    ("廣達", "2382.TW"),
]


@st.cache_data(ttl=1800)
def get_daily_trending_stocks():
    """抓取 Yahoo 奇摩股市首頁「上市熱門排行」前 5 名（成交量排序）。

    優先從頁面嵌入的 JSON 解析，失敗則退回 HTML 解析，
    最終備援使用靜態清單，確保 UI 永遠有資料可顯示。
    """
    url = "https://tw.stock.yahoo.com/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }

    try:
        res = requests.get(url, headers=headers, timeout=8)
        res.raise_for_status()
        html = res.text

        json_match = re.search(
            r"root\.App\.main\s*=\s*(\{.*?\})\s*;",
            html,
            re.DOTALL,
        )
        if json_match:
            try:
                raw_json = json_match.group(1)
                raw_json = re.sub(r'\bundefined\b', 'null', raw_json)
                data = json.loads(raw_json)
                table_store = (
                    data.get("context", {})
                    .get("dispatcher", {})
                    .get("stores", {})
                    .get("TableStore", {})
                )
                stock_list = None
                for key_suffix in ("volume", "change", "changePercent", "turnoverM"):
                    store_key = f"main-6-HotStock__{key_suffix}"
                    if store_key in table_store:
                        stock_list = table_store[store_key].get("list", [])
                        break
                if stock_list:
                    results = []
                    for item in stock_list[:6]:
                        name = item.get("symbolName") or item.get("name", "")
                        symbol = item.get("symbol", "")
                        if name and symbol:
                            results.append((name, symbol))
                    if results:
                        return results
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=re.compile(r"tw\.stock\.yahoo\.com/quote/"))
        seen = set()
        results = []
        for link in links:
            href = link.get("href", "")
            symbol_match = re.search(r"quote/([^\"/?]+)", href)
            if not symbol_match:
                continue
            symbol = symbol_match.group(1)
            if symbol in seen:
                continue
            name_el = link.find(
                "div",
                class_=lambda c: c and "Fw(600)" in c if c else False,
            )
            if not name_el:
                name_el = link.find("div", string=re.compile(r"[\u4e00-\u9fff]"))
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                name = link.get_text(strip=True)
            if name and symbol:
                results.append((name, symbol))
                seen.add(symbol)
            if len(results) >= 6:
                break
        if results:
            return results

    except requests.RequestException:
        pass

    return list(_FALLBACK_HOT_STOCKS)


# ────────────────────── 股票資訊主檔 ──────────────────────

STOCK_INFO = {
    "2344.TW": {
        "name": "華邦電",
        "base_price": 167.0,
        "industry": "記憶體 IC（DRAM / NOR Flash）",
        "sector_cycle": "谷底復甦",
        "shortage": "DRAM / NOR Flash 供給偏緊、報價止跌回升，CIS 與 Edge AI 需求挹注出貨動能，缺貨題材浮現。",
        "inventory_status": "偏高",
        "capex_warning": True,
        "beta": 1.15,
    },
    "2330.TW": {
        "name": "台積電",
        "base_price": 1085.0,
        "industry": "晶圓代工 / 先進製程",
        "sector_cycle": "高峰期",
        "shortage": "先進製程產能吃緊，AI 加速器與高效能運算需求強勁，CoWoS 封測供不應求。",
        "inventory_status": "正常",
        "capex_warning": False,
        "beta": 0.85,
    },
    "2454.TW": {
        "name": "聯發科",
        "base_price": 1420.0,
        "industry": "IC 設計（手機 / 邊緣 AI）",
        "sector_cycle": "擴張成長",
        "shortage": "手機回補庫存與 Edge AI SoC 需求發酵，旗艦晶片量產帶動營運動能。",
        "inventory_status": "正常",
        "capex_warning": False,
        "beta": 1.05,
    },
    "2317.TW": {
        "name": "鴻海",
        "base_price": 208.0,
        "industry": "電子代工 / AI 伺服器",
        "sector_cycle": "擴張成長",
        "shortage": "AI 伺服器與雲端網通需求旺，GPU 模組供貨吃緊，組裝產能利用率高。",
        "inventory_status": "正常",
        "capex_warning": True,
        "beta": 1.10,
    },
    "2603.TW": {
        "name": "長榮",
        "base_price": 212.0,
        "industry": "貨櫃海運",
        "sector_cycle": "暴衝期",
        "shortage": "紅海繞行與塞港造成運力緊張，運價急漲，短期旺季行情暴衝。",
        "inventory_status": "偏低",
        "capex_warning": True,
        "beta": 1.30,
    },
    "3481.TW": {
        "name": "群創",
        "base_price": 13.8,
        "industry": "面板製造",
        "sector_cycle": "谷底復甦",
        "shortage": "面板報價落底回升，供需結構改善，車用與醫療面板需求溫和成長。",
        "inventory_status": "偏高",
        "capex_warning": False,
        "beta": 1.25,
    },
    "2308.TW": {
        "name": "台達電",
        "base_price": 420.0,
        "industry": "電源供應器 / AI 電源 / 液冷方案",
        "sector_cycle": "擴張成長",
        "shortage": "AI 伺服器電源需求強勁，HVDC 高壓直流電源與液冷散熱方案出貨攀升，電源供應鏈供不應求。",
        "inventory_status": "正常",
        "capex_warning": False,
        "beta": 1.00,
    },
    "2382.TW": {
        "name": "廣達",
        "base_price": 310.0,
        "industry": "AI 伺服器 / ODM 代工",
        "sector_cycle": "擴張成長",
        "shortage": "AI 伺服器出貨放量，GB 系列機櫃需求強勁，GPU 供貨吃緊帶動營運動能。",
        "inventory_status": "正常",
        "capex_warning": True,
        "beta": 1.10,
    },
}

NAME_TO_CODE = {info["name"]: code for code, info in STOCK_INFO.items()}

TW_STOCK_NAMES = {
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "006208": "富邦台50",
    "00878": "國泰永續高股息",
    "00919": "群益台灣精選高息",
    "00940": "元大台灣價值高息",
    "00929": "復華台灣科技優息",
    "00692": "富邦公司治理",
    "00881": "國泰台灣5G+",
    "00713": "元大台灣高息低波",
    "00757": "統一FANG+",
    "00670L": "國泰正2台指",
    "1101": "台泥",
    "1102": "亞泥",
    "1216": "統一",
    "1301": "台塑",
    "1303": "南亞",
    "1326": "台化",
    "1402": "遠東新",
    "1504": "東元",
    "1605": "華新",
    "1707": "葡萄王",
    "2002": "中鋼",
    "2105": "正新",
    "2207": "和泰車",
    "2301": "光寶科",
    "2303": "聯電",
    "2308": "台達電",
    "2317": "鴻海",
    "2324": "仁寶",
    "2327": "國巨",
    "2330": "台積電",
    "2337": "旺宏",
    "2344": "華邦電",
    "2345": "智邦",
    "2353": "宏碁",
    "2356": "英業達",
    "2357": "華碩",
    "2368": "金像電",
    "2376": "技嘉",
    "2377": "微星",
    "2379": "瑞昱",
    "2382": "廣達",
    "2395": "研華",
    "2408": "南亞科",
    "2409": "友達",
    "2412": "中華電",
    "2449": "京元電子",
    "2454": "聯發科",
    "2458": "義隆",
    "2474": "可成",
    "2498": "宏達電",
    "2603": "長榮",
    "2609": "陽明",
    "2610": "華航",
    "2615": "萬海",
    "2618": "長榮航",
    "2637": "慧洋-KY",
    "2801": "彰銀",
    "2809": "京城銀",
    "2880": "華南金",
    "2881": "富邦金",
    "2882": "國泰金",
    "2883": "凱基金",
    "2884": "玉山金",
    "2885": "元大金",
    "2886": "兆豐金",
    "2887": "台新金",
    "2888": "新光金",
    "2890": "永豐金",
    "2891": "中信金",
    "2892": "第一金",
    "3008": "大立光",
    "3017": "奇鋐",
    "3034": "聯詠",
    "3037": "欣興",
    "3044": "健鼎",
    "3045": "台灣大",
    "3231": "緯創",
    "3293": "鈊象",
    "3406": "玉晶光",
    "3481": "群創",
    "3533": "嘉澤",
    "3596": "智易",
    "3661": "世芯-KY",
    "3702": "大聯大",
    "3711": "日月光投控",
    "4904": "遠傳",
    "4938": "和碩",
    "4958": "臻鼎-KY",
    "5269": "祥碩",
    "5274": "信驊",
    "5871": "中租-KY",
    "5876": "上海商銀",
    "6005": "群益證",
    "6116": "彩晶",
    "6223": "旺矽",
    "6239": "力成",
    "6271": "同欣電",
    "6285": "啟碁",
    "6446": "藥華藥",
    "6547": "高端疫苗",
    "6669": "緯穎",
    "6770": "力積電",
    "8046": "南電",
    "8069": "元太",
    "8112": "至上",
    "9904": "寶成",
    "9910": "豐泰",
    "9921": "巨大",
    "9945": "潤泰新",
}

for _code, _name in TW_STOCK_NAMES.items():
    NAME_TO_CODE.setdefault(_name, f"{_code}.TW")


# ── 常見別名 / 簡稱 → 完整代號映射（補足 TW_STOCK_NAMES 未涵蓋的口語簡稱） ──
_ALIAS_MAP: dict[str, str] = {
    "台積": "2330.TW",
    "台積電": "2330.TW",
    "tsmc": "2330.TW",
    "鴻海": "2317.TW",
    "foxconn": "2317.TW",
    "聯發科": "2454.TW",
    "mtk": "2454.TW",
    "發哥": "2454.TW",
    "台達電": "2308.TW",
    "台達": "2308.TW",
    "廣達": "2382.TW",
    "仁寶": "2324.TW",
    "compal": "2324.TW",
    "友達": "2409.TW",
    "auo": "2409.TW",
    "群創": "3481.TW",
    "innolux": "3481.TW",
    "彩晶": "6116.TW",
    "元太": "8069.TWO",
    "eink": "8069.TWO",
    "鈊象": "3293.TWO",
    "旺矽": "6223.TWO",
    "聯電": "2303.TW",
    "umc": "2303.TW",
    "華邦電": "2344.TW",
    "力積電": "6770.TW",
    "南電": "8046.TW",
    "世芯": "3661.TW",
    "世芯-kY": "3661.TW",
    "瑞昱": "2379.TW",
    "realtek": "2379.TW",
    "智邦": "2345.TW",
    "奇鋐": "3017.TW",
    "緯創": "3231.TW",
    "wistron": "3231.TW",
    "緯穎": "6669.TW",
    "英業達": "2356.TW",
    "inventec": "2356.TW",
    "宏碁": "2353.TW",
    "acer": "2353.TW",
    "華碩": "2357.TW",
    "asus": "2357.TW",
    "技嘉": "2376.TW",
    "gigabyte": "2376.TW",
    "微星": "2377.TW",
    "msi": "2377.TW",
    "研華": "2395.TW",
    "advantech": "2395.TW",
    "大立光": "3008.TW",
    "聯詠": "3034.TW",
    "novatek": "3034.TW",
    "日月光": "3711.TW",
    "日月光投控": "3711.TW",
    "矽品": "3711.TW",
    "祥碩": "5269.TW",
    "信驊": "5274.TW",
    "aspeed": "5274.TW",
    "嘉澤": "3533.TW",
    "中租": "5871.TW",
    "長榮": "2603.TW",
    "evergreen": "2603.TW",
    "陽明": "2609.TW",
    "萬海": "2615.TW",
    "華航": "2610.TW",
    "長榮航": "2618.TW",
    "富邦金": "2881.TW",
    "國泰金": "2882.TW",
    "中信金": "2891.TW",
    "兆豐金": "2886.TW",
    "玉山金": "2884.TW",
    "台新金": "2887.TW",
    "第一金": "2892.TW",
    "華南金": "2880.TW",
    "元大金": "2885.TW",
    "永豐金": "2890.TW",
    "彰銀": "2801.TW",
    "凱基金": "2883.TW",
    "開發金": "2883.TW",  # 舊名別名：更名前使用者仍可搜尋
    "新光金": "2888.TW",
    "中華電": "2412.TW",
    "遠傳": "4904.TW",
    "台灣大": "3045.TW",
    "台泥": "1101.TW",
    "亞泥": "1102.TW",
    "統一": "1216.TW",
    "台塑": "1301.TW",
    "南亞": "1303.TW",
    "中鋼": "2002.TW",
    "和泰車": "2207.TW",
    "光寶科": "2301.TW",
    "國巨": "2327.TW",
    "旺宏": "2337.TW",
    "南亞科": "2408.TW",
    "宏達電": "2498.TW",
    "htc": "2498.TW",
    "和碩": "4938.TW",
    "pegatron": "4938.TW",
    "寶成": "9904.TW",
    "豐泰": "9910.TW",
    "巨大": "9921.TW",
    "捷安特": "9921.TW",
    "潤泰新": "9945.TW",
    "遠東新": "1402.TW",
    "東元": "1504.TW",
    "華新": "1605.TW",
    "正新": "2105.TW",
    "義隆": "2458.TW",
    "可成": "2474.TW",
    "京元電子": "2449.TW",
    "玉晶光": "3406.TW",
    "健鼎": "3044.TW",
    "欣興": "3037.TW",
    "大聯大": "3702.TW",
    "群益證": "6005.TW",
    "力成": "6239.TW",
    "同欣電": "6271.TW",
    "啟碁": "6285.TW",
    "藥華藥": "6446.TW",
    "至上": "8112.TW",
    "京城銀": "2809.TW",
    "上海商銀": "5876.TW",
    "臻鼎": "4958.TW",
    "智易": "3596.TW",
    "慧洋": "2637.TW",
}

# ── 代碼 → 中文名稱的快取（反查用） ──
_CODE_TO_NAME: dict[str, str] = {}
for _c, _n in TW_STOCK_NAMES.items():
    _CODE_TO_NAME.setdefault(_c, _n)

# ── 建立模糊（部分比對）反查表：中文名稱片段 → 代碼列表 ──
_NAME_FRAGMENTS: dict[str, list[str]] = {}
for _code, _name in TW_STOCK_NAMES.items():
    clean_name = _name.replace("-KY", "").replace("-KY", "")
    for _frag_len in (2, 3, 4):
        for _i in range(len(clean_name) - _frag_len + 1):
            _frag = clean_name[_i : _i + _frag_len]
            _NAME_FRAGMENTS.setdefault(_frag, []).append(f"{_code}.TW")


# ── 全型 → 半型對照表 ──
_FULLSCREEN_TO_HALFWIDTH: dict[str, str] = {}
for _i in range(ord("０"), ord("９") + 1):
    _FULLSCREEN_TO_HALFWIDTH[chr(_i)] = chr(_i - ord("０") + ord("0"))
for _i in range(ord("Ａ"), ord("Ｚ") + 1):
    _FULLSCREEN_TO_HALFWIDTH[chr(_i)] = chr(_i - ord("Ａ") + ord("A"))
for _i in range(ord("ａ"), ord("ｚ") + 1):
    _FULLSCREEN_TO_HALFWIDTH[chr(_i)] = chr(_i - ord("ａ") + ord("a"))


# ────────────────────── 解析工具函式 ──────────────────────

def _normalize_input(raw: str) -> str:
    """將使用者輸入統一轉為半型、去空白、去市場別尾碼。"""
    text = raw.strip()
    text = "".join(_FULLSCREEN_TO_HALFWIDTH.get(ch, ch) for ch in text)
    text = text.replace(" ", "").replace("\u3000", "")
    text = re.sub(r"\.(TW|TWO|TWO?)$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _query_yahoo_search(query: str) -> str | None:
    """透過 Yahoo Finance 全球搜尋 API 查詢代號（對中文名稱無效，僅適用數字代碼）。"""
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=5&newsCount=0"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        res = requests.get(url, headers=headers, timeout=5)
        res.raise_for_status()
        data = res.json()
        for quote in data.get("quotes", []):
            symbol = quote.get("symbol", "")
            exchange = quote.get("exchange", "")
            if exchange in ("TAI", "TWO") and symbol.endswith((".TW", ".TWO")):
                return symbol
    except Exception:
        pass
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_tw_stock_master() -> dict[str, str]:
    """從 TWSE / TPEx 官方取得完整台股名稱→代碼對照表（24 小時 cache）。"""
    name_to_ticker: dict[str, str] = {}
    _ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )

    try:
        res = requests.get(
            "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
            headers={"User-Agent": _ua},
            timeout=15,
        )
        if "charset=" in (res.headers.get("Content-Type") or ""):
            res.encoding = res.headers["Content-Type"].rsplit("charset=", 1)[-1].strip()
        else:
            res.encoding = "cp950"
        text = res.content.decode(res.encoding, errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 1:
                    continue
                first_cell = cells[0].get_text(strip=True)
                parts = first_cell.split("\u3000")
                if len(parts) >= 2:
                    code = parts[0].strip()
                    name = parts[1].strip()
                elif len(cells) >= 3:
                    code = cells[1].get_text(strip=True)
                    name = cells[2].get_text(strip=True)
                else:
                    continue
                if name and re.fullmatch(r"\d{4,6}", code):
                    name_to_ticker[name] = f"{code}.TW"
    except Exception:
        pass

    _tpex_urls = [
        "https://www.tpex.org.tw/www/zh-tw/afterTrading/otcQuotes?l=zh-tw",
        "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_list.php?l=zh-tw",
    ]
    for tpex_url in _tpex_urls:
        try:
            res = requests.get(tpex_url, headers={"User-Agent": _ua}, timeout=10)
            ct = res.headers.get("Content-Type", "")
            if "json" in ct:
                data = res.json()
                items = data if isinstance(data, list) else data.get("aaData", data.get("data", []))
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            code = str(item[0]).strip()
                            name = str(item[1]).strip()
                            if name and re.fullmatch(r"\d{4,6}", code):
                                name_to_ticker[name] = f"{code}.TWO"
                if name_to_ticker:
                    break
            else:
                page_text = res.content.decode("utf-8", errors="replace")
                soup = BeautifulSoup(page_text, "html.parser")
                for table in soup.find_all("table"):
                    for row in table.find_all("tr")[1:]:
                        cells = row.find_all("td")
                        if len(cells) >= 2:
                            code = cells[0].get_text(strip=True)
                            name = cells[1].get_text(strip=True)
                            if name and re.fullmatch(r"\d{4,6}", code):
                                name_to_ticker[name] = f"{code}.TWO"
                if any(v.endswith(".TWO") for v in name_to_ticker.values()):
                    break
        except Exception:
            continue

    return name_to_ticker


@st.cache_data(ttl=86400, show_spinner=False)
def _get_tw_stock_name_by_code() -> dict[str, str]:
    """從官方主檔反向取得 代碼→名稱 對照表。"""
    return {ticker: name for name, ticker in _fetch_tw_stock_master().items()}


def resolve_ticker(raw: str) -> str | None:
    """全方位容錯股票代碼 / 名稱解析（五層降級）。"""
    if not raw or not raw.strip():
        return None

    text = _normalize_input(raw)
    if not text:
        return None

    lower = text.lower()
    if lower in _ALIAS_MAP:
        return _ALIAS_MAP[lower]
    if text in NAME_TO_CODE:
        return NAME_TO_CODE[text]
    if f"{text}.TW" in STOCK_INFO:
        return f"{text}.TW"

    master = _fetch_tw_stock_master()
    if text in master:
        return master[text]

    if re.fullmatch(r"\d{4,6}", text):
        for suffix in (".TW", ".TWO"):
            candidate = f"{text}{suffix}"
            try:
                import yfinance as yf
                hist = yf.Ticker(candidate).history(period="1d")
                if hist is not None and not hist.empty:
                    return candidate
            except Exception:
                continue
        if text in _CODE_TO_NAME:
            return f"{text}.TW"

    api_result = _query_yahoo_search(text)
    if api_result:
        return api_result

    if len(text) >= 2:
        candidates = set()
        for frag_len in range(min(len(text), 4), 1, -1):
            for i in range(len(text) - frag_len + 1):
                frag = text[i : i + frag_len]
                if frag in _NAME_FRAGMENTS:
                    candidates.update(_NAME_FRAGMENTS[frag])
        if candidates:
            return sorted(candidates, key=lambda s: len(s))[0]

    return None


def _candidate_tickers(ticker: str) -> list[str]:
    """依序回傳可能有效的 Yahoo 代號。"""
    if ticker.endswith((".TW", ".TWO")):
        base = ticker.rsplit(".", 1)[0]
        suffix = ticker.rsplit(".", 1)[1].upper()
        return [ticker, f"{base}.TWO" if suffix == "TW" else f"{base}.TW"]
    return [f"{ticker}.TW", f"{ticker}.TWO"]


@st.cache_data(ttl=300, show_spinner=False)
def _verify_ticker_via_yfinance(ticker: str) -> str | None:
    """以 yfinance 驗證股票 / ETF 是否真實存在。"""
    for cand in _candidate_tickers(ticker):
        try:
            import yfinance as yf
            hist = yf.Ticker(cand).history(period="1d")
            if hist is not None and not hist.empty:
                return cand
        except Exception:
            continue
    return None


def validate_stock_input(user_input: str) -> tuple[bool, str | None, str | None]:
    """全方位容錯驗證：輸入任何格式的台股代號 / 名稱皆嘗試解析。"""
    ticker = resolve_ticker(user_input)
    if ticker is None:
        return False, None, None

    info = STOCK_INFO.get(ticker)
    if info is not None:
        return True, ticker, info["name"]

    code_only = ticker.split(".")[0]
    if code_only in _CODE_TO_NAME:
        return True, ticker, _CODE_TO_NAME[code_only]

    valid_ticker = _verify_ticker_via_yfinance(ticker)
    if valid_ticker is None:
        return False, None, None

    return True, valid_ticker, valid_ticker.rsplit(".", 1)[0]


def build_search_error_message(bad_input: str) -> str:
    """產生搜尋失敗的錯誤提示文案。"""
    if not bad_input or not bad_input.strip():
        return "**請輸入股票代號（例：`2330`）或股票名稱（例：`台積電`）。**"
    return (
        f"**找不到符合「{bad_input.strip()}」的台股股票名稱，**\n\n"
        "請確認股票名稱或代號（例：`2330` 或 `台積電`）。"
    )


def _is_cjk_name(name) -> bool:
    """判斷名稱是否含中文字元（過濾 yfinance 對台股常回傳的英文羅馬拼音）。"""
    if not name:
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(name))


def _fetch_yf_live_name(ticker: str):
    """即時取得 yfinance info 的股票名稱（shortName → longName）。

    【更名防呆】優先於寫死對照表，確保更名個股（如 2883 開發金 → 凱基金、
    台新新光金控合併更名）顯示最新名稱；yfinance 對台股常回傳英文名，
    故僅接受含中文字的名稱，避免畫面出現英文羅馬拼音。
    """
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
        for key in ("shortName", "longName"):
            name = info.get(key)
            if _is_cjk_name(name):
                return str(name).strip()
    except Exception:
        pass
    return None


@st.cache_data(ttl=300, show_spinner=False)
def lookup_stock_name(ticker: str) -> str:
    """由 Yahoo 代號解析標準繁體中文股票名稱。

    優先順序：yfinance 即時 info 名稱（含中文者）→ STOCK_INFO →
    _CODE_TO_NAME → TW_STOCK_NAMES → 動態清單 → Yahoo 奇摩爬蟲 → 純代碼。
    """
    code = ticker.replace(".TW", "").replace(".TWO", "").upper()

    # ── 層級 0：即時 API 名稱（處理個股更名，如 2883 凱基金）──
    live_name = _fetch_yf_live_name(ticker)
    if live_name:
        return live_name

    info = STOCK_INFO.get(ticker)
    if info and info.get("name"):
        return info["name"]
    if code in _CODE_TO_NAME:
        return _CODE_TO_NAME[code]
    if code in TW_STOCK_NAMES:
        return TW_STOCK_NAMES[code]
    code_to_name = _get_tw_stock_name_by_code()
    if ticker in code_to_name:
        return code_to_name[ticker]
    if code in code_to_name:
        return code_to_name[code]
    meta = get_tw_stock_metadata(ticker)
    if meta["name"]:
        return meta["name"]
    return code


# ────────────────────── 產業 / 基本面 ──────────────────────

def _is_etf(ticker: str) -> bool:
    """判斷是否為 ETF。"""
    code = ticker.split(".")[0]
    return code.startswith("00")


ETF_INDUSTRY = "ETF / 指數股票型基金"
FALLBACK_INDUSTRY = "綜合產業 / 未分類"

_FALLBACK_INDUSTRY_DICT: dict[str, str] = {
    "2330": "半導體業",
    "2317": "其他電子業",
    "2454": "半導體業",
    "2308": "電機機械",
    "2382": "電腦及週邊設備業",
    "2324": "電腦及週邊設備業",
    "2303": "半導體業",
    "2345": "半導體業",
    "2399": "半導體業",
    "2327": "半導體業",
    "2379": "半導體業",
    "2409": "光電業",
    "3481": "光電業",
    "6770": "半導體業",
    "2357": "電腦及週邊設備業",
    "2301": "電腦及週邊設備業",
    "2304": "電機機械",
    "2344": "半導體業",
    "2376": "半導體業",
    "2395": "電腦及週邊設備業",
    "2377": "半導體業",
    "3034": "半導體業",
    "3231": "電腦及週邊設備業",
    "2371": "電腦及週邊設備業",
    "2356": "電腦及週邊設備業",
    "2347": "電腦及週邊設備業",
    "3037": "半導體業",
    "2340": "光電業",
    "2388": "半導體業",
    "2603": "航運業",
    "2609": "航運業",
    "2615": "航運業",
    "2618": "航空運輸",
    "2881": "金融保險業",
    "2882": "金融保險業",
    "2884": "金融保險業",
    "2886": "金融保險業",
    "2891": "金融保險業",
    "2892": "金融保險業",
    "3711": "半導體業",
    "2412": "電信業",
    "4904": "電信業",
    "4938": "電腦及週邊設備業",
    "2352": "電腦及週邊設備業",
    "6547": "半導體業",
    "3661": "半導體業",
    "2049": "半導體業",
    "3443": "半導體業",
    "8046": "半導體業",
    "5347": "半導體業",
    "3293": "電子零組件業",
    "1590": "半導體業",
    "3653": "半導體業",
    "6669": "半導體業",
    "2369": "電子零組件業",
}


@st.cache_data(ttl=1800, show_spinner=False)
def get_tw_stock_metadata(ticker: str) -> dict:
    """從 Yahoo 奇摩股市取得台股中文名稱與產業類別（30 分鐘快取）。"""
    code = ticker.split(".")[0]
    result: dict[str, str] = {"name": "", "industry": ""}

    info = STOCK_INFO.get(ticker, {})
    if info.get("name"):
        result["name"] = info["name"]
    if info.get("industry"):
        result["industry"] = info["industry"]
    if result["name"] and result["industry"]:
        return result

    if not result["name"] and code in TW_STOCK_NAMES:
        result["name"] = TW_STOCK_NAMES[code]

    yahoo_ticker = f"{code}.TW"
    url = f"https://tw.stock.yahoo.com/quote/{yahoo_ticker}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    try:
        res = requests.get(url, headers=headers, timeout=8)
        res.raise_for_status()
        html = res.text

        if not result["name"]:
            title_match = re.search(
                r"<title>\s*([^\(（<]+?)\s*[\(（]", html
            )
            if title_match:
                raw_name = title_match.group(1).strip()
                raw_name = re.sub(r"\s*(走勢圖|Yahoo股市| yahoo).*", "", raw_name, flags=re.IGNORECASE)
                raw_name = raw_name.strip()
                if raw_name and len(raw_name) <= 10:
                    result["name"] = raw_name

        if not result["name"]:
            og_match = re.search(
                r'property="og:title"\s+content="([^"]+)"', html
            )
            if og_match:
                og_title = og_match.group(1)
                name_part = re.split(r"[\(（]", og_title)[0].strip()
                name_part = re.sub(r"\s*(走勢圖|Yahoo股市| yahoo).*", "", name_part, flags=re.IGNORECASE)
                if name_part and len(name_part) <= 10:
                    result["name"] = name_part

        if not result["name"]:
            desc_match = re.search(
                r'name="description"\s+content="([^"]*?)\(', html
            )
            if desc_match:
                desc_name = desc_match.group(1).strip()
                if desc_name and len(desc_name) <= 10 and re.search(r"[\u4e00-\u9fff]", desc_name):
                    result["name"] = desc_name

        if not result["industry"]:
            sector_match = re.search(r'"sectorName"\s*:\s*"([^"]+)"', html)
            if sector_match:
                result["industry"] = sector_match.group(1)

        if not result["industry"]:
            ysector_match = re.search(r'"ySector"\s*:\s*"([^"]+)"', html)
            if ysector_match:
                result["industry"] = ysector_match.group(1)

    except requests.RequestException:
        pass

    try:
        import yfinance as yf
        yf_info = yf.Ticker(ticker).info or {}
        if not result["name"]:
            yf_name = yf_info.get("shortName") or yf_info.get("longName") or ""
            if yf_name and re.search(r"[\u4e00-\u9fff]", yf_name):
                result["name"] = yf_name
        if not result["industry"]:
            yf_ind = yf_info.get("sector") or yf_info.get("industry") or ""
            if yf_ind and isinstance(yf_ind, str) and len(yf_ind) > 1:
                result["industry"] = yf_ind
    except Exception:
        pass

    return result


@st.cache_data(ttl=1800, show_spinner=False)
def get_fundamental(ticker: str) -> dict:
    """取得基本面數據（景氣週期、缺貨題材、庫存、Beta、產業等）。"""
    info = STOCK_INFO.get(ticker, {})
    code = ticker.split(".")[0]

    if _is_etf(ticker):
        industry = ETF_INDUSTRY
    elif "industry" in info:
        industry = info["industry"]
    else:
        meta = get_tw_stock_metadata(ticker)
        industry = meta["industry"] or _FALLBACK_INDUSTRY_DICT.get(code, FALLBACK_INDUSTRY)

    yf_info = {}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        yf_info = t.info or {}
    except Exception:
        pass

    def numeric(key):
        value = yf_info.get(key)
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    return {
        "cycle": None,
        "shortage": None,
        "inventory": None,
        "capex": None,
        "beta": numeric("beta"),
        "source": "Yahoo Finance" if yf_info else None,
        "industry": industry,
        "trailing_eps": numeric("trailingEps"),
        "forward_eps": numeric("forwardEps"),
        "pe_ratio": numeric("trailingPE"),
        "forward_pe": numeric("forwardPE"),
        "roe": numeric("returnOnEquity"),
        "dividend_yield": numeric("dividendYield"),
        "market_cap": numeric("marketCap"),
    }
