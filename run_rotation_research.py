#!/usr/bin/env python3
"""Cross-sectional momentum rotation over the alt universe — Gate A research.

The edge being tested: not "is coin X trending" but "which coins are strongest
RELATIVE to each other". Every R days, rank the universe by L-day return, hold
the top K equal-weight — but only coins with positive momentum trading above
their 100d SMA (absolute-momentum filter); anything unqualified stays in cash.

Costs: 20bps round-trip charged on turnover, judged at 2x per Gate A.
Walk-forward: 5 sequential date folds, same as every other candidate.
Guard against grid luck: neighbor along the lookback axis must also pass.

    python run_rotation_research.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from itertools import product

import pandas as pd

from core.books import BANKROLL
from core.data import get_candles
from core.gates import COST_STRESS, WF_FOLDS, gate_a, profitable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UNIVERSE = {"BTC-USD": "BTC/USD", "ETH-USD": "ETH/USD", "SOL-USD": "SOL/USD",
            "XRP-USD": "XRP/USD", "DOGE-USD": "DOGE/USD", "LTC-USD": "LTC/USD",
            "LINK-USD": "LINK/USD", "AVAX-USD": "AVAX/USD", "DOT-USD": "DOT/USD",
            "BCH-USD": "BCH/USD", "UNI-USD": "UNI/USD"}
YEARS = 4.0
COST_BPS = 20.0
SMA_FILTER = 100

LOOKBACKS = [14, 30, 60, 90]     # neighbor axis
TOP_KS = [2, 3]
REBAL_DAYS = [2, 7]


def run_rotation(closes: pd.DataFrame, *, lookback: int, top_k: int,
                 rebal_days: int, cost_bps: float,
                 start_cash: float = BANKROLL) -> dict:
    """closes: daily panel (index=date, cols=symbols, NaN before listing)."""
    sma = closes.rolling(SMA_FILTER).mean()
    mom = closes.pct_change(lookback, fill_method=None)
    rets = closes.pct_change(fill_method=None)

    equity = start_cash
    weights = {s: 0.0 for s in closes.columns}
    episodes: dict[str, float] = {}          # symbol -> accumulated episode pnl
    trades, curve = [], [equity]
    side = cost_bps / 2.0 / 10000.0

    start = max(lookback, SMA_FILTER) + 1
    for i in range(start, len(closes)):
        day = closes.index[i]
        # mark to market with yesterday's weights
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
        row_mom, row_px, row_sma = mom.iloc[i], closes.iloc[i], sma.iloc[i]
        ranked = sorted(
            (s for s in closes.columns
             if pd.notna(row_mom[s]) and pd.notna(row_sma[s])
             and row_mom[s] > 0 and row_px[s] > row_sma[s]),
            key=lambda s: row_mom[s], reverse=True)[:top_k]
        target = {s: (1.0 / top_k if s in ranked else 0.0)
                  for s in closes.columns}
        turnover = sum(abs(target[s] - weights[s]) for s in closes.columns)
        if turnover > 1e-9:
            equity -= equity * turnover * side * 2   # each weight change crosses once
            for s in closes.columns:
                if weights[s] > 0 and target[s] == 0.0:   # episode closes
                    trades.append({"symbol": s, "exit": str(day.date()),
                                   "pnl": episodes.pop(s, 0.0)})
                elif weights[s] == 0.0 and target[s] > 0:
                    episodes.setdefault(s, 0.0)
            weights = target
    for s, w in weights.items():                      # close open episodes
        if w > 0:
            trades.append({"symbol": s, "exit": "open", "pnl": episodes.get(s, 0.0)})
    return {"trades": trades, "curve": curve, "end": equity}


def rot_metrics(res: dict) -> dict:
    from core.engine import metrics
    return metrics(res, BANKROLL)


def folds_for(closes: pd.DataFrame, params: dict, cost: float) -> list:
    n, seg, out = len(closes), len(closes) // WF_FOLDS, []
    for i in range(WF_FOLDS):
        a, b = i * seg, ((i + 1) * seg if i < WF_FOLDS - 1 else n)
        chunk = closes.iloc[a:b]
        if len(chunk) < SMA_FILTER + 30:
            continue
        out.append(rot_metrics(run_rotation(chunk, cost_bps=cost, **params)))
    return out


def main():
    frames = {}
    for pid, disp in UNIVERSE.items():
        print(f"[data] {pid} 1d ({YEARS:g}y)...")
        try:
            frames[disp] = get_candles(pid, "1d", YEARS)["close"]
        except Exception as e:                        # noqa: BLE001
            print(f"       failed: {e}")
    closes = pd.DataFrame(frames).sort_index()
    print(f"[panel] {closes.shape[1]} coins x {len(closes)} days")

    cells = []
    for lb, k, rd in product(LOOKBACKS, TOP_KS, REBAL_DAYS):
        params = dict(lookback=lb, top_k=k, rebal_days=rd)
        stressed = rot_metrics(run_rotation(closes, cost_bps=COST_BPS * COST_STRESS,
                                            **params))
        base = rot_metrics(run_rotation(closes, cost_bps=COST_BPS, **params))
        folds = folds_for(closes, params, COST_BPS * COST_STRESS)
        g = gate_a(stressed, folds)
        cells.append({
            "params": params,
            "passed": bool(g.passed),
            "last_fold_ok": bool(folds) and bool(profitable(folds[-1])),
            "base_ret": round(float(base["ret"]), 1),
            "ret": round(float(stressed["ret"]), 1),
            "pf": None if stressed["profit_factor"] == float("inf")
                  else round(float(stressed["profit_factor"]), 2),
            "max_dd": round(float(stressed["max_dd"]), 1),
            "trades": int(stressed["trades"]),
            "folds_won": int(sum(1 for f in folds if profitable(f)))})
        c = cells[-1]
        print(f"  L{lb:3d} top{k} r{rd}: base {c['base_ret']:+7.1f}% · "
              f"stressed {c['ret']:+7.1f}% (PF {c['pf']}, DD {c['max_dd']}%) · "
              f"folds {c['folds_won']}/{WF_FOLDS} · "
              f"{'PASS' if c['passed'] else 'fail'}")

    # neighbor guard along the lookback axis (same top_k and rebal_days)
    def neighbor_pass(cell):
        li = LOOKBACKS.index(cell["params"]["lookback"])
        for d in (-1, 1):
            if 0 <= li + d < len(LOOKBACKS):
                nb = next(c for c in cells
                          if c["params"]["lookback"] == LOOKBACKS[li + d]
                          and c["params"]["top_k"] == cell["params"]["top_k"]
                          and c["params"]["rebal_days"] == cell["params"]["rebal_days"])
                if nb["passed"]:
                    return True
        return False

    recs = [c for c in cells if c["passed"] and c["last_fold_ok"] and neighbor_pass(c)]
    doc = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "universe": list(UNIVERSE.values()), "sma_filter": SMA_FILTER,
           "cost_bps_stressed": COST_BPS * COST_STRESS,
           "cells_passing_gate_a": sum(1 for c in cells if c["passed"]),
           "cells_total": len(cells), "recommendations": recs, "cells": cells}
    out = os.path.join("ledger", "research", "rotation.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    print(f"\n# Rotation: {doc['cells_passing_gate_a']}/{len(cells)} cells pass "
          f"gate A · {len(recs)} recommendation(s)")
    for c in recs:
        p = c["params"]
        print(f"- L{p['lookback']} top{p['top_k']} r{p['rebal_days']} — "
              f"stressed {c['ret']:+.1f}%, PF {c['pf']}, DD {c['max_dd']}%, "
              f"{c['trades']} episodes, folds {c['folds_won']}/{WF_FOLDS}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
