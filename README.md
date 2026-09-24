# portfolio-manager

Your personal portfolio strategist. It helps you **build** a portfolio from a target
allocation and risk profile, **maintain** it (holdings, cash, transactions, value history)
and **suggest** changes when they are warranted. Suggestions include rebalancing with exact
share counts, trimming concentration, deploying spare cash, flagging redundant holdings and
reviewing laggards. When nothing needs doing, it says so.

> **It never trades.** It only reads prices, analyses and recommends. You make every
> decision and place every order yourself, with your own broker.

> **Not financial advice.** This is a personal tool built on simple, documented rules. It can
> be wrong, uses delayed or cached prices, and doesn't know your whole financial or tax
> situation. If in doubt, speak to a regulated financial adviser.

## 1. Setup

1. Install [uv](https://docs.astral.sh/uv/) (one-off):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. Get the code and install dependencies:
   ```bash
   git clone <this repo> portfolio-manager && cd portfolio-manager
   uv sync
   ```
3. Try the example portfolio. It is fake and runs offline on synthetic prices:
   ```bash
   uv run python -m portfolio run --as-of 2026-09-24
   ```
   Open `examples/reports/report_2026-09-24.md` to see a full report.
4. Run the tests:
   ```bash
   uv run pytest
   ```

No uv? Use pip instead: `python3.11 -m venv .venv && . .venv/bin/activate && pip install pandas numpy yfinance matplotlib pyyaml pytest`,
then run the same commands without `uv run`.

## 2. Set up your own portfolio

1. Create your private data folder (`data/` is gitignored, so it is never committed):
   ```bash
   uv run python -m portfolio init --profile balanced --base GBP
   ```
   Profiles: `cautious`, `balanced`, `growth`, `aggressive`. Base currency: `GBP` or `EUR`.
2. Choose your targets. To see a starter allocation for a profile:
   ```bash
   uv run python -m portfolio build --profile growth
   ```
   Then edit `data/targets.yaml`. Holding weights, including `CASH`, must add up to 100.
   The suggested funds are examples only; check each fund's KID/factsheet.
3. Adjust the rules in `data/config.yaml`: drift thresholds, max position, minimum trade size,
   cash limit, costs, and horizon.
4. Record your history, oldest first, either by editing `data/transactions.csv` or with the command:
   ```bash
   uv run python -m portfolio add-transaction --action deposit --quantity 10000 --date 2026-01-05
   uv run python -m portfolio add-transaction --action buy --ticker VWRL.L --quantity 50 --price 120.5 --fees 0 --date 2026-01-06
   uv run python -m portfolio add-transaction --action buy --ticker IGLT.L --quantity 200 --price 1052 --currency GBp --date 2026-01-06
   ```
5. Run the full check (fetches live prices from Yahoo Finance, writes a report and a snapshot):
   ```bash
   uv run python -m portfolio run
   ```
   Output: `data/reports/report_<date>.md` (with charts) and `data/history/`.
6. For a quick look at any time:
   ```bash
   uv run python -m portfolio status
   ```

## 3. Day-to-day routine

1. Whenever you trade, deposit or get a dividend, record it with `add-transaction`.
2. Run `uv run python -m portfolio run` monthly, or after adding money.
3. Read the **Suggestions** section. Each one gives *what*, *why* (the rule and its numbers),
   the effect on weights, and the estimated cost.
4. If you act on one, place the order yourself, then record it with `add-transaction`.

## transactions.csv format

| column | meaning |
|---|---|
| `date` | `YYYY-MM-DD` |
| `ticker` | Yahoo ticker, e.g. `VWRL.L`, `VWCE.DE`, `AAPL`; empty for deposit/withdrawal |
| `action` | `buy`, `sell`, `dividend`, `deposit`, `withdrawal` |
| `quantity` | shares; for deposit/withdrawal the cash amount |
| `price` | per share; `1` for deposit/withdrawal; for a dividend, per share (or quantity 1 and the total) |
| `currency` | `GBP`, `GBp` (pence), `EUR`, `USD`, ... |
| `fees` | total fees for the row, in `currency` |
| `note` | optional |

Cash moves by `quantity x price` (plus or minus fees). Cash is tracked per currency and valued in
your base currency. If you only record buys, deposits are assumed where needed and the report
tells you.

## What the rules check

| Rule | Triggers when | Suggests |
|---|---|---|
| Drift band (5/25) | a weight is more than 5 pp, or 25% relative, from target | buy/sell quantities, spare cash first |
| Max position | a holding is above `max_position_pct` | trim quantity |
| Excess cash | cash is above `cash.max_pct` | which underweights to buy, and how many shares |
| Correlation | two holdings' daily returns correlate above the threshold | review for redundancy |
| Underperformer | a holding trails its asset-class peers by more than the threshold | review (not an automatic sell) |
| Risk profile | the target equity share does not fit the profile or horizon | review targets |

Every method (TWR, XIRR, Sharpe, drawdown, HHI, drift bands and so on) is cited, with what
it showed, in each report's **Methods** section and in `portfolio/methods.py`.

## Project layout

```
portfolio/       code (run as `python -m portfolio`)
examples/        fake example portfolio + synthetic price cache (committed)
data/            your real config, targets, transactions, reports, history (gitignored)
tests/           pytest suite (no network)
CLAUDE.md        conventions and the "recommend only, never trade" rule
```

## Notes and limits

- Prices come from Yahoo Finance through `yfinance`: free, delayed, and occasionally patchy.
  Prices are cached in `data/prices_cache/`. If a download fails, the cache is used and the report
  says so. Use `--offline` to skip downloading.
- Cost basis uses average cost. Gains on non-base-currency holdings use today's FX rate.
- Suggested trades are rounded down to whole shares (set `fractional_shares: true` if your
  broker allows fractions). Trades below `min_trade_size` are dropped.
- Taxes are not modelled. Selling in a taxable account may create a capital gain, so check
  before acting.
