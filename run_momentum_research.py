#!/usr/bin/env python3
"""US-equity momentum book — Gate A research (the systematic "video 1").

The testable core of the discretionary month: concentrate in the strongest
names, cap the downside, never short. Every R trading days, rank a liquid US
universe by L-day return and hold the top K equal-weight, subject to:
  - per-name absolute momentum (return > 0 and price above its 100d SMA)
  - a market regime gate: SPY below its 200d SMA -> the whole book sits in
    cash (this is the "capped downside" leg; the LEAPS convexity leg is not
    testable without paid options history, so leverage is a sizing decision
    to be made only if the underlying edge passes the gates)

Known bias, disclosed: the universe is today's liquid large caps held fixed
over 10 years, so results carry survivorship bias. Deliberately including
decade-long losers (INTC, PYPL, T, F, BA, PFE...) softens but does not remove
it. Judge accordingly: Gate A here is necessary, not sufficient.

Costs: 6bps round-trip on turnover (zero-commission liquid large caps),
judged at 2x per Gate A. Walk-forward: 5 sequential date folds.
Guard against grid luck: neighbor along the lookback axis must also pass.

    python run_momentum_research.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from itertools import product

import pandas as pd

from core.books import BANKROLL
from core.engine import metrics
from core.gates import COST_STRESS, WF_FOLDS, gate_a, profitable
from core.equities import REGIME_SMA, SMA_FILTER, get_close_panel, run_momentum

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
            "AVGO", "CRM", "ADBE", "NFLX", "INTC", "CSCO", "ORCL", "QCOM",
            "MU", "TXN", "IBM", "PYPL", "JPM", "BAC", "GS", "V", "MA",
            "XOM", "CVX", "COP", "UNH", "JNJ", "PFE", "MRK", "LLY",
            "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "DIS",
            "BA", "CAT", "GE", "F", "T", "VZ"]
REGIME_SYM = "SPY"
YEARS = 10.0
COST_BPS = 6.0

LOOKBACKS = [21, 63, 126, 252]   # neighbor axis (1m / 3m / 6m / 12m)
TOP_KS = [3, 5]
REBAL_DAYS = [5, 10]


def mom_metrics(res: dict) -> dict:
    return metrics(res, BANKROLL)


def folds_for(closes: pd.DataFrame, spy: pd.Series, params: dict,
              cost: float) -> list:
    n, seg, out = len(closes), len(closes) // WF_FOLDS, []
    for i in range(WF_FOLDS):
        a, b = i * seg, ((i + 1) * seg if i < WF_FOLDS - 1 else n)
        chunk, spy_chunk = closes.iloc[a:b], spy.iloc[a:b]
        if len(chunk) < max(REGIME_SMA, params["lookback"]) + 60:
            continue
        out.append(mom_metrics(run_momentum(chunk, spy_chunk, cost_bps=cost,
                                            **params)))
    return out


def main():
    panel = get_close_panel(UNIVERSE + [REGIME_SYM], YEARS)
    spy = panel.pop(REGIME_SYM)
    print(f"[panel] {panel.shape[1]} names x {len(panel)} days "
          f"({panel.index[0].date()} .. {panel.index[-1].date()})")

    cells = []
    for lb, k, rd in product(LOOKBACKS, TOP_KS, REBAL_DAYS):
        params = dict(lookback=lb, top_k=k, rebal_days=rd)
        stressed = mom_metrics(run_momentum(panel, spy,
                                            cost_bps=COST_BPS * COST_STRESS,
                                            **params))
        base = mom_metrics(run_momentum(panel, spy, cost_bps=COST_BPS, **params))
        folds = folds_for(panel, spy, params, COST_BPS * COST_STRESS)
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
            "trades_per_year": round(float(stressed["trades"]) / YEARS, 1),
            "folds_won": int(sum(1 for f in folds if profitable(f)))})
        c = cells[-1]
        print(f"  L{lb:3d} top{k} r{rd:2d}: base {c['base_ret']:+7.1f}% · "
              f"stressed {c['ret']:+7.1f}% (PF {c['pf']}, DD {c['max_dd']}%) · "
              f"{c['trades']} episodes · folds {c['folds_won']}/{WF_FOLDS} · "
              f"{'PASS' if c['passed'] else 'fail'}")

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

    recs = [c for c in cells if c["passed"] and c["last_fold_ok"]
            and neighbor_pass(c)]
    doc = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "universe": UNIVERSE, "regime": f"{REGIME_SYM}>{REGIME_SMA}sma",
           "sma_filter": SMA_FILTER, "years": YEARS,
           "cost_bps_stressed": COST_BPS * COST_STRESS,
           "survivorship_bias": "fixed present-day universe over history",
           "cells_passing_gate_a": sum(1 for c in cells if c["passed"]),
           "cells_total": len(cells), "recommendations": recs, "cells": cells}
    out = os.path.join("ledger", "research", "momentum.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    # if the registry has an equity book, emit its Gate A verdict file too
    # (the frozen cell's result, in the schema run_status.py expects)
    from core.books import BOOKS
    spec = BOOKS.get("equity_momentum")
    cell = spec and next((c for c in cells if c["params"] == spec["params"]), None)
    if cell:
        verdict = {"book": "equity_momentum", "label": spec["label"],
                   "interval": spec["interval"], "params": cell["params"],
                   "cost_bps_base": COST_BPS,
                   "cost_bps_stressed": COST_BPS * COST_STRESS,
                   "generated": doc["generated"],
                   "passed": bool(cell["passed"] and cell["last_fold_ok"]),
                   "note": (f"panel book, {YEARS:g}y, stressed "
                            f"{cell['ret']:+.1f}% PF {cell['pf']} "
                            f"DD {cell['max_dd']}% folds "
                            f"{cell['folds_won']}/{WF_FOLDS}; survivorship "
                            "bias disclosed (fixed present-day universe)"),
                   "cell": cell}
        vp = os.path.join("ledger", "research",
                          f"{spec['gate_a_file']}.json")
        with open(vp, "w", encoding="utf-8") as f:
            json.dump(verdict, f, indent=2)
        print(f"wrote {vp} — {'PASS' if verdict['passed'] else 'fail'}")

    print(f"\n# Momentum: {doc['cells_passing_gate_a']}/{len(cells)} cells pass "
          f"gate A · {len(recs)} recommendation(s)")
    for c in recs:
        p = c["params"]
        print(f"- L{p['lookback']} top{p['top_k']} r{p['rebal_days']} — "
              f"stressed {c['ret']:+.1f}%, PF {c['pf']}, DD {c['max_dd']}%, "
              f"{c['trades_per_year']}/yr, folds {c['folds_won']}/{WF_FOLDS}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
