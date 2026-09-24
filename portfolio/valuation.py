"""Value the portfolio in the base currency, today and for every past business day."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import CASH, Config, Targets
from .holdings import Holdings, add_implicit_deposits, cash_delta, quantity_delta, rebuild_holdings
from .prices import PriceBook

HISTORY_BUFFER_DAYS = 10


@dataclass
class Valuation:
    as_of: pd.Timestamp
    base: str
    table: pd.DataFrame  # one row per holding + CASH (see _current_table)
    daily: pd.DataFrame  # columns: value, flow (external deposits(+)/withdrawals(-)), both in base
    prices_base: pd.DataFrame  # daily closes converted to base currency
    fx: pd.DataFrame  # base per unit of each currency
    tx: pd.DataFrame  # transactions incl. implicit deposits
    holdings: Holdings
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return float(self.table["value"].sum())

    @property
    def cash(self) -> float:
        return float(self.table.loc[CASH, "value"]) if CASH in self.table.index else 0.0


def _bday(dates: pd.Series) -> pd.Series:
    """Move weekend/holiday dates to the next business day."""
    return dates.map(pd.offsets.BDay().rollforward)


def value_portfolio(
    tx: pd.DataFrame,
    targets: Targets,
    cfg: Config,
    book: PriceBook,
    as_of: pd.Timestamp | None = None,
) -> Valuation:
    as_of = pd.offsets.BDay().rollback(pd.Timestamp(as_of or pd.Timestamp.today()).normalize())
    warnings: list[str] = []
    tx = tx[tx["date"] <= as_of].copy()
    tx, implicit = add_implicit_deposits(tx) if not tx.empty else (tx, 0)
    if implicit:
        warnings.append(
            f"{implicit} implicit deposit(s) were assumed because cash would have gone negative. "
            "Record your deposits in transactions.csv for accurate money-weighted returns."
        )

    lookback = max(cfg.corr_lookback_days, cfg.underperf_lookback_days)
    first_tx = tx["date"].min() if not tx.empty else as_of
    start = min(first_tx, as_of - pd.Timedelta(days=lookback)) - pd.Timedelta(days=HISTORY_BUFFER_DAYS)

    traded = [t for t in tx["ticker"].unique() if t] if not tx.empty else []
    tickers = list(dict.fromkeys(targets.tickers + traded))
    book.currency_hints.update({t.ticker: t.currency for t in targets.holdings.values() if t.currency})
    closes = book.closes(tickers, start, as_of)
    currencies = set(book.currencies.values()) | set(tx["currency"]) | {cfg.base_currency}
    fx = book.fx(currencies, start, as_of)
    for ccy in fx.columns:
        if fx[ccy].isna().all():
            warnings.append(f"No FX rate for {ccy}->{cfg.base_currency}; holdings in {ccy} are excluded.")

    # Prices in base currency. Where a price is missing, fall back to the last
    # traded price from transactions.csv so the holding is not valued at zero.
    prices_base = pd.DataFrame(index=closes.index)
    for t in tickers:
        prices_base[t] = closes[t] * fx[book.currencies[t]]
        if prices_base[t].isna().any():
            trades = tx[(tx["ticker"] == t) & tx["action"].isin(["buy", "sell"])]
            if not trades.empty:
                tp = pd.Series(
                    (trades["price"] * [fx[c].asof(d) for c, d in zip(trades["currency"], _bday(trades["date"]))]).values,
                    index=_bday(trades["date"]),
                )
                tp = tp[~tp.index.duplicated(keep="last")].reindex(closes.index).ffill()
                if pd.isna(prices_base.loc[as_of, t]) and not pd.isna(tp.loc[as_of]):
                    warnings.append(f"No market price for {t}; using the last traded price.")
                prices_base[t] = prices_base[t].fillna(tp)

    daily = _daily_values(tx, prices_base, fx, first_tx, as_of)
    holdings = rebuild_holdings(tx)
    table = _current_table(holdings, targets, closes, prices_base, fx, book, as_of)
    for ccy, bal in holdings.cash.items():
        if bal < -0.005:
            warnings.append(f"Cash balance in {ccy} is negative ({bal:,.2f}).")
    return Valuation(as_of, cfg.base_currency, table, daily, prices_base, fx, tx, holdings, warnings + book.warnings)


def _daily_values(tx, prices_base, fx, first_tx, as_of) -> pd.DataFrame:
    index = pd.bdate_range(first_tx, as_of)
    if tx.empty or len(index) == 0:
        return pd.DataFrame({"value": [], "flow": []}, dtype=float)
    tx = tx.assign(bdate=_bday(tx["date"]))

    # Share counts per day: cumulative sum of buys/sells.
    q = tx.assign(dq=tx.apply(quantity_delta, axis=1))
    q = q[q["ticker"] != ""]
    qty = q.pivot_table(index="bdate", columns="ticker", values="dq", aggfunc="sum")
    qty = qty.reindex(index, fill_value=0).fillna(0).cumsum() if not qty.empty else pd.DataFrame(index=index)
    holdings_value = (qty * prices_base.reindex(index)[qty.columns]).fillna(0).sum(axis=1)

    # Cash per currency per day, converted at that day's FX rate.
    c = tx.assign(dc=tx.apply(cash_delta, axis=1))
    cash = c.pivot_table(index="bdate", columns="currency", values="dc", aggfunc="sum")
    cash = cash.reindex(index, fill_value=0).fillna(0).cumsum()
    cash_value = (cash * fx.reindex(index)[cash.columns]).fillna(0).sum(axis=1)

    # External flows (money in/out of the portfolio), in base currency.
    ext = tx[tx["action"].isin(["deposit", "withdrawal"])]
    # The flow is the amount paid in/out; any fee on it counts as a portfolio cost.
    signed = ext["amount"].where(ext["action"] == "deposit", -ext["amount"])
    rate = [fx[ccy].asof(d) for ccy, d in zip(ext["currency"], ext["bdate"])]
    flow = pd.Series((signed * rate).values, index=ext["bdate"].values).groupby(level=0).sum()
    return pd.DataFrame({"value": holdings_value + cash_value, "flow": flow.reindex(index, fill_value=0.0)})


def _current_table(holdings, targets, closes, prices_base, fx, book, as_of) -> pd.DataFrame:
    rows = []
    open_pos = holdings.open_positions()
    for t in dict.fromkeys(targets.tickers + list(open_pos)):
        pos = open_pos.get(t)
        qty = pos.quantity if pos else 0.0
        price_base = prices_base.loc[as_of, t] if t in prices_base else float("nan")
        cost_fx = fx[pos.currency].loc[as_of] if pos else 1.0
        tgt = targets.holdings.get(t)
        rows.append({
            "ticker": t,
            "name": tgt.name if tgt else "",
            "asset_class": tgt.asset_class if tgt else "untargeted",
            "quantity": qty,
            "currency": book.currencies.get(t, ""),
            "price": closes.loc[as_of, t] if t in closes else float("nan"),
            "price_base": price_base,
            "value": qty * price_base if qty and pd.notna(price_base) else 0.0,
            # Cost converted at today's FX rate (a simplification for non-base holdings).
            "cost": pos.cost * cost_fx if pos else 0.0,
            "target": tgt.weight if tgt else 0.0,
        })
    cash_value = sum(bal * fx[ccy].loc[as_of] for ccy, bal in holdings.cash.items() if pd.notna(fx[ccy].loc[as_of]))
    rows.append({
        "ticker": CASH, "name": "Cash", "asset_class": "cash", "quantity": cash_value, "currency": "",
        "price": 1.0, "price_base": 1.0, "value": cash_value, "cost": cash_value, "target": targets.cash_weight,
    })
    table = pd.DataFrame(rows).set_index("ticker")
    total = table["value"].sum()
    table["weight"] = table["value"] / total if total else 0.0
    table["drift"] = table["weight"] - table["target"]
    table["gain"] = table["value"] - table["cost"]
    table["gain_pct"] = (table["gain"] / table["cost"]).where(table["cost"] > 0)
    return table
