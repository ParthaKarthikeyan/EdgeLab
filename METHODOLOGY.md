# EdgeLab — Methodology

The goal being tested: **average +$100/day on a $10k book** (~+1%/day, measured as a
rolling 21-session average — monthly, not daily). This document is the contract; the
code in `core/gates.py` enforces it.

## The math, stated up front

+1%/day compounds to ~12x/year. To average +1%/day with a -2% daily loss stop you
need an annualized Sharpe of roughly 7–8; the best quant funds in history sustain
2–3. So the honest project is not "hit $100/day" — it is:

> find any strategy with positive daily expectancy after costs, prove it FORWARD,
> then scale it. The $100/day is a milestone to grow into, not a parameter to set.

Consequences baked into the design:

- **No daily profit caps.** Profit targets amputate the right tail of the P&L
  distribution, and for most strategies with edge, the right tail IS the edge.
  The scoreboard is the monthly average.
- **Daily loss stop stays** (-2% of book): loss caps genuinely protect a book;
  profit caps mostly don't.
- **Venues chosen by cost structure**: deep books and single-digit-bps effective
  costs only — BTC/ETH crypto (24/7), majors FX at measured spread, and liquid
  US large caps on daily bars (~1–3bps spread, zero commission). No low-float
  stocks, no premarket, no thin-book execution environments. Quick trading is a
  war against the spread; we don't volunteer for the hardest front.
- **Options are admitted under a stricter rule**: defined-risk credit structures
  only (the long wings are the stop — no naked short options, ever), on
  penny-quoted, deep-OPRA underlyings (QQQ/SPY class). Costs are modeled in
  $/leg (spread + regulatory fees), not bps, and Gate A still judges at 2x.

## The gate ladder (enforced by `core/gates.py`)

Every strategy is a plug-in that must climb:

| Gate | What it proves | Pass criteria (coded, not vibes) |
|---|---|---|
| **A — Research** | Edge survives costs out-of-sample | Profitable (ret > 0 AND PF > 1) in >50% of 5 sequential walk-forward folds **at 2x modeled costs**, and net-positive over the full period at 2x costs. A candidate that only works at 1x is fragile. |
| **B — Paper** | Edge survives reality | Enough forward evidence on a $10k paper book — **10 sessions, or 7+ sessions with 20+ closed trades**: total P&L > 0 **on actual broker fills**, max drawdown inside the book's stated budget, reconciliation gap < 10% of gross P&L. |
| **C — Scale** | The average is what it claims | ≥ 21 sessions (a full rolling window) with the rolling-21 $/day average positive and the drawdown still in budget. After go-live this gate is judged on the LIVE ledger and controls whether size grows. |

*(Gate B amended 2026-07-04 from 30/14/40 sessions/trades: hard deadline — validation
completes over the 10 trading sessions 2026-07-06 → 2026-07-17, live trading starts
2026-07-20. Ten sessions is thin evidence; the compensating control is that go-live
size is small and Gate C, now measured on live results, gates every increase.)*

Backtests choose candidates. Only the forward ledger promotes them.

## Operating rules

- **Four concurrent paper books maximum, one per venue** (crypto, FX,
  equities, options). Forward sessions are the scarce resource; attention is
  split by venue, not by whim. *(Amended 2026-07-04 from two — the equity
  momentum book trades daily bars on its own venue; amended 2026-07-08 from
  three — the QQQ condor book is a short-volatility income stream,
  deliberately uncorrelated with the three long-directional books, and its
  Gate B runs on its own 10-session clock from its first paper trade.)*
- **Ledgers are the source of truth**: committed JSON in `ledger/`, one schema for
  every book, per-trade logs storing *intended* vs *filled* prices with the gap
  computed every session. Rule changes bump `rules_version` and re-stamp the
  forward clock — forward records never mix rule sets.
- **Execution honesty**: every order tagged with a `client_order_id` prefix
  (`el-<book>-`) so fills attribute to their book on a shared paper account.
- **Everything is paper** until Gate C, and Gate C only earns a conversation.

## Relationship to DayTrade

The sibling repo (DayTrade) keeps running its books unchanged — daily-governed
premarket, capped experiments. Both projects now share the same clock: paper
validation over the 10 sessions ending 2026-07-17, go-live decision that weekend.
The two form a natural A/B — gate-driven uncapped books vs daily-target-governed
books, on the same dashboard axes. Losing that comparison honestly is also a result.

## Go-live plan (2026-07-20)

- Only books that **pass Gate B on 2026-07-17** go live; the rest stay paper.
- Live size starts at a fraction of the eventual $10k (small enough that a full
  drawdown-budget hit is an acceptable tuition bill), with the same -2% daily
  loss stop and per-book kill criteria: breach of the drawdown budget or a
  reconciliation gap ≥ 10% halts the book back to paper.
- Scaling live size requires Gate C on the live ledger — 21+ live sessions with
  the rolling-21 $/day average positive.
