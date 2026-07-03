#!/usr/bin/env python3
"""Assemble ledger/status.json — the dashboard's single source for where every
book sits on the gate ladder (A/B/C, with per-criterion detail). Run after any
research or paper run; the workflows commit it with the ledgers.
"""

import json
import os
from datetime import datetime, timezone

from core import ledger as L
from core.books import BANKROLL, BOOKS
from core.gates import ROLLING_WINDOW, book_status

OUT = os.path.join("ledger", "status.json")


def main():
    books = {}
    for name, spec in BOOKS.items():
        ga = None
        p = os.path.join("ledger", "research", f"{spec['gate_a_file']}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            ga = {"passed": doc["passed"], "generated": doc.get("generated"),
                  "checks": [{"name": "all_symbols_pass",
                              "passed": doc["passed"],
                              "detail": doc.get("note", "")}]}
        led = L.load(name) if spec["active"] else None
        tlog = (L.load(name, "trades") or {}).get("history", []) if led else []
        dep = led.get("deployment_date", "9999") if led else "9999"
        trades = [t for t in tlog if t["date"] >= dep]
        st = book_status(led, trades, ga, spec["dd_budget_pct"])

        rows = [r for r in (led or {}).get("history", []) if r["date"] >= dep]
        tail = rows[-ROLLING_WINDOW:]
        books[name] = {
            "label": spec["label"], "active": spec["active"],
            "interval": spec["interval"], "dd_budget_pct": spec["dd_budget_pct"],
            "rules_version": spec["rules_version"],
            "deployment_date": led.get("deployment_date") if led else None,
            "sessions": len(rows),
            "pnl": round(sum(r["pnl"] for r in rows), 2),
            "rolling_avg": round(sum(r["pnl"] for r in tail) / len(tail), 2)
                           if tail else None,
            "rolling_window_filled": len(tail),
            **st,
        }

    doc = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "bankroll": BANKROLL, "rolling_window": ROLLING_WINDOW,
           "books": books}
    # skip the write when nothing but the timestamp changed — otherwise every
    # hourly run produces a pointless ledger commit
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
        if {k: v for k, v in old.items() if k != "generated"} == \
           {k: v for k, v in doc.items() if k != "generated"}:
            print(f"{OUT} unchanged")
            return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
