#!/usr/bin/env python3
"""QQQ 1-DTE iron condor — paper runner (books.py: options_condor).

Stateless per run; any run at any time is safe because every action sits
behind an ET time-window guard:

  09:35-15:25  profit-take check (if the frozen cell has one)
  14:20-15:25  entry: sell the next-session condor (if flat for that expiry)
  15:30-15:58  force-close anything expiring today (no pin/assignment risk)
  any time     mark to market, upsert the daily row, push gist state

The wings are the only stop. Sizing: floor(risk_pct of equity / max defined
loss). The -2% daily stop blocks NEW entries only. If a close run is missed
and a position expires on the books, the next run settles it from the
official close of the expiry day — the ledger self-heals.

Orders are Alpaca multi-leg ("mleg") MARKET orders on the paper account —
fine for paper; switch to marketable limits before real money. Options
orders accept exactly one time_in_force: "day".

    python run_condor_paper.py            # normal (needs ALPACA keys)
    python run_condor_paper.py --dry-run  # decisions only, no orders
    python run_condor_paper.py --probe    # read-only account/market checks
"""

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import requests

from core import ledger as L
from core.books import BANKROLL, DAILY_STOP_PCT, BOOKS
from core.gist import GistPublisher
from core.options import ET, MIN_CREDIT, VIX_MAX, cell_strikes

BOOK = "options_condor"
TRADE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

ENTRY_WINDOW = ("14:20", "15:25")
PT_WINDOW = ("09:35", "15:25")
CLOSE_WINDOW = ("15:30", "15:58")


def _headers() -> dict:
    k = os.getenv("ALPACA_API_KEY", "")
    s = os.getenv("ALPACA_SECRET_KEY", "")
    if not (k and s):
        raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY first.")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s,
            "Content-Type": "application/json"}


def _req(method: str, url: str, **kw) -> dict:
    r = requests.request(method, url, headers=_headers(), timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def in_window(now_et: pd.Timestamp, win: tuple[str, str]) -> bool:
    hm = now_et.strftime("%H:%M")
    return win[0] <= hm <= win[1]


# --- market data ----------------------------------------------------------------
def latest_spot() -> float:
    out = _req("GET", f"{DATA_URL}/v2/stocks/QQQ/trades/latest",
               params={"feed": "iex"})
    return float(out["trade"]["p"])


def daily_close(date: str) -> float | None:
    """Official close for settlement self-heal (Yahoo; IEX closes drift)."""
    try:
        import yfinance as yf
        s = yf.download("QQQ", start=date, interval="1d",
                        progress=False, auto_adjust=False)["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = s.index.strftime("%Y-%m-%d")
        return float(s[date]) if date in s.index else None
    except Exception:  # noqa: BLE001
        return None


def latest_quotes(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """symbol -> (bid, ask); missing/one-sided quotes fall back to the trade."""
    out = _req("GET", f"{DATA_URL}/v1beta1/options/quotes/latest",
               params={"symbols": ",".join(symbols)})
    quotes = {}
    for sym, q in (out.get("quotes") or {}).items():
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        quotes[sym] = (bid, ask)
    missing = [s for s in symbols if s not in quotes or quotes[s] == (0.0, 0.0)]
    if missing:
        out = _req("GET", f"{DATA_URL}/v1beta1/options/trades/latest",
                   params={"symbols": ",".join(missing)})
        for sym, t in (out.get("trades") or {}).items():
            px = float(t.get("p") or 0)
            if px:
                quotes[sym] = (px, px)
    return quotes


def vix_prev_close(today: str) -> float | None:
    try:
        import yfinance as yf
        vix = yf.download("^VIX", period="10d", interval="1d",
                          progress=False, auto_adjust=False)["Close"]
        if isinstance(vix, pd.DataFrame):
            vix = vix.iloc[:, 0]
        vix.index = vix.index.strftime("%Y-%m-%d")
        prev = vix[vix.index < today]
        return float(prev.iloc[-1]) if len(prev) else None
    except Exception as e:  # noqa: BLE001 — VIX outage must not kill the run
        print(f"[vix] fetch failed ({e}); entry will be skipped")
        return -1.0          # sentinel: gate can't be evaluated -> don't trade


# --- trading --------------------------------------------------------------------
def next_sessions(today: str) -> list[str]:
    end = (pd.Timestamp(today) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    out = _req("GET", f"{TRADE_URL}/v2/calendar",
               params={"start": today, "end": end})
    return [row["date"] for row in out]


def market_open() -> bool:
    return bool(_req("GET", f"{TRADE_URL}/v2/clock")["is_open"])


def options_level() -> int:
    acct = _req("GET", f"{TRADE_URL}/v2/account")
    return int(acct.get("options_trading_level")
               or acct.get("options_approved_level") or 0)


def find_contract(expiry: str, cp: str, target: float) -> dict | None:
    """Nearest tradable contract to the target strike (QQQ is $1-dense)."""
    out = _req("GET", f"{TRADE_URL}/v2/options/contracts",
               params={"underlying_symbols": "QQQ", "expiration_date": expiry,
                       "type": "put" if cp == "P" else "call",
                       "strike_price_gte": str(target - 2),
                       "strike_price_lte": str(target + 2), "limit": 500})
    rows = [c for c in out.get("option_contracts", []) if c.get("tradable")]
    if not rows:
        return None
    return min(rows, key=lambda c: abs(float(c["strike_price"]) - target))


def place_mleg(legs: list[dict], qty: int, tag: str) -> dict:
    """Market mleg order; poll to a terminal state. Returns the final order."""
    body = {"order_class": "mleg", "qty": str(qty), "type": "market",
            "time_in_force": "day",       # the only TIF options orders accept
            "client_order_id": f"el-{BOOK}-{tag}-{uuid.uuid4().hex[:8]}",
            "legs": legs}
    o = _req("POST", f"{TRADE_URL}/v2/orders", json=body)
    for _ in range(30):
        time.sleep(1)
        o = _req("GET", f"{TRADE_URL}/v2/orders/{o['id']}")
        if o["status"] in ("filled", "canceled", "expired", "rejected"):
            break
    return o


def leg_fill_net(order: dict) -> float | None:
    """Net credit(+)/debit(-) per share from the order's per-leg fills."""
    legs = order.get("legs") or []
    if order.get("status") != "filled" or not legs:
        return None
    net = 0.0
    for leg in legs:
        px = float(leg.get("filled_avg_price") or 0)
        net += px if leg["side"] == "sell" else -px
    return net


# --- read-only pre-flight (account level, contracts, NBBO) ---------------------
def probe():
    """No orders, no ledger writes — proves the whole read path works."""
    p = BOOKS[BOOK]["params"]
    acct = _req("GET", f"{TRADE_URL}/v2/account")
    lvl = int(acct.get("options_trading_level")
              or acct.get("options_approved_level") or 0)
    print(f"[probe] options_trading_level={lvl} "
          f"(approved={acct.get('options_approved_level')}) "
          f"buying_power=${float(acct.get('options_buying_power') or 0):,.0f}")
    if lvl < 3:
        print("[probe] LEVEL < 3 — multi-leg spreads will be rejected. "
              "Enable options level 3 in the paper account settings.")
    today = pd.Timestamp.now(tz=ET).strftime("%Y-%m-%d")
    cal = next_sessions(today)
    expiry = next((d for d in cal if d > today), None)
    spot = latest_spot()
    vix = vix_prev_close(today)
    print(f"[probe] spot QQQ {spot:.2f} · next expiry {expiry} · "
          f"prev VIX close {vix}")
    tsp, tlp, tsc, tlc = cell_strikes(spot, p["put_off"], p["call_off"],
                                      p["width"])
    picks = {}
    for name, cp, target in (("sp", "P", tsp), ("lp", "P", tlp),
                             ("sc", "C", tsc), ("lc", "C", tlc)):
        c = find_contract(expiry, cp, target)
        picks[name] = c
        print(f"[probe] {name} target {target:g} -> "
              f"{c['symbol'] if c else 'NOT FOUND'}")
    if all(picks.values()):
        q = latest_quotes([c["symbol"] for c in picks.values()])
        for name, c in picks.items():
            bid, ask = q.get(c["symbol"], (0.0, 0.0))
            print(f"[probe] {c['symbol']}: bid {bid:.2f} ask {ask:.2f}")
        credit = (q[picks["sp"]["symbol"]][0] + q[picks["sc"]["symbol"]][0]
                  - q[picks["lp"]["symbol"]][1] - q[picks["lc"]["symbol"]][1])
        width_eff = min(float(picks["sp"]["strike_price"])
                        - float(picks["lp"]["strike_price"]),
                        float(picks["lc"]["strike_price"])
                        - float(picks["sc"]["strike_price"]))
        max_loss = (width_eff - credit) * 100
        n = int(10000 * p["risk_pct"] // max_loss) if max_loss > 0 else 0
        print(f"[probe] conservative credit {credit:.2f} · width {width_eff:g} "
              f"· max loss ${max_loss:,.0f}/contract · would size {n}x")
    print("[probe] done — read path OK")


# --- the run --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="EdgeLab condor paper runner")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="read-only account/contract/quote checks; no orders")
    args = ap.parse_args()

    if args.probe:
        probe()
        return

    spec = BOOKS[BOOK]
    if not spec["active"]:
        print(f"{BOOK} is not active in the registry; nothing to do")
        return
    p = spec["params"]
    now = datetime.now(timezone.utc)
    now_et = pd.Timestamp(now).tz_convert(ET)
    today = now_et.strftime("%Y-%m-%d")

    led = L.open_ledger(BOOK, BANKROLL, spec["rules_version"], today)
    tlog = L.load(BOOK, "trades") or {"book": BOOK, "history": []}
    gist = GistPublisher()
    actions, session_trades = [], []

    lvl = None if args.dry_run else options_level()
    if lvl is not None:
        print(f"[account] options trading level: {lvl}")

    # ---- settle anything that expired on the books (missed close run) ----
    for key in list(led["positions"]):
        pos = led["positions"][key]
        if pos["expiry"] >= today:
            continue
        close_px = daily_close(pos["expiry"]) if not args.dry_run else None
        if close_px is None:
            actions.append(f"{key}: expired, close of {pos['expiry']} not "
                           "available yet — will settle next run")
            continue
        sp, lp = pos["strikes"]["sp"], pos["strikes"]["lp"]
        sc, lc = pos["strikes"]["sc"], pos["strikes"]["lc"]
        settle = (max(0.0, sp - close_px) - max(0.0, lp - close_px)
                  + max(0.0, close_px - sc) - max(0.0, close_px - lc))
        c = pos["contracts"]
        led["cash"] -= settle * 100 * c
        tr = L.make_trade(
            date=today, symbol=key, side="short", units=c * 100,
            entry_time=pos["entry_time"],
            exit_time=f"{pos['expiry']} 16:00",
            intended_entry=pos["intended_credit"], intended_exit=settle,
            entry_price=pos["credit"], exit_price=settle, reason="expired")
        session_trades.append(tr)
        actions.append(f"{key}: EXPIRED settle {settle:.2f} "
                       f"pnl {tr['pnl']:+,.2f}")
        led["positions"].pop(key)

    # ---- mark open positions ----
    marks, qcache = {}, {}
    for key, pos in led["positions"].items():
        syms = [leg["symbol"] for leg in pos["legs"]]
        if args.dry_run:
            continue
        q = latest_quotes(syms)
        qcache[key] = q
        mark = 0.0
        for leg in pos["legs"]:
            bid, ask = q.get(leg["symbol"], (0.0, 0.0))
            mark += ask if leg["side"] == "short" else -bid   # cost to close
        marks[key] = mark

    def equity() -> float:
        liab = sum(marks.get(k, pos["credit"]) * 100 * pos["contracts"]
                   for k, pos in led["positions"].items())
        return led["cash"] - liab

    if led.get("day_anchor", {}).get("date") != today:
        led["day_anchor"] = {"date": today, "equity": round(equity(), 2)}
    stopped = equity() <= led["day_anchor"]["equity"] * (1 - DAILY_STOP_PCT)

    open_now = market_open() if not args.dry_run else False

    def close_position(key: str, reason: str):
        """Buy back the shorts always; sell long wings only if they still bid.
        A $0-bid long wing is left to expire — OTM longs die at zero and are
        never auto-exercised, so leaving them carries no assignment risk
        (unlike a market sell into an empty book, which can reject)."""
        pos = led["positions"][key]
        q = qcache.get(key, {})
        legs, left = [], []
        for leg in pos["legs"]:
            if leg["side"] == "short":
                legs.append({"symbol": leg["symbol"], "ratio_qty": "1",
                             "side": "buy", "position_intent": "buy_to_close"})
            elif q.get(leg["symbol"], (0.0, 0.0))[0] > 0:
                legs.append({"symbol": leg["symbol"], "ratio_qty": "1",
                             "side": "sell",
                             "position_intent": "sell_to_close"})
            else:
                left.append(leg["symbol"])
        o = place_mleg(legs, pos["contracts"], "close")
        debit = leg_fill_net(o)
        if debit is None:
            actions.append(f"{key}: close NOT filled (status {o['status']})")
            return
        debit = -debit                        # buying back = net debit
        c = pos["contracts"]
        led["cash"] -= debit * 100 * c
        tr = L.make_trade(
            date=today, symbol=key, side="short", units=c * 100,
            entry_time=pos["entry_time"],
            exit_time=now_et.strftime("%Y-%m-%d %H:%M"),
            intended_entry=pos["intended_credit"],
            intended_exit=marks.get(key, debit),
            entry_price=pos["credit"], exit_price=debit, reason=reason)
        session_trades.append(tr)
        actions.append(f"{key}: CLOSED @ {debit:.2f} ({reason}) "
                       f"pnl {tr['pnl']:+,.2f}"
                       + (f" · zero-bid wings left to expire: "
                          f"{', '.join(left)}" if left else ""))
        led["positions"].pop(key)
        marks.pop(key, None)

    # ---- force-close what expires today (15:30-15:58) ----
    if open_now and in_window(now_et, CLOSE_WINDOW):
        for key in [k for k, v in led["positions"].items()
                    if v["expiry"] == today]:
            close_position(key, "expiry_close")

    # ---- profit take ----
    pt = p.get("profit_take")
    if open_now and pt is not None and in_window(now_et, PT_WINDOW):
        for key in list(led["positions"]):
            pos, mark = led["positions"][key], marks.get(key)
            if mark is not None and mark <= pos["credit"] * (1 - pt):
                close_position(key, "profit_take")

    # ---- entry ----
    if open_now and in_window(now_et, ENTRY_WINDOW) and not stopped:
        cal = next_sessions(today)
        expiry = next((d for d in cal if d > today), None)
        pending = [k for k, v in led["positions"].items()
                   if v["expiry"] > today]
        vix = vix_prev_close(today)
        if pending:
            actions.append("entry skipped — position already open for a "
                           "future expiry")
        elif expiry is None:
            actions.append("entry skipped — no next session in calendar")
        elif p["vix_gate"] and (vix is None or vix < 0 or vix >= VIX_MAX):
            actions.append(f"entry skipped — VIX gate "
                           f"(prev close {vix if vix and vix > 0 else 'n/a'})")
        elif lvl is not None and lvl < 3:
            raise SystemExit(f"options trading level {lvl} < 3 — enable "
                             "options (level 3) on the paper account")
        else:
            spot = latest_spot()
            tsp, tlp, tsc, tlc = cell_strikes(spot, p["put_off"],
                                              p["call_off"], p["width"])
            picks = {}
            for name, cp, target in (("sp", "P", tsp), ("lp", "P", tlp),
                                     ("sc", "C", tsc), ("lc", "C", tlc)):
                c = find_contract(expiry, cp, target)
                if c is None:
                    break
                picks[name] = c
            if len(picks) < 4:
                actions.append(f"entry skipped — contracts missing near "
                               f"{tsp}/{tlp}P {tsc}/{tlc}C {expiry}")
            else:
                syms = {k: c["symbol"] for k, c in picks.items()}
                ks = {k: float(c["strike_price"]) for k, c in picks.items()}
                q = latest_quotes(list(syms.values()))
                credit_cons = (q[syms["sp"]][0] + q[syms["sc"]][0]
                               - q[syms["lp"]][1] - q[syms["lc"]][1])
                width_eff = min(ks["sp"] - ks["lp"], ks["lc"] - ks["sc"])
                max_loss = (width_eff - credit_cons) * 100
                contracts = (int(equity() * p["risk_pct"] // max_loss)
                             if max_loss > 0 else 0)
                if credit_cons < MIN_CREDIT:
                    actions.append(f"entry skipped — thin credit "
                                   f"{credit_cons:.2f} at NBBO")
                elif contracts < 1:
                    actions.append("entry skipped — risk budget under one "
                                   "contract")
                else:
                    legs = [
                        {"symbol": syms["sp"], "ratio_qty": "1", "side": "sell",
                         "position_intent": "sell_to_open"},
                        {"symbol": syms["lp"], "ratio_qty": "1", "side": "buy",
                         "position_intent": "buy_to_open"},
                        {"symbol": syms["sc"], "ratio_qty": "1", "side": "sell",
                         "position_intent": "sell_to_open"},
                        {"symbol": syms["lc"], "ratio_qty": "1", "side": "buy",
                         "position_intent": "buy_to_open"},
                    ]
                    o = place_mleg(legs, contracts, "open")
                    credit = leg_fill_net(o)
                    if credit is None:
                        actions.append(f"entry NOT filled "
                                       f"(status {o['status']})")
                    else:
                        key = f"QQQ {expiry} {ks['sp']:g}/{ks['sc']:g}"
                        led["cash"] += credit * 100 * contracts
                        led["positions"][key] = {
                            "legs": [{"symbol": syms["sp"], "side": "short"},
                                     {"symbol": syms["lp"], "side": "long"},
                                     {"symbol": syms["sc"], "side": "short"},
                                     {"symbol": syms["lc"], "side": "long"}],
                            "strikes": ks, "contracts": contracts,
                            "credit": round(credit, 4),
                            "intended_credit": round(credit_cons, 4),
                            "expiry": expiry,
                            "entry_time": now_et.strftime("%Y-%m-%d %H:%M")}
                        marks[key] = credit
                        actions.append(
                            f"{key}: SOLD {contracts}x condor @ {credit:.2f} "
                            f"credit (NBBO said {credit_cons:.2f}, "
                            f"max loss ${max_loss * contracts:,.0f})")

    # ---- daily row + persistence ----
    eq = round(equity(), 2)
    day_start = led["day_anchor"]["equity"]
    row = {"date": today, "book_start": round(day_start, 2), "book_end": eq,
           "pnl": round(eq - day_start, 2),
           "pnl_pct": round((eq / day_start - 1) * 100, 2) if day_start else 0.0,
           "trades": len([t for t in tlog["history"] if t["date"] == today])
                     + len(session_trades),
           "open_positions": len(led["positions"]),
           "stopped": "loss_stop" if stopped else None}
    prev = next((r for r in led["history"] if r["date"] == today), None)
    material = (bool(session_trades) or prev is None
                or prev.get("book_end") != row["book_end"])
    led["history"] = L.upsert_row(led["history"], row)
    led["last_run"] = now_et.strftime("%Y-%m-%d %H:%M")
    tlog["history"].extend(session_trades)
    tlog["last_run"] = led["last_run"]
    if material and not args.dry_run:
        L.save(BOOK, led)
        L.save(BOOK, tlog, "trades")
        print(f"[{BOOK}] ledger written")

    if not args.dry_run:
        gist.push(f"{BOOK}_live.json", {
            "book": BOOK, "label": spec["label"],
            "updated_at": led["last_run"],
            "equity": eq, "bankroll": led["bankroll"],
            "pnl": round(eq - led["bankroll"], 2),
            "day_pnl": row["pnl"], "stopped": row["stopped"],
            # short structure: negative share-units make the dashboard's
            # units*(mark-entry) uPnL arithmetic come out right
            "positions": [{"symbol": k, "units": -pos["contracts"] * 100,
                           "entry": pos["credit"],
                           "entry_time": pos["entry_time"]}
                          for k, pos in led["positions"].items()],
            "last_prices": {k: round(m, 2) for k, m in marks.items()},
            "notes": [],
        }, force=True)

    lines = [f"## {spec['label']} — book ${day_start:,.2f} → ${eq:,.2f} today "
             f"({row['pnl']:+,.2f})" + ("  ⛔ -2% stop" if stopped else "")]
    lines += [f"- {a}" for a in actions] or ["- no action this run"]
    report = "\n".join(lines)
    print("\n" + report)
    sp_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp_path:
        with open(sp_path, "a", encoding="utf-8") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
