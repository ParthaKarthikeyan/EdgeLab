"""Offline tests for the measurement rig and engines — synthetic data only, no
network, no keys. `python -m pytest tests -q` must pass before anything runs
on a schedule.
"""

import numpy as np
import pandas as pd
import pytest

from core import ledger as L
from core.engine import metrics, run_channel, run_fade, run_trend, walk_forward
from core.gates import (GATE_B_FAST_SESSIONS, GATE_B_FAST_TRADES,
                        GATE_B_SESSIONS, gate_a, gate_b, gate_c, book_status)


# --- fixtures -----------------------------------------------------------------
def make_df(closes, start="2024-01-01", freq="6h", spread=0.5):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c + spread, "low": c - spread,
                         "close": c, "volume": 1000.0}, index=idx)


def trending_up(n=600, start=100.0, drift=0.4, seed=7):
    rng = np.random.default_rng(seed)
    return start + drift * np.arange(n) + rng.normal(0, 1.0, n).cumsum() * 0.3


def choppy(n=600, level=100.0, seed=7):
    rng = np.random.default_rng(seed)
    return level + np.sin(np.arange(n) / 5.0) * 3 + rng.normal(0, 0.4, n)


# --- engine sanity -------------------------------------------------------------
def test_trend_profits_in_trend():
    res = run_trend(make_df(trending_up()), cost_bps=20)
    assert res["trades"], "trend engine never traded a clean uptrend"
    assert res["end"] > 10000

def test_trend_flat_without_regime():
    res = run_trend(make_df(choppy()), cost_bps=20)
    m = metrics(res, 10000)
    assert m["max_dd"] < 10, "chop should not produce deep drawdown (regime filter)"

def test_costs_hurt():
    df = make_df(trending_up())
    cheap = run_trend(df, cost_bps=10)["end"]
    dear = run_trend(df, cost_bps=80)["end"]
    assert dear < cheap, "raising costs must lower net equity"

def test_fade_trades_dips_in_uptrend():
    closes = trending_up(800, drift=0.15)
    closes[300:305] -= 8          # inject a sharp dip below the band
    res = run_fade(make_df(closes, freq="1h"), cost_bps=20)
    assert res["trades"], "fade engine never bought a dip in an uptrend"

def test_no_short_on_spot():
    res = run_fade(make_df(choppy(), freq="1h"), cost_bps=20, allow_short=False)
    assert all(t["side"] == 1 for t in res["trades"])

def test_channel_profits_in_trend():
    res = run_channel(make_df(trending_up()), cost_bps=20)
    assert res["trades"], "channel engine never traded a clean uptrend"
    assert res["end"] > 10000

def test_adx_gate_reduces_trades():
    df = make_df(choppy(800))
    open_gate = run_channel(df, cost_bps=20, adx_min=0.0)
    tight = run_channel(df, cost_bps=20, adx_min=25.0)
    assert len(tight["trades"]) <= len(open_gate["trades"])

def test_momentum_regime_gate_goes_to_cash():
    from core.equities import run_momentum, momentum_targets
    rng = np.random.default_rng(7)
    n, dates = 700, pd.date_range("2022-01-03", periods=700, freq="B")
    up = pd.DataFrame({f"S{i}": 100 * np.exp(np.cumsum(
        rng.normal(0.001 * (i + 1), 0.01, n))) for i in range(6)}, index=dates)
    spy_up = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0006, 0.008, n))),
                       index=dates)
    res = run_momentum(up, spy_up, lookback=63, top_k=3, rebal_days=5,
                       cost_bps=12.0)
    assert res["trades"] and res["end"] > 10000
    # SPY collapsing below its 200d SMA must force all-cash targets
    spy_down = pd.Series(np.linspace(200, 80, n), index=dates)
    assert momentum_targets(up, spy_down, lookback=63, top_k=3) == {}
    tgt = momentum_targets(up, spy_up, lookback=63, top_k=3)
    assert len(tgt) <= 3 and abs(sum(tgt.values()) - 1.0) < 1e-9

def test_volume_filter_reduces_fade_trades():
    closes = trending_up(800, drift=0.15)
    closes[300:305] -= 8
    df = make_df(closes, freq="1h")
    base = run_fade(df, cost_bps=20)
    filtered = run_fade(df, cost_bps=20, vol_mult=1.5)
    assert len(filtered["trades"]) <= len(base["trades"])

def test_walk_forward_shapes():
    folds = walk_forward(make_df(trending_up(1000)), run_trend, {}, 40, n_folds=5)
    assert len(folds) == 5
    assert all("profit_factor" in f for f in folds)


# --- gates ---------------------------------------------------------------------
def _m(ret, pf, trades=50):
    return {"ret": ret, "profit_factor": pf, "trades": trades,
            "end": 10000 * (1 + ret / 100), "win_rate": 50, "max_dd": 5}

def test_gate_a_pass_and_fail():
    good = gate_a(_m(20, 1.5), [_m(5, 1.2)] * 3 + [_m(-2, 0.8)] * 2)
    assert good.passed
    bad = gate_a(_m(20, 1.5), [_m(-1, 0.9)] * 3 + [_m(5, 1.3)] * 2)
    assert not bad.passed, "walk-forward minority must fail Gate A"
    thin = gate_a(_m(20, 1.5, trades=10), [_m(5, 1.2)] * 5)
    assert not thin.passed, "too few trades must fail Gate A"

def _ledger(n_days, daily_pnl, bankroll=10000.0):
    rows, eq = [], bankroll
    for i in range(n_days):
        start = eq
        eq += daily_pnl
        rows.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                     "book_start": start, "book_end": eq, "pnl": daily_pnl,
                     "pnl_pct": 0, "trades": 1, "open_positions": 0,
                     "stopped": None})
    return {"book": "x", "bankroll": bankroll, "deployment_date": rows[0]["date"],
            "history": rows}

def test_gate_b_needs_sessions_and_profit():
    short = gate_b(_ledger(GATE_B_SESSIONS - 5, 10), [], 20)
    assert not short.passed
    losing = gate_b(_ledger(GATE_B_SESSIONS, -5), [], 20)
    assert not losing.passed
    ok = gate_b(_ledger(GATE_B_SESSIONS, 10), [], 20)
    assert ok.passed

def test_gate_b_fast_path_via_trades():
    trades = [{"pnl": 5.0, "slippage": 0.1}] * (GATE_B_FAST_TRADES + 5)
    fast = gate_b(_ledger(GATE_B_FAST_SESSIONS, 10), trades, 20)
    assert fast.passed, "enough sessions + enough trades should clear gate B"
    few_trades = gate_b(_ledger(GATE_B_FAST_SESSIONS, 10),
                        trades[:GATE_B_FAST_TRADES // 2], 20)
    assert not few_trades.passed, "too few trades is not evidence"
    too_short = gate_b(_ledger(GATE_B_FAST_SESSIONS - 2, 10), trades, 20)
    assert not too_short.passed, "trade count cannot excuse too few sessions"

def test_gate_b_reconciliation_gap():
    trades = [{"pnl": 100.0, "slippage": 20.0}] * 5   # 20% gap
    r = gate_b(_ledger(GATE_B_SESSIONS, 10), trades, 20)
    assert not r.passed, "20% slippage-vs-gross must fail reconciliation"

def test_gate_c_rolling_average():
    assert gate_c(_ledger(70, 12), 20).passed
    assert not gate_c(_ledger(70, -1), 20).passed

def test_book_status_stages():
    st = book_status(None, [], {"passed": False}, 20)
    assert st["stage"] == "gate_a"
    st = book_status(_ledger(3, 5), [], {"passed": True}, 20)
    assert st["stage"] == "paper"
    st = book_status(_ledger(70, 12), [], {"passed": True}, 20)
    assert st["stage"] == "promoted"


# --- ledger contract -------------------------------------------------------------
def test_rules_bump_restamps_forward_clock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    led = L.open_ledger("t", 10000, 1, "2026-07-01")
    L.save("t", led)
    led2 = L.open_ledger("t", 10000, 2, "2026-07-03")
    assert led2["deployment_date"] == "2026-07-03"

def test_upsert_replaces_same_day():
    h = L.upsert_row([], {"date": "2026-07-01", "pnl": 1})
    h = L.upsert_row(h, {"date": "2026-07-01", "pnl": 2})
    assert len(h) == 1 and h[0]["pnl"] == 2

def test_trade_slippage_sign():
    t = L.make_trade(date="2026-07-01", symbol="BTC/USD", side="long", units=1,
                     entry_time="a", exit_time="b",
                     intended_entry=100, intended_exit=110,
                     entry_price=101, exit_price=109, reason="x")
    assert t["pnl"] == pytest.approx(8.0)
    assert t["slippage"] == pytest.approx(2.0), "worse fills => positive slippage"
