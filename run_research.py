#!/usr/bin/env python3
"""Gate A research runner — the only door into a paper book.

For each book in the registry (or --book NAME), fetch its data, run the frozen
rules at 1x and at the 2x stressed cost, walk-forward at the stressed cost, and
apply Gate A. The verdict (with per-criterion detail and per-symbol metrics) is
committed to ledger/research/<book>.json so the dashboard — and the promotion
decision — can cite it.

    python run_research.py                 # all books in the registry
    python run_research.py --book crypto_fade_1h
"""

import argparse
import json
import os
from datetime import datetime, timezone

from core.books import BOOKS, BANKROLL
from core.data import get_candles
from core.engine import metrics, walk_forward
from core.gates import COST_STRESS, WF_FOLDS, gate_a

OUT_DIR = os.path.join("ledger", "research")


def research_book(name: str, spec: dict) -> dict:
    per_symbol, agg_folds = {}, []
    # aggregate across symbols by averaging fold metrics is statistically mushy;
    # instead Gate A must hold for EVERY symbol the book trades.
    verdicts = []
    for alp, product in spec["symbols"].items():
        df = get_candles(product, interval=spec["interval"],
                         years=spec["history_years"])
        base = metrics(spec["fn"](df, start_cash=BANKROLL,
                                  cost_bps=spec["cost_bps"], **spec["params"]),
                       BANKROLL)
        stressed = metrics(spec["fn"](df, start_cash=BANKROLL,
                                      cost_bps=spec["cost_bps"] * COST_STRESS,
                                      **spec["params"]), BANKROLL)
        folds = walk_forward(df, spec["fn"], spec["params"],
                             spec["cost_bps"] * COST_STRESS,
                             n_folds=WF_FOLDS, start_cash=BANKROLL)
        g = gate_a(stressed, folds)
        verdicts.append(g.passed)
        span_years = max((df.index[-1] - df.index[0]).days / 365.0, 0.01)
        per_symbol[alp] = {
            "bars": len(df), "trades_per_year": round(base["trades"] / span_years, 1),
            "base": {k: (None if v == float("inf") else round(v, 2))
                     for k, v in base.items()},
            "stressed": {k: (None if v == float("inf") else round(v, 2))
                         for k, v in stressed.items()},
            "folds_stressed": [{k: (None if v == float("inf") else round(v, 2))
                                for k, v in f.items()} for f in folds],
            "gate_a": g.to_dict(),
        }
        print(f"  {alp}: base {base['ret']:+.1f}% · stressed {stressed['ret']:+.1f}% "
              f"(PF {stressed['profit_factor']:.2f}) · "
              f"WF {sum(1 for f in folds if f['ret'] > 0 and f['profit_factor'] > 1)}"
              f"/{len(folds)} · gate A {'PASS' if g.passed else 'fail'}")

    passed = bool(verdicts) and all(verdicts)
    return {
        "book": name, "label": spec["label"], "interval": spec["interval"],
        "params": {k: v for k, v in spec["params"].items()},
        "cost_bps_base": spec["cost_bps"],
        "cost_bps_stressed": spec["cost_bps"] * COST_STRESS,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "passed": passed,
        "note": "Gate A requires every traded symbol to pass at stressed costs.",
        "symbols": per_symbol,
    }


def main():
    ap = argparse.ArgumentParser(description="EdgeLab Gate A research")
    ap.add_argument("--book", default=None, help="research a single book")
    args = ap.parse_args()

    names = [args.book] if args.book else list(BOOKS)
    os.makedirs(OUT_DIR, exist_ok=True)
    lines = ["# EdgeLab research — Gate A verdicts", ""]
    for name in names:
        spec = BOOKS[name]
        print(f"[research] {name} ({spec['label']})")
        doc = research_book(name, spec)
        out = os.path.join(OUT_DIR, f"{spec['gate_a_file']}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        lines.append(f"- **{spec['label']}** — "
                     f"{'✅ PASSES Gate A' if doc['passed'] else '❌ fails Gate A'} "
                     f"(2x costs = {doc['cost_bps_stressed']:.0f}bps round-trip)")
        print(f"[research] wrote {out} — {'PASS' if doc['passed'] else 'fail'}")

    report = "\n".join(lines)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    print("\n" + report)


if __name__ == "__main__":
    main()
