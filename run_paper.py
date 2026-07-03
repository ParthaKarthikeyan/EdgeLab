#!/usr/bin/env python3
"""EdgeLab paper runner — trades every ACTIVE book against Alpaca crypto paper.

Stateless per run (hourly cron): for each active book, fetch fresh candles,
evaluate the frozen rules on the last COMPLETED bar, reconcile the book's
positions, and order any difference with tagged orders (client_order_id prefix
`el-<book>-` so fills attribute to their book on the shared paper account).

Per METHODOLOGY.md:
- no profit caps — the scoreboard is the rolling monthly average;
- -2% daily loss stop: when the book is down 2% from the day's anchor, no NEW
  entries for the rest of the UTC day (open positions keep their normal exits);
- every trade logs intended vs filled prices, so slippage is visible per trade.

    python run_paper.py                # all active books, live paper orders
    python run_paper.py --dry-run      # signals only, no keys needed
"""

import argparse
import os
import time
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core import ledger as L
from core.books import BANKROLL, DAILY_STOP_PCT, active_books
from core.data import completed_bars, get_candles
from core.engine import atr, ema
from core.gist import GistPublisher

MIN_NOTIONAL = 10.0          # Alpaca minimum crypto order ~$10


# --- frozen-rule evaluation on the last completed bar ------------------------
def prep_trend(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    d = df.copy()
    d["fast"] = ema(d["close"], p["trend_fast"])
    d["slow"] = ema(d["close"], p["trend_slow"])
    d["atr"] = atr(d, p["atr_period"])
    d["don_hi"] = d["high"].rolling(p["entry_lookback"]).max().shift(1)
    d["exit_lo"] = d["low"].rolling(p["exit_lookback"]).min().shift(1)
    return d


def prep_fade(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    d = df.copy()
    d["sma"] = d["close"].rolling(p["sma_period"]).mean()
    d["std"] = d["close"].rolling(p["sma_period"]).std()
    d["ema"] = ema(d["close"], p["trend_ema"])
    d["lower"] = d["sma"] - p["band_k"] * d["std"]
    return d


def trend_entry(d, p) -> dict | None:
    r = d.iloc[-1]
    if np.isnan(r["slow"]) or np.isnan(r["atr"]) or np.isnan(r["don_hi"]):
        return None
    if r["fast"] > r["slow"] and r["close"] > r["don_hi"]:
        return {"dist": p["atr_stop_mult"] * float(r["atr"])}
    return None


def trend_exit(d, p, pos) -> str | None:
    r = d.iloc[-1]
    if np.isnan(r["slow"]) or np.isnan(r["atr"]):
        return None
    seg = d[d.index >= pd.Timestamp(pos["entry_time"])]
    peak, stop = pos["entry"], -np.inf
    for _, x in seg.iterrows():
        if np.isnan(x["atr"]):
            continue
        peak = max(peak, float(x["high"]))
        stop = max(stop, peak - p["atr_stop_mult"] * float(x["atr"]))
    if float(r["low"]) <= stop:
        return "stop"
    if not np.isnan(r["exit_lo"]) and float(r["close"]) < float(r["exit_lo"]):
        return "exit_break"
    if float(r["fast"]) < float(r["slow"]):
        return "regime_flip"
    return None


def fade_entry(d, p) -> dict | None:
    r = d.iloc[-1]
    if np.isnan(r["std"]) or r["std"] <= 0 or np.isnan(r["ema"]):
        return None
    if r["close"] < r["lower"] and r["close"] > r["ema"]:
        return {"dist": p["stop_k"] * float(r["std"])}
    return None


def fade_exit(d, p, pos) -> str | None:
    r = d.iloc[-1]
    if np.isnan(r["std"]) or r["std"] <= 0:
        return None
    entry_t = pd.Timestamp(pos["entry_time"])
    if float(r["low"]) <= pos["entry"] - p["stop_k"] * float(r["std"]):
        return "stop"
    if float(r["high"]) >= float(r["sma"]):
        return "mean"
    if len(d[d.index > entry_t]) >= p["max_hold"]:
        return "time"
    return None


RULES = {"run_trend": (prep_trend, trend_entry, trend_exit),
         "run_fade": (prep_fade, fade_entry, fade_exit)}


# --- broker -------------------------------------------------------------------
class Broker:
    def __init__(self):
        from alpaca.trading.client import TradingClient
        key, sec = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
        if not (key and sec):
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY first.")
        self.trading = TradingClient(key, sec, paper=True)

    def market(self, book: str, alp_symbol: str, qty: float, side: str):
        """Tagged market order; poll to fill. Returns (filled_qty, avg_price)."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        oid = f"el-{book}-{uuid.uuid4().hex[:10]}"
        o = self.trading.submit_order(MarketOrderRequest(
            symbol=alp_symbol, qty=round(qty, 6),
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC, client_order_id=oid))
        for _ in range(20):
            time.sleep(1)
            o = self.trading.get_order_by_id(o.id)
            status = str(o.status).split(".")[-1].lower()
            if status == "filled":
                return float(o.filled_qty), float(o.filled_avg_price)
            if status in {"canceled", "cancelled", "expired", "rejected"}:
                break
        print(f"[order] {alp_symbol} {side} not filled cleanly (status {o.status})")
        return float(o.filled_qty or 0), float(o.filled_avg_price or 0)


# --- one book, one run ---------------------------------------------------------
def run_book(name: str, spec: dict, broker, gist, now) -> list:
    today = now.strftime("%Y-%m-%d")
    led = L.open_ledger(name, BANKROLL, spec["rules_version"], today)
    tlog = L.load(name, "trades") or {"book": name, "history": []}

    prep, entry_fn, exit_fn = RULES[spec["fn"].__name__]
    frames, last_px, notes = {}, {}, []
    for alp, product in spec["symbols"].items():
        try:
            df = completed_bars(prep(get_candles(
                product, interval=spec["interval"],
                years=spec["history_years"]), spec["params"]), spec["interval"])
            frames[alp] = df
            last_px[alp] = float(df["close"].iloc[-1])
        except Exception as e:                       # noqa: BLE001
            notes.append(f"{alp}: no data ({e})")

    def equity():
        return led["cash"] + sum(p["units"] * last_px.get(s, p["entry"])
                                 for s, p in led["positions"].items())

    if led.get("day_anchor", {}).get("date") != today:
        led["day_anchor"] = {"date": today, "equity": round(equity(), 2)}
    stopped = equity() <= led["day_anchor"]["equity"] * (1 - DAILY_STOP_PCT)

    session_trades, actions = [], []
    for alp, d in frames.items():
        pos = led["positions"].get(alp)
        px = last_px[alp]
        if pos is not None:
            reason = exit_fn(d, spec["params"], pos)
            if reason:
                units, fill_px = pos["units"], px
                if broker is not None and units > 0:
                    fq, fp = broker.market(name, alp, units, "sell")
                    if fq > 0:
                        units, fill_px = fq, fp
                led["cash"] += units * fill_px
                led["positions"].pop(alp, None)
                tr = L.make_trade(
                    date=today, symbol=alp, side="long", units=units,
                    entry_time=pos["entry_time"],
                    exit_time=now.strftime("%Y-%m-%d %H:%M"),
                    intended_entry=pos.get("intended_entry", pos["entry"]),
                    intended_exit=px, entry_price=pos["entry"],
                    exit_price=fill_px, reason=reason)
                session_trades.append(tr)
                actions.append(f"{alp}: EXIT {reason} @ {fill_px:,.2f} "
                               f"pnl {tr['pnl']:+,.2f} slip {tr['slippage']:+,.2f}")
        else:
            sig = entry_fn(d, spec["params"])
            if sig and stopped:
                actions.append(f"{alp}: entry signal SKIPPED — -2% daily stop active")
            elif sig:
                eq = equity()
                units = min(eq * spec["params"]["risk_pct"] / sig["dist"],
                            led["cash"] / px)
                if units * px >= MIN_NOTIONAL:
                    fill_px = px
                    if broker is not None:
                        fq, fp = broker.market(name, alp, units, "buy")
                        if fq <= 0:
                            continue
                        units, fill_px = fq, fp
                    led["cash"] -= units * fill_px
                    led["positions"][alp] = {
                        "units": round(units, 8), "entry": round(fill_px, 4),
                        "intended_entry": round(px, 4),
                        "entry_time": str(d.index[-1])}
                    actions.append(f"{alp}: ENTER long {units:.6f} @ {fill_px:,.2f}")

    # daily row (upsert) + persistence only on material change
    eq = round(equity(), 2)
    day_start = led["day_anchor"]["equity"]
    row = {"date": today, "book_start": round(day_start, 2), "book_end": eq,
           "pnl": round(eq - day_start, 2),
           "pnl_pct": round((eq / day_start - 1) * 100, 2) if day_start else 0.0,
           "trades": len([t for t in tlog["history"] if t["date"] == today])
                     + len(session_trades),
           "open_positions": len(led["positions"]),
           "stopped": "loss_stop" if stopped else None}
    material = bool(session_trades) or not any(
        r["date"] == today for r in led["history"])
    led["history"] = L.upsert_row(led["history"], row)
    led["last_run"] = now.strftime("%Y-%m-%d %H:%M")
    tlog["history"].extend(session_trades)
    tlog["last_run"] = led["last_run"]
    if material:
        L.save(name, led)
        L.save(name, tlog, "trades")
        print(f"[{name}] ledger written")

    # near-live state for the dashboard
    gist.push(f"{name}_live.json", {
        "book": name, "label": spec["label"], "updated_at": led["last_run"],
        "equity": eq, "bankroll": led["bankroll"],
        "pnl": round(eq - led["bankroll"], 2),
        "day_pnl": row["pnl"], "stopped": row["stopped"],
        "positions": [{"symbol": s, **p} for s, p in led["positions"].items()],
        "last_prices": {s: round(p, 2) for s, p in last_px.items()},
        "notes": notes,
    }, force=True)

    label = spec["label"]
    lines = [f"## {label} — book ${day_start:,.2f} → ${eq:,.2f} today "
             f"({row['pnl']:+,.2f})" + ("  ⛔ -2% stop" if stopped else "")]
    lines += [f"- {a}" for a in actions] or ["- no action this run"]
    if notes:
        lines.append(f"- _data issues: {'; '.join(notes)}_")
    return lines


def main():
    ap = argparse.ArgumentParser(description="EdgeLab paper runner")
    ap.add_argument("--dry-run", action="store_true",
                    help="signals only; no orders, no keys needed")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    broker = None if args.dry_run else Broker()
    gist = GistPublisher()

    md = [f"# EdgeLab paper runner — {now:%Y-%m-%d %H:%M} UTC", ""]
    for name, spec in active_books().items():
        md += run_book(name, spec, broker, gist, now) + [""]

    report = "\n".join(md)
    print("\n" + report)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
