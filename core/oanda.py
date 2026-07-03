"""FX candles from OANDA's v20 REST API (practice host).

Why this venue matters for the methodology: majors trade at ~1bp spreads —
the cheapest cost structure available to us — and the candles endpoint returns
bid/ask alongside mid, so the cost model can be MEASURED from the data instead
of assumed. Needs OANDA_API_KEY (practice token). Max 5000 candles/request;
paginate backwards. FX is closed weekends, so bars/day averages ~5/7 of 24h.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

HOST = "https://api-fxpractice.oanda.com"
GRAN_HOURS = {"H1": 1, "H6": 6, "D": 24}


def get_fx_candles(instrument: str, granularity: str = "H6",
                   years: float = 4.0) -> tuple[pd.DataFrame, float]:
    """Return (mid OHLCV frame, mean round-trip spread in bps).

    Spread is measured per bar from bid/ask closes; the mean is the honest
    round-trip cost of crossing the spread once in and once out.
    """
    import requests

    token = os.getenv("OANDA_API_KEY", "")
    if not token:
        raise RuntimeError("OANDA_API_KEY not set")
    headers = {"Authorization": f"Bearer {token}",
               "Accept-Datetime-Format": "RFC3339"}
    url = f"{HOST}/v3/instruments/{instrument}/candles"

    hours = GRAN_HOURS[granularity]
    target = datetime.now(timezone.utc) - timedelta(days=int(years * 365))
    rows, spreads = [], []
    to = None            # OANDA 400s if `to` >= server now; omit on first page
    for _ in range(200):
        full = f"{url}?granularity={granularity}&price=MBA&count=5000"
        if to is not None:
            full += "&to=" + to.strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.get(full, headers=headers, timeout=30)
        r.raise_for_status()
        candles = r.json().get("candles", [])
        if not candles:
            break
        for c in candles:
            if not c.get("complete"):
                continue
            ts = pd.Timestamp(c["time"]).tz_convert("UTC")
            mid, bid, ask = c["mid"], c["bid"], c["ask"]
            m_c = float(mid["c"])
            rows.append((ts, float(mid["o"]), float(mid["h"]), float(mid["l"]),
                         m_c, float(c["volume"])))
            if m_c > 0:
                spreads.append((float(ask["c"]) - float(bid["c"])) / m_c * 10000)
        oldest = pd.Timestamp(candles[0]["time"]).tz_convert("UTC")
        to = (oldest - timedelta(hours=hours)).to_pydatetime()
        if to <= target:
            break
        time.sleep(0.1)
    if not rows:
        raise RuntimeError(f"OANDA returned no candles for {instrument}")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low",
                                     "close", "volume"]).set_index("timestamp")
    df = df[~df.index.duplicated()].sort_index()
    df = df[df.index >= pd.Timestamp(target)]
    spread_bps = float(pd.Series(spreads).mean()) if spreads else 2.0
    return df, spread_bps


class OandaBroker:
    """Market orders + position reads on the OANDA practice account.
    Client extension id carries the `el-<book>-` tag so fills attribute
    to their book, mirroring the Alpaca client_order_id convention."""

    def __init__(self):
        import requests

        self.rq = requests
        self.token = os.getenv("OANDA_API_KEY", "")
        self.account = os.getenv("OANDA_ACCOUNT_ID", "")
        if not (self.token and self.account):
            raise SystemExit("Set OANDA_API_KEY and OANDA_ACCOUNT_ID first.")
        self.h = {"Authorization": f"Bearer {self.token}",
                  "Content-Type": "application/json"}

    def market(self, book: str, instrument: str, units: int) -> tuple[int, float]:
        """Signed units (negative = short). Returns (filled_units, price);
        (0, 0.0) when not filled (market halted, weekend, rejection)."""
        import uuid
        body = {"order": {"type": "MARKET", "instrument": instrument,
                          "units": str(int(units)), "timeInForce": "FOK",
                          "positionFill": "DEFAULT",
                          "clientExtensions": {
                              "id": f"el-{book}-{uuid.uuid4().hex[:10]}",
                              "tag": f"el-{book}"}}}
        r = self.rq.post(f"{HOST}/v3/accounts/{self.account}/orders",
                         json=body, headers=self.h, timeout=20)
        if r.status_code not in (200, 201):
            print(f"[oanda] order rejected ({r.status_code}): {r.text[:200]}")
            return 0, 0.0
        fill = r.json().get("orderFillTransaction")
        if not fill:
            reason = r.json().get("orderCancelTransaction", {}).get("reason", "?")
            print(f"[oanda] {instrument} not filled ({reason})")
            return 0, 0.0
        return int(float(fill["units"])), float(fill["price"])
