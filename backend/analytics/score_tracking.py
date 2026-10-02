"""Forward observation starts after real recording time, never backdated."""

from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

from backend.services.research_repository import fingerprint

VERSION = 'forward-next-open-v1'


def completed_prices(frame, now):
    data = frame.copy()
    today = now.astimezone(ZoneInfo('Asia/Taipei'))
    cutoff = today.date()
    dates = pd.to_datetime(data['Date']).dt.date
    return data[dates < cutoff if today.hour * 60 + today.minute < 810 else dates <= cutoff].copy()


def validate_prices(frame, allowed_sources=('yfinance',), columns=('Open', 'Close')):
    if frame.attrs.get('price_source') not in allowed_sources:
        raise ValueError('只接受已驗證來源的真實行情。')
    data = frame.copy()
    data['Date'] = pd.to_datetime(data['Date']).dt.date
    if data.empty or data['Date'].isna().any() or data['Date'].duplicated().any() or not data['Date'].is_monotonic_increasing:
        raise ValueError('行情日期無效。')
    values = data[list(columns)].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('行情價格無效。')
    return data


def measure_snapshots(snapshots, frame):
    data = validate_prices(frame)
    rows = []
    for snapshot in snapshots:
        recorded = pd.Timestamp(snapshot['recorded_at'])
        if recorded.tzinfo is None:
            raise ValueError('評分記錄必須包含時區。')
        recording_day = recorded.tz_convert('Asia/Taipei').date()
        base_day = max(recording_day, snapshot['price_date'])
        future = data[data['Date'] > base_day].reset_index(drop=True)
        # Require input coverage spanning the recording date. A truncated price
        # window must never silently shift entry to a much later trading date.
        covered = data['Date'].iloc[0] <= base_day
        for horizon in (5, 20, 60):
            row = {'snapshot_id': snapshot['id'], 'horizon': horizon,
                   'score': snapshot['score'], 'score_max': snapshot['score_max'],
                   'model_version': snapshot['model_version'], 'recorded_at': snapshot['recorded_at'],
                   'calculation_version': VERSION, 'observed_days': min(len(future), horizon),
                   'status': 'pending' if covered else 'insufficient_history'}
            if covered and len(future) >= horizon:
                window = future.iloc[:horizon]
                entry = float(window['Open'].iloc[0])
                row.update(status='complete', entry_date=window['Date'].iloc[0],
                           end_date=window['Date'].iloc[-1],
                           return_pct=(float(window['Close'].iloc[-1]) / entry - 1) * 100,
                           lowest_close_return_pct=(float(window['Close'].min()) / entry - 1) * 100,
                           input_hash=fingerprint(window[['Date', 'Open', 'Close']].values.tolist()))
            rows.append(row)
    return rows


def relative_return(stock, benchmark, sessions=20):
    own, market = validate_prices(stock), validate_prices(benchmark, ('yfinance', 'TPEx'), ('Close',))
    if len(own) <= sessions:
        raise ValueError('股票行情不足以比較。')
    window = own.tail(sessions + 1)
    first, last = window['Date'].iloc[0], window['Date'].iloc[-1]
    market = market.set_index('Date')
    if first not in market.index or last not in market.index:
        raise ValueError(f'股票比較區間 {first} ～ {last}；指數已取得區間 '
                         f'{market.index.min()} ～ {market.index.max()}。日期尚未對齊，暫不計算，請待官方更新後重試。')
    own_return = (float(window['Close'].iloc[-1]) / float(window['Close'].iloc[0]) - 1) * 100
    market_return = (float(market.loc[last, 'Close']) / float(market.loc[first, 'Close']) - 1) * 100
    return {'start_date': first, 'end_date': last, 'stock_return_pct': own_return,
            'market_return_pct': market_return, 'excess_pp': own_return - market_return}
