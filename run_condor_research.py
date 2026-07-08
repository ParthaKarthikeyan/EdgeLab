#!/usr/bin/env python3
"""QQQ 1-DTE iron condor — Gate A research (the "$300 options challenge").

Mechanical rules under test: at the 14:30 ET bar, if the prior session's VIX
close is under 20, sell a QQQ iron condor expiring the next session; take
profit at a fraction of the credit or settle at expiry. Real Alpaca option
bars (since 2024-02), conservative fills (unfavorable side + per-leg slippage,
fees on every leg), judged at 2x costs with 5 walk-forward folds per
METHODOLOGY.md. Neighbor guard runs along the strike-offset axis.

Needs ALPACA_API_KEY / ALPACA_SECRET_KEY (data endpoints).

    python run_condor_research.py
"""

import json
import os
import sys
from datetime import date, datetime, timezone
from itertools import product

import pandas as pd

from core.engine import metrics
from core.gates import COST_STRESS, WF_FOLDS, gate_a, profitable
from core.options import (DECISION, OFFSETS, PROFIT_TAKES, WIDTHS, ET,
                          all_candidate_symbols, build_day, cache_load,
                          cache_save, get_option_bars, get_stock_bars,
                          run_condor, sessions)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

START = "2024-02-05"          # Alpaca option history begins 2024-02
SLIP = 0.01                   # $/share per leg-fill (1x; Gate A stresses 2x)
FEE = 0.05                    # $/contract per leg-fill (regulatory, no commission)
RISK_PCT = 0.05               # 5% of book equity per position (max defined loss)
BANKROLL = 10000.0

VIDEO_CELL = {"put_off": 2.0, "call_off": 1.75, "width": 1, "profit_take": 0.3,
              "vix_gate": True}


def fetch_days() -> list[dict]:
    today = date.today().isoformat()
    cal = sessions(START, today)
    if cal and cal[-1] == today:
        cal = cal[:-1]                      # today is still trading; exclude
    print(f"[calendar] {len(cal)} sessions {cal[0]} .. {cal[-1]}")

    qqq5 = get_stock_bars("QQQ", START, today, "5Min")

    # official closes (settlement) and VIX from Yahoo — IEX closes can drift
    # a few cents from the consolidated close that options settle against
    import yfinance as yf

    def daily_series(ticker: str) -> pd.Series:
        s = yf.download(ticker, start=START, interval="1d",
                        progress=False, auto_adjust=False)["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = s.index.strftime("%Y-%m-%d")
        return s

    qqq_daily = daily_series("QQQ")
    daily_close = {d: float(v) for d, v in qqq_daily.items()}
    vix = daily_series("^VIX")
    print(f"[qqq] {len(qqq5)} 5-min bars, {len(daily_close)} daily closes; "
          f"[vix] {len(vix)} closes")

    days, fetched = [], 0
    for i, d in enumerate(cal[:-1]):
        expiry = cal[i + 1]
        day = cache_load(d)
        if day is None:
            dec = pd.Timestamp(f"{d} {DECISION}", tz=ET)
            bar = qqq5[qqq5.index == dec]
            spot = float(bar["c"].iloc[0]) if len(bar) else None
            legs = {}
            if spot is not None:
                syms = all_candidate_symbols(spot, expiry)
                start_iso = pd.Timestamp(f"{d} 13:00", tz=ET).isoformat()
                end_iso = pd.Timestamp(f"{expiry} 16:15", tz=ET).isoformat()
                legs = get_option_bars(syms, start_iso, end_iso)
            prev_vix = vix[vix.index < d]
            day = build_day(d, expiry, spot,
                            float(prev_vix.iloc[-1]) if len(prev_vix) else None,
                            daily_close.get(expiry), legs)
            cache_save(d, day)
            fetched += 1
            if fetched % 25 == 0:
                print(f"[fetch] {fetched} days fetched (at {d})")
        days.append(day)
    print(f"[days] {len(days)} sessions ready ({fetched} fetched fresh)")
    return days


def cell_metrics(days: list[dict], params: dict, stress: float) -> dict:
    res = run_condor(days, slip=SLIP * stress, fee=FEE * stress,
                     risk_pct=RISK_PCT, start_cash=BANKROLL, **params)
    m = metrics(res, BANKROLL)
    m["skips"] = res["skips"]
    m["trade_rows"] = res["trades"]
    return m


def folds_for(days: list[dict], params: dict) -> list:
    n, seg, out = len(days), len(days) // WF_FOLDS, []
    for i in range(WF_FOLDS):
        a, b = i * seg, ((i + 1) * seg if i < WF_FOLDS - 1 else n)
        out.append(cell_metrics(days[a:b], params, COST_STRESS))
    return out


def main():
    days = fetch_days()

    cells = []
    for (po, co), w, pt, vg in product(OFFSETS, WIDTHS, PROFIT_TAKES,
                                       (True, False)):
        params = dict(put_off=po, call_off=co, width=w, profit_take=pt,
                      vix_gate=vg)
        stressed = cell_metrics(days, params, COST_STRESS)
        base = cell_metrics(days, params, 1.0)
        folds = folds_for(days, params)
        g = gate_a(stressed, folds)
        losses = sorted(t["pnl"] for t in base["trade_rows"])
        cells.append({
            "params": params,
            "passed": bool(g.passed),
            "last_fold_ok": bool(folds) and bool(profitable(folds[-1])),
            "base_ret": round(float(base["ret"]), 1),
            "ret": round(float(stressed["ret"]), 1),
            "pf": None if stressed["profit_factor"] == float("inf")
                  else round(float(stressed["profit_factor"]), 2),
            "win_rate": round(float(stressed["win_rate"]), 1),
            "max_dd": round(float(stressed["max_dd"]), 1),
            "trades": int(stressed["trades"]),
            "folds_won": int(sum(1 for f in folds if profitable(f))),
            "worst_day": round(losses[0], 2) if losses else 0.0,
            "worst3": [round(x, 2) for x in losses[:3]],
            "skips": stressed["skips"]})
        c = cells[-1]
        pt_s = f"pt{int(pt * 100)}" if pt else "expiry"
        print(f"  {po:.1f}/{co:.2f} w{w} {pt_s:6s} vix{'Y' if vg else 'N'}: "
              f"base {c['base_ret']:+6.1f}% · stressed {c['ret']:+6.1f}% "
              f"(PF {c['pf']}, WR {c['win_rate']}%, DD {c['max_dd']}%) · "
              f"{c['trades']} trades · folds {c['folds_won']}/{WF_FOLDS} · "
              f"worst {c['worst_day']:+.0f} · "
              f"{'PASS' if c['passed'] else 'fail'}")

    def neighbor_pass(cell):
        oi = OFFSETS.index((cell["params"]["put_off"],
                            cell["params"]["call_off"]))
        for d in (-1, 1):
            if 0 <= oi + d < len(OFFSETS):
                po, co = OFFSETS[oi + d]
                nb = next(c for c in cells
                          if c["params"]["put_off"] == po
                          and c["params"]["call_off"] == co
                          and c["params"]["width"] == cell["params"]["width"]
                          and c["params"]["profit_take"] == cell["params"]["profit_take"]
                          and c["params"]["vix_gate"] == cell["params"]["vix_gate"])
                if nb["passed"]:
                    return True
        return False

    recs = [c for c in cells if c["passed"] and c["last_fold_ok"]
            and neighbor_pass(c)]
    recs.sort(key=lambda c: (c["pf"] or 0, c["ret"]), reverse=True)
    chosen = next((c for c in recs if c["params"] == VIDEO_CELL), None)
    if chosen is None and recs:
        chosen = recs[0]

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    slim = [{k: v for k, v in c.items() if k != "worst3"} for c in cells]
    full = {"generated": gen, "start": START, "sessions": len(days),
            "cost_model": {"slip_per_leg": SLIP, "fee_per_leg_contract": FEE,
                           "stress": COST_STRESS},
            "risk_pct": RISK_PCT, "video_cell": VIDEO_CELL,
            "cells_passing_gate_a": sum(1 for c in cells if c["passed"]),
            "cells_total": len(cells),
            "recommendations": recs[:10], "cells": slim}
    os.makedirs(os.path.join("ledger", "research"), exist_ok=True)
    with open(os.path.join("ledger", "research", "condor.json"), "w",
              encoding="utf-8") as f:
        json.dump(full, f, indent=2)

    passed = chosen is not None
    if passed:
        p, pt = chosen["params"], chosen["params"]["profit_take"]
        note = (f"QQQ 1-DTE condor {p['put_off']:.1f}%/{p['call_off']:.2f}% "
                f"w${p['width']} "
                f"{'pt' + str(int(pt * 100)) + '%' if pt else 'hold-to-expiry'} "
                f"vix{'<20' if p['vix_gate'] else ' off'}: stressed "
                f"{chosen['ret']:+.1f}% PF {chosen['pf']} "
                f"WR {chosen['win_rate']}% DD {chosen['max_dd']}% over "
                f"{chosen['trades']} trades, folds "
                f"{chosen['folds_won']}/{WF_FOLDS}; worst day "
                f"{chosen['worst_day']:+.0f}, worst-3 {chosen['worst3']}. "
                f"Bars-only data (no historical quotes) — slippage haircut "
                f"is the budget for stale OTM marks.")
    else:
        note = ("no cell passed Gate A with a profitable last fold and a "
                "passing offset neighbor at 2x costs — short-vol edge does "
                "not survive conservative fills on this window")
    verdict = {"book": "options_condor", "label": "QQQ 1-DTE iron condor",
               "interval": "1d", "params": chosen["params"] if chosen else None,
               "cost_model": full["cost_model"], "generated": gen,
               "passed": passed, "note": note, "cell": chosen}
    vp = os.path.join("ledger", "research", "options_condor.json")
    with open(vp, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2)

    print(f"\n# Condor: {full['cells_passing_gate_a']}/{len(cells)} cells pass "
          f"gate A · {len(recs)} recommendation(s)")
    for c in recs[:8]:
        p = c["params"]
        pt_s = f"pt{int(p['profit_take'] * 100)}" if p["profit_take"] else "expiry"
        print(f"- {p['put_off']:.1f}/{p['call_off']:.2f} w{p['width']} {pt_s} "
              f"vix{'Y' if p['vix_gate'] else 'N'} — stressed {c['ret']:+.1f}%, "
              f"PF {c['pf']}, WR {c['win_rate']}%, DD {c['max_dd']}%, "
              f"worst {c['worst_day']:+.0f}")
    print(f"\nwrote ledger/research/condor.json and {vp} — "
          f"{'PASS' if passed else 'FAIL'}")
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write(f"## Condor Gate A — {'PASS' if passed else 'FAIL'}\n\n"
                    f"{note}\n\nRecommendations: {len(recs)} of {len(cells)} "
                    f"cells\n")


if __name__ == "__main__":
    main()
