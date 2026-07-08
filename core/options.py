"""QQQ 1-DTE iron condor: Alpaca options data + a conservative-fill engine.

The strategy under test (the "$300 options challenge" videos, made mechanical):
at ~14:30 ET, if VIX < 20, sell a QQQ iron condor expiring the NEXT trading
day — short put ~2% below spot, short call ~1.75% above, defined-width wings.
Exit at a fraction of the collected credit, or let it settle at expiry. The
long wings cap the loss structurally; there is no other stop.

Data honesty, on the record:
- Alpaca historical option data starts 2024-02 and has BARS only (no quotes),
  so every mark here is a last-trade price. Entry marks use the most recent
  trade at or before the decision bar; a stale OTM wing print is possible.
  The slippage haircut (stressed 2x at Gate A) is the budget for that.
- Settlement uses the official underlying close (QQQ options are PM-settled);
  exit fees are charged at expiry as if the position were bought back, which
  overstates costs slightly — conservative by construction.
"""

from __future__ import annotations

import os
import pickle
import time

import pandas as pd
import requests

DATA_URL = "https://data.alpaca.markets"
TRADE_URL = "https://paper-api.alpaca.markets"
ET = "America/New_York"
CACHE_DIR = os.path.join("data_cache", "condor")

DECISION = "14:30"       # the 5-minute bar we decide on; fills at its close
MARK_FROM = "13:00"      # entry marks may be up to 90 min stale (trades only)
MIN_CREDIT = 0.05        # $/share after entry slippage; below this, no edge
VIX_MAX = 20.0

# The research grid (offsets are the neighbor axis, video cell in the middle)
OFFSETS = [(1.5, 1.25), (2.0, 1.75), (2.5, 2.25)]   # % (put below, call above)
WIDTHS = [1, 2, 5]                                   # $ wings
PROFIT_TAKES = [0.3, 0.5, None]                      # None = hold to expiry


# --- Alpaca REST (raw requests; same env keys as the paper runner) -----------
def _headers() -> dict:
    k = os.getenv("ALPACA_API_KEY", "")
    s = os.getenv("ALPACA_SECRET_KEY", "")
    if not (k and s):
        raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY first.")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


def _get(url: str, params: dict) -> dict:
    for attempt in range(6):
        r = requests.get(url, params=params, headers=_headers(), timeout=30)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate-limited beyond retries: {url}")


def sessions(start: str, end: str) -> list[str]:
    """Trading session dates (YYYY-MM-DD) from the Alpaca calendar."""
    out = _get(f"{TRADE_URL}/v2/calendar", {"start": start, "end": end})
    return [row["date"] for row in out]


def get_stock_bars(symbol: str, start: str, end: str,
                   timeframe: str = "5Min") -> pd.DataFrame:
    """IEX bars, ET-indexed (SIP is subscription-gated on this account).
    IEX covers a few % of consolidated volume, but for QQQ that is still
    constant prints — good enough to round a strike to the $1 grid."""
    rows, token = [], None
    while True:
        params = {"symbols": symbol, "timeframe": timeframe,
                  "start": start, "end": end, "feed": "iex",
                  "adjustment": "raw", "limit": 10000}
        if token:
            params["page_token"] = token
        out = _get(f"{DATA_URL}/v2/stocks/bars", params)
        rows += out.get("bars", {}).get(symbol, [])
        token = out.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"]).dt.tz_convert(ET)
    return df.set_index("t").sort_index()


def get_option_bars(symbols: list[str], start_iso: str,
                    end_iso: str) -> dict[str, pd.DataFrame]:
    """5-minute bars for many contracts at once, ET-indexed per symbol."""
    merged: dict[str, list] = {}
    token = None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "5Min",
                  "start": start_iso, "end": end_iso, "limit": 10000}
        if token:
            params["page_token"] = token
        out = _get(f"{DATA_URL}/v1beta1/options/bars", params)
        for sym, bars in (out.get("bars") or {}).items():
            merged.setdefault(sym, []).extend(bars)
        token = out.get("next_page_token")
        if not token:
            break
    frames = {}
    for sym, bars in merged.items():
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"]).dt.tz_convert(ET)
        frames[sym] = df.set_index("t").sort_index()
    return frames


# --- contract math -------------------------------------------------------------
def occ(root: str, expiry: str, cp: str, strike: float) -> str:
    """OCC symbol, e.g. QQQ260709P00489000 (expiry as YYYY-MM-DD)."""
    ymd = expiry[2:4] + expiry[5:7] + expiry[8:10]
    return f"{root}{ymd}{cp}{int(round(strike * 1000)):08d}"


def cell_strikes(spot: float, put_off: float, call_off: float,
                 width: float) -> tuple[float, float, float, float]:
    """(short put, long put, short call, long call) on the $1 QQQ grid."""
    sp = float(round(spot * (1 - put_off / 100)))
    sc = float(round(spot * (1 + call_off / 100)))
    return sp, sp - width, sc, sc + width


def all_candidate_symbols(spot: float, expiry: str) -> list[str]:
    """Every contract any grid cell could need for one session."""
    syms = set()
    for po, co in OFFSETS:
        for w in WIDTHS:
            sp, lp, sc, lc = cell_strikes(spot, po, co, w)
            syms |= {occ("QQQ", expiry, "P", sp), occ("QQQ", expiry, "P", lp),
                     occ("QQQ", expiry, "C", sc), occ("QQQ", expiry, "C", lc)}
    return sorted(syms)


# --- one day's raw material -----------------------------------------------------
def build_day(date: str, expiry: str, spot: float, vix_prev: float | None,
              expiry_close: float, leg_bars: dict[str, pd.DataFrame]) -> dict:
    """Digest bar frames into the compact record the engine consumes.

    entry_mark: last trade-close in [MARK_FROM, DECISION] on `date`.
    path:       (iso_ts, close) after the decision bar on `date`, plus the
                whole expiry day — the marks profit-taking walks through.
    """
    legs = {}
    dec = pd.Timestamp(f"{date} {DECISION}", tz=ET)
    frm = pd.Timestamp(f"{date} {MARK_FROM}", tz=ET)
    for sym, df in leg_bars.items():
        if df.empty:
            continue
        pre = df[(df.index >= frm) & (df.index <= dec)]
        entry_mark = float(pre["c"].iloc[-1]) if len(pre) else None
        post = df[df.index > dec]
        path = [(ts.isoformat(), float(c)) for ts, c in post["c"].items()]
        legs[sym] = {"entry_mark": entry_mark, "path": path}
    return {"date": date, "expiry": expiry, "spot": spot,
            "vix_prev": vix_prev, "expiry_close": expiry_close, "legs": legs}


def cache_path(date: str) -> str:
    return os.path.join(CACHE_DIR, f"{date}.pkl")


def cache_load(date: str) -> dict | None:
    p = cache_path(date)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return pickle.load(f)


def cache_save(date: str, day: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(date), "wb") as f:
        pickle.dump(day, f)


# --- the engine -----------------------------------------------------------------
def run_condor(days: list[dict], *, put_off: float, call_off: float,
               width: float, profit_take: float | None, vix_gate: bool,
               slip: float = 0.01, fee: float = 0.05,
               risk_pct: float = 0.05, start_cash: float = 10000.0) -> dict:
    """Backtest one grid cell over prebuilt day records.

    slip — $/share haircut per leg-fill (worse entry credit, worse exit debit);
    fee  — $ per contract per leg-fill (regulatory; Alpaca options have no
           commission). Both are doubled by Gate A's cost stress.
    Sizing is the paper book's rule: contracts = floor(5% of equity / max
    defined loss); the wings are the only stop.
    """
    equity, trades, curve = start_cash, [], [start_cash]
    skips: dict[str, int] = {}

    def skip(reason: str):
        skips[reason] = skips.get(reason, 0) + 1
        curve.append(equity)

    for day in days:
        if day.get("spot") is None or day.get("expiry_close") is None:
            skip("no_underlying_data")
            continue
        if vix_gate and day.get("vix_prev") is not None \
                and day["vix_prev"] >= VIX_MAX:
            skip("vix_gate")
            continue
        sp, lp, sc, lc = cell_strikes(day["spot"], put_off, call_off, width)
        legs = []                               # (sign, strike, leg record)
        for cp, k, sgn in (("P", sp, +1), ("P", lp, -1),
                           ("C", sc, +1), ("C", lc, -1)):
            rec = day["legs"].get(occ("QQQ", day["expiry"], cp, k))
            if rec is None or rec.get("entry_mark") is None:
                legs = []
                break
            legs.append((sgn, k, rec))
        if not legs:
            skip("missing_leg_data")
            continue

        credit_mid = sum(sgn * rec["entry_mark"] for sgn, _, rec in legs)
        credit = credit_mid - 4 * slip          # unfavorable side of the bar
        if credit < MIN_CREDIT:
            skip("thin_credit")
            continue
        max_loss_pc = (width - credit) * 100
        if max_loss_pc <= 0:
            skip("degenerate_structure")
            continue
        contracts = int(equity * risk_pct // max_loss_pc)
        if contracts < 1:
            skip("risk_budget_too_small")
            continue

        pnl_pc, reason = None, "expiry"
        if profit_take is not None:
            target = credit_mid * (1 - profit_take)
            maps = [(sgn, {ts: px for ts, px in rec["path"]})
                    for sgn, _, rec in legs]
            last = [rec["entry_mark"] for _, _, rec in legs]
            ts_all = sorted({ts for _, m in maps for ts in m})
            for ts in ts_all:
                for i, (sgn, m) in enumerate(maps):
                    if ts in m:
                        last[i] = m[ts]
                mark = sum(sgn * last[i] for i, (sgn, _) in enumerate(maps))
                if mark <= target:
                    pnl_pc = (credit - (mark + 4 * slip)) * 100 - 8 * fee
                    reason = "profit_take"
                    break
        if pnl_pc is None:                      # settle on the official close
            s = day["expiry_close"]
            settle = (max(0.0, sp - s) - max(0.0, lp - s)
                      + max(0.0, s - sc) - max(0.0, s - lc))
            pnl_pc = (credit - settle) * 100 - 8 * fee

        pnl = round(pnl_pc * contracts, 2)
        equity += pnl
        trades.append({"date": day["date"], "pnl": pnl, "reason": reason,
                       "contracts": contracts, "credit": round(credit_mid, 2)})
        curve.append(equity)

    return {"trades": trades, "curve": curve, "end": equity, "skips": skips}
