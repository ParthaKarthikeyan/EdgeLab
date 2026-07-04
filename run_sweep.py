#!/usr/bin/env python3
"""Candidate sweep — a wider, still-honest search for a Book 2 that trades
often enough to accumulate forward evidence quickly.

Grid: strategy family x parameter variant x symbol x interval, every cell
judged by the standard Gate A (walk-forward majority + net-positive, all at
2x costs). Because sweeping many cells and keeping winners is exactly how
overfitting happens, a cell only becomes a RECOMMENDATION if it also passes
a selection guard the gate itself doesn't require:

  - the FINAL walk-forward fold (most recent data) must be profitable, and
  - at least one PARAMETER NEIGHBOR in the same family must also pass Gate A
    (an edge that exists only at one magic parameter value is noise).

Output: ledger/research/sweep.json + console/step-summary table.

    python run_sweep.py
"""

import json
import os
from datetime import datetime, timezone
from itertools import product

from core.books import BANKROLL
from core.data import get_candles
from core.engine import metrics, run_channel, run_fade, run_trend, walk_forward
from core.gates import COST_STRESS, WF_FOLDS, gate_a, profitable

COST_BPS = 20.0
# symbol -> (display, intervals). Universe = liquid pairs listed on BOTH
# Coinbase (data) and Alpaca (execution). Alts are swept at 6h only: the
# majors sweep showed 1h fails across the board at stressed costs, and alt
# spreads are wider still — testing alts at 1h would be theater.
SYMBOLS_PLAN = {
    "BTC-USD": ("BTC/USD", ["1h", "6h"]),
    "ETH-USD": ("ETH/USD", ["1h", "6h"]),
    "SOL-USD": ("SOL/USD", ["1h", "6h"]),
    "XRP-USD": ("XRP/USD", ["6h"]),
    "DOGE-USD": ("DOGE/USD", ["6h"]),
    "LTC-USD": ("LTC/USD", ["6h"]),
    "LINK-USD": ("LINK/USD", ["6h"]),
    "AVAX-USD": ("AVAX/USD", ["6h"]),
    "DOT-USD": ("DOT/USD", ["6h"]),
    "BCH-USD": ("BCH/USD", ["6h"]),
    "UNI-USD": ("UNI/USD", ["6h"]),
}
SYMBOLS = {p: d for p, (d, _) in SYMBOLS_PLAN.items()}
INTERVAL_YEARS = {"1h": 3.0, "6h": 4.0}     # interval -> years of history

# Parameter families. Neighbors within a family = same knobs, adjacent values.
TREND_GRID = [dict(entry_lookback=el, exit_lookback=el // 2, atr_period=14,
                   atr_stop_mult=sm, trend_fast=50, trend_slow=200,
                   risk_pct=0.01, allow_short=False)
              for el, sm in product([14, 20, 28, 40, 55], [2.5, 3.0, 3.5])]
FADE_GRID = [dict(sma_period=sp, band_k=bk, stop_k=bk + 1.5, trend_ema=200,
                  max_hold=mh, risk_pct=0.01, allow_short=False)
             for sp, bk, mh in product([14, 20, 30], [2.0, 2.5], [12, 24])]
# families flagged by mass-backtest survivor stats: Keltner(+ADX) breakouts
# and Bollinger-with-volume-confirmation fades
CHANNEL_GRID = [dict(ema_period=20, atr_period=10, channel_mult=cm,
                     adx_min=am, atr_stop_mult=3.0, risk_pct=0.01,
                     allow_short=False)
                for cm, am in product([1.5, 2.0, 2.5], [0.0, 20.0, 25.0])]
FADE_VOL_GRID = [dict(sma_period=sp, band_k=bk, stop_k=bk + 1.5, trend_ema=200,
                      max_hold=mh, vol_mult=1.5, risk_pct=0.01,
                      allow_short=False)
                 for sp, bk, mh in product([14, 20, 30], [2.0, 2.5], [12, 24])]
FAMILIES = {"trend": (run_trend, TREND_GRID), "fade": (run_fade, FADE_GRID),
            "channel": (run_channel, CHANNEL_GRID),
            "fade_vol": (run_fade, FADE_VOL_GRID)}


def eval_cell(df, fn, params):
    stressed = metrics(fn(df, start_cash=BANKROLL,
                          cost_bps=COST_BPS * COST_STRESS, **params), BANKROLL)
    folds = walk_forward(df, fn, params, COST_BPS * COST_STRESS,
                         n_folds=WF_FOLDS, start_cash=BANKROLL)
    g = gate_a(stressed, folds)
    last_fold_ok = bool(folds) and profitable(folds[-1])
    span_years = max((df.index[-1] - df.index[0]).days / 365.0, 0.01)
    # numpy scalars leak out of pandas comparisons; coerce for json
    return {"passed": bool(g.passed), "last_fold_ok": bool(last_fold_ok),
            "ret": round(float(stressed["ret"]), 1),
            "pf": None if stressed["profit_factor"] == float("inf")
                  else round(float(stressed["profit_factor"]), 2),
            "max_dd": round(float(stressed["max_dd"]), 1),
            "trades_per_year": round(float(stressed["trades"]) / span_years, 1),
            "folds_won": int(sum(1 for f in folds if profitable(f)))}


def main():
    data = {}
    for product_id, (_disp, intervals) in SYMBOLS_PLAN.items():
        for interval in intervals:
            years = INTERVAL_YEARS[interval]
            print(f"[data] {product_id} {interval} ({years:g}y)...")
            try:
                data[(product_id, interval)] = get_candles(product_id, interval, years)
            except Exception as e:                # noqa: BLE001
                print(f"[data] {product_id} {interval} failed: {e}")

    cells = []
    for fam, (fn, grid) in FAMILIES.items():
        for idx, params in enumerate(grid):
            for (product_id, interval), df in data.items():
                r = eval_cell(df, fn, params)
                cells.append({"family": fam, "grid_idx": idx,
                              "symbol": SYMBOLS[product_id], "interval": interval,
                              "params": params, **r})
        print(f"[sweep] {fam}: {sum(1 for c in cells if c['family'] == fam and c['passed'])}"
              f"/{sum(1 for c in cells if c['family'] == fam)} cells pass gate A")

    # neighbor guard: a passing cell needs an adjacent grid index in the same
    # family/symbol/interval that also passes
    by_key = {}
    for c in cells:
        by_key.setdefault((c["family"], c["symbol"], c["interval"]), {})[c["grid_idx"]] = c
    recs = []
    for c in cells:
        if not (c["passed"] and c["last_fold_ok"]):
            continue
        sibs = by_key[(c["family"], c["symbol"], c["interval"])]
        if any(sibs.get(c["grid_idx"] + d, {}).get("passed") for d in (-1, 1)):
            recs.append(c)
    recs.sort(key=lambda c: (-c["trades_per_year"], -(c["pf"] or 0)))

    doc = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "cost_bps_stressed": COST_BPS * COST_STRESS,
           "cells_total": len(cells),
           "cells_passing_gate_a": sum(1 for c in cells if c["passed"]),
           "recommendations": recs,
           "note": ("Recommendation = Gate A pass + profitable final fold + a "
                    "passing parameter neighbor. Sweep selection is still "
                    "in-sample choice; the paper ledger remains the judge."),
           "cells": cells}
    os.makedirs(os.path.join("ledger", "research"), exist_ok=True)
    out = os.path.join("ledger", "research", "sweep.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    lines = [f"# Sweep: {doc['cells_passing_gate_a']}/{len(cells)} cells pass "
             f"gate A · {len(recs)} recommendation(s)", ""]
    for c in recs[:12]:
        lines.append(f"- {c['family']} {c['symbol']} {c['interval']} "
                     f"grid#{c['grid_idx']} — ret {c['ret']:+.1f}% @2x, "
                     f"PF {c['pf']}, {c['trades_per_year']}/yr, "
                     f"folds {c['folds_won']}/{WF_FOLDS}")
    report = "\n".join(lines)
    print("\n" + report)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
