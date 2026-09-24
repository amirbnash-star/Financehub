"""Turn target weights into concrete, sized trade suggestions.

Method: tolerance-band ("drift-band") rebalancing. A holding is only traded
when its weight leaves a band around its target. The band here is the
Swedroe "5/25" rule: an absolute drift of X pp or a relative drift of Y%,
whichever triggers first (Swedroe & Kizer, "The Only Guide to a Winning
Bond Strategy You'll Ever Need", 2008; Vanguard, "Best practices for
portfolio rebalancing", Jaconetti, Kinniry & Zilbering, 2010). Vanguard
finds bands give a similar risk control to calendar rebalancing with fewer
trades, and that using new cash flows first lowers cost and tax.

Cash-first: spare cash goes to underweight holdings in proportion to how
far each is below target. Sells are suggested only when a holding is still
outside its band and selling is allowed.

These are suggestions only. Nothing here places an order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .config import CASH, Config


@dataclass
class Trade:
    ticker: str
    side: str  # "buy" or "sell"
    quantity: float
    price: float  # per share, in the holding's quote currency (major unit)
    currency: str
    value: float  # in base currency
    cost: float  # estimated fees + spread, base currency


@dataclass
class Plan:
    trades: list[Trade]
    before: pd.Series  # weights incl. CASH
    after: pd.Series
    total_cost: float
    new_money_needed: float | None = None  # deposit that would fix overweights without selling
    notes: list[str] = field(default_factory=list)


def band(target: float, cfg: Config) -> float:
    """Half-width of the tolerance band around a target weight."""
    return min(cfg.abs_drift, cfg.rel_drift * target) if target > 0 else cfg.abs_drift


def is_breached(weight: float, target: float, cfg: Config) -> bool:
    """True when |weight - target| exceeds the absolute OR the relative threshold."""
    drift = abs(weight - target)
    if drift > cfg.abs_drift + 1e-12:
        return True
    return target > 0 and drift / target > cfg.rel_drift + 1e-12


def trade_cost(value: float, cfg: Config) -> float:
    return cfg.fixed_fee + abs(value) * (cfg.commission_bps + cfg.spread_bps) / 10_000


def _shares(value: float, price_base: float, cfg: Config) -> float:
    if price_base <= 0 or math.isnan(price_base):
        return 0.0
    q = value / price_base
    return round(q, 4) if cfg.fractional_shares else math.floor(q + 1e-9)


def plan_rebalance(table: pd.DataFrame, cfg: Config, *, allow_sells: bool, tradeable: set[str] | None = None) -> Plan:
    """Size trades that move targeted holdings toward target, spending spare cash first.

    `table` is Valuation.table. Holdings with no price or no target are never traded.
    """
    t = table.copy()
    total = float(t["value"].sum())
    invest = t.drop(index=CASH, errors="ignore")
    ok = invest["price_base"].notna() & (invest["price_base"] > 0) & (invest["target"] > 0)
    if tradeable is not None:
        ok &= invest.index.isin(list(tradeable))
    invest = invest[ok]
    cash_target = float(table.loc[CASH, "target"]) * total if CASH in table.index else 0.0
    rate = (cfg.commission_bps + cfg.spread_bps) / 10_000

    sells: dict[str, float] = {}
    if allow_sells:
        for tk, r in invest.iterrows():
            if r["weight"] > r["target"] and is_breached(r["weight"], r["target"], cfg):
                sells[tk] = r["value"] - r["target"] * total
    sell_trades = []
    for tk, value in sells.items():
        r = invest.loc[tk]
        qty = min(r["quantity"], round(value / r["price_base"], 4) if cfg.fractional_shares else round(value / r["price_base"]))
        if qty * r["price_base"] >= cfg.min_trade_size:
            v = qty * r["price_base"]
            sell_trades.append(Trade(tk, "sell", qty, r["price"], r["currency"], v, trade_cost(v, cfg)))

    # Budget = spare cash above the cash target + net sell proceeds.
    spare = float(table.loc[CASH, "value"]) - cash_target if CASH in table.index else 0.0
    budget = max(spare, 0.0) + sum(s.value - s.cost for s in sell_trades)
    deficits = {tk: r["target"] * total - r["value"] for tk, r in invest.iterrows()
                if tk not in sells and r["target"] * total - r["value"] > 0}
    buy_trades = []
    if budget > 0 and deficits:
        spend = min(budget, sum(deficits.values()))
        n = len(deficits)
        net = max(spend - cfg.fixed_fee * n, 0.0) / (1 + rate)  # leave room for costs
        for tk, d in sorted(deficits.items(), key=lambda kv: -kv[1]):
            r = invest.loc[tk]
            qty = _shares(net * d / sum(deficits.values()), r["price_base"], cfg)
            v = qty * r["price_base"]
            if qty > 0 and v >= cfg.min_trade_size:
                buy_trades.append(Trade(tk, "buy", qty, r["price"], r["currency"], v, trade_cost(v, cfg)))

    trades = sell_trades + buy_trades
    after_value = t["value"].astype(float)
    for tr in trades:
        sign = 1 if tr.side == "buy" else -1
        after_value[tr.ticker] += sign * tr.value
        after_value[CASH] -= sign * tr.value + tr.cost
    total_cost = sum(tr.cost for tr in trades)
    after = after_value / (total - total_cost) if total else after_value

    plan = Plan(trades, t["weight"], after, total_cost)
    if not allow_sells:
        plan.new_money_needed = new_money_to_fix_overweights(table, cfg)
    return plan


def new_money_to_fix_overweights(table: pd.DataFrame, cfg: Config) -> float | None:
    """Smallest deposit D such that every overweight holding falls back inside its
    band, i.e. value_i / (V + D) <= target_i + band_i, if D were invested elsewhere."""
    total = float(table["value"].sum())
    needed = 0.0
    for tk, r in table.drop(index=CASH, errors="ignore").iterrows():
        if r["target"] > 0 and r["weight"] > r["target"] and is_breached(r["weight"], r["target"], cfg):
            upper = r["target"] + band(r["target"], cfg)
            needed = max(needed, r["value"] / upper - total)
    return needed if needed > 0 else None


def trim_trade(row: pd.Series, total: float, limit: float, cfg: Config) -> Trade | None:
    """Sell just enough shares of one holding to bring it to `limit` weight."""
    excess = row["value"] - limit * total
    if excess <= 0 or not row["price_base"] or pd.isna(row["price_base"]):
        return None
    qty = round(excess / row["price_base"], 4) if cfg.fractional_shares else math.ceil(excess / row["price_base"])
    qty = min(qty, row["quantity"])
    v = qty * row["price_base"]
    if v < cfg.min_trade_size:
        return None
    return Trade(row.name, "sell", qty, row["price"], row["currency"], v, trade_cost(v, cfg))
