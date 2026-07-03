"""The book registry — every candidate strategy, its frozen rules, and where it
sits on the gate ladder. Max two books may have `active: True` at once
(METHODOLOGY.md: forward sessions are the scarce resource).

`gate_a_file` points at the committed research verdict (ledger/research/*.json,
written by run_research.py) so the dashboard can show why a book was admitted.
"""

from core.engine import run_trend, run_fade

BANKROLL = 10000.0
DAILY_STOP_PCT = 0.02        # -2% daily loss stop (loss caps protect; profit caps don't)

# venue -> (data source, broker, accounting):
#   "crypto": Coinbase candles, Alpaca paper spot (cash accounting, long-only)
#   "fx":     OANDA candles,   OANDA practice margin (pnl accounting, shorts ok)

BOOKS = {
    "crypto_trend": {
        "label": "Crypto 6h trend",
        "venue": "crypto",
        "active": True,
        # v2: SOL admitted after the sweep — passes Gate A with these exact
        # frozen params on all 15 neighboring variants (ledger/research/).
        "rules_version": 2,
        "fn": run_trend,
        "params": dict(entry_lookback=20, exit_lookback=10, atr_period=14,
                       atr_stop_mult=3.0, trend_fast=50, trend_slow=200,
                       risk_pct=0.01, allow_short=False),
        # Admission is per symbol at these frozen params: BTC and SOL pass,
        # ETH failed at 2x costs (2/5 walk-forward folds) and can reapply.
        "symbols": {"BTC/USD": "BTC-USD", "SOL/USD": "SOL-USD"},
        "interval": "6h",
        "history_years": 4.0,
        "cost_bps": 20.0,            # modeled 1x cost; Gate A judges at 2x
        "dd_budget_pct": 20.0,
        "gate_a_file": "crypto_trend",
    },
    "fx_fade_1h": {
        "label": "FX 1h fade (AUD/USD)",
        "venue": "fx",
        # Book 2, admitted from the FX sweep: AUD_USD H1 fade passes Gate A at
        # 2x MEASURED spread costs with 4/5 walk-forward folds, a profitable
        # final fold, and a passing parameter plateau (sma 14/20/30 all pass
        # at band_k=2.5, max_hold=12). Verdict: ledger/research/fx_sweep.json.
        "active": True,
        "rules_version": 1,
        "fn": run_fade,
        "params": dict(sma_period=20, band_k=2.5, stop_k=4.0, trend_ema=200,
                       max_hold=12, risk_pct=0.01, allow_short=True,
                       max_leverage=5.0),
        "symbols": {"AUD/USD": "AUD_USD"},   # display -> oanda instrument
        "interval": "H1",
        "history_years": 0.5,                # live eval window (research uses 3y)
        "cost_bps": 2.7,                     # measured spread + slippage buffer
        "dd_budget_pct": 15.0,
        "gate_a_file": "fx_fade_1h",
    },
    # Candidates — researched by run_research.py; a candidate becomes active
    # only by passing Gate A (and only if a book slot is free).
    "crypto_trend_1h": {
        "label": "Crypto 1h trend (candidate)",
        "active": False,
        "rules_version": 1,
        "fn": run_trend,
        "params": dict(entry_lookback=48, exit_lookback=24, atr_period=14,
                       atr_stop_mult=3.0, trend_fast=50, trend_slow=200,
                       risk_pct=0.01, allow_short=False),
        "symbols": {"BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD"},
        "interval": "1h",
        "history_years": 3.0,
        "cost_bps": 20.0,
        "dd_budget_pct": 15.0,
        "gate_a_file": "crypto_trend_1h",
    },
    "crypto_fade_1h": {
        "label": "Crypto 1h fade (candidate)",
        "active": False,
        "rules_version": 1,
        "fn": run_fade,
        "params": dict(sma_period=20, band_k=2.0, stop_k=3.5, trend_ema=200,
                       max_hold=24, risk_pct=0.01, allow_short=False),
        "symbols": {"BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD"},
        "interval": "1h",
        "history_years": 3.0,
        "cost_bps": 20.0,
        "dd_budget_pct": 15.0,
        "gate_a_file": "crypto_fade_1h",
    },
    "crypto_fade_15m": {
        "label": "Crypto 15m fade (candidate)",
        "active": False,
        "rules_version": 1,
        "fn": run_fade,
        "params": dict(sma_period=20, band_k=2.0, stop_k=3.5, trend_ema=200,
                       max_hold=32, risk_pct=0.01, allow_short=False),
        "symbols": {"BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD"},
        "interval": "15m",
        "history_years": 1.5,
        "cost_bps": 20.0,
        "dd_budget_pct": 15.0,
        "gate_a_file": "crypto_fade_15m",
    },
}


def active_books() -> dict:
    act = {k: v for k, v in BOOKS.items() if v["active"]}
    assert len(act) <= 2, "METHODOLOGY: max two concurrent paper books"
    return act
