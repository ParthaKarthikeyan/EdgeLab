"""The acceptance gates as code. METHODOLOGY.md is the contract; this enforces it.

Gate A (research): does the edge survive costs out-of-sample?
Gate B (paper):    does it survive reality (real fills, 10-session window)?
Gate C (scale):    is the monthly average what it claims (rolling-21 positive)?

Each check returns a GateResult with per-criterion detail so the dashboard can
show WHY a book is where it is, not just a pass/fail flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- thresholds (the methodology, in numbers) --------------------------------
WF_FOLDS = 5                 # sequential out-of-sample folds
COST_STRESS = 2.0            # Gate A judged at 2x modeled costs
# Gate B needs EVIDENCE, and evidence is trades as much as calendar:
# either the full 10 sessions, or 7+ sessions with 20+ closed trades.
# (Amended 2026-07-04 from 30/14/40: hard go-live deadline of 2026-07-20.
# 10 sessions is thin evidence and everyone involved knows it — the risk is
# priced in by going live SMALL and letting Gate C, now judged on the live
# ledger, decide whether size ever grows.)
GATE_B_SESSIONS = 10
GATE_B_FAST_SESSIONS = 7
GATE_B_FAST_TRADES = 20
# Gate C is the SCALE gate: after go-live it runs on the live ledger — at
# least a full rolling window of sessions with the $/day average positive.
GATE_C_SESSIONS = 21
RECON_GAP_MAX = 0.10         # reconciliation gap < 10% of gross P&L
ROLLING_WINDOW = 21          # sessions in the "monthly" average


@dataclass
class GateResult:
    gate: str
    passed: bool
    checks: list = field(default_factory=list)   # [{name, passed, detail}]

    def add(self, name: str, passed: bool, detail: str):
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})

    def finalize(self):
        self.passed = all(c["passed"] for c in self.checks)
        return self

    def to_dict(self) -> dict:
        return {"gate": self.gate, "passed": self.passed, "checks": self.checks}


# --- Gate A -------------------------------------------------------------------
def profitable(m: dict) -> bool:
    return m["ret"] > 0 and m["profit_factor"] > 1.0


def gate_a(full_stressed: dict, folds_stressed: list) -> GateResult:
    """`full_stressed`: metrics of the whole-period backtest at COST_STRESS x costs.
    `folds_stressed`: per-fold metrics (walk-forward) at the same stressed costs."""
    r = GateResult("A", False)
    wins = sum(1 for f in folds_stressed if profitable(f))
    r.add("walk_forward_majority", wins > len(folds_stressed) / 2,
          f"{wins}/{len(folds_stressed)} folds profitable at {COST_STRESS:.0f}x costs")
    r.add("net_positive_stressed", profitable(full_stressed),
          f"full period at {COST_STRESS:.0f}x costs: ret {full_stressed['ret']:+.1f}%, "
          f"PF {full_stressed['profit_factor']:.2f}")
    r.add("enough_trades", full_stressed["trades"] >= 30,
          f"{full_stressed['trades']} trades in the backtest (need >= 30 for the "
          "stats to mean anything)")
    return r.finalize()


# --- Gate B / C (computed from the committed ledger + trade log) --------------
def _sessions(ledger: dict) -> list:
    dep = ledger.get("deployment_date", "9999")
    return [row for row in ledger.get("history", []) if row["date"] >= dep]


def _max_dd_pct(rows: list, bankroll: float) -> float:
    peak, mdd = bankroll, 0.0
    for row in rows:
        e = row["book_end"]
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return mdd * 100


def gate_b(ledger: dict, trades: list, dd_budget_pct: float) -> GateResult:
    """`trades`: trade-log rows since deployment (filled prices + slippage)."""
    r = GateResult("B", False)
    rows = _sessions(ledger)
    bank = ledger.get("bankroll", 10000.0)
    n = len(rows)
    n_trades = len(trades)
    pnl = sum(row["pnl"] for row in rows)
    enough = n >= GATE_B_SESSIONS or (
        n >= GATE_B_FAST_SESSIONS and n_trades >= GATE_B_FAST_TRADES)
    r.add("evidence", enough,
          f"{n} sessions, {n_trades} closed trades (need {GATE_B_SESSIONS} "
          f"sessions, or {GATE_B_FAST_SESSIONS}+ with "
          f"{GATE_B_FAST_TRADES}+ trades)")
    r.add("positive_on_fills", pnl > 0, f"total P&L {pnl:+,.2f} (filled prices)")
    dd = _max_dd_pct(rows, bank)
    r.add("drawdown_in_budget", dd <= dd_budget_pct,
          f"max DD {dd:.1f}% vs budget {dd_budget_pct:.0f}%")
    gross = sum(abs(t["pnl"]) for t in trades)
    gap = abs(sum(t.get("slippage", 0.0) for t in trades))
    ok = gross == 0 or gap / gross < RECON_GAP_MAX
    r.add("reconciliation", ok,
          f"slippage ${gap:,.2f} vs gross ${gross:,.2f} "
          f"({(gap / gross * 100) if gross else 0:.1f}%, cap {RECON_GAP_MAX * 100:.0f}%)")
    return r.finalize()


def gate_c(ledger: dict, dd_budget_pct: float) -> GateResult:
    r = GateResult("C", False)
    rows = _sessions(ledger)
    bank = ledger.get("bankroll", 10000.0)
    n = len(rows)
    r.add("sessions", n >= GATE_C_SESSIONS, f"{n}/{GATE_C_SESSIONS} forward sessions")
    tail = rows[-ROLLING_WINDOW:]
    avg = (sum(row["pnl"] for row in tail) / len(tail)) if tail else 0.0
    r.add("rolling_avg_positive", len(tail) >= ROLLING_WINDOW and avg > 0,
          f"rolling-{ROLLING_WINDOW} average {avg:+,.2f} $/day")
    dd = _max_dd_pct(rows, bank)
    r.add("drawdown_in_budget", dd <= dd_budget_pct,
          f"max DD {dd:.1f}% vs budget {dd_budget_pct:.0f}%")
    return r.finalize()


def book_status(ledger: dict | None, trades: list, gate_a_result: dict | None,
                dd_budget_pct: float) -> dict:
    """The dashboard's one-call summary: which gate is this book in, and why."""
    if ledger is None:
        a_ok = bool(gate_a_result and gate_a_result.get("passed"))
        return {"stage": "gate_a" if not a_ok else "awaiting_paper",
                "gate_a": gate_a_result, "gate_b": None, "gate_c": None}
    b = gate_b(ledger, trades, dd_budget_pct)
    if not b.passed:
        return {"stage": "paper", "gate_a": gate_a_result,
                "gate_b": b.to_dict(), "gate_c": None}
    c = gate_c(ledger, dd_budget_pct)
    return {"stage": "promoted" if c.passed else "gate_c_proving",
            "gate_a": gate_a_result, "gate_b": b.to_dict(), "gate_c": c.to_dict()}
