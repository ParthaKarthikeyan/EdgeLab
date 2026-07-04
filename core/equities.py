"""US-equity momentum: data (Yahoo daily closes) + the rotation engine.

Data is research-grade daily bars via yfinance, no key needed (Stooq's CSV
endpoint now sits behind a JavaScript proof-of-work wall). Auto-adjusted
closes (splits + dividends).

The engine is the systematic core of the "video 1" book: every R trading
days, rank the universe by L-day return, hold the top K equal-weight —
per-name absolute momentum required (return > 0, price above its 100d SMA)
and the whole book sits in cash while SPY is below its 200d SMA.
"""

import pandas as pd

SMA_FILTER = 100     # per-name trend filter (days)
REGIME_SMA = 200     # SPY regime gate (days)


def get_close_panel(symbols: list[str], years: float = 10.0) -> pd.DataFrame:
    """Daily adjusted-close panel (index=date, cols=symbols)."""
    import yfinance as yf

    raw = yf.download(symbols, period=f"{int(years)}y", interval="1d",
                      auto_adjust=True, progress=False, group_by="column",
                      threads=True)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else \
        raw[["Close"]].rename(columns={"Close": symbols[0]})
    closes = closes.dropna(axis=1, how="all").sort_index()
    got, missing = set(closes.columns), [s for s in symbols
                                         if s not in set(closes.columns)]
    for s in sorted(got):
        print(f"[data] {s}: {closes[s].notna().sum()} days")
    for s in missing:
        print(f"[data] {s}: FAILED (no data from Yahoo)")
    if closes.empty:
        raise RuntimeError("Yahoo returned nothing for the whole universe")
    return closes


def momentum_targets(closes: pd.DataFrame, spy: pd.Series, *, lookback: int,
                     top_k: int) -> dict[str, float]:
    """Target weights as of the LAST row of the panel (the live decision)."""
    if len(closes) < max(lookback, SMA_FILTER, REGIME_SMA) + 1:
        return {}
    if not float(spy.iloc[-1]) > float(spy.rolling(REGIME_SMA).mean().iloc[-1]):
        return {}                                     # bear regime -> all cash
    row_px = closes.iloc[-1]
    row_sma = closes.rolling(SMA_FILTER).mean().iloc[-1]
    row_mom = closes.pct_change(lookback, fill_method=None).iloc[-1]
    ranked = sorted(
        (s for s in closes.columns
         if pd.notna(row_mom[s]) and pd.notna(row_sma[s])
         and row_mom[s] > 0 and row_px[s] > row_sma[s]),
        key=lambda s: row_mom[s], reverse=True)[:top_k]
    return {s: 1.0 / top_k for s in ranked}


def run_momentum(closes: pd.DataFrame, spy: pd.Series, *, lookback: int,
                 top_k: int, rebal_days: int, cost_bps: float,
                 start_cash: float = 10000.0) -> dict:
    """Backtest of the same rules; costs charged on turnover."""
    sma = closes.rolling(SMA_FILTER).mean()
    mom = closes.pct_change(lookback, fill_method=None)
    rets = closes.pct_change(fill_method=None)
    regime_ok = spy > spy.rolling(REGIME_SMA).mean()

    equity = start_cash
    weights = {s: 0.0 for s in closes.columns}
    episodes: dict[str, float] = {}
    trades, curve = [], [equity]
    side = cost_bps / 2.0 / 10000.0

    start = max(lookback, SMA_FILTER, REGIME_SMA) + 1
    for i in range(start, len(closes)):
        day = closes.index[i]
        day_pnl = 0.0
        for s, w in weights.items():
            r = rets.iloc[i].get(s)
            if w > 0 and pd.notna(r):
                pnl = equity * w * r
                day_pnl += pnl
                episodes[s] = episodes.get(s, 0.0) + pnl
        equity += day_pnl
        curve.append(equity)

        if (i - start) % rebal_days:
            continue
        if bool(regime_ok.iloc[i]):
            row_mom, row_px, row_sma = mom.iloc[i], closes.iloc[i], sma.iloc[i]
            ranked = sorted(
                (s for s in closes.columns
                 if pd.notna(row_mom[s]) and pd.notna(row_sma[s])
                 and row_mom[s] > 0 and row_px[s] > row_sma[s]),
                key=lambda s: row_mom[s], reverse=True)[:top_k]
        else:
            ranked = []
        target = {s: (1.0 / top_k if s in ranked else 0.0)
                  for s in closes.columns}
        turnover = sum(abs(target[s] - weights[s]) for s in closes.columns)
        if turnover > 1e-9:
            equity -= equity * turnover * side * 2
            for s in closes.columns:
                if weights[s] > 0 and target[s] == 0.0:
                    trades.append({"symbol": s, "exit": str(day.date()),
                                   "pnl": episodes.pop(s, 0.0)})
                elif weights[s] == 0.0 and target[s] > 0:
                    episodes.setdefault(s, 0.0)
            weights = target
    for s, w in weights.items():
        if w > 0:
            trades.append({"symbol": s, "exit": "open",
                           "pnl": episodes.get(s, 0.0)})
    return {"trades": trades, "curve": curve, "end": equity}
