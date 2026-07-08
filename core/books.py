"""The book registry — every candidate strategy, its frozen rules, and where it
sits on the gate ladder. Max four books may have `active: True` at once, one
per venue (METHODOLOGY.md: forward sessions are the scarce resource).

`gate_a_file` points at the committed research verdict (ledger/research/*.json,
written by run_research.py) so the dashboard can show why a book was admitted.
"""

from core.engine import run_trend, run_fade
from core.equities import run_momentum

BANKROLL = 10000.0
DAILY_STOP_PCT = 0.02        # -2% daily loss stop (loss caps protect; profit caps don't)

# venue -> (data source, broker, accounting):
#   "crypto":  Coinbase candles, Alpaca paper spot (cash accounting, long-only)
#   "fx":      OANDA candles,   OANDA practice margin (pnl accounting, shorts ok)
#   "equity":  Yahoo daily closes, Alpaca paper stocks (cash accounting,
#              long-only, rebalance-to-weights on Mondays during market hours)
#   "options": Alpaca option quotes/bars, Alpaca paper mleg orders (credit
#              accounting: cash holds the credit, open structures are marked
#              as liabilities; defined-risk spreads only — wings are the stop)

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
    "equity_momentum": {
        "label": "US equity momentum (top 5)",
        "venue": "equity",
        # Book 3, the systematic "video 1": rank a fixed liquid large-cap
        # universe by 63d return, hold the top 5 equal-weight, refresh weekly;
        # cash when SPY < 200d SMA. Gate A: 16/16 grid cells pass at 2x costs;
        # this cell wins 5/5 walk-forward folds (PF 1.94, DD 25.9% stressed)
        # and sits on a passing plateau (lookback 21 and 126 neighbors pass).
        # Known bias, on the record: fixed present-day universe -> survivorship;
        # the forward ledger is the real test. ledger/research/momentum.json.
        "active": True,
        "rules_version": 1,
        "fn": run_momentum,
        "params": dict(lookback=63, top_k=5, rebal_days=5),
        "universe": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
                     "AMD", "AVGO", "CRM", "ADBE", "NFLX", "INTC", "CSCO",
                     "ORCL", "QCOM", "MU", "TXN", "IBM", "PYPL", "JPM", "BAC",
                     "GS", "V", "MA", "XOM", "CVX", "COP", "UNH", "JNJ", "PFE",
                     "MRK", "LLY", "WMT", "COST", "HD", "MCD", "NKE", "SBUX",
                     "DIS", "BA", "CAT", "GE", "F", "T", "VZ"],
        "regime_symbol": "SPY",
        "symbols": {},                       # panel book; universe above
        "interval": "1d",
        "history_years": 2.0,                # live window (research uses 10y)
        "cost_bps": 6.0,
        "dd_budget_pct": 25.0,
        "gate_a_file": "equity_momentum",
    },
    "options_condor": {
        "label": "QQQ 1-DTE iron condor",
        "venue": "options",
        # Book 4 candidate (the "$300 options challenge" videos): sell a
        # defined-risk QQQ condor at 14:30 ET expiring next session; the
        # wings are the only stop. Params are FROZEN from the Gate A verdict
        # (ledger/research/options_condor.json) before activation; the values
        # below are the video's cell as a placeholder until then.
        "active": False,
        "rules_version": 1,
        "fn": None,                          # bespoke runner: run_condor_paper.py
        "params": dict(put_off=2.0, call_off=1.75, width=1, profit_take=0.3,
                       vix_gate=True, risk_pct=0.05),
        "symbols": {"QQQ": "QQQ"},
        "interval": "1d",
        "history_years": 2.4,                # Alpaca option bars begin 2024-02
        "cost_bps": 0.0,                     # costs are $/leg, not bps (options.py)
        "dd_budget_pct": 20.0,
        "gate_a_file": "options_condor",
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
    assert len(act) <= 4, "METHODOLOGY: max four paper books, one per venue"
    venues = [v.get("venue", "crypto") for v in act.values()]
    assert len(venues) == len(set(venues)), "METHODOLOGY: one book per venue"
    return act
