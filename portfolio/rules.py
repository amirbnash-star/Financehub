"""Rule engine: check the portfolio against its rules and produce suggestions.

Each Suggestion states WHAT to consider doing, WHY (the rule and the numbers
that triggered it), the expected EFFECT on weights, and the estimated COST.
If no rule fires the list is empty and the report says nothing needs doing.

Suggestions are advice for the owner to act on (or not). Nothing here trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .analytics import Analytics
from .build import risk_profile_issues
from .config import CASH, Config, Targets
from .rebalance import Plan, Trade, band, is_breached, plan_rebalance, trim_trade
from .valuation import Valuation

ACTION, REVIEW, INFO = "action", "review", "info"


@dataclass
class Suggestion:
    rule: str
    severity: str  # action | review | info
    title: str
    what: str
    why: str
    trades: list[Trade] = field(default_factory=list)
    effect: dict[str, tuple[float, float]] = field(default_factory=dict)  # ticker -> (before, after)
    cost: float = 0.0
    methods: list[str] = field(default_factory=list)  # keys into methods.METHODS


def _effect(plan: Plan, tickers) -> dict[str, tuple[float, float]]:
    return {t: (float(plan.before.get(t, 0)), float(plan.after.get(t, 0))) for t in tickers}


def _describe_trades(trades: list[Trade], base: str) -> str:
    return "; ".join(
        f"{t.side.upper()} {t.quantity:g} {t.ticker} @ ~{t.price:,.2f} {t.currency} (≈{base} {t.value:,.0f})"
        for t in trades
    )


def evaluate(val: Valuation, an: Analytics, targets: Targets, cfg: Config) -> list[Suggestion]:
    out: list[Suggestion] = []
    table = val.table
    total = val.total
    base = cfg.base_currency
    if total <= 0:
        return out

    missing = [t for t in table.index if t != CASH and pd.isna(table.loc[t, "price_base"])]
    if missing:
        out.append(Suggestion(
            "data", REVIEW, "Missing prices",
            f"Check the tickers {', '.join(missing)} (spelling, exchange suffix) or add a price manually.",
            "No market price or traded price was found, so these holdings are valued at zero and not traded.",
        ))

    # 1. Drift-band rebalancing -----------------------------------------------
    invest = table.drop(index=CASH)
    breached = [t for t, r in invest.iterrows()
                if r["target"] > 0 and pd.notna(r["price_base"]) and is_breached(r["weight"], r["target"], cfg)]
    rebalance_plan = None
    if breached:
        plan = plan_rebalance(table, cfg, allow_sells=cfg.allow_sells)
        rebalance_plan = plan
        lines = []
        for t in breached:
            r = invest.loc[t]
            lines.append(
                f"{t} is {r['weight']:.1%} vs target {r['target']:.1%} (drift {r['drift'] * 100:+.1f} pp, "
                f"{r['drift'] / r['target']:+.0%} relative; band ±{band(r['target'], cfg) * 100:.1f} pp)"
            )
        classes = an.class_table.drop(index="cash", errors="ignore")
        for cls, r in classes.iterrows():
            if r["target"] > 0 and is_breached(r["weight"], r["target"], cfg):
                lines.append(f"asset class {cls} is {r['weight']:.1%} vs target {r['target']:.1%}")
        why = (f"Drift-band rule (±{cfg.abs_drift * 100:g} pp absolute or ±{cfg.rel_drift:.0%} relative): "
               + "; ".join(lines) + ".")
        if plan.trades:
            what = _describe_trades(plan.trades, base) + "."
            if any(t.side == "buy" for t in plan.trades) and not any(t.side == "sell" for t in plan.trades):
                what += " Uses spare cash only, no sells."
            severity = ACTION
        else:
            what = "No trade is large enough to pass the minimum trade size; keep watching."
            severity = INFO
        if plan.new_money_needed:
            what += (f" Selling is switched off: depositing about {base} {plan.new_money_needed:,.0f} and investing it "
                     "in the underweight holdings would bring the overweights back inside their bands.")
        tickers = {t.ticker for t in plan.trades} | set(breached)
        out.append(Suggestion("rebalance", severity, "Rebalance toward targets", what, why, plan.trades,
                              _effect(plan, sorted(tickers) + [CASH]), plan.total_cost, ["drift_band", "cash_first"]))

    # 2. Concentration --------------------------------------------------------
    for t, r in invest.iterrows():
        if r["weight"] <= cfg.max_position:
            continue
        limit = min(cfg.max_position, r["target"]) if r["target"] > 0 else cfg.max_position
        covered = rebalance_plan and any(
            tr.ticker == t and tr.side == "sell" and rebalance_plan.after.get(t, 1) <= cfg.max_position
            for tr in rebalance_plan.trades)
        why = (f"Max position rule: {t} is {r['weight']:.1%} of the portfolio, above the "
               f"{cfg.max_position:.0%} limit. HHI {an.hhi:.3f} (≈{an.effective_n or 0:.1f} equal-sized positions).")
        if covered:
            out.append(Suggestion("concentration", INFO, f"{t} above max weight", "Already handled by the rebalance "
                                  "suggestion above.", why, methods=["hhi"]))
            continue
        trade = trim_trade(r, total, limit, cfg)
        if trade is None:
            continue
        after_w = (r["value"] - trade.value) / (total - trade.cost)
        cash_after = (val.cash + trade.value - trade.cost) / (total - trade.cost)
        out.append(Suggestion(
            "concentration", ACTION, f"Trim {t}",
            _describe_trades([trade], base) + ". Proceeds go to cash; run again to see where to deploy them.",
            why, [trade], {t: (float(r["weight"]), after_w), CASH: (float(table.loc[CASH, "weight"]), cash_after)},
            trade.cost, ["hhi"],
        ))

    # 3. Excess cash ---------------------------------------------------------
    cash_w = float(table.loc[CASH, "weight"])
    cash_used = rebalance_plan and any(t.side == "buy" for t in rebalance_plan.trades)
    if cash_w > cfg.cash_max and not cash_used:
        plan = plan_rebalance(table, cfg, allow_sells=False)
        why = (f"Cash rule: cash is {cash_w:.1%} ({base} {val.cash:,.0f}) of the portfolio, above the "
               f"{cfg.cash_max:.0%} maximum (target {table.loc[CASH, 'target']:.1%}).")
        if plan.trades:
            out.append(Suggestion(
                "cash", ACTION, "Deploy spare cash",
                _describe_trades(plan.trades, base) + ". Buys go to the most underweight holdings first.",
                why, plan.trades, _effect(plan, [t.ticker for t in plan.trades] + [CASH]), plan.total_cost,
                ["cash_first"],
            ))

    # 4. Correlation / redundancy -------------------------------------------
    for a, b, c in an.corr_pairs:
        out.append(Suggestion(
            "correlation", REVIEW, f"{a} and {b} move together",
            f"Review whether you need both {a} and {b}; they may be largely the same exposure. "
            "Keeping both is fine if it is deliberate (e.g. a tilt).",
            f"Correlation rule: daily returns over the last {cfg.corr_lookback_days} days have a correlation of "
            f"{c:.2f}, above the {cfg.corr_threshold:.2f} threshold.",
            methods=["correlation"],
        ))

    # 5. Underperformers vs asset-class peers --------------------------------
    rets = {t: an.holding_return.get(t) for t in invest.index if invest.loc[t, "value"] > 0}
    for t, r in rets.items():
        if r is None:
            continue
        cls = invest.loc[t, "asset_class"]
        peers = [v for p, v in rets.items() if p != t and v is not None and invest.loc[p, "asset_class"] == cls]
        if not peers:
            continue
        peer_avg = sum(peers) / len(peers)
        if r < peer_avg - cfg.underperf_threshold:
            out.append(Suggestion(
                "underperformer", REVIEW, f"Review {t}",
                f"Check whether {t} still does its job in the portfolio (costs, tracking, thesis). "
                "Underperformance alone is not a reason to sell.",
                f"Underperformance rule: {t} returned {r:+.1%} over {cfg.underperf_lookback_days} days vs "
                f"{peer_avg:+.1%} for other {cls} holdings, a gap of {(r - peer_avg) * 100:.1f} pp "
                f"(threshold {cfg.underperf_threshold * 100:g} pp).",
                methods=["period_return"],
            ))

    # 6. Holdings without a target -------------------------------------------
    for t, r in invest.iterrows():
        if r["target"] == 0 and r["value"] > 0:
            out.append(Suggestion(
                "untargeted", REVIEW, f"{t} has no target",
                f"Add {t} to targets.yaml, or consider whether to keep it. It is left out of rebalancing.",
                f"{t} is {r['weight']:.1%} of the portfolio but has no target weight.",
            ))

    # 7. Risk profile fit -----------------------------------------------------
    for issue in risk_profile_issues(targets, cfg):
        out.append(Suggestion("risk-profile", INFO, "Targets vs risk profile",
                              "Review targets.yaml or risk_profile in config.yaml.", issue, methods=["risk_profile"]))
    return out
