# portfolio-manager — notes for Claude

A personal portfolio strategist. It helps the owner **build** a portfolio from a
target allocation and risk profile, **maintain** it (holdings, cash,
transactions, value history), and **suggest** changes when rules are breached.

## The one hard rule: recommend only, never trade

- This tool **never places, submits, schedules or simulates-then-sends orders**.
  No broker APIs, no order endpoints, no credentials for trading accounts.
- It only reads prices, analyses, and writes recommendations to reports.
- The only user data file it writes is `transactions.csv`, and only via
  `add-transaction`, to record a trade the owner **already made** themselves.
- Every report and the README carry a "not financial advice" disclaimer.
- If a request would require executing a trade, refuse and explain this rule.

## Layout

- `portfolio/` — the package; `python -m portfolio <command>`.
  - `config.py` load/validate `config.yaml` + `targets.yaml`
  - `transactions.py` load/validate/append `transactions.csv`
  - `holdings.py` rebuild positions, average cost, cash from transactions
  - `prices.py` yfinance prices + FX with a local CSV cache
  - `valuation.py` values in base currency, daily value series, external flows
  - `analytics.py` weights, drift, TWR, XIRR, volatility, drawdown, Sharpe, correlation, HHI
  - `rebalance.py` cash-first trade sizing, rounding, min trade size, costs
  - `rules.py` rule engine → `Suggestion` objects
  - `build.py` model allocations per risk profile (starter `targets.yaml`)
  - `report.py` dated Markdown report + PNG charts; `snapshots.py` history
  - `methods.py` single source for method descriptions + citations
  - `cli.py` argparse only, no business logic
- `examples/` — committed fake portfolio + **synthetic** price cache (offline).
- `data/` — the owner's real files. **Gitignored. Never commit real data.**
- `tests/` — pytest, uses a fake price provider; no network in tests.

## Conventions

- Python 3.11+, managed with `uv` (`uv run pytest`, `uv run python -m portfolio run`).
- Plain files only: YAML for config/targets, CSV for transactions/cache/history. No database.
- Percentages in YAML are written as percent (5 = 5%); internally use fractions (0.05).
- Money is valued in the base currency (GBP or EUR). LSE prices quoted in pence
  (`GBp`/`GBX`) are converted to pounds in `prices.normalise_currency`.
- Keep code simple and readable: plain functions and dataclasses, no frameworks.
- Every analytical method has a citation in `methods.py` and a comment where it is
  implemented. Add both when introducing a new method.
- Add tests for any calculation or rule logic. Tests must not hit the network.
- Suggestions must state: what, why (rule + triggering numbers), expected effect
  on weights, and estimated cost. When nothing breaches a rule, say nothing needs doing.
- Commit in small working steps with clear messages.
