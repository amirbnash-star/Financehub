"""Regenerate the example portfolio: SYNTHETIC prices and a fake transaction history.

The tickers are real funds but the prices in examples/prices_cache/ are made up
(a seeded random walk), so the example runs offline and gives the same result
every time. Do not read anything into the example's numbers.

    uv run python examples/make_example.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
START, END = "2023-01-03", "2026-09-24"
SEED = 38

# ticker: (start price, quote currency, annual drift, annual vol, loading on the equity factor)
ASSETS = {
    "VWRL.L": (95.0, "GBP", 0.09, 0.14, 0.98),
    "VUSA.L": (68.0, "GBP", 0.11, 0.16, 0.96),
    "EMIM.L": (2600.0, "GBp", 0.01, 0.17, 0.60),
    "IGLT.L": (1080.0, "GBp", -0.02, 0.08, 0.05),
    "SGLN.L": (2900.0, "GBp", 0.10, 0.14, 0.10),
}


def make_prices() -> dict[str, pd.Series]:
    rng = np.random.default_rng(SEED)
    idx = pd.bdate_range(START, END)
    market = rng.normal(0, 1, len(idx))
    out = {}
    for ticker, (p0, ccy, mu, vol, beta) in ASSETS.items():
        noise = rng.normal(0, 1, len(idx))
        z = beta * market + np.sqrt(1 - beta ** 2) * noise
        daily = (mu - 0.5 * vol ** 2) / 252 + vol / np.sqrt(252) * z
        daily[0] = 0.0
        out[ticker] = pd.Series(p0 * np.exp(np.cumsum(daily)), index=idx).round(2)
    cache = HERE / "prices_cache"
    cache.mkdir(exist_ok=True)
    for ticker, s in out.items():
        pd.DataFrame({"date": s.index.strftime("%Y-%m-%d"), "close": s.values,
                      "currency": ASSETS[ticker][1]}).to_csv(cache / f"{ticker}.csv", index=False)
    return out


def make_transactions(prices: dict[str, pd.Series]) -> None:
    rows = []

    def add(date, ticker, action, qty, price, ccy, fees=0.0, note=""):
        rows.append([date.strftime("%Y-%m-%d"), ticker, action, round(qty, 4), round(price, 4), ccy, fees, note])

    def buy(date, ticker, value):
        price = prices[ticker].asof(date)
        qty = int(value // (price * (0.01 if ASSETS[ticker][1] == "GBp" else 1)))
        add(date, ticker, "buy", qty, price, ASSETS[ticker][1], 0.0)

    d0 = pd.Timestamp(START)
    add(d0, "", "deposit", 25_000, 1, "GBP", note="initial lump sum")
    for ticker, value in {"VWRL.L": 11_000, "EMIM.L": 2_500, "VUSA.L": 2_000, "IGLT.L": 7_500, "SGLN.L": 1_250}.items():
        buy(d0, ticker, value)
    # Monthly saving of 400, always into the global tracker (so equity drifts up).
    for d in pd.date_range("2023-02-01", "2026-08-01", freq="MS"):
        d = pd.offsets.BDay().rollforward(d)
        add(d, "", "deposit", 400, 1, "GBP", note="monthly saving")
        buy(d, "VWRL.L", 400)
    # Quarterly dividends on the distributing global tracker.
    for d in pd.date_range("2023-03-28", "2026-06-28", freq="QS-MAR") + pd.Timedelta(days=27):
        add(pd.offsets.BDay().rollforward(d), "VWRL.L", "dividend", 1, 45.0, "GBP", note="quarterly dividend")
    add(pd.Timestamp("2025-03-14"), "SGLN.L", "sell", 5, prices["SGLN.L"].asof("2025-03-14"), "GBp", 0.0, "took profit")
    add(pd.Timestamp("2026-09-01"), "", "deposit", 3_000, 1, "GBP", note="bonus, not yet invested")

    df = pd.DataFrame(rows, columns=["date", "ticker", "action", "quantity", "price", "currency", "fees", "note"])
    df.sort_values("date", kind="stable").to_csv(HERE / "transactions.csv", index=False)


if __name__ == "__main__":
    make_transactions(make_prices())
    print("Wrote examples/transactions.csv and examples/prices_cache/")
