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

## The gate ladder (enforced by `core/gates.py`)

Every strategy is a plug-in that must climb:

| Gate | What it proves | Pass criteria (coded, not vibes) |
|---|---|---|
| **A — Research** | Edge survives costs out-of-sample | Profitable (ret > 0 AND PF > 1) in >50% of 5 sequential walk-forward folds **at 2x modeled costs**, and net-positive over the full period at 2x costs. A candidate that only works at 1x is fragile. |
| **B — Paper** | Edge survives reality | Enough forward evidence on a $10k paper book — **30 sessions, or 14+ sessions with 40+ closed trades** (evidence is trades as much as calendar; a book trading daily shouldn't wait a month, a book trading twice a month can't be judged in two weeks): total P&L > 0 **on actual broker fills**, max drawdown inside the book's stated budget, reconciliation gap < 10% of gross P&L. |
| **C — Scale** | The average is what it claims | ≥ 60 sessions and the rolling-21 $/day average positive with the drawdown still in budget. Only now is real capital even discussed. |

Backtests choose candidates. Only the forward ledger promotes them.

## Operating rules

- **Three concurrent paper books maximum, one per venue** (crypto, FX,
  equities). Forward sessions are the scarce resource; attention is split by
  venue, not by whim. *(Amended 2026-07-04 from two: the equity momentum book
  trades daily bars on its own venue and does not compete for the intraday
  attention budget of the other two.)*
- **Ledgers are the source of truth**: committed JSON in `ledger/`, one schema for
  every book, per-trade logs storing *intended* vs *filled* prices with the gap
  computed every session. Rule changes bump `rules_version` and re-stamp the
  forward clock — forward records never mix rule sets.
- **Execution honesty**: every order tagged with a `client_order_id` prefix
  (`el-<book>-`) so fills attribute to their book on a shared paper account.
- **Everything is paper** until Gate C, and Gate C only earns a conversation.

## Relationship to DayTrade

The sibling repo (DayTrade) keeps running its books unchanged — daily-governed
premarket, capped experiments. After ~6 weeks the two projects form a natural A/B:
gate-driven uncapped books vs daily-target-governed books, on the same dashboard
axes. Losing that comparison honestly is also a result.
