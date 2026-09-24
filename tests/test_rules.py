from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio.config import CASH, Config, Target, Targets
from portfolio.rebalance import is_breached, new_money_to_fix_overweights, plan_rebalance, trade_cost
from portfolio.rules import evaluate


def make_table(rows, cash, cash_target):
    """rows: ticker -> (quantity, price, target, asset_class)"""
    recs = [{"ticker": t, "quantity": q, "price": p, "price_base": p, "currency": "GBP", "value": q * p,
             "target": tg, "asset_class": cls} for t, (q, p, tg, cls) in rows.items()]
    recs.append({"ticker": CASH, "quantity": cash, "price": 1.0, "price_base": 1.0, "currency": "", "value": cash,
                 "target": cash_target, "asset_class": "cash"})
    t = pd.DataFrame(recs).set_index("ticker")
    t["weight"] = t["value"] / t["value"].sum()
    t["drift"] = t["weight"] - t["target"]
    return t


def cfg(**kw):
    base = dict(abs_drift=0.05, rel_drift=0.25, max_position=0.6, min_trade_size=50, cash_max=0.05,
                spread_bps=0, commission_bps=0, fixed_fee=0)
    base.update(kw)
    return Config(**base)


def run_rules(table, targets, config, corr_pairs=(), returns=None):
    total = table["value"].sum()
    val = SimpleNamespace(table=table, total=total, cash=table.loc[CASH, "value"])
    classes = table.groupby("asset_class")[["weight", "target"]].sum()
    an = SimpleNamespace(class_table=classes, hhi=0.5, effective_n=2.0, corr_pairs=list(corr_pairs),
                         holding_return=returns or {})
    return evaluate(val, an, targets, config)


def targets_for(weights):
    return Targets({t: Target(t, w, "cash" if t == CASH else cls) for t, (w, cls) in weights.items()})


# --- band logic ---------------------------------------------------------------

@pytest.mark.parametrize("w, t, expected", [
    (0.56, 0.50, True),    # 6 pp absolute
    (0.54, 0.50, False),   # 4 pp, 8% relative
    (0.13, 0.10, True),    # 3 pp but 30% relative
    (0.12, 0.10, False),   # 2 pp, 20% relative
    (0.00, 0.10, True),
])
def test_is_breached(w, t, expected):
    assert is_breached(w, t, cfg()) is expected


def test_trade_cost():
    assert trade_cost(10_000, cfg(fixed_fee=5, commission_bps=10, spread_bps=10)) == pytest.approx(25)


# --- sizing -----------------------------------------------------------------------

def test_cash_first_buys_underweight_without_selling():
    # A 50% (target 50), B 30% (target 45) -> underweight; cash 20% (target 5).
    table = make_table({"A": (50, 10, 0.50, "equity"), "B": (30, 10, 0.45, "bonds")}, 200, 0.05)
    plan = plan_rebalance(table, cfg(), allow_sells=True)
    assert [(t.ticker, t.side) for t in plan.trades] == [("B", "buy")]
    assert plan.trades[0].quantity == 15  # 150 spare cash / 10
    assert plan.after["B"] == pytest.approx(0.45)
    assert plan.after[CASH] == pytest.approx(0.05)


def test_sells_overweight_when_cash_cannot_fix_it():
    table = make_table({"A": (70, 10, 0.50, "equity"), "B": (30, 10, 0.50, "bonds")}, 0, 0.0)
    plan = plan_rebalance(table, cfg(), allow_sells=True)
    sides = {t.ticker: (t.side, t.quantity) for t in plan.trades}
    assert sides == {"A": ("sell", 20), "B": ("buy", 20)}
    assert plan.after["A"] == pytest.approx(0.5)


def test_no_sells_reports_new_money_needed():
    table = make_table({"A": (70, 10, 0.50, "equity"), "B": (30, 10, 0.50, "bonds")}, 0, 0.0)
    plan = plan_rebalance(table, cfg(), allow_sells=False)
    assert plan.trades == []
    # A must fall to 55%: 700 / 0.55 - 1000
    assert plan.new_money_needed == pytest.approx(700 / 0.55 - 1000)
    assert new_money_to_fix_overweights(table, cfg()) == pytest.approx(plan.new_money_needed)


def test_min_trade_size_drops_small_trades():
    table = make_table({"A": (50, 10, 0.50, "equity"), "B": (45, 10, 0.50, "bonds")}, 50, 0.0)
    plan = plan_rebalance(table, cfg(min_trade_size=100), allow_sells=True)
    assert plan.trades == []


def test_costs_are_left_out_of_cash():
    table = make_table({"A": (0, 10, 0.95, "equity")}, 1000, 0.05)
    plan = plan_rebalance(table, cfg(fixed_fee=10, spread_bps=50), allow_sells=True)
    buy = plan.trades[0]
    assert buy.value + buy.cost <= 950
    assert plan.total_cost == pytest.approx(10 + buy.value * 0.005)


# --- rules ------------------------------------------------------------------------

def test_quiet_when_on_target():
    table = make_table({"A": (50, 10, 0.50, "equity"), "B": (48, 10, 0.48, "bonds")}, 20, 0.02)
    targets = targets_for({"A": (0.5, "equity"), "B": (0.48, "bonds"), CASH: (0.02, "cash")})
    assert run_rules(table, targets, cfg(risk_profile="balanced")) == []


def test_drift_rule_explains_numbers():
    table = make_table({"A": (70, 10, 0.50, "equity"), "B": (30, 10, 0.50, "bonds")}, 0, 0.0)
    targets = targets_for({"A": (0.5, "equity"), "B": (0.5, "bonds")})
    s = [x for x in run_rules(table, targets, cfg(max_position=0.9)) if x.rule == "rebalance"][0]
    assert s.severity == "action"
    assert "A is 70.0% vs target 50.0%" in s.why and "+20.0 pp" in s.why
    assert s.effect["A"] == pytest.approx((0.7, 0.5))
    assert "SELL 20 A" in s.what and "BUY 20 B" in s.what


def test_concentration_trim():
    table = make_table({"A": (70, 10, 0.70, "equity"), "B": (30, 10, 0.30, "bonds")}, 0, 0.0)
    targets = targets_for({"A": (0.7, "equity"), "B": (0.3, "bonds")})
    s = [x for x in run_rules(table, targets, cfg(max_position=0.6)) if x.rule == "concentration"][0]
    assert s.trades[0].side == "sell" and s.trades[0].quantity == 10
    assert s.effect["A"][1] == pytest.approx(0.6)


def test_excess_cash_is_deployed():
    # Weights are inside bands, but cash is 10% (> 5% max, target 0).
    table = make_table({"A": (45, 10, 0.5, "equity"), "B": (45, 10, 0.5, "bonds")}, 100, 0.0)
    targets = targets_for({"A": (0.5, "equity"), "B": (0.5, "bonds")})
    rules = {s.rule: s for s in run_rules(table, targets, cfg())}
    assert "rebalance" not in rules
    assert rules["cash"].severity == "action"
    assert {t.ticker for t in rules["cash"].trades} == {"A", "B"}
    assert "10.0%" in rules["cash"].why


def test_correlation_and_underperformer_flags():
    table = make_table({"A": (40, 10, 0.4, "equity"), "B": (40, 10, 0.4, "equity"), "C": (20, 10, 0.2, "bonds")}, 0, 0)
    targets = targets_for({"A": (0.4, "equity"), "B": (0.4, "equity"), "C": (0.2, "bonds")})
    out = run_rules(table, targets, cfg(), corr_pairs=[("A", "B", 0.97)],
                    returns={"A": -0.10, "B": 0.15, "C": -0.30})
    rules = {s.rule: s for s in out}
    assert "0.97" in rules["correlation"].why
    assert rules["underperformer"].title == "Review A"  # C has no bond peers, so it is not flagged
