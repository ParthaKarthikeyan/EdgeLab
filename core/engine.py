"""Backtest engines with a percentage cost model (fees + spread as round-trip
basis points of notional, half charged per leg). Everything causal: indicators
shifted where needed, no same-bar peeking.

Two families:
- run_trend: Donchian breakout + EMA regime + ATR chandelier (long/flat by
  default — spot venues can't short).
- run_fade:  Bollinger mean-reversion, trend-aligned, with a max-hold bail.

Both return {trades, curve, end} for core.metrics / core.gates.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def metrics(res: dict, start_cash: float) -> dict:
    trades, curve = res["trades"], res["curve"]
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    peak, mdd = curve[0], 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak if peak else 0.0)
    return {"end": res["end"], "ret": (res["end"] / start_cash - 1) * 100,
            "trades": len(trades),
            "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
            "profit_factor": (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0),
            "max_dd": mdd * 100}


def _size(equity, risk_pct, dist, entry, max_leverage=1.0):
    if dist <= 0 or entry <= 0:
        return 0.0
    return min(equity * risk_pct / dist, equity * max_leverage / entry)


def run_trend(df: pd.DataFrame, *, start_cash: float = 10000.0,
              cost_bps: float = 20.0, risk_pct: float = 0.01,
              entry_lookback: int = 20, exit_lookback: int = 10,
              atr_period: int = 14, atr_stop_mult: float = 3.0,
              trend_fast: int = 50, trend_slow: int = 200,
              allow_short: bool = False, max_leverage: float = 1.0) -> dict:
    if len(df) < trend_slow + entry_lookback + 5:
        return {"trades": [], "curve": [start_cash], "end": start_cash}
    d = df.copy()
    d["fast"] = ema(d["close"], trend_fast)
    d["slow"] = ema(d["close"], trend_slow)
    d["atr"] = atr(d, atr_period)
    d["don_hi"] = d["high"].rolling(entry_lookback).max().shift(1)
    d["don_lo"] = d["low"].rolling(entry_lookback).min().shift(1)
    d["exit_hi"] = d["high"].rolling(exit_lookback).max().shift(1)
    d["exit_lo"] = d["low"].rolling(exit_lookback).min().shift(1)
    side_frac = cost_bps / 2.0 / 10000.0

    equity, pos = start_cash, None
    trades, curve = [], [equity]

    def close(px, reason, ts):
        nonlocal equity, pos
        gross = pos["side"] * pos["units"] * (px - pos["entry"])
        cost = side_frac * pos["units"] * (pos["entry"] + px)
        pnl = gross - cost
        trades.append({"side": pos["side"], "entry_time": pos["entry_time"],
                       "exit_time": ts, "entry": pos["entry"], "exit": px,
                       "units": pos["units"], "pnl": pnl, "reason": reason})
        equity += pnl
        curve.append(equity)
        pos = None

    for ts, r in d.iterrows():
        if np.isnan(r["slow"]) or np.isnan(r["atr"]) or np.isnan(r["don_hi"]):
            continue
        long_reg, short_reg = r["fast"] > r["slow"], r["fast"] < r["slow"]
        if pos is not None:
            s = pos["side"]
            if s == 1:
                pos["peak"] = max(pos["peak"], r["high"])
                pos["stop"] = max(pos["stop"], pos["peak"] - atr_stop_mult * r["atr"])
            else:
                pos["peak"] = min(pos["peak"], r["low"])
                pos["stop"] = min(pos["stop"], pos["peak"] + atr_stop_mult * r["atr"])
            stop_hit = (s == 1 and r["low"] <= pos["stop"]) or \
                       (s == -1 and r["high"] >= pos["stop"])
            don_exit = (s == 1 and r["close"] < r["exit_lo"]) or \
                       (s == -1 and r["close"] > r["exit_hi"])
            if stop_hit:
                close(pos["stop"], "stop", ts)
            elif don_exit:
                close(r["close"], "exit_break", ts)
            elif (s == 1 and short_reg) or (s == -1 and long_reg):
                close(r["close"], "regime_flip", ts)
        if pos is None:
            long_sig = long_reg and r["close"] > r["don_hi"]
            short_sig = allow_short and short_reg and r["close"] < r["don_lo"]
            side = 1 if long_sig else (-1 if short_sig else 0)
            if side:
                entry = r["close"]
                dist = atr_stop_mult * r["atr"]
                units = _size(equity, risk_pct, dist, entry, max_leverage)
                if units > 0:
                    pos = {"side": side, "entry": entry, "stop": entry - side * dist,
                           "peak": entry, "units": units, "entry_time": ts}
    if pos is not None:
        close(float(d["close"].iloc[-1]), "eod", d.index[-1])
    return {"trades": trades, "curve": curve, "end": equity}


def run_fade(df: pd.DataFrame, *, start_cash: float = 10000.0,
             cost_bps: float = 20.0, risk_pct: float = 0.01,
             sma_period: int = 20, band_k: float = 2.0, stop_k: float = 3.5,
             trend_ema: int = 200, max_hold: int = 24,
             allow_short: bool = False, max_leverage: float = 1.0) -> dict:
    """Fade Bollinger extremes back to the mean, only WITH the long-EMA trend
    (long fades in an uptrend; short fades need allow_short)."""
    if len(df) < max(sma_period, trend_ema) + 5:
        return {"trades": [], "curve": [start_cash], "end": start_cash}
    d = df.copy()
    d["sma"] = d["close"].rolling(sma_period).mean()
    d["std"] = d["close"].rolling(sma_period).std()
    d["ema"] = ema(d["close"], trend_ema)
    d["upper"] = d["sma"] + band_k * d["std"]
    d["lower"] = d["sma"] - band_k * d["std"]
    side_frac = cost_bps / 2.0 / 10000.0

    equity, pos = start_cash, None
    trades, curve = [], [equity]

    def close(px, reason, ts):
        nonlocal equity, pos
        gross = pos["side"] * pos["units"] * (px - pos["entry"])
        cost = side_frac * pos["units"] * (pos["entry"] + px)
        pnl = gross - cost
        trades.append({"side": pos["side"], "entry_time": pos["entry_time"],
                       "exit_time": ts, "entry": pos["entry"], "exit": px,
                       "units": pos["units"], "pnl": pnl, "reason": reason})
        equity += pnl
        curve.append(equity)
        pos = None

    for ts, r in d.iterrows():
        if np.isnan(r["std"]) or r["std"] <= 0 or np.isnan(r["ema"]):
            continue
        if pos is not None:
            s = pos["side"]
            pos["bars"] += 1
            stop_hit = (s == 1 and r["low"] <= pos["stop"]) or \
                       (s == -1 and r["high"] >= pos["stop"])
            tp_hit = (s == 1 and r["high"] >= r["sma"]) or \
                     (s == -1 and r["low"] <= r["sma"])
            if stop_hit:
                close(pos["stop"], "stop", ts)
            elif tp_hit:
                close(r["sma"], "mean", ts)
            elif pos["bars"] >= max_hold:
                close(r["close"], "time", ts)
        if pos is None:
            long_sig = r["close"] < r["lower"] and r["close"] > r["ema"]
            short_sig = allow_short and r["close"] > r["upper"] and r["close"] < r["ema"]
            side = 1 if long_sig else (-1 if short_sig else 0)
            if side:
                entry = r["close"]
                dist = stop_k * r["std"]
                units = _size(equity, risk_pct, dist, entry, max_leverage)
                if units > 0:
                    pos = {"side": side, "entry": entry, "stop": entry - side * dist,
                           "units": units, "bars": 0, "entry_time": ts}
    if pos is not None:
        close(float(d["close"].iloc[-1]), "eod", d.index[-1])
    return {"trades": trades, "curve": curve, "end": equity}


def walk_forward(df: pd.DataFrame, fn, params: dict, cost_bps: float,
                 n_folds: int = 5, start_cash: float = 10000.0) -> list:
    """Sequential non-overlapping folds, frozen params — per-fold metrics."""
    n, seg, folds = len(df), len(df) // n_folds, []
    for i in range(n_folds):
        a, b = i * seg, ((i + 1) * seg if i < n_folds - 1 else n)
        chunk = df.iloc[a:b]
        if len(chunk) < 50:
            continue
        folds.append(metrics(fn(chunk, start_cash=start_cash,
                                cost_bps=cost_bps, **params), start_cash))
    return folds
