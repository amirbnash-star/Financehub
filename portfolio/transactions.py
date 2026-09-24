"""Load, validate and append transactions.csv.

Columns: date, ticker, action, quantity, price, currency, fees, note

The cash amount of every row is quantity x price:
- buy / sell:            quantity = shares, price = price per share
- dividend:              quantity = shares (or 1), price = dividend per share (or total)
- deposit / withdrawal:  leave ticker empty; quantity = amount, price = 1
Fees are always a positive number in the row's currency and are paid from cash.
Prices in pence (currency GBp or GBX) are converted to pounds on load.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from .prices import normalise_currency

COLUMNS = ["date", "ticker", "action", "quantity", "price", "currency", "fees", "note"]
ACTIONS = ("buy", "sell", "dividend", "deposit", "withdrawal")
TRADE_ACTIONS = ("buy", "sell")
CASH_ACTIONS = ("deposit", "withdrawal")


class TransactionError(ValueError):
    """Raised for a malformed transaction row."""


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS + ["amount"])


def validate_row(row: dict, line: int | str = "?") -> dict:
    """Return a cleaned copy of one transaction row, or raise TransactionError."""
    where = f"transactions.csv line {line}"
    out = {}
    try:
        out["date"] = pd.Timestamp(str(row.get("date", "")).strip()).normalize()
    except (ValueError, TypeError):
        raise TransactionError(f"{where}: bad date {row.get('date')!r}") from None
    out["ticker"] = str(row.get("ticker") or "").strip().upper()
    out["action"] = str(row.get("action") or "").strip().lower()
    if out["action"] not in ACTIONS:
        raise TransactionError(f"{where}: action must be one of {ACTIONS}, got {row.get('action')!r}")

    def num(key: str, default=None) -> float:
        val = row.get(key)
        if val is None or str(val).strip() == "" or (isinstance(val, float) and pd.isna(val)):
            if default is None:
                raise TransactionError(f"{where}: {key} is required")
            return default
        try:
            return float(val)
        except ValueError:
            raise TransactionError(f"{where}: {key} must be a number, got {val!r}") from None

    quantity = num("quantity")
    price = num("price", default=1.0 if out["action"] in CASH_ACTIONS else None)
    fees = num("fees", default=0.0)
    if quantity <= 0 or price < 0 or fees < 0:
        raise TransactionError(f"{where}: quantity must be > 0, price and fees must be >= 0")

    currency = str(row.get("currency") or "").strip()
    if not currency:
        raise TransactionError(f"{where}: currency is required")
    currency, factor = normalise_currency(currency)
    out["quantity"] = quantity
    out["price"] = price * factor
    out["fees"] = fees * factor
    out["currency"] = currency
    out["note"] = str(row.get("note") or "").strip()

    if out["action"] in CASH_ACTIONS and out["ticker"]:
        raise TransactionError(f"{where}: {out['action']} must not have a ticker")
    if out["action"] not in CASH_ACTIONS and not out["ticker"]:
        raise TransactionError(f"{where}: {out['action']} needs a ticker")
    out["amount"] = out["quantity"] * out["price"]
    return out


def load_transactions(path: Path) -> pd.DataFrame:
    """Read and validate transactions; returns a DataFrame sorted by date."""
    path = Path(path)
    if not path.exists():
        return empty_frame()
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, comment="#")
    missing = {"date", "action", "quantity", "currency"} - set(raw.columns)
    if missing:
        raise TransactionError(f"{path.name} is missing columns: {sorted(missing)}")
    rows = [validate_row(r, line=i + 2) for i, r in enumerate(raw.to_dict("records"))]
    if not rows:
        return empty_frame()
    df = pd.DataFrame(rows)
    # Stable sort keeps same-day rows in file order (e.g. deposit before buy).
    return df.sort_values("date", kind="stable").reset_index(drop=True)


def append_transaction(path: Path, row: dict) -> dict:
    """Validate and append one transaction the owner has ALREADY made.

    This only records history; it never places an order anywhere.
    """
    validate_row(row, line="(new)")
    path = Path(path)
    new_file = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if not new_file and not path.read_text().endswith("\n"):
        with path.open("a") as f:
            f.write("\n")
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLUMNS})
    return row
