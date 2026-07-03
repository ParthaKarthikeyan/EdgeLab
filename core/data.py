"""Free deep-history crypto OHLC from Coinbase Exchange's public candles endpoint.

No key needed, reachable from GitHub runners, years of history. Max 300 candles
per request, newest-first, so we paginate backwards. Granularity in seconds:
60, 300, 900, 3600, 21600, 86400.
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

GRAN = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


def _parse(rows) -> pd.DataFrame:
    """Coinbase rows are [time, low, high, open, close, volume] (unix seconds)."""
    out = [(pd.Timestamp(int(c[0]), unit="s", tz="UTC"),
            float(c[3]), float(c[2]), float(c[1]), float(c[4]), float(c[5]))
           for c in rows]
    if not out:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(out, columns=["timestamp", "open", "high", "low", "close",
                                    "volume"]).set_index("timestamp")
    return df[~df.index.duplicated()].sort_index()


def get_candles(product: str, interval: str = "6h", years: float = 4.0) -> pd.DataFrame:
    import requests

    gran = GRAN[interval]
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    headers = {"User-Agent": "edgelab-research"}
    end = datetime.now(timezone.utc)
    target = end - timedelta(days=int(years * 365))
    frames, cur_end = [], end
    for _ in range(3000):                       # safety cap on pages
        cur_start = max(cur_end - timedelta(seconds=gran * 300), target)
        params = {"granularity": gran, "start": cur_start.isoformat(),
                  "end": cur_end.isoformat()}
        for attempt in range(3):                # tolerate transient 429/5xx
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                break
            time.sleep(0.5 * (attempt + 1))
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        frames.append(_parse(rows))
        oldest = min(int(c[0]) for c in rows)
        cur_end = datetime.fromtimestamp(oldest, tz=timezone.utc) - timedelta(seconds=gran)
        if cur_end <= target:
            break
        time.sleep(0.15)                        # be gentle on the public endpoint
    if not frames:
        raise RuntimeError(f"Coinbase returned no candles for {product} ({interval})")
    df = pd.concat(frames)
    return df[~df.index.duplicated()].sort_index()


def completed_bars(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Drop the still-forming candle: a bar is complete once now >= open + width."""
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=GRAN[interval])
    return df[df.index <= cutoff]
