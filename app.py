# -*- coding: utf-8 -*-
"""
財神爺選股 — AI 智慧選股與技術診斷
台股技術面 / 籌碼面 / 基本面 / 風險管理 / AI 綜合決策儀表板
"""

import re
import json
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup
from plotly.subplots import make_subplots

# ====================== 頁面基本設定 ======================
st.set_page_config(
    page_title="財神爺選股｜AI 智慧選股與技術診斷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 演算法升級後，首次載入時清除舊版排行榜快取（僅執行一次）
_SCOREBOARD_VER_KEY = "_scoreboard_cache_ver"
_CURRENT_VER = "v3_3dim"
if st.session_state.get(_SCOREBOARD_VER_KEY) != _CURRENT_VER:
    try:
        st.cache_data.clear()
    except Exception:
        pass
    st.session_state[_SCOREBOARD_VER_KEY] = _CURRENT_VER

# ====================== 手機版響應式 CSS（電腦端維持原樣） ======================
st.markdown(
    """
    <style>
    /* 僅在手機與平板小螢幕 (寬度 <= 768px) 生效 */
    @media (max-width: 768px) {
        /* 1. 手機端 K 線圖高度適中，方便滑動看下方診斷 */
        .js-plotly-plot, .plot-container {
            max-height: 400px !important;
        }

        /* 2. 手機端價位策略卡片改為單欄直向排列 (Vertical Stack) */
        .price-card-container {
            flex-direction: column !important;
            gap: 8px !important;
        }

        /* 3. 縮減手機端頂部與邊緣留白，最大化利用螢幕空間
           （併用新版 Streamlit 的 data-testid，確保 .main 不存在的新版仍生效） */
        .main .block-container,
        [data-testid="stMainBlockContainer"] {
            padding-top: 1rem !important;
            padding-bottom: 1rem !important;
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
        }

        /* 4. 手機端按鈕與輸入框大小與字體優化 */
        .stButton > button {
            width: 100% !important;
            font-size: 14px !important;
        }

        /* 5. 手機端觸控目標最小 44px（符合 Apple / Android 觸控標準） */
        .stButton > button,
        .stDownloadButton > button,
        .stTextInput input,
        .stSelectbox [data-baseweb="select"] > div,
        .stRadio > div[role="radiogroup"] > label {
            min-height: 44px !important;
        }
        .stButton > button {
            padding-top: 8px !important;
            padding-bottom: 8px !important;
        }

        /* 6. 防止手勢誤選文字 / 誤觸高亮（原生 App 手感） */
        .stButton > button,
        .stTextInput input,
        .stSelectbox [data-baseweb="select"] > div,
        [data-testid="stHorizontalBlock"] > div {
            user-select: none !important;
            -webkit-user-select: none !important;
            -webkit-tap-highlight-color: transparent;
        }

        /* 7. 多欄位手機端自動換行（原生 App 卡片堆疊感） */
        [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        [data-testid="stHorizontalBlock"] > div {
            min-width: 45% !important;
            flex: 1 1 auto !important;
        }

        /* 8. AI 多空診斷卡片樣式 */
        .ai-diagnosis-card {
            background: linear-gradient(135deg, #1E222D 0%, #2A2E39 100%);
            border-radius: 14px;
            padding: 20px 22px;
            margin-bottom: 16px;
            color: #F9FAFB;
            font-family: 'Microsoft JhengHei', Arial, sans-serif;
            line-height: 1.65;
        }
        .ai-diagnosis-card summary::-webkit-details-marker { display: none; }
        .ai-diagnosis-card summary::marker { display: none; content: ''; }
        .ai-diagnosis-card details[open] summary > span:first-child { transform: rotate(90deg); }

        /* 9. 手機端 AI 診斷卡片自適應 */
        @media (max-width: 768px) {
            .ai-diagnosis-card {
                padding: 14px 12px !important;
                border-radius: 10px !important;
                font-size: 0.9rem !important;
            }
            .ai-diagnosis-card > div:first-child span[style*="font-size:1.8rem"] {
                font-size: 1.3rem !important;
            }
            .ai-diagnosis-card > div:first-child div span[style*="font-size:1.35rem"] {
                font-size: 1.05rem !important;
            }
            .ai-diagnosis-card details > div > div[style*="grid-template-columns"] {
                grid-template-columns: 1fr !important;
            }
            .ai-diagnosis-card > div[style*="display:flex"][style*="gap:14px"] {
                flex-direction: column !important;
            }
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

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

        # --- 方法 1：從嵌入 JSON 解析 TableStore ---
        # 首頁 <script> 中包含 root.App.main = { ... };
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
                # 預設取成交量排行；若不存在嘗試其他 key
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

        # --- 方法 2：從 HTML 結構解析 ---
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
            # 從 link 的子元素或相鄰元素找中文名
            name_el = link.find(
                "div",
                class_=lambda c: c and "Fw(600)" in c if c else False,
            )
            if not name_el:
                name_el = link.find("div", string=re.compile(r"[\u4e00-\u9fff]"))
            name = name_el.get_text(strip=True) if name_el else ""
            # 若找不到名稱，嘗試純文字
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

    # --- 備援：靜態清單 ---
    return list(_FALLBACK_HOT_STOCKS)




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
    "2883": "開發金",
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
    "開發金": "2883.TW",
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
    "南亞科": "2408.TW",
}

# ── 代碼 → 中文名稱的快取（反查用） ──
_CODE_TO_NAME: dict[str, str] = {}
for _c, _n in TW_STOCK_NAMES.items():
    _CODE_TO_NAME.setdefault(_c, _n)

# ── 建立模糊（部分比對）反查表：中文名稱片段 → 代碼列表 ──
_NAME_FRAGMENTS: dict[str, list[str]] = {}
for _code, _name in TW_STOCK_NAMES.items():
    # 拆解名稱中的每個 2 字元片段
    clean_name = _name.replace("-KY", "").replace("-KY", "")
    for _frag_len in (2, 3, 4):
        for _i in range(len(clean_name) - _frag_len + 1):
            _frag = clean_name[_i : _i + _frag_len]
            _NAME_FRAGMENTS.setdefault(_frag, []).append(f"{_code}.TW")


# ── 全型 → 半型對照表 ──
_FULLWIDTH_TO_HALFWIDTH: dict[str, str] = {}
for _i in range(ord("０"), ord("９") + 1):
    _FULLWIDTH_TO_HALFWIDTH[chr(_i)] = chr(_i - ord("０") + ord("0"))
for _i in range(ord("Ａ"), ord("Ｚ") + 1):
    _FULLWIDTH_TO_HALFWIDTH[chr(_i)] = chr(_i - ord("Ａ") + ord("A"))
for _i in range(ord("ａ"), ord("ｚ") + 1):
    _FULLWIDTH_TO_HALFWIDTH[chr(_i)] = chr(_i - ord("ａ") + ord("a"))


def _normalize_input(raw: str) -> str:
    """將使用者輸入統一轉為半型、去空白、去市場別尾碼。"""
    text = raw.strip()
    # 全型轉半型
    text = "".join(_FULLWIDTH_TO_HALFWIDTH.get(ch, ch) for ch in text)
    # 移除所有空白（含全型空格）
    text = text.replace(" ", "").replace("\u3000", "")
    # 移除常見尾碼（保留純代碼或名稱）
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
            # 只取台灣交易所的股票 / ETF
            if exchange in ("TAI", "TWO") and symbol.endswith((".TW", ".TWO")):
                return symbol
    except Exception:
        pass
    return None


def resolve_ticker(raw: str) -> str | None:
    """全方位容錯股票代碼 / 名稱解析（四層降級）。

    層級 1：別名 / 完整名稱精確比對（零網路請求）
    層級 2：純數字代碼 → 嘗試 .TW（上市）再 .TWO（上櫃）yfinance 驗證
    層級 3：Yahoo Finance 全球搜尋 API（數字代碼）
    層級 4：部分名稱片段模糊比對（2–4 字元）
    """
    if not raw or not raw.strip():
        return None

    text = _normalize_input(raw)
    if not text:
        return None

    # ── 層級 1a：精確別名 / 名稱比對（最快、零網路） ──
    lower = text.lower()
    if lower in _ALIAS_MAP:
        return _ALIAS_MAP[lower]
    if text in NAME_TO_CODE:
        return NAME_TO_CODE[text]
    # 嘗試加 .TW 後比對
    if f"{text}.TW" in STOCK_INFO:
        return f"{text}.TW"

    # ── 層級 2：純數字代碼 → yfinance 驗證 ──
    if re.fullmatch(r"\d{4,6}", text):
        # 先嘗試 .TW（上市 / 上市 ETF），再嘗試 .TWO（上櫃）
        for suffix in (".TW", ".TWO"):
            candidate = f"{text}{suffix}"
            try:
                import yfinance as yf
                hist = yf.Ticker(candidate).history(period="1d")
                if hist is not None and not hist.empty:
                    return candidate
            except Exception:
                continue
        # 嘗試從內建字典補位
        if text in _CODE_TO_NAME:
            return f"{text}.TW"

    # ── 層級 3：Yahoo Finance 全球搜尋 API ──
    api_result = _query_yahoo_search(text)
    if api_result:
        return api_result

    # ── 層級 4：部分名稱片段模糊比對 ──
    if len(text) >= 2:
        candidates = set()
        # 從名稱片段中找匹配
        for frag_len in range(min(len(text), 4), 1, -1):
            for i in range(len(text) - frag_len + 1):
                frag = text[i : i + frag_len]
                if frag in _NAME_FRAGMENTS:
                    candidates.update(_NAME_FRAGMENTS[frag])
        if candidates:
            # 優先回傳最短代碼（通常是主要上市股）
            return sorted(candidates, key=lambda s: len(s))[0]

    return None


def _candidate_tickers(ticker: str) -> list[str]:
    """依序回傳可能有效的 Yahoo 代號：集中市場 / 上市 ETF（.TW）優先，櫃買（.TWO）次之。"""
    if ticker.endswith((".TW", ".TWO")):
        base = ticker.rsplit(".", 1)[0]
        suffix = ticker.rsplit(".", 1)[1].upper()
        return [ticker, f"{base}.TWO" if suffix == "TW" else f"{base}.TW"]
    return [f"{ticker}.TW", f"{ticker}.TWO"]


@st.cache_data(ttl=300, show_spinner=False)
def _verify_ticker_via_yfinance(ticker: str) -> str | None:
    """以 yfinance 驗證股票 / ETF 是否真實存在；成功回傳有效代號，失敗回傳 None。"""
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
    """全方位容錯驗證：輸入任何格式的台股代號 / 名稱皆嘗試解析。

    回傳 (是否有效, 正規化代號, 股票名稱)；無效時回傳 (False, None, None)。
    只有在所有解析層級都失敗時才回傳 False。
    """
    ticker = resolve_ticker(user_input)
    if ticker is None:
        return False, None, None

    # 直接查內建清單（最快）
    info = STOCK_INFO.get(ticker)
    if info is not None:
        return True, ticker, info["name"]

    # 嘗試從 NAME_TO_CODE 反查名稱
    code_only = ticker.split(".")[0]
    if code_only in _CODE_TO_NAME:
        return True, ticker, _CODE_TO_NAME[code_only]

    # yfinance 最終驗證（確保代號真實存在）
    valid_ticker = _verify_ticker_via_yfinance(ticker)
    if valid_ticker is None:
        return False, None, None

    return True, valid_ticker, valid_ticker.rsplit(".", 1)[0]


def build_search_error_message(bad_input: str) -> str:
    """產生搜尋失敗的錯誤提示文案。"""
    if not bad_input or not bad_input.strip():
        return "**請輸入股票代號（例：`2330`）或股票名稱（例：`台積電`）。**"
    return (
        f"**搜尋失敗：查無「{bad_input.strip()}」這檔股票！**\n\n"
        "請重新確認您輸入的台股代號（例：`2330`）或股票名稱（例：`台積電`）。"
    )


@st.cache_data(ttl=300, show_spinner=False)
def lookup_stock_name(ticker: str) -> str:
    """由 Yahoo 代號解析標準繁體中文股票名稱。優先中文，絕不回傳英文名。"""
    code = ticker.replace(".TW", "").replace(".TWO", "").upper()
    # 快查：內建字典（零網路）
    info = STOCK_INFO.get(ticker)
    if info and info.get("name"):
        return info["name"]
    if code in _CODE_TO_NAME:
        return _CODE_TO_NAME[code]
    if code in TW_STOCK_NAMES:
        return TW_STOCK_NAMES[code]
    # 爬蟲：Yahoo 奇摩股市（最權威中文來源）
    meta = get_tw_stock_metadata(ticker)
    if meta["name"]:
        return meta["name"]
    # 最終退路：僅回傳純數字代碼（不顯示英文名）
    return code


# ====================== 工具函式 ======================
def _seed(key: str):
    return np.random.default_rng(zlib.crc32(key.encode("utf-8")))


def is_mobile_request() -> bool:
    """以瀏覽器 User-Agent 偵測手機 / 平板，供手機端 UI 最佳化使用。

    偵測不到（bare mode / 舊版）時一律回傳 False，確保電腦端行為不變。
    """
    try:
        headers = st.context.headers
        ua = headers.get("User-Agent", "") if hasattr(headers, "get") else ""
        if not ua and hasattr(headers, "to_dict"):
            ua = headers.to_dict().get("User-Agent", "")
    except Exception:
        return False
    ua = (ua or "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone", "ipad"))


TAIWAN_TZ = ZoneInfo("Asia/Taipei")
AUTORUN_INTERVAL_SECONDS = 15


def force_scroll_to_top():
    """利用 MutationObserver + 多重延遲，打斷 Streamlit 原生 scroll-retention，強制置頂。"""
    scroll_js = """
    <script>
        (function forceScroll() {
            var targetSidebar = window.parent.document.querySelector('section[data-testid="stSidebar"]');
            var targetMain = window.parent.document.querySelector('section.main');

            if (targetSidebar) {
                targetSidebar.scrollTop = 0;
                var innerSidebar = targetSidebar.querySelector('div[data-testid="stVerticalBlock"]');
                if (innerSidebar) innerSidebar.scrollTop = 0;
            }
            if (targetMain) {
                targetMain.scrollTop = 0;
            }

            [50, 100, 200, 500].forEach(function(delay) {
                setTimeout(function() {
                    if (targetSidebar) targetSidebar.scrollTop = 0;
                    if (targetMain) targetMain.scrollTop = 0;
                    window.parent.scrollTo(0, 0);
                }, delay);
            });
        })();
    </script>
    """
    st.components.v1.html(scroll_js, height=0, width=0)


def get_market_status() -> dict:
    """判斷台股盤態：週一至週五 09:00-13:30（Asia/Taipei）為開盤中。"""
    now = datetime.now(TAIWAN_TZ)
    is_weekday = now.weekday() < 5
    minutes = now.hour * 60 + now.minute
    in_session = 9 * 60 <= minutes <= 13 * 60 + 30
    return {
        "is_open": is_weekday and in_session,
        "now": now,
        "is_weekday": is_weekday,
        "in_session": in_session,
    }


def setup_autorun(is_open: bool) -> None:
    """開盤期間自動刷新頁面；閉盤或假日停用，避免不必要的 API 請求與效能消耗。

    註：PyPI 上並無 `streamlit-autorun` 套件，此處改用社群標準的
    `streamlit-autorefresh`（interval 單位為毫秒）；若元件載入失敗則退回
    HTML meta refresh 方案。
    """
    if not is_open:
        return
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=AUTORUN_INTERVAL_SECONDS * 1000, key="market_autorun")
    except Exception:
        st.markdown(
            f'<meta http-equiv="refresh" content="{AUTORUN_INTERVAL_SECONDS}">',
            unsafe_allow_html=True,
        )


def render_market_badge(status: dict) -> None:
    now = status["now"]
    if status["is_open"]:
        st.markdown(
            '<div style="background:#dcfce7;color:#15803d;padding:10px 16px;border-radius:10px;'
            'font-weight:800;text-align:center;font-size:1.05rem;">'
            "🟢 市場狀態：台股開盤中（即時價格更新）</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"台灣時間 {now:%Y-%m-%d %H:%M:%S}　|　自動刷新：每 {AUTORUN_INTERVAL_SECONDS} 秒"
            "（yfinance 1 分鐘級即時報價 + 日 K 動態更新）"
        )
    else:
        st.markdown(
            '<div style="background:#e5e7eb;color:#374151;padding:10px 16px;border-radius:10px;'
            'font-weight:800;text-align:center;font-size:1.05rem;">'
            "🔴 市場狀態：已收盤（顯示盤後最終數據）</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"台灣時間 {now:%Y-%m-%d %H:%M:%S}　|　自動刷新：已停用（非交易時段，節省 API 請求）"
        )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_live_price(ticker: str):
    """開盤期間抓取 1 分鐘等級最新成交資料（best-effort，失敗回傳 None）。"""
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


def generate_mock_data(ticker: str, days: int = 180) -> pd.DataFrame:
    """產生可重現的離線模擬 K 線資料（含 5 / 20 / 60 日均線）。

    使用均值回歸 (Ornstein-Uhlenbeck) 隨機路徑，使末端價格收斂至基準價，
    展示上較接近真實多空結構。
    """
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


_PERIOD_HISTORY = {
    "日 K": "2y",
    "週 K": "3y",
    "月 K": "5y",
}


def _download_daily(ticker: str, history_period: str = "1y"):
    """抓取日 K 級資料；失敗或延遲時自動切換 Mock Data。

    history_period：向 yfinance 請求的歷史資料時間跨度。繪製週 / 月 K 時需更長資料
    （請依 _PERIOD_HISTORY 傳入 3y / 5y），否則 60 週線 / 20 月線 / 60 月線會因
    資料根數不足而全部變成 NaN。
    """
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
    """閉盤時使用快取（5 分鐘），避免盤後過度請求 API。

    開盤期間請直接呼叫 _download_daily（不走快取），以取得「動態浮動」的最新 K 線資料。
    """
    return _download_daily(ticker)


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
    """將日 K 資料轉為指定週期（日 K / 週 K / 月 K）之 K 線 DataFrame。

    重抽樣後以 min_periods=1 補上 5 / 20 / 60 期均線，即使前段資料根數不足
    也能繪出連續折線，長天期均線（60 週線 / 20 月線 / 60 月線）不會中斷或消失。
    """
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


def timeframe_pattern(closes: pd.Series):
    """以均線排列粗略判斷各時間框架型態。"""
    closes = closes.dropna()
    if len(closes) < 5:
        return "資料不足", "warning"
    ma_s = float(closes.rolling(5).mean().iloc[-1])
    ma_m = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else float(closes.mean())
    ma_l = float(closes.rolling(60).mean().iloc[-1]) if len(closes) >= 60 else np.nan
    last = float(closes.iloc[-1])

    if not np.isnan(ma_l):
        if ma_s > ma_m > ma_l:
            return "多頭排列 / 趨勢強勁", "success"
        if ma_s < ma_m < ma_l:
            return "空頭排列 / 趨勢弱勢", "error"
        return "區間震盪 / 高檔洗盤", "warning"
    if last > ma_m:
        return "多頭初升 / 中繼續強", "success"
    return "走勢轉弱 / 觀望", "warning"


def compute_levels(close: float):
    """建議操作價位（相對收盤價換算，僅供參考）。"""
    return {
        "short_low": (round(close * 0.97, 1), round(close * 1.01, 1)),
        "swing_def": (round(close * 0.90, 1), round(close * 0.93, 1)),
        "stop": round(close * 0.88, 1),
    }


def compute_price_strategy(df: pd.DataFrame, close_now: float) -> dict:
    """依近期 20 / 60 日 K 線動態計算買賣價位策略。

    回傳進場區（支撐附近）、建議入場價、目標止盈價、嚴格停損價
    與風報比（盈虧比，理想值需 ≥ 2.0）。
    """
    d20 = df.tail(20)
    d60 = df.tail(60)

    support20 = float(d20["Low"].min()) if len(d20) else close_now
    resistance20 = float(d20["High"].max()) if len(d20) else close_now
    ma20 = float(d20["Close"].mean()) if len(d20) >= 5 else close_now

    # 最低風險進場區：近 20 日支撐位附近 [支撐價位 ~ 支撐價位 * 1.02]
    buy_lo = round(support20, 2)
    buy_hi = round(support20 * 1.02, 2)

    # 建議入場價：拉回至支撐或 MA20 附近分批佈局（限制於進場區內）
    entry = round(max(min(ma20, support20 * 1.02), support20), 2)

    # 嚴格停損價：跌破近 20 日最低點並下浮 3%~5%
    stop_loss = round(support20 * 0.96, 2)

    # 目標止盈價：近 20 日壓力位 或 黃金切割位（取較高者）
    spread = max(resistance20 - support20, 0.01)
    target = round(max(resistance20, support20 + spread * 0.618), 2)

    if target <= entry:
        target = round(entry * 1.08, 2)
    if stop_loss >= entry:
        stop_loss = round(entry * 0.95, 2)

    risk = entry - stop_loss
    reward = target - entry
    rr = reward / risk if risk > 0 else 0.0

    return {
        "buy_lo": buy_lo,
        "buy_hi": buy_hi,
        "entry": entry,
        "target": target,
        "stop_loss": stop_loss,
        "rr": round(rr, 2),
        "reward_pct": reward / entry * 100 if entry else 0.0,
        "risk_pct": risk / entry * 100 if entry else 0.0,
    }


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> tuple:
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    return k, d


def compute_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """計算 MACD（DIF、MACD 柱狀體、訊號線）。"""
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    macd_bar = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - macd_bar
    return dif, macd_bar, hist


def compute_ultimate_diagnosis(df: pd.DataFrame) -> dict:
    """雙維度矩陣：中長線趨勢 × 短線動能 → 四大綜合診斷結論。

    維度一 — 中長線趨勢 (trend)：判斷 20MA、60MA 與當前股價排列。
    維度二 — 短線動能 (momentum)：判斷 RSI(14) 位階與 MACD 柱狀體狀態。

    回傳 dict 含：
      trend / momentum / verdict / emoji / label / color / advice /
      ma20 / ma60 / rsi / macd_hist / vol_ratio / vol_change_pct
    """
    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])

    # ---- 均線 ----
    ma5 = float(df["MA5"].iloc[-1]) if not pd.isna(df["MA5"].iloc[-1]) else last
    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last

    # ---- RSI ----
    rsi_series = compute_rsi(closes, 14)
    rsi_now = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

    # ---- MACD ----
    dif, macd_bar, hist = compute_macd(closes)
    dif_now = float(dif.iloc[-1]) if not pd.isna(dif.iloc[-1]) else 0.0
    dif_prev = float(dif.iloc[-2]) if len(dif) >= 2 and not pd.isna(dif.iloc[-2]) else dif_now
    bar_now = float(macd_bar.iloc[-1]) if not pd.isna(macd_bar.iloc[-1]) else 0.0
    bar_prev = float(macd_bar.iloc[-2]) if len(macd_bar) >= 2 and not pd.isna(macd_bar.iloc[-2]) else bar_now
    hist_now = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0
    hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 and not pd.isna(hist.iloc[-2]) else hist_now

    golden_cross = dif_prev <= bar_prev and dif_now > bar_now
    death_cross = dif_prev >= bar_prev and dif_now < bar_now

    # ---- 成交量 ----
    vol_now = float(df["Volume"].iloc[-1])
    vol_prev = float(df["Volume"].iloc[-2]) if len(df) >= 2 else vol_now
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ratio = vol_now / vol_ma5 if vol_ma5 else 1.0
    vol_change_pct = (vol_now / vol_prev - 1) * 100 if vol_prev else 0.0

    # ================================================================
    #  維度一：中長線趨勢
    # ================================================================
    above_ma20 = last > ma20
    ma20_above_ma60 = ma20 > ma60
    below_ma20 = last < ma20
    ma20_below_ma60 = ma20 < ma60

    if above_ma20 and ma20_above_ma60:
        trend = "bull"
        trend_label = "多頭趨勢"
    elif below_ma20 and ma20_below_ma60:
        trend = "bear"
        trend_label = "空頭趨勢"
    else:
        trend = "neutral"
        trend_label = "震盪格局"

    # ================================================================
    #  維度二：短線動能
    # ================================================================
    rsi_overbought = rsi_now >= 70
    rsi_bullish = 50 < rsi_now < 70
    rsi_neutral = 40 <= rsi_now <= 50
    rsi_bearish = rsi_now < 40
    hist_positive = hist_now > 0
    hist_increasing = hist_now > hist_prev

    if (rsi_bullish or rsi_overbought) and hist_positive and (golden_cross or hist_increasing):
        momentum = "bull"
        momentum_label = "偏多動能"
    elif rsi_bearish and (death_cross or (not hist_positive and not hist_increasing)):
        momentum = "bear"
        momentum_label = "偏空動能"
    else:
        momentum = "neutral"
        momentum_label = "觀望中性"

    # ================================================================
    #  四大綜合診斷矩陣
    # ================================================================
    if trend == "bull" and momentum == "bull":
        verdict = "bull_bull"
        emoji = "🟢"
        label = "多頭主升段"
        advice = "中短線方向一致，建議順勢持股續抱或逢拉回分批佈局。"
        color = "#10B981"
    elif trend == "bull" and momentum != "bull":
        verdict = "bull_other"
        emoji = "🟡"
        label = "中多短洗（高檔震盪）"
        advice = "中長線趨勢保護短線，當前屬高檔洗盤，建議靜待拉回支撐位再行觀望佈局。"
        color = "#F59E0B"
    elif trend == "bear" and momentum == "bull":
        verdict = "bear_bull"
        emoji = "🟠"
        label = "弱勢反彈"
        advice = "中長線趨勢仍偏空，短線屬技術性反彈，留意上方均線反壓，不宜盲目追高。"
        color = "#F97316"
    else:
        verdict = "bear_bear"
        emoji = "🔴"
        label = "空頭防守"
        advice = "趨勢與動能雙弱，建議空手觀望為主，並嚴格執行停損防線。"
        color = "#EF4444"

    # ---- 均線排列文字 ----
    if above_ma20 and ma20_above_ma60:
        ma_text = f"多頭排列：股價 ({last:.2f}) 站穩 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之上"
    elif below_ma20 and ma20_below_ma60:
        ma_text = f"空頭排列：股價 ({last:.2f}) 跌破 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之下"
    elif above_ma20 and not ma20_above_ma60:
        ma_text = f"股價 ({last:.2f}) 站上 20MA ({ma20:.2f})，但 20MA 仍低於 60MA ({ma60:.2f})，趨勢未完全轉多"
    elif not above_ma20 and ma20_above_ma60:
        ma_text = f"股價 ({last:.2f}) 跌破 20MA ({ma20:.2f})，但 20MA 仍在 60MA ({ma60:.2f}) 之上，留意回測支撐"
    else:
        ma_text = f"均線糾結：股價 ({last:.2f}) 在 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之間震盪"

    # ---- RSI 文字 ----
    if rsi_overbought:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 70 以上過熱區，短線慎防拉回修正"
    elif rsi_bullish:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 50~70 偏多區，短線多方動能尚存"
    elif rsi_neutral:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 40~50 觀望中性區"
    elif rsi_bearish:
        rsi_text = f"RSI(14) = {rsi_now:.1f}，處於 40 以下偏空區，短線動能偏弱"
    else:
        rsi_text = f"RSI(14) = {rsi_now:.1f}"

    # ---- MACD 文字 ----
    if golden_cross:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，剛出現黃金交叉，短線動能轉多"
    elif death_cross:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，剛出現死亡交叉，短線動能轉空"
    elif hist_now > 0 and hist_increasing:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體翻正且放大，多頭動能增強"
    elif hist_now > 0:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體為正但縮小，多頭動能趨緩"
    elif hist_now < 0 and hist_increasing:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體為負但收斂，空頭動能減弱"
    elif hist_now < 0:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，柱狀體為負且擴大，空頭動能增強"
    else:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}"

    # ---- 成交量文字 ----
    if vol_ratio >= 1.5:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，屬於放量表態"
    elif vol_ratio >= 1.1:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，量能溫和放大"
    elif vol_ratio <= 0.6:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，屬於明顯縮量"
    elif vol_ratio <= 0.9:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，屬於縮量震盪"
    else:
        vol_text = f"成交量較昨日變動 {vol_change_pct:+.1f}%，量能為 5 日均量 {vol_ratio:.2f} 倍，量能平穩"

    return {
        "trend": trend,
        "trend_label": trend_label,
        "momentum": momentum,
        "momentum_label": momentum_label,
        "verdict": verdict,
        "emoji": emoji,
        "label": label,
        "color": color,
        "advice": advice,
        "last": last,
        "ma20": ma20,
        "ma60": ma60,
        "rsi": rsi_now,
        "macd_hist": hist_now,
        "macd_cross": "golden" if golden_cross else ("death" if death_cross else "none"),
        "vol_ratio": vol_ratio,
        "vol_change_pct": vol_change_pct,
        "ma_text": ma_text,
        "rsi_text": rsi_text,
        "macd_text": macd_text,
        "vol_text": vol_text,
    }


def render_ultimate_diagnosis_card(diag: dict, df: pd.DataFrame) -> None:
    """渲染 AI 三維度綜合診斷卡片：基本面 + 技術面 + 籌碼關注度。"""
    score = diag["score"]
    icon = diag["icon"]
    status = diag["status"]

    if score >= 80:
        color = "#10B981"
        glow = "rgba(16,185,129,0.40)"
    elif score >= 50:
        color = "#F59E0B"
        glow = "rgba(245,158,11,0.35)"
    else:
        color = "#EF4444"
        glow = "rgba(239,68,68,0.40)"

    st.markdown(
        f"""
<div class="ai-diagnosis-card" style="border-left:5px solid {color};
    box-shadow:0 0 22px {glow}, 0 6px 18px rgba(0,0,0,0.30);">

  <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
    <span style="font-size:1.8rem;">{icon}</span>
    <div>
      <div style="font-size:1.35rem;font-weight:900;color:{color};letter-spacing:1px;">AI 三維度綜合診斷：{status}</div>
      <div style="color:#9CA3AF;font-size:0.82rem;margin-top:2px;">綜合評分 {score} / 100　｜　基本面(30) + 技術面(50) + 籌碼關注度(20)</div>
    </div>
  </div>

  <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px;">
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #3B82F6;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📊 基本面體質</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("fundamental_text", "基本面數據解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #60A5FA;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📈 技術面趨勢</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("ma_text", "均線數據解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #A78BFA;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">💪 動能與指標</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("rsi_text", "RSI 指標解析中...")}｜{diag.get("macd_text", "MACD 動能解析中...")}</div>
    </div>
    <div style="flex:1;min-width:220px;background:rgba(255,255,255,0.05);border-radius:10px;padding:12px 14px;border-top:3px solid #FBBF24;">
      <div style="font-size:0.78rem;color:#9CA3AF;margin-bottom:4px;">📦 量能與籌碼</div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;">{diag.get("vol_text", "成交量解析中...")}</div>
    </div>
  </div>

  <div style="color:#6B7280;font-size:0.75rem;margin-top:10px;text-align:right;">
    三維度加權評分：基本面(30) + 技術面(50) + 籌碼關注度(20)　|　僅供參考
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # ── 數據剖析展開區 ──
    with st.expander("📊 診斷原因與數據剖析", expanded=False):
        expander_css = (
            "background:rgba(255,255,255,0.06);border-radius:8px;"
            "padding:10px 12px;border-left:3px solid {border_c};"
            "margin-bottom:8px;"
        )
        detail_items = [
            ("📊 基本面體質", "#3B82F6", diag.get("fundamental_text", "基本面數據解析中...")),
            ("📈 均線排列 (MA)", "#60A5FA", diag.get("ma_text", "均線數據解析中...")),
            ("💪 RSI 動能指標", "#A78BFA", diag.get("rsi_text", "RSI 指標解析中...")),
            ("📊 MACD 柱狀體", "#34D399", diag.get("macd_text", "MACD 動能解析中...")),
            ("📦 成交量與籌碼", "#FBBF24", diag.get("vol_text", "成交量解析中...")),
        ]
        grid_html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">'
        for title, border_c, body in detail_items:
            grid_html += (
                f'<div style="{expander_css.format(border_c=border_c)}">'
                f'<div style="font-size:0.78rem;color:{border_c};font-weight:700;margin-bottom:4px;">{title}</div>'
                f'<div style="font-size:0.85rem;color:#E5E7EB;line-height:1.55;">{body}</div>'
                f'</div>'
            )
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)


def render_key_support_resistance(df: pd.DataFrame, close_now: float) -> None:
    """計算並展示 4 個關鍵支撐 / 壓力 / 風控指標。"""
    d20 = df.tail(20)
    d5 = df.tail(5)

    resistance = float(d20["High"].max()) if len(d20) else close_now
    support = float(d20["Low"].min()) if len(d20) else close_now
    recent_5d_low = float(d5["Low"].min()) if len(d5) else support
    stop_loss = max(support * 0.97, recent_5d_low)

    upside = ((resistance - close_now) / close_now * 100) if close_now else 0
    downside = ((close_now - stop_loss) / close_now * 100) if close_now else 0
    rr_ratio = upside / downside if downside > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("近期壓力位 (Resistance)", f"{resistance:,.1f} 元", f"近 20 日最高價")
    c2.metric("近期支撐位 (Support)", f"{support:,.1f} 元", f"近 20 日最低價")
    c3.metric("建議參考停損價", f"{stop_loss:,.1f} 元", f"支撐位 -3% / 近 5 日低點")
    c4.metric("潛在風險報酬比", f"{rr_ratio:.2f}", f"上行 {upside:+.1f}% / 下行 -{downside:.1f}%", delta_color="off")

    if rr_ratio >= 2.0:
        st.success(f"風報比 {rr_ratio:.2f} 達標（≥ 2.0），從技術面看進場風險相對可控。")
    elif rr_ratio >= 1.0:
        st.info(f"風報比 {rr_ratio:.2f} 尚可（≥ 1.0），需搭配其他訊號綜合判斷。")
    else:
        st.warning(f"風報比 {rr_ratio:.2f} 偏低（< 1.0），短線追高風險較大，建議等待更好進場點。")


def _build_kline_figure(df: pd.DataFrame, period: str = "日 K") -> go.Figure:
    """純函式：將指定週期 K 線資料（df）轉為 Plotly K 線 + 成交量 Figure。

    不含 uirevision / 視角鎖定，這兩者會依呼叫端狀態在「渲染前」再疊加，
    因此本函式可安全地被 @st.cache_data 快取——開盤自動刷新不會重建圖表，
    使用者縮放 / 平移的視角與檢視區域可 100% 定格。
    period：日 K / 週 K / 月 K，用於標題、均線名稱與週末空窗設定。
    """
    closes = df["Close"]
    last = float(closes.iloc[-1])
    chg_pct_series = closes.diff() / closes.shift(1) * 100

    unit = "日" if period == "日 K" else ("週" if period == "週 K" else "月")
    if period == "日 K":
        ma_label = {"MA5": "5日線", "MA20": "20日線(月線)", "MA60": "60日線(季線)"}
    else:
        ma_label = {"MA5": f"5{unit}線", "MA20": f"20{unit}線", "MA60": f"60{unit}線"}

    resistance = float(df["High"].tail(60).max())
    support = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last * 0.95

    customdata = np.column_stack(
        [
            df["Open"].values,
            df["High"].values,
            df["Low"].values,
            df["Close"].values,
            chg_pct_series.fillna(0).values,
            df["MA5"].values,
            df["MA20"].values,
            df["MA60"].values,
        ]
    )

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.7, 0.3],
        subplot_titles=(f"{period} 股價走勢（K 線 + 5 / 20 / 60 均線）", "成交量"),
    )
    fig.update_annotations(font=dict(size=13, color="#374151", family="Microsoft JhengHei"))

    fig.add_trace(
        go.Candlestick(
            x=df["Date"],
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="K 線",
            customdata=customdata,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "開 %{customdata[0]:.2f}　高 %{customdata[1]:.2f}<br>"
                "低 %{customdata[2]:.2f}　收 %{customdata[3]:.2f}<br>"
                "漲跌幅 %{customdata[4]:+.2f}%<br>"
                "MA5 %{customdata[5]:.2f}　MA20 %{customdata[6]:.2f}　MA60 %{customdata[7]:.2f}"
                "<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )
    for col, color in (
        ("MA5", "#f59e0b"),
        ("MA20", "#3b82f6"),
        ("MA60", "#ef4444"),
    ):
        fig.add_trace(
            go.Scatter(
                x=df["Date"],
                y=df[col],
                mode="lines",
                name=ma_label[col],
                line=dict(width=1.4, color=color),
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )

    vol_colors = ["#22c55e" if c >= o else "#ef4444" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(
        go.Bar(
            x=df["Date"],
            y=df["Volume"],
            name="成交量",
            marker_color=vol_colors,
            marker_line_width=0,
            hovertemplate="成交量 %{y:,.0f} 張<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.add_hline(y=resistance, line_dash="dash", line_color="#dc2626", line_width=1.6, row=1, col=1)
    fig.add_hline(y=support, line_dash="dash", line_color="#16a34a", line_width=1.6, row=1, col=1)
    fig.add_annotation(
        x=df["Date"].iloc[-1],
        y=resistance,
        text=f"壓力位 {resistance:.1f}",
        showarrow=False,
        xanchor="right",
        yshift=10,
        font=dict(color="#dc2626", size=14, family="Microsoft JhengHei"),
    )
    fig.add_annotation(
        x=df["Date"].iloc[-1],
        y=support,
        text=f"支撐位 {support:.1f}",
        showarrow=False,
        xanchor="right",
        yshift=-10,
        font=dict(color="#16a34a", size=14, family="Microsoft JhengHei"),
    )

    fig.update_layout(
        height=640,
        hovermode="x",
        hoverlabel=dict(
            bgcolor="white",
            bordercolor="#cbd5e1",
            font=dict(size=12.5, color="#111827", family="Microsoft JhengHei"),
        ),
        xaxis_rangeslider_visible=False,
        dragmode="pan",  # 滑鼠預設互動模式 = 平移（手掌抓取），而非框選縮放
        legend=dict(
            orientation="h",
            x=0,
            xanchor="left",
            y=1.01,
            yanchor="bottom",
            font=dict(size=12.5, color="#1f2937"),
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#d1d5db",
            borderwidth=1,
        ),
        modebar=dict(
            bgcolor="rgba(17,24,39,0.9)",
            color="rgba(249,250,251,0.95)",
            activecolor="#f59e0b",
            orientation="h",
        ),
        margin=dict(l=10, r=10, t=70, b=10),
        template="plotly_white",
    )
    # 週期標題移到圖例正上方的留白區（y=1.0 + yshift 像素定位），與 Legend 垂直堆疊、絕不重疊
    if fig.layout.annotations:
        fig.layout.annotations[0].update(
            y=1.0,
            yanchor="bottom",
            yshift=50,
            bgcolor=None,
            bordercolor=None,
            borderwidth=None,
            borderpad=None,
        )
    fig.update_yaxes(title_text="股價 (NT$)", row=1, col=1, autorange=True)
    fig.update_yaxes(title_text="成交量 (張)", row=2, col=1)
    if period == "日 K":
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], row=1, col=1)
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1, spikecolor="#9ca3af")
    fig.update_yaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1, spikecolor="#9ca3af")
    return fig


@st.cache_data(ttl=300, show_spinner=False)
def get_kline_chart(stock_id: str, period: str = "日 K") -> go.Figure:
    """快取 K 線圖 Figure（快取鍵 = stock_id + period）。

    - 觸發重建條件：stock_id / period 變更、伺服器重啟、或使用者手動按「🔄 更新 K 線數據」
      （內部呼叫 get_kline_chart.clear() 清空全部週期快取）。
    - 開盤期間的自動刷新（每 15 秒）不會重建此圖，因此使用者縮放 / 平移的視角
      與檢視區域 100% 定格，絕不會因即時價格更新而被重置。
    """
    df, _ = _download_daily(stock_id, _PERIOD_HISTORY.get(period, "2y"))
    kdf = resample_kline(df, period)
    return _build_kline_figure(kdf, period)


def render_kline_chart(
    fig: go.Figure,
    ticker: str,
    view_lock: dict | None = None,
    uirevision: str | None = None,
    period: str = "日 K",
) -> None:
    """渲染 K 線圖：在快取的 Figure 上疊加視角狀態後輸出。

    - uirevision（預設 = ticker）：資料刷新時 100% 保留使用者縮放 / 平移視角；
      「切換股票 / 按復原按鈕」時由呼叫端改值，強制 Plotly 重新套用預設視角。
    - view_lock：Session State 雙向座標鎖定。非 None 時強制重送 x/y 軸範圍
      （僅「首次載入 / 切換股票 / 使用者按復原」成立），並同時關閉 autorange；
      為 None 時完全不送 range，純靠 uirevision 保留使用者已調整的視角。
    - 深色高對比水平 Modebar：只保留截圖、區域放大、平移、放大、縮小、重置等常用工具。
    """
    layout_update = {"uirevision": (uirevision if uirevision is not None else ticker)}
    if view_lock is not None:
        layout_update["xaxis_range"] = view_lock["xaxis_range"]
        layout_update["yaxis_range"] = view_lock["yaxis_range"]
    fig.update_layout(**layout_update)
    fig.update_yaxes(autorange=(view_lock is None), row=1, col=1)

    st.markdown(
        """
        <style>
        .js-plotly-plot .modebar {
            background: rgba(17, 24, 39, 0.92) !important;
            border-radius: 8px;
            padding: 3px 5px !important;
        }
        .js-plotly-plot .modebar-btn {
            padding: 4px !important;
        }
        .js-plotly-plot .modebar-btn svg {
            width: 17px !important;
            height: 17px !important;
        }
        .js-plotly-plot .modebar-btn path {
            fill: rgba(249, 250, 251, 0.95) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    mobile = is_mobile_request()
    modebar_remove = [
        "lasso2d",
        "select2d",
        "autoScale2d",
        "toggleSpikelines",
        "hoverClosestCartesian",
        "hoverCompareCartesian",
    ]
    if mobile:
        # 手機端只保留縮放 / 平移 / 重置，移除易誤觸的截圖與其餘工具
        modebar_remove.append("toImage")
    config = {
        "displaylogo": False,
        "responsive": True,
        "scrollZoom": True,
        "displayModeBar": False if mobile else True,
        "showAxisDragHandles": True,
        "modeBarButtonsToRemove": modebar_remove,
    }
    st.plotly_chart(fig, config=config, use_container_width=True, key=f"kline_{ticker}_{period}")


def render_timeframe_cards(df: pd.DataFrame, weekly: pd.DataFrame, monthly: pd.DataFrame) -> None:
    """三時態（天圖 / 週圖 / 月圖）精細化診斷卡片。"""
    closes = df["Close"]
    last = float(closes.iloc[-1])

    # ---- 日線指標 ----
    rsi = compute_rsi(closes)
    kd_k, kd_d = compute_kd(df["High"], df["Low"], closes)
    kd_j = 3 * kd_k - 2 * kd_d
    rsi_now = float(rsi.iloc[-1])
    k_now, d_now, j_now = float(kd_k.iloc[-1]), float(kd_d.iloc[-1]), float(kd_j.iloc[-1])
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ratio = float(df["Volume"].iloc[-1]) / vol_ma5 if vol_ma5 else 1.0
    deduct = float(closes.iloc[-20]) if len(closes) >= 20 else float(closes.mean())

    rsi_label = "過熱(短線慎追高)" if rsi_now >= 70 else ("超賣(留意反彈)" if rsi_now <= 30 else "中性")
    kd_label = "K>D 黃金交叉" if k_now >= d_now else "K<D 死亡交叉"
    j_label = "J 值過熱" if j_now >= 80 else ("J 值超賣" if j_now <= 20 else "J 值正常")
    vol_text = "放量上攻" if vol_ratio >= 1.3 else ("量能萎縮" if vol_ratio <= 0.7 else "量能平穩")

    pattern_daily, lvl_daily = timeframe_pattern(closes)
    pattern_weekly, lvl_weekly = timeframe_pattern(weekly["Close"])
    pattern_monthly, lvl_monthly = timeframe_pattern(monthly["Close"])

    # ---- 週線指標 ----
    wc = weekly["Close"].astype(float)
    wma12 = wc.rolling(12).mean()
    wma12_now = float(wma12.iloc[-1]) if not pd.isna(wma12.iloc[-1]) else float(wc.mean())
    w_bias = (float(wc.iloc[-1]) / wma12_now - 1) * 100 if wma12_now else 0.0
    w_vol5 = float(weekly["Volume"].tail(5).mean())
    w_ratio = float(weekly["Volume"].iloc[-1]) / w_vol5 if w_vol5 else 1.0
    w_turn = float(wc.max()) if len(wc) >= 3 else float(wc.iloc[-1])

    # ---- 月線指標 ----
    mc = monthly["Close"].astype(float)
    n_m = len(mc)
    if n_m >= 12:
        mom = (float(mc.iloc[-1]) / float(mc.iloc[-12]) - 1) * 100
        mom_span = 12
    elif n_m >= 6:
        mom = (float(mc.iloc[-1]) / float(mc.iloc[-6]) - 1) * 100
        mom_span = 6
    elif n_m >= 3:
        mom = (float(mc.iloc[-1]) / float(mc.iloc[0]) - 1) * 100
        mom_span = n_m - 1
    else:
        mom, mom_span = None, None
    m_ma = mc.rolling(12).mean().iloc[-1]
    m_defense = float(m_ma) if not pd.isna(m_ma) else float(mc.mean())
    if mom is None:
        cycle_text = "月線資料不足，僅約略評估"
    elif mom >= 30:
        cycle_text = "超級循環 / 大紅棒期"
    elif mom <= -10:
        cycle_text = "谷底打底期（低基期轉折）"
    else:
        cycle_text = "循環中段 / 區間築底"
    safety = {
        "success": "長線多頭架構完好，長線持股安全度較高。",
        "warning": "長線結構中性，長線持股需留意區間風險。",
        "error": "長線架構偏弱，長線持股安全度較低。",
    }[lvl_monthly]

    d1, d2, d3 = st.columns(3)
    with d1:
        st.markdown("**日線（天圖）診斷卡**")
        st.metric("RSI(14)", f"{rsi_now:.1f}", rsi_label, delta_color="off")
        st.metric("KD(9,3,3)", f"K {k_now:.1f} / D {d_now:.1f} / J {j_now:.1f}", f"{kd_label}・{j_label}", delta_color="off")
        st.markdown("**型態解讀**")
        render_pill(pattern_daily, lvl_daily)
        st.markdown("**觀察重點**")
        st.caption(f"今日量能為 5 日均量 {vol_ratio:.2f} 倍（{vol_text}），短線留意量價配合。")
        st.caption(
            f"MA20 明日扣抵值 {deduct:.1f}，今收{'高於' if last > deduct else '低於'}扣抵值，"
            f"月線短線將{'走升' if last > deduct else '走降'}；若後續數日扣抵高檔，留意月線下壓預警。"
        )

    with d2:
        st.markdown("**週線（週圖）診斷卡**")
        st.metric("週 MA12 乖離率", f"{w_bias:+.1f}%", "乖離過大留意拉回" if abs(w_bias) >= 15 else "乖離尚屬合理", delta_color="off")
        st.metric("週成交量倍數", f"{w_ratio:.2f} 倍", "爆量換手" if w_ratio >= 1.8 else ("量縮整理" if w_ratio <= 0.6 else "量能正常"), delta_color="off")
        st.markdown("**型態解讀**")
        render_pill(pattern_weekly, lvl_weekly)
        st.markdown("**觀察重點**")
        st.caption(f"中期主力防守區參考週 MA12 = {wma12_now:.1f}，回測不破則中線結構健康。")
        st.caption(f"留意週 K 是否帶量突破前波高點 {w_turn:.1f}（轉折確認訊號），否則僅屬高檔震盪。")

    with d3:
        st.markdown("**月線（月圖）診斷卡**")
        mom_value = f"{mom:+.1f}%" if mom is not None else "—"
        mom_label = f"近 {mom_span} 個月累計報酬" if mom is not None else "月線資料不足"
        st.metric(mom_label, mom_value, cycle_text, delta_color="off")
        st.metric("月 MA12 防守價", f"{m_defense:.1f}", "", delta_color="off")
        st.markdown("**型態解讀**")
        render_pill(pattern_monthly, lvl_monthly)
        st.markdown("**觀察重點**")
        st.caption(safety)
        st.caption(f"長線防守價設於月 MA12 = {m_defense:.1f}，長線持股以此為下檔依歸。")


def _price_block(label: str, value: str, note: str, color: str) -> None:
    st.markdown(
        f'<div style="border-top:3px solid {color};border-radius:8px;padding:8px 6px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.08);height:100%;">'
        f'<div style="font-weight:800;font-size:0.9rem;color:{color};">{label}</div>'
        f'<div style="font-weight:800;font-size:1.3rem;margin:6px 0;color:#111827;">{value}</div>'
        f'<div style="color:#6b7280;font-size:0.78rem;">{note}</div></div>',
        unsafe_allow_html=True,
    )


def _strategy_block(label: str, value: str, note: str, color: str) -> None:
    """價位策略卡片：深色底 + 亮白加粗大數字，提高對比度與可讀性。"""
    st.markdown(
        f'<div style="background-color:#1e222d;border-top:3px solid {color};border-radius:8px;'
        f'padding:12px 10px;height:100%;">'
        f'<span style="color:{color};font-weight:bold;font-size:0.95rem;">{label}</span><br>'
        f'<span style="color:#FFFFFF;font-size:26px;font-weight:bold;line-height:1.4;">{value}</span><br>'
        f'<span style="color:{color};font-size:12px;">{note}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_price_dashboard(levels: dict) -> None:
    """關鍵價格防禦儀表板（高對比實心卡片 + 超大亮色發光數字）。"""
    low_buy = levels["short_low"]
    core = levels["swing_def"]
    stop = levels["stop"]
    resistance = levels["resistance"]
    with st.container(border=True):
        st.markdown("**關鍵價格防禦儀表板**")
        st.caption("以當前股價與技術位階推算，僅供參考；實際操作請依盤勢與個人風險承受度調整。")
        st.markdown(
            f"""
<style>
.price-card {{
    background-color: #262c36;
    border-radius: 10px;
    padding: 16px;
    margin: 5px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.3);
}}
.price-title {{ font-size: 15px; font-weight: bold; margin-bottom: 8px; }}
.price-num {{ font-size: 28px; font-weight: 900; color: #FFD700; text-shadow: 0 0 10px rgba(255,215,0,0.3); margin: 6px 0; }}
.price-desc {{ font-size: 13px; color: #D1D5DB; }}
</style>

<div class="price-card-container" style="display: flex; gap: 10px; justify-content: space-between;">
    <div class="price-card" style="flex: 1; border-left: 5px solid #10B981;">
        <div class="price-title" style="color: #34D399;">🎯 短線低吸進場區</div>
        <div class="price-num">{low_buy[0]} ~ {low_buy[1]} 元</div>
        <div class="price-desc">回測區間分批佈局，不追高</div>
    </div>
    <div class="price-card" style="flex: 1; border-left: 5px solid #3B82F6;">
        <div class="price-title" style="color: #60A5FA;">🛡️ 波段核心防禦區</div>
        <div class="price-num">{core[0]} ~ {core[1]} 元</div>
        <div class="price-desc">跌破代表波段走勢轉弱</div>
    </div>
    <div class="price-card" style="flex: 1; border-left: 5px solid #EF4444;">
        <div class="price-title" style="color: #F87171;">🚨 關鍵停損防守位</div>
        <div class="price-num">{stop} 元</div>
        <div class="price-desc">跌破應紀律停損，嚴控風險</div>
    </div>
    <div class="price-card" style="flex: 1; border-left: 5px solid #A855F7;">
        <div class="price-title" style="color: #C084FC;">🚀 上檔第一阻力位</div>
        <div class="price-num">{resistance} 元</div>
        <div class="price-desc">帶量突破可望打開上行空間</div>
    </div>
</div>
""",
            unsafe_allow_html=True,
        )


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


def _is_etf(ticker: str) -> bool:
    """判斷是否為 ETF：代碼以 00 開頭（如 0050、00878、0056、006208、00919 等）。"""
    code = ticker.split(".")[0]
    return code.startswith("00")


ETF_INDUSTRY = "ETF / 指數股票型基金"
FALLBACK_INDUSTRY = "綜合產業 / 未分類"

# ── 層級 C：常見熱門台股產業備援字典（key 為純數字代碼） ──
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
    "2303": "半導體業",
    "3231": "電腦及週邊設備業",
    "2371": "電腦及週邊設備業",
    "2356": "電腦及週邊設備業",
    "2347": "電腦及週邊設備業",
    "3037": "半導體業",
    "2324": "電腦及週邊設備業",
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
    "2399": "半導體業",
    "6547": "半導體業",
    "3661": "半導體業",
    "2049": "半導體業",
    "3443": "半導體業",
    "8046": "半導體業",
    "5347": "半導體業",
    "3293": "電子零組件業",
    "2327": "半導體業",
    "1590": "半導體業",
    "3653": "半導體業",
    "6669": "半導體業",
    "3034": "半導體業",
    "2301": "電腦及週邊設備業",
    "2357": "電腦及週邊設備業",
    "2369": "電子零組件業",
    "2376": "半導體業",
    "2345": "半導體業",
    "2301": "電腦及週邊設備業",
    "2382": "電腦及週邊設備業",
    "2356": "電腦及週邊設備業",
    "2395": "電腦及週邊設備業",
    "2347": "電腦及週邊設備業",
    "3231": "電腦及週邊設備業",
    "2352": "電腦及週邊設備業",
    "2371": "電腦及週邊設備業",
    "4938": "電腦及週邊設備業",
    "2377": "半導體業",
    "3037": "半導體業",
    "3711": "半導體業",
    "2379": "半導體業",
    "2388": "半導體業",
    "3661": "半導體業",
    "6669": "半導體業",
    "2303": "半導體業",
    "2340": "光電業",
    "2409": "光電業",
    "3481": "光電業",
    "2327": "半導體業",
    "8046": "半導體業",
    "5347": "半導體業",
    "3443": "半導體業",
    "2049": "半導體業",
    "1590": "半導體業",
    "3653": "半導體業",
    "6547": "半導體業",
}


@st.cache_data(ttl=1800, show_spinner=False)
def get_tw_stock_metadata(ticker: str) -> dict:
    """一次性從 Yahoo 奇摩股市取得台股中文名稱與產業類別（30 分鐘快取）。

    回傳 {"name": "仁寶", "industry": "電腦週邊"}。
    任何欄位取得失敗時回傳空字串，由呼叫端決定 fallback。
    """
    code = ticker.split(".")[0]
    result: dict[str, str] = {"name": "", "industry": ""}

    # ── 層級 1：STOCK_INFO 硬編碼（最快） ──
    info = STOCK_INFO.get(ticker, {})
    if info.get("name"):
        result["name"] = info["name"]
    if info.get("industry"):
        result["industry"] = info["industry"]
    if result["name"] and result["industry"]:
        return result

    # ── 層級 2：TW_STOCK_NAMES 快查 ──
    if not result["name"] and code in TW_STOCK_NAMES:
        result["name"] = TW_STOCK_NAMES[code]

    # ── 層級 3：Yahoo 奇摩股市網頁爬蟲（最權威中文來源） ──
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

        # --- 名稱：從 <title> 或 og:title 提取 ---
        if not result["name"]:
            title_match = re.search(
                r"<title>\s*([^\(（<]+?)\s*[\(（]", html
            )
            if title_match:
                raw_name = title_match.group(1).strip()
                # 清理：移除 "走勢圖" " Yahoo股市" 等尾巴
                raw_name = re.sub(r"\s*(走勢圖|Yahoo股市| yahoo).*", "", raw_name, flags=re.IGNORECASE)
                raw_name = raw_name.strip()
                if raw_name and len(raw_name) <= 10:
                    result["name"] = raw_name

        # --- 名稱：從 og:title 再嘗試一次 ---
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

        # --- 名稱：從 meta description 提取 ---
        if not result["name"]:
            desc_match = re.search(
                r'name="description"\s+content="([^"]*?)\(', html
            )
            if desc_match:
                desc_name = desc_match.group(1).strip()
                if desc_name and len(desc_name) <= 10 and re.search(r"[\u4e00-\u9fff]", desc_name):
                    result["name"] = desc_name

        # --- 產業：regex 搜尋 "sectorName":"xxx" ---
        if not result["industry"]:
            sector_match = re.search(r'"sectorName"\s*:\s*"([^"]+)"', html)
            if sector_match:
                result["industry"] = sector_match.group(1)

        # --- 產業：從 ySector 標記取得 ---
        if not result["industry"]:
            ysector_match = re.search(r'"ySector"\s*:\s*"([^"]+)"', html)
            if ysector_match:
                result["industry"] = ysector_match.group(1)

    except requests.RequestException:
        pass

    # ── 層級 4：yfinance 退路（僅補不足的欄位） ──
    try:
        import yfinance as yf
        yf_info = yf.Ticker(ticker).info or {}
        if not result["name"]:
            # 優先取 shortName（通常較短），避免 longName 是英文全名
            yf_name = yf_info.get("shortName") or yf_info.get("longName") or ""
            # 若取得的是純英文，放棄（不覆蓋空值讓 fallback 字典處理）
            if yf_name and re.search(r"[\u4e00-\u9fff]", yf_name):
                result["name"] = yf_name
        if not result["industry"]:
            yf_ind = yf_info.get("sector") or yf_info.get("industry") or ""
            if yf_ind and isinstance(yf_ind, str) and len(yf_ind) > 1:
                result["industry"] = yf_ind
    except Exception:
        pass

    return result


def get_fundamental(ticker: str) -> dict:
    info = STOCK_INFO.get(ticker, {})
    code = ticker.split(".")[0]

    if _is_etf(ticker):
        industry = ETF_INDUSTRY
    elif "industry" in info:
        industry = info["industry"]
    else:
        meta = get_tw_stock_metadata(ticker)
        industry = meta["industry"] or _FALLBACK_INDUSTRY_DICT.get(code, FALLBACK_INDUSTRY)

    # 從 yfinance info 取得基本面數據
    yf_info = {}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        yf_info = t.info or {}
    except Exception:
        pass

    return {
        "cycle": info.get("sector_cycle", "擴張成長"),
        "shortage": info.get("shortage", "市場供需與漲價題材待觀察。"),
        "inventory": info.get("inventory_status", "正常"),
        "capex": info.get("capex_warning", False),
        "beta": float(info.get("beta", 1.0)),
        "industry": industry,
        "trailing_eps": yf_info.get("trailingEps"),
        "forward_eps": yf_info.get("forwardEps"),
        "pe_ratio": yf_info.get("trailingPE"),
        "forward_pe": yf_info.get("forwardPE"),
        "roe": yf_info.get("returnOnEquity"),
        "dividend_yield": yf_info.get("dividendYield"),
        "market_cap": yf_info.get("marketCap"),
    }


def calculate_precise_ai_score(df: pd.DataFrame, chip: dict, fund: dict) -> tuple[int, str, str]:
    """三維度全方位綜合評分（總分 100 分）：基本面(30) + 技術面(50) + 籌碼關注度(20)。
    回傳 (score, icon, status)。
    """
    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])
    vol_now = float(df["Volume"].iloc[-1])
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ma20 = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else vol_ma5

    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last
    rsi_series = compute_rsi(closes, 14)
    rsi_now = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    _, _, hist = compute_macd(closes)
    hist_now = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0

    # ══════════════════════════════════════════════════════════
    #  維度一：基本面 (Fundamental) — 權重 30 分
    # ══════════════════════════════════════════════════════════
    fund_score = 0

    # EPS & 獲利能力 (10分)
    eps = fund.get("trailing_eps")
    if eps is not None and eps > 0:
        fund_score += 10

    # 本益比 P/E Ratio (10分)
    pe = fund.get("pe_ratio")
    if pe is not None and 10 <= pe <= 25:
        fund_score += 10

    # ROE / 殖利率 (10分)
    roe = fund.get("roe")
    div_yield = fund.get("dividend_yield")
    roe_ok = roe is not None and roe > 0.10
    div_ok = div_yield is not None and div_yield > 0.04
    if roe_ok or div_ok:
        fund_score += 10

    # ══════════════════════════════════════════════════════════
    #  維度二：技術面 (Technical) — 權重 50 分
    # ══════════════════════════════════════════════════════════
    tech_score = 0

    # MA 趨勢 (20分)
    if last > ma20 and ma20 > ma60:
        tech_score += 20
    elif last > ma20 and ma20 <= ma60:
        tech_score += 10
    elif last < ma20 and last < ma60:
        tech_score -= 10

    # 動能指標 (20分)
    if hist_now > 0:
        tech_score += 10
    if 50 < rsi_now < 70:
        tech_score += 10
    elif rsi_now > 70:
        tech_score += 5

    # 成交量與支撐壓力 (10分)
    support = float(df["Close"].tail(20).min())
    vol_above_ma5 = vol_now > vol_ma5
    above_support = last > support
    if vol_above_ma5 and above_support:
        tech_score += 10
    elif above_support:
        tech_score += 5

    # ══════════════════════════════════════════════════════════
    #  維度三：籌碼與關注度 (Flow & Interest) — 權重 20 分
    # ══════════════════════════════════════════════════════════
    flow_score = 0

    # 量能爆發度 (10分)：當日成交量 > 20日均量 1.5 倍
    if vol_ma20 > 0 and vol_now > vol_ma20 * 1.5:
        flow_score += 10
    elif vol_ma20 > 0 and vol_now > vol_ma20 * 1.2:
        flow_score += 5

    # 市值大戶保護力 (10分)：大型權值股/核心 ETF
    market_cap = fund.get("market_cap")
    if market_cap is not None and market_cap >= 200_000_000_000:
        flow_score += 10

    # ══════════════════════════════════════════════════════════
    #  總分 = 基本面 + 技術面 + 籌碼關注度
    # ══════════════════════════════════════════════════════════
    total = fund_score + tech_score + flow_score
    total = int(np.clip(total, 0, 100))

    if total >= 80:
        return total, "🟢", "多頭主升 (基本面+技術面極佳)"
    elif total >= 50:
        return total, "🟡", "震盪整理 (指標分歧/觀望)"
    else:
        return total, "🔴", "空頭防守 (趨勢偏弱)"


# ====================== 權威統一診斷（全站唯一來源） ======================
def get_global_precise_diagnosis(ticker: str, df: pd.DataFrame, chip: dict, fund: dict) -> dict:
    """全站唯一的權威 AI 診斷函式，同時輸出分數、燈號、狀態標籤。
    左邊排行榜與右邊詳細診斷卡片，一律呼叫此函式，確保 100% 同步。
    """
    score, icon, status = calculate_precise_ai_score(df, chip, fund)
    code = ticker.split(".")[0]
    name = lookup_stock_name(ticker)
    if name == code:
        name = fund.get("industry", code)

    closes = df["Close"].astype(float)
    last = float(closes.iloc[-1])
    ma20 = float(df["MA20"].iloc[-1]) if not pd.isna(df["MA20"].iloc[-1]) else last
    ma60 = float(df["MA60"].iloc[-1]) if not pd.isna(df["MA60"].iloc[-1]) else last
    rsi_series = compute_rsi(closes, 14)
    rsi_now = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    _, _, hist = compute_macd(closes)
    hist_now = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0
    vol_now = float(df["Volume"].iloc[-1])
    vol_ma5 = float(df["Volume"].tail(5).mean())
    vol_ma20 = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else vol_ma5
    vol_ratio = vol_now / vol_ma5 if vol_ma5 else 1.0
    vol_prev = float(df["Volume"].iloc[-2]) if len(df) >= 2 else vol_now
    vol_change_pct = (vol_now / vol_prev - 1) * 100 if vol_prev else 0.0

    # ── 基本面診斷文字 ──
    eps = fund.get("trailing_eps")
    pe = fund.get("pe_ratio")
    roe = fund.get("roe")
    div_yield = fund.get("dividend_yield")
    eps_desc = f"EPS = {eps:.2f}" if eps is not None else "EPS 暫無資料"
    pe_desc = f"P/E = {pe:.1f}倍（合理）" if pe is not None and 10 <= pe <= 25 else \
              f"P/E = {pe:.1f}倍（偏高）" if pe is not None and pe > 25 else \
              f"P/E = {pe:.1f}倍" if pe is not None else "P/E 暫無資料"
    roe_desc = f"ROE = {roe * 100:.1f}%" if roe is not None else "ROE 暫無資料"
    div_desc = f"殖利率 = {div_yield * 100:.2f}%" if div_yield is not None else ""
    fundamental_text = f"{eps_desc}｜{pe_desc}｜{roe_desc}" + (f"｜{div_desc}" if div_desc else "")

    # ── 技術面診斷文字 ──
    if last > ma20 and ma20 > ma60:
        ma_text = f"多頭排列：股價 ({last:.2f}) 站穩 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之上"
    elif last > ma20 and ma20 <= ma60:
        ma_text = f"反彈格局：股價 ({last:.2f}) 站上 20MA ({ma20:.2f})，但 20MA 仍低於 60MA ({ma60:.2f})"
    elif last < ma20 and last < ma60:
        ma_text = f"空頭排列：股價 ({last:.2f}) 跌破 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之下"
    else:
        ma_text = f"均線糾結：股價 ({last:.2f}) 在 20MA ({ma20:.2f}) 與 60MA ({ma60:.2f}) 之間震盪"

    rsi_text = f"RSI(14) = {rsi_now:.1f}" + (
        "，處於 70 以上過熱區" if rsi_now >= 70 else
        "，處於 50~70 偏多區" if rsi_now > 50 else
        "，處於 40~50 觀望中性區" if rsi_now >= 40 else
        "，處於 40 以下偏空區"
    )

    if hist_now > 0:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，多方動能"
    else:
        macd_text = f"MACD 柱狀體 = {hist_now:+.2f}，空方動能"

    # ── 量能與籌碼文字 ──
    vol_ratio_20 = vol_now / vol_ma20 if vol_ma20 else 1.0
    if vol_ratio_20 >= 1.5:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能爆發（大戶進場關照）"
    elif vol_ratio_20 >= 1.2:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能溫和放大"
    elif vol_ratio_20 <= 0.6:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，明顯縮量"
    else:
        vol_text = f"成交量為 20 日均量 {vol_ratio_20:.2f} 倍，量能平穩"

    market_cap = fund.get("market_cap")
    if market_cap is not None and market_cap >= 200_000_000_000:
        cap_text = f"市值 {market_cap / 1e12:.1f} 兆，屬大型權值股（大戶保護力強）"
    elif market_cap is not None:
        cap_text = f"市值 {market_cap / 1e9:.0f} 億"
    else:
        cap_text = ""
    if cap_text:
        vol_text += f"｜{cap_text}"

    return {
        "ticker": ticker,
        "code": code,
        "name": name,
        "score": score,
        "icon": icon,
        "status": status,
        "fundamental_text": fundamental_text,
        "ma_text": ma_text,
        "rsi_text": rsi_text,
        "macd_text": macd_text,
        "vol_text": vol_text,
    }


# ====================== AI 高分飆股排行榜（100 檔精選池） ======================
_TOP100_TICKERS: list[str] = [
    # ── 半導體 ──
    "2330.TW", "2303.TW", "2454.TW", "3711.TW", "2379.TW", "3034.TW",
    "2408.TW", "2344.TW", "2337.TW", "3443.TW", "6770.TW", "8046.TW",
    "6239.TW", "3661.TW", "5269.TW", "5274.TW", "6547.TW", "3653.TW",
    "6669.TW", "3037.TW", "2327.TW", "3017.TW", "2368.TW", "6182.TW",
    "3293.TWO", "8069.TWO", "6223.TWO", "3533.TW",
    # ── AI 伺服器 / 散熱 / ODM ──
    "2382.TW", "3231.TW", "2376.TW", "2357.TW", "2356.TW", "2353.TW",
    "4938.TW", "2301.TW", "2377.TW", "2395.TW", "3044.TW", "3596.TW",
    "2345.TW", "2388.TW", "3293.TWO", "3045.TW", "4958.TW",
    # ── 面板 / 光電 ──
    "3481.TW", "2409.TW", "6116.TW", "3008.TW", "3406.TW", "2474.TW",
    # ── 電子零組件 / 連接器 ──
    "2324.TW", "2308.TW", "2327.TW", "2308.TW", "2301.TW", "2449.TW",
    "2458.TW", "3702.TW", "8112.TW", "6271.TW", "6285.TW",
    # ── 金融 ──
    "2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW", "2892.TW",
    "2880.TW", "2885.TW", "2890.TW", "2887.TW", "2883.TW", "2801.TW",
    "5880.TW", "5876.TW", "2892.TW", "2888.TW",
    # ── 航運 ──
    "2603.TW", "2609.TW", "2615.TW", "2618.TW", "2610.TW", "2637.TW",
    # ── 傳產 / 重電 / 水泥 / 鋼鐵 ──
    "1101.TW", "1301.TW", "1303.TW", "2002.TW", "1504.TW", "1605.TW",
    "9904.TW", "9910.TW", "9921.TW", "2207.TW", "1216.TW",
    # ── 電信 / 軟體 ──
    "2412.TW", "4904.TW", "3045.TW",
    # ── 高股息 ETF ──
    "0050.TW", "0056.TW", "00878.TW", "00919.TW", "00929.TW",
    "00940.TW", "006208.TW", "00692.TW", "00881.TW", "00713.TW",
]
# 去重
_TOP100_TICKERS = list(dict.fromkeys(_TOP100_TICKERS))


def _compute_one_stock(ticker: str) -> dict | None:
    """為單一股票計算統一的 AI 診斷（分數 + 燈號 + 診斷）。"""
    try:
        df, _ = fetch_stock_data(ticker)
        if df is None or len(df) < 30:
            return None
        chip = generate_chip_data(ticker)
        fund = get_fundamental(ticker)
        return get_global_precise_diagnosis(ticker, df, chip, fund)
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def get_top_ranked_stocks() -> list[dict]:
    """並行計算 100 檔精選台股的 AI 綜合評分，回傳由高至低排序的排行榜。"""
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_compute_one_stock, t): t for t in _TOP100_TICKERS}
        for future in as_completed(futures):
            r = future.result()
            if r is not None:
                results.append(r)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def render_ai_scoreboard() -> None:
    """在側邊欄渲染 AI 高分飆股排行榜（前 15 名）。"""
    st.markdown("**🏆 AI 高分選股排行榜**")
    with st.spinner("正在為您計算 100 檔精選股 AI 評分，首次載入約需 5~10 秒..."):
        scoreboard = get_top_ranked_stocks()
    if not scoreboard:
        st.caption("暫無排行榜資料")
        return
    for item in scoreboard[:15]:
        label = f"【{item['score']}分 {item['icon']}】{item['name']} ({item['code']})"
        if st.button(label, key=f"rank_{item['ticker']}", use_container_width=True):
            st.session_state["selected_stock"] = item["ticker"]
            st.session_state["sidebar_key"] += 1
            force_scroll_to_top()
            st.rerun()


def streak_text(n: int, who: str) -> str:
    if n > 0:
        return f"{who} 連買 {n} 日"
    if n < 0:
        return f"{who} 連賣 {-n} 日"
    return f"{who} 買賣持平"


def render_pill(text: str, level: str):
    bg = {"success": "#dcfce7", "warning": "#fef3c7", "error": "#fee2e2"}[level]
    fg = {"success": "#15803d", "warning": "#b45309", "error": "#b91c1c"}[level]
    st.markdown(
        f'<span style="background:{bg};color:{fg};padding:4px 12px;border-radius:999px;'
        f'font-weight:700;">{text}</span>',
        unsafe_allow_html=True,
    )


def cycle_level(cycle: str) -> str:
    if cycle in ("谷底復甦", "擴張成長"):
        return "success"
    if cycle in ("暴衝期", "高峰期"):
        return "warning"
    return "error"



# ====================== Sidebar 搜尋與設定 ======================
if "selected_stock" not in st.session_state:
    st.session_state["selected_stock"] = None
if "sidebar_key" not in st.session_state:
    st.session_state["sidebar_key"] = 0

with st.sidebar:
    with st.container(key=f"sidebar_{st.session_state['sidebar_key']}"):
        st.markdown('<div id="sidebar-top"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:1.4rem;font-weight:900;letter-spacing:1px;'
            'background:linear-gradient(90deg,#FFD700 0%,#FFA500 100%);'
            '-webkit-background-clip:text;background-clip:text;'
            '-webkit-text-fill-color:transparent;color:transparent;'
            'filter:drop-shadow(0 2px 3px rgba(180,120,0,0.30));">'
            '💰 財神爺選股</div>',
            unsafe_allow_html=True,
        )
        st.caption("台股智慧投資分析助理（教學用途）")

        ticker_input = st.text_input(
            "股票代號 / 名稱",
            placeholder="例如：2330.TW 或 台積電",
            help="支援台股 4 碼代號（如 2330）或中文名（如 台積電）。",
        )
        if st.button("🔍 開始 AI 診斷", type="primary", use_container_width=True, key="btn_search_sidebar"):
            valid, ticker_code, _ = validate_stock_input(ticker_input)
            if valid and ticker_code:
                st.session_state["selected_stock"] = ticker_code
                st.session_state["sidebar_key"] += 1
                force_scroll_to_top()
            else:
                st.session_state["search_error"] = build_search_error_message(ticker_input)
                st.rerun()

        st.markdown("**🔥 熱門推薦 Quick Pick**")
        for name, code in get_daily_trending_stocks():
            if st.button(f"{name}　{code.replace('.TW', '')}", key=f"quick_{code}", use_container_width=True):
                st.session_state["selected_stock"] = code
                st.session_state["sidebar_key"] += 1
                force_scroll_to_top()

        st.divider()
        render_ai_scoreboard()
        st.divider()
        if st.button("🏠 回到首頁 / 重新搜尋", key="btn_home_sidebar", use_container_width=True):
            st.session_state["selected_stock"] = None
            st.session_state["sidebar_key"] += 1
            force_scroll_to_top()
            st.rerun()

        st.caption("資料來源：優先使用 Yahoo Finance，離線或延遲時自動以 Mock Data 展示。")
        st.caption("交易時段（週一至五 09:00–13:30）將自動每 15 秒刷新頁面，呈現即時價格浮動。")

st.markdown('<div id="main-top"></div>', unsafe_allow_html=True)

status = get_market_status()
setup_autorun(status["is_open"])

ticker = st.session_state["selected_stock"]

# 防呆：session 中若殘留無法解析的非法代號，退回首頁模式並提示
if ticker is not None and resolve_ticker(ticker) is None:
    st.session_state["search_error"] = build_search_error_message(ticker)
    st.session_state["selected_stock"] = None
    ticker = None

# 顯示搜尋錯誤提示（若有），置於主要區域頂端、搜尋欄上方
if "search_error" in st.session_state:
    st.error(st.session_state.pop("search_error"), icon="⚠️")

# ====================== 首頁模式：熱門推薦與搜尋引導 ======================
if ticker is None:
    # 熱門卡片點擊後同步搜尋框（必須在元件實體化「前」套用）
    if "home_search_sync" in st.session_state:
        st.session_state["home_search"] = st.session_state.pop("home_search_sync")

    st.markdown(
        '<div style="text-align:center;padding:48px 20px 8px;">'
        '<div style="display:flex;align-items:center;justify-content:center;gap:15px;margin-bottom:5px;">'
        '<img src="https://img.icons8.com/color/96/gold-bars.png" width="45" height="45" style="object-fit:contain;">'
        '<div style="font-size:42px;font-weight:900;letter-spacing:2px;color:#FFD700;'
        'text-shadow:0 3px 6px rgba(180,120,0,0.40);">財神爺選股</div>'
        '<img src="https://img.icons8.com/color/96/gold-bars.png" width="45" height="45" style="object-fit:contain;"></div>'
        '<div style="color:#b45309;font-size:0.98rem;font-weight:700;margin-top:14px;">'
        '「富貴雙收 ‧ 點石成金 ｜ AI 智慧選股與技術診斷」</div>'
        '<div style="color:#6b7280;font-size:1.02rem;margin-top:10px;">'
        '輸入台股代號或名稱，或直接點選下方熱門標的，'
        '立即取得技術面 K 線、籌碼面、基本面與 AI 綜合診斷。</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # 搜尋輸入框：Enter 或「開始 AI 診斷」按鈕皆可送出，直接載入完整分析
    with st.form(key="home_search_form"):
        st.text_input(
            "🔍 股票代號 / 名稱",
            placeholder="例如：2330 或 台積電",
            key="home_search",
        )
        home_submitted = st.form_submit_button("🔍 開始 AI 診斷", use_container_width=True, key="home_submit_btn")
    if home_submitted:
        valid, ticker_code, _ = validate_stock_input(st.session_state["home_search"])
        if valid and ticker_code:
            st.session_state["selected_stock"] = ticker_code
            st.session_state["sidebar_key"] += 1
            force_scroll_to_top()
            st.rerun()
        else:
            st.session_state["search_error"] = build_search_error_message(st.session_state["home_search"])
            st.rerun()

    st.markdown("### 🔥 熱門推薦標的 Quick Pick")
    hot_trending = get_daily_trending_stocks()
    hot_columns = st.columns(len(hot_trending))
    for col, (name, code) in zip(hot_columns, hot_trending):
        with col:
            if st.button(
                f"**{name}**\n\n{code.replace('.TW', '')}",
                key=f"hot_pick_{code}",
                use_container_width=True,
            ):
                st.session_state["selected_stock"] = code
                st.session_state["home_search_sync"] = name
                st.session_state["sidebar_key"] += 1
                force_scroll_to_top()
                st.rerun()

    st.divider()
    st.caption("點擊任一熱門標的，或於左側搜尋欄輸入股票代號 / 名稱後按「開始 AI 診斷」，即可進入完整分析。")
    st.stop()

info = STOCK_INFO.get(ticker, {})
stock_code = ticker.rsplit(".", 1)[0]
stock_name = lookup_stock_name(ticker)

with st.spinner("💰 財神爺正在讀取基本面、籌碼面與 K 線資料，請稍候..."):
    fund = get_fundamental(ticker)
    chip = generate_chip_data(ticker)
    if status["is_open"]:
        df, source = _download_daily(ticker)
        live = fetch_live_price(ticker)
        if live is not None:
            source += "（1 分鐘級即時報價）"
    else:
        df, source = fetch_stock_data(ticker)
        live = None

# ====================== 頂部概覽與 AI 評級 ======================
header_left, header_right = st.columns([6, 1])
with header_left:
    st.markdown(f"# {stock_name} ({stock_code})")
with header_right:
    if st.button("🏠 回到首頁", key="btn_back_home", use_container_width=True):
        st.session_state["selected_stock"] = None
        st.session_state["sidebar_key"] += 1
        force_scroll_to_top()
        st.rerun()
st.caption(f"產業：{fund['industry']}　|　資料來源：{source}")
render_market_badge(status)

# ====================== AI 終極綜合診斷面板（統一權威來源） ======================
precise_diag = get_global_precise_diagnosis(ticker, df, chip, fund)
render_ultimate_diagnosis_card(precise_diag, df)

close_now = float(df["Close"].iloc[-1])
close_prev = float(df["Close"].iloc[-2])
chg = close_now - close_prev
chg_pct = chg / close_prev * 100
vol_now = float(df["Volume"].iloc[-1])
vol_prev = float(df["Volume"].iloc[-2])
vol_pct = (vol_now / vol_prev - 1) * 100 if vol_prev else 0.0

if live is not None:
    close_now = live["price"]
    chg = live["change"]
    chg_pct = live["change_pct"]

pattern_daily, level_daily = timeframe_pattern(df["Close"])
weekly = resample_ohlc(df, "W-FRI")
monthly = resample_ohlc(df, _month_rule())
pattern_weekly, level_weekly = timeframe_pattern(weekly["Close"])
pattern_monthly, level_monthly = timeframe_pattern(monthly["Close"])

score = precise_diag["score"]
levels = compute_levels(close_now)

c1, c2, c3 = st.columns(3)
c1.metric("目前股價 (NT$)", f"{close_now:,.2f}", delta=f"{chg_pct:+.2f}%")
c2.metric("當日漲跌幅", f"{chg_pct:+.2f}%", delta=f"{chg:+.2f} 元")
c3.metric("成交量", f"{vol_now / 1000:,.1f} 仟張", delta=f"較昨日 {vol_pct:+.1f}%")
st.caption(f"AI 綜合分數：{score} / 100（技術面 {pattern_daily}）")

st.divider()

# ====================== 技術面 K 線與型態診斷 ======================
st.subheader("技術面：K 線與型態診斷")

if "chart_view" not in st.session_state:
    st.session_state["chart_view"] = {}
if "chart_uirev" not in st.session_state:
    st.session_state["chart_uirev"] = 0

# K 線週期切換：日 K / 週 K / 月 K
kline_period = st.radio(
    "📊 選擇 K 線週期：",
    ["日 K", "週 K", "月 K"],
    horizontal=True,
    key="kline_period",
)

# 依所選週期重抽樣，作為「預設視角」與「繪圖資料」來源
kdf = resample_kline(df, kline_period)

# 預設視角（以最新 80 根所選週期 K 計算）
n_show = 80
window = kdf.tail(n_show)
price_min, price_max = float(window["Low"].min()), float(window["High"].max())
pad = (price_max - price_min) * 0.05
default_view = {
    "xaxis_range": [window["Date"].iloc[0], window["Date"].iloc[-1]],
    "yaxis_range": [price_min - pad, price_max + pad],
}

# 手動更新按鈕：點擊後清空 K 線圖快取，本輪即重新抓取日 K 並重繪
b_left, b_right = st.columns([1, 1])
with b_left:
    if st.button(
        "🔄 更新 K 線數據",
        key=f"btn_kline_refresh_{ticker}",
        help="重新抓取最新日 K 資料並重繪（開盤期間自動刷新不會觸發）",
    ):
        get_kline_chart.clear()
with b_right:
    reset_clicked = st.button(
        "🔄 復原全圖視角",
        key=f"btn_chart_reset_{ticker}",
        help=f"回復到最新 80 根{kline_period.replace(' K', '')} K 的預設視角",
    )

view_key = f"{ticker}|{kline_period}"
apply_default = reset_clicked or (view_key not in st.session_state["chart_view"])

if apply_default:
    st.session_state["chart_view"][view_key] = default_view
    st.session_state["chart_uirev"] += 1

view_lock = st.session_state["chart_view"].get(view_key) if apply_default else None
uirevision = f"{ticker}|{kline_period}-v{st.session_state['chart_uirev']}"

# 快取 K 線圖：開盤自動刷新完全不會重建此圖，視角 100% 定格
fig_kline = get_kline_chart(ticker, kline_period)
render_kline_chart(fig_kline, ticker, view_lock=view_lock, uirevision=uirevision, period=kline_period)

render_timeframe_cards(df, weekly, monthly)

# ====================== 關鍵支撐 / 壓力 / 風控指標 ======================
st.subheader("關鍵價位與風控防線")
render_key_support_resistance(df, close_now)

levels["resistance"] = round(float(df["High"].tail(60).max()), 1)
render_price_dashboard(levels)

st.divider()

# ====================== AI 價位策略與風險評估 ======================
strategy = compute_price_strategy(df, close_now)
st.subheader("🎯 AI 價位策略與風險評估")
st.caption("依據近期 20 / 60 日 K 線動態計算；僅供教學與研究參考，不構成投資建議。")

with st.container(border=True):
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        _strategy_block(
            "🟢 最佳低風險進場區",
            f"{strategy['buy_lo']:,.2f} ~ {strategy['buy_hi']:,.2f} 元",
            "此區間接近支撐，盈虧比最佳、風險最小",
            "#00FF7F",
        )
    with p2:
        _strategy_block(
            "🔵 建議入場價",
            f"{strategy['entry']:,.2f} 元",
            "拉回至支撐 / MA20 附近分批佈局",
            "#00BFFF",
        )
    with p3:
        _strategy_block(
            "🎯 目標止盈價",
            f"{strategy['target']:,.2f} 元",
            f"潛在報酬 +{strategy['reward_pct']:.1f}%",
            "#FF6B6B",
        )
    with p4:
        _strategy_block(
            "🛑 嚴格停損價",
            f"{strategy['stop_loss']:,.2f} 元",
            f"防守風險 -{strategy['risk_pct']:.1f}%",
            "#FFA500",
        )

    rr = strategy["rr"]
    if rr >= 2.0:
        st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，目前達標、風險相對可控。")
    elif rr >= 1.5:
        st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，接近達標、可小量試單。")
    else:
        st.caption(f"風報比（盈虧比）＝ {rr:.1f}，理想值需 ≥ 2.0，尚未達標、追價風險偏高。")

    # AI 風險指引評語
    if close_now > strategy["buy_hi"] * 1.05:
        st.warning("⚠️ 目前股價離支撐區較遠，追高風險較大，建議耐心等待回檔至低風險區附近再分批佈局。")
    elif strategy["buy_lo"] * 0.98 <= close_now <= strategy["buy_hi"] * 1.03:
        st.info(f"✅ 當前價格位於相對低風險區間，且風報比達 {rr:.1f}，適合分批建立基本倉位。")
    else:
        st.info(f"📊 目前股價位於低風險區上緣附近，可等待拉回 {strategy['buy_hi']:,.2f} 元以下再分批佈局。")

st.divider()

# ====================== 籌碼面四大指標 ======================
st.subheader("籌碼面：法人 / 大戶 / 主力 / 融資融券")

cc1, cc2, cc3, cc4 = st.columns(4)
with cc1:
    st.markdown("**法人動向**")
    st.metric("外資", f"{chip['foreign']:+d} 日", streak_text(chip["foreign"], "外資"))
    st.metric("投信", f"{chip['it']:+d} 日", streak_text(chip["it"], "投信"))
    st.metric("自營商", f"{chip['dealer']:+d} 日", streak_text(chip["dealer"], "自營商"))
    if chip["foreign"] > 0 and chip["it"] > 0:
        st.success("外資與投信同步偏多，籌碼面助漲。")
    elif chip["foreign"] < 0 and chip["it"] < 0:
        st.warning("外資與投信同步賣超，籌碼面有壓。")
    else:
        st.info("法人多空分歧，以區間應對。")

with cc2:
    st.markdown("**集保大戶 vs 散戶集中度**")
    st.metric("大戶持股比重", f"{chip['large_holder']}%", f"{chip['large_delta']:+.1f} 百分點")
    st.metric("散戶持股比重", f"{chip['retail']}%")
    if chip["large_delta"] > 0:
        st.success("大戶持股集中度上升，籌碼趨向集中。")
    else:
        st.warning("大戶持股鬆動、散戶進場，留意反轉風險。")

with cc3:
    st.markdown("**主力買賣家數差**")
    st.metric("買賣家數差", f"{chip['main_diff']:+d} 家")
    if chip["main_diff"] > 0:
        st.success("買超家數較多，短線主力偏多。")
    elif chip["main_diff"] < 0:
        st.warning("賣超家數較多，短線主力偏空。")
    else:
        st.info("主力買賣家數大致平衡。")

with cc4:
    st.markdown("**融資融券氣象**")
    st.metric("融資餘額變動", f"{chip['margin_chg']:+.1f}%")
    if chip["margin_chg"] > 8:
        st.warning("融資暴增警示：人氣過熱，高檔恐有多殺多風險。")
    elif chip["margin_chg"] < -5:
        st.success("融資大幅減碼，浮額清洗相對乾淨。")
    else:
        st.info("融資餘額變動溫和，籌碼相對穩定。")

st.divider()

# ====================== 基本面與缺貨題材 ======================
st.subheader("基本面：景氣週期與缺貨漲價題材")

f1, f2 = st.columns(2)
with f1:
    st.markdown("**產業景氣週期位階**")
    render_pill(fund["cycle"], cycle_level(fund["cycle"]))
    st.caption(
        "谷底復甦 = 低基期轉折；暴衝 / 高峰期 = 獲利與股價風險同步放大；修正期 = 獲利下修進行式。"
    )
with f2:
    st.markdown("**產品缺貨與漲價效應**")
    st.write(fund["shortage"])

st.divider()

# ====================== 風險管理與護城河 Check List ======================
st.subheader("風險管理：Check List")

r1, r2, r3 = st.columns(3)
with r1:
    st.markdown("**庫存天數與去化速度**")
    if fund["inventory"] == "偏高":
        st.warning("庫存水位偏高，需留意下游去化放緩與毛利率壓力。")
    elif fund["inventory"] == "偏低":
        st.success("庫存水位偏低，供需相對健康。")
    else:
        st.success("庫存水位正常。")

with r2:
    st.markdown("**資本支出 / 折舊警訊**")
    if fund["capex"]:
        st.warning("資本支出與折舊負擔較重，新品量產前可能侵蝕獲利。")
    else:
        st.success("資本支出與折舊負擔相對可控。")

with r3:
    st.markdown("**大盤系統性風險連動度**")
    st.metric("Beta（大盤連動度）", f"{fund['beta']:.2f}")
    if fund["beta"] >= 1.2:
        st.warning("Beta 偏高，市場修正時個股跌幅恐放大，需留意停損紀律。")
    else:
        st.success("Beta 低於 1，相對抗跌，系統性風險影響較小。")

st.divider()

st.caption(
    "本 App 僅供教學與研究用途，所有數據（尤其 Mock Data）與診斷結果均不構成投資建議；"
    "投資有風險，決策前請自行審慎評估。"
)
