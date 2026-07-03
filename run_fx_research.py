#!/usr/bin/env python3
"""FX candidate sweep over OANDA majors — same honesty rules as run_sweep.py,
with one upgrade the crypto sweep can't have: the cost model is MEASURED from
bid/ask candles (mean round-trip spread per instrument), not assumed.

Gate A still judges at 2x the measured cost. Recommendation guards are the
same: profitable final walk-forward fold + a passing parameter neighbor.

FX differences vs crypto:
- shorting is symmetric (allow_short=True),
- modest leverage is normal (units capped at 5x equity notional; retail
  regulation allows far more, we don't take it),
- market closed weekends, so "sessions" are Mon-Fri.

Needs OANDA_API_KEY (practice token).

    python run_fx_research.py
"""

import json
import os
from datetime import datetime, timezone
from itertools import product

from core.books import BANKROLL
from core.engine import metrics, run_fade, run_trend, walk_forward
from core.gates import COST_STRESS, WF_FOLDS, gate_a, profitable
from core.oanda import get_fx_candles

INSTRUMENTS = ["EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD"]
GRANULARITIES = {"H1": 3.0, "H6": 4.0}     # granularity -> years
SLIPPAGE_BUFFER_BPS = 0.5                  # on top of measured spread
MAX_LEV = 5.0

TREND_GRID = [dict(entry_lookback=el, exit_lookback=el // 2, atr_period=14,
                   atr_stop_mult=sm, trend_fast=50, trend_slow=200,
                   risk_pct=0.01, allow_short=True, max_leverage=MAX_LEV)
              for el, sm in product([14, 20, 28, 40, 55], [2.5, 3.0, 3.5])]
FADE_GRID = [dict(sma_period=sp, band_k=bk, stop_k=bk + 1.5, trend_ema=200,
                  max_hold=mh, risk_pct=0.01, allow_short=True,
                  max_leverage=MAX_LEV)
             for sp, bk, mh in product([14, 20, 30], [2.0, 2.5], [12, 24])]
FAMILIES = {"trend": (run_trend, TREND_GRID), "fade": (run_fade, FADE_GRID)}


def eval_cell(df, fn, params, cost_bps):
    stressed = metrics(fn(df, start_cash=BANKROLL,
                          cost_bps=cost_bps * COST_STRESS, **params), BANKROLL)
    folds = walk_forward(df, fn, params, cost_bps * COST_STRESS,
                         n_folds=WF_FOLDS, start_cash=BANKROLL)
    g = gate_a(stressed, folds)
    span_years = max((df.index[-1] - df.index[0]).days / 365.0, 0.01)
    return {"passed": bool(g.passed),
            "last_fold_ok": bool(folds) and bool(profitable(folds[-1])),
            "ret": round(float(stressed["ret"]), 1),
            "pf": None if stressed["profit_factor"] == float("inf")
                  else round(float(stressed["profit_factor"]), 2),
            "max_dd": round(float(stressed["max_dd"]), 1),
            "trades_per_year": round(float(stressed["trades"]) / span_years, 1),
            "folds_won": int(sum(1 for f in folds if profitable(f)))}


def main():
    data, costs = {}, {}
    for inst, (gran, years) in product(INSTRUMENTS, GRANULARITIES.items()):
        print(f"[data] {inst} {gran} ({years:g}y)...")
        try:
            df, spread = get_fx_candles(inst, gran, years)
            data[(inst, gran)] = df
            costs[(inst, gran)] = spread + SLIPPAGE_BUFFER_BPS
            print(f"       {len(df)} bars, measured spread {spread:.2f}bps rt "
                  f"-> cost {costs[(inst, gran)]:.2f}bps (stress "
                  f"{costs[(inst, gran)] * COST_STRESS:.2f})")
        except Exception as e:                    # noqa: BLE001
            print(f"       failed: {e}")

    cells = []
    for fam, (fn, grid) in FAMILIES.items():
        for idx, params in enumerate(grid):
            for (inst, gran), df in data.items():
                r = eval_cell(df, fn, params, costs[(inst, gran)])
                cells.append({"family": fam, "grid_idx": idx, "symbol": inst,
                              "interval": gran, "params": params,
                              "cost_bps": round(costs[(inst, gran)], 2), **r})
        n_pass = sum(1 for c in cells if c["family"] == fam and c["passed"])
        n_all = sum(1 for c in cells if c["family"] == fam)
        print(f"[sweep] {fam}: {n_pass}/{n_all} cells pass gate A")

    by_key = {}
    for c in cells:
        by_key.setdefault((c["family"], c["symbol"], c["interval"]), {})[c["grid_idx"]] = c
    recs = [c for c in cells
            if c["passed"] and c["last_fold_ok"]
            and any(by_key[(c["family"], c["symbol"], c["interval"])]
                    .get(c["grid_idx"] + d, {}).get("passed") for d in (-1, 1))]
    recs.sort(key=lambda c: (-c["trades_per_year"], -(c["pf"] or 0)))

    doc = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "venue": "oanda_fx_practice", "max_leverage": MAX_LEV,
           "cost_model": f"measured bid/ask spread + {SLIPPAGE_BUFFER_BPS}bps, "
                         f"judged at {COST_STRESS:.0f}x",
           "cells_total": len(cells),
           "cells_passing_gate_a": sum(1 for c in cells if c["passed"]),
           "recommendations": recs,
           "cells": cells}
    os.makedirs(os.path.join("ledger", "research"), exist_ok=True)
    out = os.path.join("ledger", "research", "fx_sweep.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    lines = [f"# FX sweep: {doc['cells_passing_gate_a']}/{len(cells)} cells "
             f"pass gate A · {len(recs)} recommendation(s)", ""]
    for c in recs[:12]:
        lines.append(f"- {c['family']} {c['symbol']} {c['interval']} "
                     f"grid#{c['grid_idx']} — ret {c['ret']:+.1f}% @2x "
                     f"({c['cost_bps'] * COST_STRESS:.1f}bps), PF {c['pf']}, "
                     f"{c['trades_per_year']}/yr, folds {c['folds_won']}/{WF_FOLDS}")
    report = "\n".join(lines)
    print("\n" + report)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
