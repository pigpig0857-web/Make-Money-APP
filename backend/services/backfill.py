"""Small resumable steps; committed real data survives reruns and failed sources."""

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.analytics.score_tracking import completed_prices, measure_snapshots
from backend.services.stock_fetcher import _download_daily, StockDataUnavailableError
from backend.services.stock_repository import save_daily_prices, load_daily_prices
from backend.services.analysis_repository import load_institutional_flows, save_institutional_flows
from backend.services.institutional_fetcher import fetch_daily_report
from backend.services.research_repository import load_tracking_snapshots, save_outcomes


def new_job(tickers, days=60, period='2y'):
    if days not in (20, 60, 120) or period not in ('1y', '2y', '5y'):
        raise ValueError('Unsupported backfill range.')
    return {'tickers': list(dict.fromkeys(tickers)), 'index': 0, 'stage': 'prices',
            'days': days, 'period': period, 'results': [], 'current': None,
            'report_requests': 0, 'max_report_requests': 60, 'done': not tickers}


def run_step(job, now=None):
    """One price download, one official day, or one outcome update per call.

    UI stores this dictionary before executing. State advances only after its
    operation returns; re-executing an interrupted step uses database upserts.
    """
    if job['done']:
        return
    now = now or datetime.now(ZoneInfo('Asia/Taipei'))
    ticker = job['tickers'][job['index']]
    if job['stage'] == 'prices':
        phase = '行情下載'
        try:
            frame, _ = _download_daily(ticker, job['period'], force_refresh=True)
            if frame.attrs.get('is_stale'):
                raise StockDataUnavailableError('來源失敗，僅有離線行情；本次未宣稱日 K 已更新。')
            phase = '完成日 K 驗證'
            frame = completed_prices(frame, now)
            phase = '日 K 資料庫寫入'
            count = save_daily_prices(ticker, job['period'], frame, source='yfinance')
            dates = [d.date() for d in frame['Date'].tail(job['days'])]
            current = {'ticker': ticker, 'price_rows': count, 'first_date': frame['Date'].iloc[0].date(),
                       'last_date': frame['Date'].iloc[-1].date(), 'dates': dates,
                       'attempted': [], 'notes': [], 'outcomes': 0}
            job['current'] = current
            job['stage'] = 'institutions'
        except Exception as exc:
            # Show stage/type without leaking raw connection strings or args.
            if isinstance(exc, TypeError) and 'force_refresh' in str(exc) and 'unexpected keyword' in str(exc):
                reason = '下載函式仍是舊版本；請停止並重新啟動 Streamlit，再按補齊。'
            elif isinstance(exc, StockDataUnavailableError):
                reason = '真實行情來源不可用或僅剩離線資料，請稍後重試。'
            elif phase == '日 K 資料庫寫入':
                reason = '請檢查資料庫連線並初始化專案資料表，再按補齊。'
            elif phase == '完成日 K 驗證':
                reason = '行情格式或日期無法驗證，本次未繼續補齊。'
            else:
                reason = '下載失敗；請重新啟動 Streamlit 後重試，若持續失敗請回報此錯誤代碼。'
            job['results'].append({'ticker': ticker, 'status': 'failed',
                                   'error_stage': phase, 'error_type': type(exc).__name__,
                                   'notes': [reason]})
            _next_stock(job)
        return
    current = job['current']
    if job['stage'] == 'institutions':
        try:
            stored = load_institutional_flows(ticker, current['dates'])
            known = {r['trade_date'] for r in stored}
            missing = [d for d in current['dates'] if d not in known and d not in current['attempted']]
            if missing and job['report_requests'] < job['max_report_requests']:
                day = missing[0]
                market = 'TPEx' if ticker.endswith('.TWO') else 'TWSE'
                # Count before network access to keep reruns within this batch's
                # bounded request budget. Failed steps remain missing in DB.
                job['report_requests'] += 1
                record = fetch_daily_report(market, day).get(ticker.split('.')[0])
                if record:
                    save_institutional_flows(ticker, [record])
                current['attempted'].append(day)
                return
            if missing:
                current['notes'].append('已達每批 60 份法人日報上限；再次按補齊可繼續。')
            job['stage'] = 'outcomes'
        except Exception:
            current['notes'].append('法人來源或儲存失敗；已完成資料保留，缺漏可下次重試。')
            job['stage'] = 'outcomes'
        return
    if job['stage'] == 'outcomes':
        try:
            saved = load_daily_prices(ticker, job['period'], max_age_seconds=None)
            if saved is None:
                raise ValueError('Price history missing')
            frame = completed_prices(saved[0], now)
            snapshots = load_tracking_snapshots(ticker, limit=10000)
            measurements = measure_snapshots(snapshots, frame)
            save_outcomes(measurements)
            current['outcomes'] = sum(r['status'] == 'complete' for r in measurements)
            if any(r['status'] == 'insufficient_history' for r in measurements):
                current['notes'].append('部分評分超出下載範圍；改選較長行情範圍再補齊。')
        except Exception:
            current['notes'].append('評分績效讀取或儲存失敗，可再次重試。')
        try:
            known = {r['trade_date'] for r in load_institutional_flows(ticker, current['dates'])}
            current['missing_dates'] = [d for d in current['dates'] if d not in known]
            current['institution_rows'] = len(current['dates']) - len(current['missing_dates'])
            current['status'] = 'partial' if current['missing_dates'] or current['notes'] else 'complete'
        except Exception:
            current['status'] = 'partial'
            current['missing_dates'] = current['dates']
            current['institution_rows'] = 0
            current['notes'].append('法人完整性無法確認。')
        job['results'].append(dict(current))
        _next_stock(job)


def _next_stock(job):
    job['index'] += 1
    job['stage'] = 'prices'
    job['current'] = None
    job['done'] = job['index'] >= len(job['tickers'])
