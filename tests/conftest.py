import pandas as pd
import pytest

from portfolio.transactions import validate_row


def make_tx(rows):
    """Build a validated transactions DataFrame from short tuples:
    (date, ticker, action, quantity, price, currency, fees)."""
    cols = ["date", "ticker", "action", "quantity", "price", "currency", "fees"]
    clean = [validate_row(dict(zip(cols, r))) for r in rows]
    return pd.DataFrame(clean).sort_values("date", kind="stable").reset_index(drop=True)


class FakeSource:
    """Price source that serves fixed series and counts calls; no network."""

    def __init__(self, data: dict, currencies: dict | None = None, fail: bool = False):
        self.data = data
        self.currencies = currencies or {}
        self.fail = fail
        self.calls = []

    def history(self, ticker, start):
        self.calls.append(ticker)
        if self.fail:
            raise ConnectionError("no network")
        s = self.data.get(ticker, pd.Series(dtype=float))
        return s[s.index >= start], self.currencies.get(ticker)


@pytest.fixture
def fake_source():
    return FakeSource
