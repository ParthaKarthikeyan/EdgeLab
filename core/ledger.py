"""The ledger contract — one schema for every book, committed to git as the
source of truth.

A book ledger (ledger/<book>_ledger.json):
{
  "book": "crypto_trend",
  "rules_version": 1,
  "deployment_date": "YYYY-MM-DD",     # forward clock; re-stamped on rules change
  "bankroll": 10000.0,                 # the notional the book was seeded with
  "cash": ...,                         # uninvested cash
  "positions": {symbol: {units, entry, entry_time, ...}},
  "day_anchor": {"date": ..., "equity": ...},   # daily loss-stop reference
  "history": [ {date, book_start, book_end, pnl, pnl_pct, trades,
                open_positions, stopped} ],     # one row per calendar day
  "last_run": "YYYY-MM-DD HH:MM"
}

A trade log (ledger/<book>_trades.json):
{
  "book": ...,
  "history": [ {date, symbol, side, units,
                entry_time, exit_time,
                intended_entry, intended_exit,   # what the strategy wanted
                entry_price, exit_price,         # what the broker filled
                pnl,                             # on FILLED prices
                slippage,                        # filled-vs-intended $ impact
                reason} ],
  "last_run": ...
}

Intended vs filled is recorded per trade so the reconciliation gap is visible at
the trade level, not discovered at the account level weeks later.
"""

from __future__ import annotations

import json
import os
from typing import Optional

LEDGER_DIR = "ledger"


def _path(book: str, kind: str) -> str:
    return os.path.join(LEDGER_DIR, f"{book}_{kind}.json")


def load(book: str, kind: str = "ledger") -> Optional[dict]:
    p = _path(book, kind)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save(book: str, doc: dict, kind: str = "ledger") -> None:
    p = _path(book, kind)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, p)


def new_ledger(book: str, bankroll: float, rules_version: int, today: str) -> dict:
    return {"book": book, "rules_version": rules_version,
            "deployment_date": today, "bankroll": bankroll, "cash": bankroll,
            "positions": {}, "day_anchor": {"date": today, "equity": bankroll},
            "history": []}


def open_ledger(book: str, bankroll: float, rules_version: int, today: str) -> dict:
    """Load the book's ledger, creating it on first run. A rules_version change
    re-stamps deployment_date: the forward record never mixes rule sets."""
    led = load(book)
    if led is None:
        return new_ledger(book, bankroll, rules_version, today)
    if led.get("rules_version") != rules_version:
        led["rules_version"] = rules_version
        led["deployment_date"] = today
    led.setdefault("positions", {})
    led.setdefault("history", [])
    led.setdefault("bankroll", bankroll)
    return led


def upsert_row(history: list, row: dict) -> list:
    """One row per date; same-day re-runs replace their row."""
    history = [r for r in history if r.get("date") != row["date"]]
    history.append(row)
    history.sort(key=lambda r: r["date"])
    return history


def make_trade(*, date: str, symbol: str, side: str, units: float,
               entry_time: str, exit_time: str,
               intended_entry: float, intended_exit: float,
               entry_price: float, exit_price: float, reason: str) -> dict:
    """Build a trade-log row; pnl is on FILLED prices, slippage is the $ cost of
    fills deviating from intent (positive slippage = fills were worse)."""
    sgn = 1 if side == "long" else -1
    pnl = sgn * units * (exit_price - entry_price)
    intended_pnl = sgn * units * (intended_exit - intended_entry)
    return {"date": date, "symbol": symbol, "side": side,
            "units": round(units, 8),
            "entry_time": entry_time, "exit_time": exit_time,
            "intended_entry": round(intended_entry, 4),
            "intended_exit": round(intended_exit, 4),
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "pnl": round(pnl, 2),
            "slippage": round(intended_pnl - pnl, 2),
            "reason": reason}
