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
import sys
import time
import uuid
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):        # Windows consoles default cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from core import ledger as L
from core.books import BANKROLL, DAILY_STOP_PCT, active_books
from core.data import completed_bars, get_candles
from core.engine import atr, ema
from core.equities import get_close_panel, momentum_targets
from core.gist import GistPublisher
from core.oanda import OandaBroker, get_fx_candles

MIN_NOTIONAL = 10.0          # Alpaca minimum crypto order ~$10
MIN_EQ_DELTA = 50.0          # skip equity rebalance legs smaller than this


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
    d["upper"] = d["sma"] + p["band_k"] * d["std"]
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
        return {"dist": p["stop_k"] * float(r["std"]), "side": 1}
    if p.get("allow_short") and r["close"] > r["upper"] and r["close"] < r["ema"]:
        return {"dist": p["stop_k"] * float(r["std"]), "side": -1}
    return None


def fade_exit(d, p, pos) -> str | None:
    r = d.iloc[-1]
    if np.isnan(r["std"]) or r["std"] <= 0:
        return None
    s = pos.get("side", 1)
    entry_t = pd.Timestamp(pos["entry_time"])
    stop = pos["entry"] - s * p["stop_k"] * float(r["std"])
    if (s == 1 and float(r["low"]) <= stop) or (s == -1 and float(r["high"]) >= stop):
        return "stop"
    if (s == 1 and float(r["high"]) >= float(r["sma"])) or \
       (s == -1 and float(r["low"]) <= float(r["sma"])):
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

    def market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)

    def market_stock(self, book: str, symbol: str, qty: float, side: str):
        """Fractional stock market order (DAY tif required for fractional)."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        oid = f"el-{book}-{uuid.uuid4().hex[:10]}"
        o = self.trading.submit_order(MarketOrderRequest(
            symbol=symbol, qty=round(qty, 4),
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, client_order_id=oid))
        for _ in range(20):
            time.sleep(1)
            o = self.trading.get_order_by_id(o.id)
            status = str(o.status).split(".")[-1].lower()
            if status == "filled":
                return float(o.filled_qty), float(o.filled_avg_price)
            if status in {"canceled", "cancelled", "expired", "rejected"}:
                break
        print(f"[order] {symbol} {side} not filled cleanly (status {o.status})")
        return float(o.filled_qty or 0), float(o.filled_avg_price or 0)


# --- the equity panel book (rebalance-to-weights, weekly) ----------------------
def run_equity_book(name: str, spec: dict, broker, gist, now) -> list:
    """Cash accounting like crypto, but the unit of decision is the whole
    panel: on Mondays during market hours, rebalance to the momentum target
    weights; every other run just marks to market. The -2% daily stop blocks
    the BUY legs of a rebalance, never the sells."""
    p = spec["params"]
    today = now.strftime("%Y-%m-%d")
    led = L.open_ledger(name, BANKROLL, spec["rules_version"], today)
    tlog = L.load(name, "trades") or {"book": name, "history": []}

    panel = get_close_panel(spec["universe"] + [spec["regime_symbol"]],
                            spec["history_years"])
    notes = []
    if spec["regime_symbol"] not in panel.columns or \
            panel.shape[1] - 1 < 0.9 * len(spec["universe"]):
        return [f"## {spec['label']} — SKIPPED",
                f"panel incomplete ({panel.shape[1]}/{len(spec['universe']) + 1} "
                "columns); not trading a mangled universe"]
    spy = panel.pop(spec["regime_symbol"])
    last_px = {s: float(panel[s].dropna().iloc[-1]) for s in panel.columns
               if panel[s].notna().any()}

    def equity():
        return led["cash"] + sum(pos["units"] * last_px.get(s, pos["entry"])
                                 for s, pos in led["positions"].items())

    if led.get("day_anchor", {}).get("date") != today:
        led["day_anchor"] = {"date": today, "equity": round(equity(), 2)}
    stopped = equity() <= led["day_anchor"]["equity"] * (1 - DAILY_STOP_PCT)

    session_trades, actions = [], []
    rebalanced = False
    is_rebal_day = now.weekday() == 0 or not led.get("last_rebalance")
    already = led.get("last_rebalance") == today
    market_open = True if broker is None else broker.market_open()

    if is_rebal_day and not already and market_open:
        targets = momentum_targets(panel, spy, lookback=p["lookback"],
                                   top_k=p["top_k"])
        eq = equity()
        legs = []
        for s in set(led["positions"]) | set(targets):
            px = last_px.get(s)
            if px is None:
                continue
            cur = led["positions"].get(s, {}).get("units", 0.0)
            delta = eq * targets.get(s, 0.0) / px - cur
            if abs(delta * px) >= MIN_EQ_DELTA:
                legs.append((s, delta, px))
        legs.sort(key=lambda leg: leg[1])          # sells first, frees cash
        for s, delta, px in legs:
            side = "sell" if delta < 0 else "buy"
            if side == "buy" and stopped:
                actions.append(f"{s}: buy leg SKIPPED — -2% daily stop active")
                continue
            qty, fill_px = abs(delta), px
            if broker is not None:
                fq, fp = broker.market_stock(name, s, qty, side)
                if fq <= 0:
                    actions.append(f"{s}: {side} not filled")
                    continue
                qty, fill_px = fq, fp
            pos = led["positions"].get(s)
            if side == "buy":
                if qty * fill_px > led["cash"]:
                    qty = max(led["cash"], 0.0) / fill_px
                    if qty * fill_px < MIN_EQ_DELTA:
                        continue
                led["cash"] -= qty * fill_px
                if pos:                              # add: weighted avg entry
                    tot = pos["units"] + qty
                    pos["entry"] = round((pos["units"] * pos["entry"]
                                          + qty * fill_px) / tot, 4)
                    pos["units"] = round(tot, 4)
                else:
                    led["positions"][s] = {
                        "units": round(qty, 4), "entry": round(fill_px, 4),
                        "intended_entry": round(px, 4), "side": 1,
                        "entry_time": today}
                actions.append(f"{s}: BUY {qty:,.4g} @ {fill_px:,.2f}")
            else:
                qty = min(qty, pos["units"]) if pos else 0.0
                if qty <= 0:
                    continue
                led["cash"] += qty * fill_px
                pos["units"] = round(pos["units"] - qty, 4)
                if pos["units"] * fill_px < 1.0:     # position closed: log it
                    tr = L.make_trade(
                        date=today, symbol=s, side="long",
                        units=qty + pos["units"],
                        entry_time=pos["entry_time"],
                        exit_time=now.strftime("%Y-%m-%d %H:%M"),
                        intended_entry=pos.get("intended_entry", pos["entry"]),
                        intended_exit=px, entry_price=pos["entry"],
                        exit_price=fill_px, reason="rotate")
                    session_trades.append(tr)
                    led["positions"].pop(s, None)
                    actions.append(f"{s}: EXIT rotate @ {fill_px:,.2f} "
                                   f"pnl {tr['pnl']:+,.2f}")
                else:
                    actions.append(f"{s}: TRIM {qty:,.4g} @ {fill_px:,.2f}")
        led["last_rebalance"] = today
        rebalanced = True
        if not legs:
            actions.append("rebalance checked — already at target weights")
    elif is_rebal_day and not already:
        actions.append("rebalance day, but market closed — waiting")

    eq = round(equity(), 2)
    day_start = led["day_anchor"]["equity"]
    row = {"date": today, "book_start": round(day_start, 2), "book_end": eq,
           "pnl": round(eq - day_start, 2),
           "pnl_pct": round((eq / day_start - 1) * 100, 2) if day_start else 0.0,
           "trades": len([t for t in tlog["history"] if t["date"] == today])
                     + len(session_trades),
           "open_positions": len(led["positions"]),
           "stopped": "loss_stop" if stopped else None}
    material = bool(session_trades) or rebalanced or not any(
        r["date"] == today for r in led["history"])
    led["history"] = L.upsert_row(led["history"], row)
    led["last_run"] = now.strftime("%Y-%m-%d %H:%M")
    tlog["history"].extend(session_trades)
    tlog["last_run"] = led["last_run"]
    if material:
        L.save(name, led)
        L.save(name, tlog, "trades")
        print(f"[{name}] ledger written")

    gist.push(f"{name}_live.json", {
        "book": name, "label": spec["label"], "updated_at": led["last_run"],
        "equity": eq, "bankroll": led["bankroll"],
        "pnl": round(eq - led["bankroll"], 2),
        "day_pnl": row["pnl"], "stopped": row["stopped"],
        "positions": [{"symbol": s, **pos}
                      for s, pos in led["positions"].items()],
        "last_prices": {s: round(v, 2) for s, v in last_px.items()
                        if s in led["positions"]},
        "notes": notes,
    }, force=True)

    lines = [f"## {spec['label']} — book ${day_start:,.2f} → ${eq:,.2f} today "
             f"({row['pnl']:+,.2f})" + ("  ⛔ -2% stop" if stopped else "")]
    lines += [f"- {a}" for a in actions] or ["- no action this run"]
    return lines


# --- one book, one run ---------------------------------------------------------
def run_book(name: str, spec: dict, broker, gist, now) -> list:
    """Venue differences handled here:
    crypto — spot cash accounting (cash moves on fills), long-only, float qty;
    fx     — margin accounting (cash fixed, positions carry pnl), signed int
             units, shorts allowed. Both share the same ledger/gist plumbing."""
    venue = spec.get("venue", "crypto")
    today = now.strftime("%Y-%m-%d")
    led = L.open_ledger(name, BANKROLL, spec["rules_version"], today)
    tlog = L.load(name, "trades") or {"book": name, "history": []}

    prep, entry_fn, exit_fn = RULES[spec["fn"].__name__]
    frames, last_px, notes = {}, {}, []
    for disp, product in spec["symbols"].items():
        try:
            if venue == "fx":
                raw, _spread = get_fx_candles(product, spec["interval"],
                                              spec["history_years"])
                df = prep(raw, spec["params"])       # OANDA returns complete bars
            else:
                df = completed_bars(prep(get_candles(
                    product, interval=spec["interval"],
                    years=spec["history_years"]), spec["params"]),
                    spec["interval"])
            frames[disp] = df
            last_px[disp] = float(df["close"].iloc[-1])
        except Exception as e:                       # noqa: BLE001
            notes.append(f"{disp}: no data ({e})")

    def equity():
        if venue == "fx":
            return led["cash"] + sum(
                p["units"] * (last_px.get(s, p["entry"]) - p["entry"])
                for s, p in led["positions"].items())
        return led["cash"] + sum(p["units"] * last_px.get(s, p["entry"])
                                 for s, p in led["positions"].items())

    if led.get("day_anchor", {}).get("date") != today:
        led["day_anchor"] = {"date": today, "equity": round(equity(), 2)}
    stopped = equity() <= led["day_anchor"]["equity"] * (1 - DAILY_STOP_PCT)

    session_trades, actions = [], []
    for disp, d in frames.items():
        pos = led["positions"].get(disp)
        px = last_px[disp]
        instrument = spec["symbols"][disp]
        if pos is not None:
            reason = exit_fn(d, spec["params"], pos)
            if reason:
                units, fill_px = pos["units"], px
                if broker is not None:
                    if venue == "fx":
                        fq, fp = broker.market(name, instrument, -int(units))
                    else:
                        fq, fp = broker.market(name, instrument, units, "sell")
                    if fq != 0:
                        fill_px = fp
                if venue == "fx":
                    led["cash"] += units * (fill_px - pos["entry"])
                    side = "long" if units > 0 else "short"
                    tr_units = abs(units)
                else:
                    led["cash"] += units * fill_px
                    side, tr_units = "long", units
                led["positions"].pop(disp, None)
                tr = L.make_trade(
                    date=today, symbol=disp, side=side, units=tr_units,
                    entry_time=pos["entry_time"],
                    exit_time=now.strftime("%Y-%m-%d %H:%M"),
                    intended_entry=pos.get("intended_entry", pos["entry"]),
                    intended_exit=px, entry_price=pos["entry"],
                    exit_price=fill_px, reason=reason)
                session_trades.append(tr)
                actions.append(f"{disp}: EXIT {reason} @ {fill_px:,.4f} "
                               f"pnl {tr['pnl']:+,.2f} slip {tr['slippage']:+,.2f}")
        else:
            sig = entry_fn(d, spec["params"])
            if sig and stopped:
                actions.append(f"{disp}: entry signal SKIPPED — -2% daily stop active")
            elif sig:
                eq = equity()
                side = sig.get("side", 1)
                units = eq * spec["params"]["risk_pct"] / sig["dist"]
                max_lev = spec["params"].get("max_leverage", 1.0)
                units = min(units, eq * max_lev / px)
                if venue == "crypto":
                    units = min(units, led["cash"] / px)
                if units * px < MIN_NOTIONAL:
                    continue
                fill_px = px
                if venue == "fx":
                    units = side * int(units)
                    if units == 0:
                        continue
                    if broker is not None:
                        fq, fp = broker.market(name, instrument, units)
                        if fq == 0:
                            actions.append(f"{disp}: entry not filled "
                                           "(market closed?)")
                            continue
                        units, fill_px = fq, fp
                else:
                    if broker is not None:
                        fq, fp = broker.market(name, instrument, units, "buy")
                        if fq <= 0:
                            continue
                        units, fill_px = fq, fp
                    led["cash"] -= units * fill_px
                led["positions"][disp] = {
                    "units": round(units, 8), "entry": round(fill_px, 5),
                    "intended_entry": round(px, 5), "side": side,
                    "entry_time": str(d.index[-1])}
                word = "long" if side == 1 else "short"
                actions.append(f"{disp}: ENTER {word} {abs(units):,.6g} "
                               f"@ {fill_px:,.4f}")

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
    gist = GistPublisher()

    # one broker per venue, created lazily; a venue with missing keys skips
    # loudly instead of poisoning the ledger with pretend fills
    KEYS = {"crypto": ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"),
            "fx": ("OANDA_API_KEY", "OANDA_ACCOUNT_ID"),
            "equity": ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
    MAKERS = {"crypto": Broker, "fx": OandaBroker, "equity": Broker}
    brokers: dict = {}

    md = [f"# EdgeLab paper runner — {now:%Y-%m-%d %H:%M} UTC", ""]
    errors = 0
    for name, spec in active_books().items():
        venue = spec.get("venue", "crypto")
        if args.dry_run:
            broker = None
        else:
            if venue not in brokers:
                missing = [k for k in KEYS[venue] if not os.getenv(k)]
                brokers[venue] = None if missing else MAKERS[venue]()
            broker = brokers[venue]
            if broker is None:
                md += [f"## {spec['label']} — SKIPPED",
                       f"missing secrets: {', '.join(k for k in KEYS[venue] if not os.getenv(k))}",
                       ""]
                continue
        runner = run_equity_book if venue == "equity" else run_book
        try:
            md += runner(name, spec, broker, gist, now) + [""]
        except Exception as e:  # noqa: BLE001 — one book must not kill the rest
            errors += 1
            md += [f"## {spec['label']} — ERROR",
                   f"{type(e).__name__}: {e}",
                   "book skipped this run; other books and the status build "
                   "continue", ""]
            print(f"[error] {name}: {type(e).__name__}: {e}")

    report = "\n".join(md)
    print("\n" + report)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    if errors:
        # healthy books' ledgers are already written/committed by the workflow;
        # exit nonzero so the failure email still fires for the broken book
        raise SystemExit(f"{errors} book(s) errored this run")


if __name__ == "__main__":
    main()
