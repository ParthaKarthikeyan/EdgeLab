"""Offline tests for the condor engine — synthetic day records, no network."""

from core.options import (all_candidate_symbols, cell_strikes, occ,
                          run_condor)

# spot 500, offsets 2.0/1.75, width 1 -> strikes 490/489 puts, 509/510 calls
CELL = dict(put_off=2.0, call_off=1.75, width=1, profit_take=None,
            vix_gate=True)


def run_cell(days, **overrides):
    return run_condor(days, slip=0.01, fee=0.05, **{**CELL, **overrides})


def make_day(date="2026-07-06", expiry="2026-07-07", spot=500.0,
             vix=15.0, expiry_close=500.0, marks=(0.30, 0.10, 0.28, 0.08),
             paths=None):
    sp, lp, sc, lc = cell_strikes(spot, 2.0, 1.75, 1)
    keys = [occ("QQQ", expiry, "P", sp), occ("QQQ", expiry, "P", lp),
            occ("QQQ", expiry, "C", sc), occ("QQQ", expiry, "C", lc)]
    paths = paths or [(), (), (), ()]
    return {"date": date, "expiry": expiry, "spot": spot, "vix_prev": vix,
            "expiry_close": expiry_close,
            "legs": {k: {"entry_mark": m, "path": list(p)}
                     for k, m, p in zip(keys, marks, paths)}}


def test_occ_symbol_format():
    assert occ("QQQ", "2026-07-09", "P", 489) == "QQQ260709P00489000"
    assert occ("QQQ", "2026-07-09", "C", 510.5) == "QQQ260709C00510500"


def test_cell_strikes_on_dollar_grid():
    sp, lp, sc, lc = cell_strikes(500.0, 2.0, 1.75, 1)
    assert (sp, lp, sc, lc) == (490.0, 489.0, 509.0, 510.0)


def test_candidate_symbols_cover_all_cells():
    syms = all_candidate_symbols(500.0, "2026-07-07")
    for k in (490, 489, 509, 510):
        cp = "P" if k < 500 else "C"
        assert occ("QQQ", "2026-07-07", cp, k) in syms


def test_expiry_otm_keeps_credit():
    # credit_mid 0.40, conservative credit 0.36; settles worthless
    res = run_cell([make_day()])
    assert len(res["trades"]) == 1
    t = res["trades"][0]
    # max loss (1 - 0.36) * 100 = 64 -> $500 budget -> 7 contracts
    assert t["contracts"] == 7
    assert abs(t["pnl"] - 7 * (0.36 * 100 - 8 * 0.05)) < 1e-6


def test_gap_through_put_wing_is_capped():
    res = run_cell([make_day(expiry_close=470.0)])
    t = res["trades"][0]
    # settle pinned at the width: (0.36 - 1.00) * 100 - 0.40 per contract
    assert abs(t["pnl"] - 7 * ((0.36 - 1.0) * 100 - 0.4)) < 1e-6
    assert t["pnl"] < 0


def test_profit_take_exits_early():
    # condor mark decays 0.40 -> 0.10 intraday; pt50 target is 0.20
    ts = "2026-07-07T10:00:00-04:00"
    paths = [[(ts, 0.05)], [(ts, 0.01)], [(ts, 0.07)], [(ts, 0.01)]]
    day = make_day(expiry_close=470.0, paths=paths)  # bad close never reached
    res = run_cell([day], profit_take=0.5)
    t = res["trades"][0]
    assert t["reason"] == "profit_take"
    # buys back at mark 0.10 + 4x slip: (0.36 - 0.14) * 100 - 0.40 each
    assert abs(t["pnl"] - 7 * ((0.36 - 0.14) * 100 - 0.4)) < 1e-6


def test_vix_gate_skips_hot_days():
    res = run_cell([make_day(vix=25.0)])
    assert not res["trades"]
    assert res["skips"].get("vix_gate") == 1
    ungated = run_cell([make_day(vix=25.0)], vix_gate=False)
    assert len(ungated["trades"]) == 1


def test_thin_credit_is_skipped():
    res = run_cell([make_day(marks=(0.05, 0.02, 0.04, 0.02))])
    assert not res["trades"]
    assert res["skips"].get("thin_credit") == 1
