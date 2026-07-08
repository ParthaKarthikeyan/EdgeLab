# EdgeLab

Second attempt at the same goal as [DayTrade](https://github.com/ParthaKarthikeyan/DayTrade):
**average +$100/day on a $10k book** — but this time designed backwards from
what went wrong the first time. Read [METHODOLOGY.md](METHODOLOGY.md) first;
everything here is that document turned into code.

**Dashboard:** https://parthakarthikeyan.github.io/EdgeLab/

## What's different from DayTrade

| | DayTrade | EdgeLab |
|---|---|---|
| Target | +1% *per day* (daily governor) | +$100/day *monthly average* (no profit caps) |
| Venues | premarket low-floats, futures, crypto | crypto, majors FX, liquid US large caps — chosen by cost structure |
| Admission | strategies deployed, then measured | Gate A (2x-cost walk-forward) **before** any bot exists |
| Books | as many as we built | max four paper books, one per venue |
| Honesty | added later (rules_version, fills) | intended-vs-filled per trade from day one |

## Layout

- `core/` — the measurement rig: `gates.py` (Gate A/B/C as code), `ledger.py`
  (one schema for every book), `engine.py` (trend + fade backtests with a
  bps cost model), `data.py` (Coinbase candles), `books.py` (the registry),
  `gist.py` (near-live dashboard feed)
- `run_research.py` — Gate A runner; verdicts committed to `ledger/research/`
- `run_paper.py` — trades every *active* book on Alpaca crypto paper, hourly
- `run_status.py` — assembles `ledger/status.json` for the dashboard
- `web/` — the dashboard (Vite + React + TS + Tailwind + Recharts)
- `tests/` — offline tests for the rig; CI runs them on every push

## Books

The registry is `core/books.py`. A book becomes `active` only after Gate A
passes on **every symbol it trades** at **2x modeled costs**, out-of-sample.
Verdicts live in `ledger/research/`, status in `ledger/status.json`.

## Running locally

```bash
pip install -r requirements.txt
python -m pytest tests -q          # offline, no keys
python run_research.py             # Gate A for every book (network: Coinbase)
python run_paper.py --dry-run      # signals only, no orders
```

Live paper runs need `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (and optionally
`GIST_TOKEN` / `GIST_ID` for the live feed). Orders are tagged
`el-<book>-<uuid>` so this repo's fills never mix with DayTrade's on the same
paper account.
