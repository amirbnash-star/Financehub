"""Rebuild holdings, average cost and cash from the transaction log.

Cost basis uses the average-cost method (as used by HMRC's "section 104
pool" for UK shares): each sale removes cost in proportion to the shares
sold, and the realised gain is proceeds minus that share of the pool.
Reference: HMRC Capital Gains Manual CG51560.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .transactions import TransactionError

QTY_EPS = 1e-9


@dataclass
class Position:
    ticker: str
    currency: str
    quantity: float = 0.0
    cost: float = 0.0  # total cost of shares still held, incl. fees, in `currency`
    realised: float = 0.0  # realised gain from sales, in `currency`
    dividends: float = 0.0

    @property
    def average_cost(self) -> float:
        return self.cost / self.quantity if self.quantity > QTY_EPS else 0.0


@dataclass
class Holdings:
    positions: dict[str, Position] = field(default_factory=dict)
    cash: dict[str, float] = field(default_factory=dict)  # currency -> balance

    def open_positions(self) -> dict[str, Position]:
        return {t: p for t, p in self.positions.items() if p.quantity > QTY_EPS}


def cash_delta(row) -> float:
    """Change in cash (in the row's currency) caused by one transaction."""
    amount, fees, action = row["amount"], row["fees"], row["action"]
    if action in ("buy", "withdrawal"):
        return -amount - fees
    return amount - fees  # sell, dividend, deposit


def quantity_delta(row) -> float:
    """Change in share count caused by one transaction."""
    if row["action"] == "buy":
        return row["quantity"]
    if row["action"] == "sell":
        return -row["quantity"]
    return 0.0


def rebuild_holdings(tx: pd.DataFrame, as_of: pd.Timestamp | None = None) -> Holdings:
    """Replay transactions in date order up to and including `as_of`."""
    h = Holdings()
    if as_of is not None:
        tx = tx[tx["date"] <= pd.Timestamp(as_of)]
    for row in tx.to_dict("records"):
        ccy = row["currency"]
        h.cash[ccy] = h.cash.get(ccy, 0.0) + cash_delta(row)
        ticker = row["ticker"]
        if not ticker:
            continue
        pos = h.positions.setdefault(ticker, Position(ticker=ticker, currency=ccy))
        if pos.currency != ccy and row["action"] in ("buy", "sell"):
            raise TransactionError(
                f"{ticker}: traded in {ccy} on {row['date']:%Y-%m-%d} but earlier in {pos.currency}; "
                "record all trades of one ticker in the same currency"
            )
        if row["action"] == "buy":
            pos.quantity += row["quantity"]
            pos.cost += row["amount"] + row["fees"]
        elif row["action"] == "sell":
            if row["quantity"] > pos.quantity + QTY_EPS:
                raise TransactionError(
                    f"{ticker}: selling {row['quantity']:g} on {row['date']:%Y-%m-%d} "
                    f"but only {pos.quantity:g} held"
                )
            removed_cost = pos.average_cost * row["quantity"]
            pos.realised += row["amount"] - row["fees"] - removed_cost
            pos.cost -= removed_cost
            pos.quantity -= row["quantity"]
            if pos.quantity <= QTY_EPS:
                pos.quantity, pos.cost = 0.0, 0.0
        elif row["action"] == "dividend" and ccy == pos.currency:
            pos.dividends += row["amount"] - row["fees"]
    return h


def add_implicit_deposits(tx: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Insert a deposit wherever a transaction would push a cash balance below zero.

    Many people only record their buys, not the cash they paid in. Without a
    deposit, cash goes negative and return calculations break. The inserted
    rows are marked note="implicit deposit" and the number added is returned
    so the report can mention it.
    """
    rows, balance, added = [], {}, 0
    for row in tx.to_dict("records"):
        ccy = row["currency"]
        after = balance.get(ccy, 0.0) + cash_delta(row)
        if after < -0.005:
            shortfall = -after
            rows.append({
                "date": row["date"], "ticker": "", "action": "deposit", "quantity": shortfall,
                "price": 1.0, "currency": ccy, "fees": 0.0, "note": "implicit deposit", "amount": shortfall,
            })
            after, added = 0.0, added + 1
        balance[ccy] = after
        rows.append(row)
    return (pd.DataFrame(rows, columns=tx.columns) if rows else tx), added
