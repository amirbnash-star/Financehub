"""Starter target allocations per risk profile, and a check that targets fit the profile.

The equity/bond split per profile follows the common "strategic asset allocation"
bands used by UK/EU model portfolios (e.g. Vanguard LifeStrategy 20/40/60/80/100;
Vanguard, "Vanguard's framework for constructing globally diversified portfolios",
2015). A small gold sleeve is added as a diversifier (Baur & Lucey, "Is Gold a
Hedge or a Safe Haven?", Financial Review, 2010).

The ETFs below are widely held, low-cost, UCITS index funds. They are
starting points, NOT recommendations: check each fund's KID/factsheet, costs,
and whether it suits your tax wrapper before using it.
"""

from __future__ import annotations

from .config import Config, Targets

# profile -> (allowed equity range, model mix of asset classes in %)
PROFILES = {
    "cautious":   {"equity_range": (0.20, 0.45), "mix": {"equity": 35, "bonds": 55, "gold": 5, "cash": 5}},
    "balanced":   {"equity_range": (0.45, 0.70), "mix": {"equity": 60, "bonds": 33, "gold": 5, "cash": 2}},
    "growth":     {"equity_range": (0.70, 0.90), "mix": {"equity": 80, "bonds": 15, "gold": 3, "cash": 2}},
    "aggressive": {"equity_range": (0.88, 1.00), "mix": {"equity": 95, "bonds": 0, "gold": 3, "cash": 2}},
}

# Building blocks per base currency: asset class -> list of (ticker, share of class, meta)
UNIVERSE = {
    "GBP": {
        "equity": [
            ("VWRL.L", 0.85, {"region": "global", "currency": "GBP", "name": "Vanguard FTSE All-World"}),
            ("EMIM.L", 0.15, {"region": "emerging", "currency": "GBp", "name": "iShares Core MSCI EM IMI"}),
        ],
        "bonds": [("IGLT.L", 1.0, {"region": "uk", "currency": "GBp", "name": "iShares Core UK Gilts"})],
        "gold": [("SGLN.L", 1.0, {"region": "global", "currency": "GBp", "name": "iShares Physical Gold"})],
    },
    "EUR": {
        "equity": [
            ("VWCE.DE", 0.85, {"region": "global", "currency": "EUR", "name": "Vanguard FTSE All-World (Acc)"}),
            ("IS3N.DE", 0.15, {"region": "emerging", "currency": "EUR", "name": "iShares Core MSCI EM IMI"}),
        ],
        "bonds": [("EUNH.DE", 1.0, {"region": "eurozone", "currency": "EUR", "name": "iShares Core Euro Govt Bond"})],
        "gold": [("4GLD.DE", 1.0, {"region": "global", "currency": "EUR", "name": "Xetra-Gold"})],
    },
}

SHORT_HORIZON_YEARS = 5
SHORT_HORIZON_MAX_EQUITY = 0.60


def model_targets(profile: str, base: str) -> dict:
    """Return a targets.yaml-shaped dict for a risk profile and base currency."""
    mix = PROFILES[profile]["mix"]
    holdings: dict[str, dict] = {}
    for cls, pct in mix.items():
        if pct <= 0:
            continue
        if cls == "cash":
            holdings["CASH"] = {"weight": pct, "asset_class": "cash"}
            continue
        for ticker, share, meta in UNIVERSE[base][cls]:
            holdings[ticker] = {"weight": round(pct * share, 2), "asset_class": cls, **meta}
    # Fix rounding so weights sum to exactly 100.
    diff = round(100 - sum(h["weight"] for h in holdings.values()), 2)
    first = next(iter(holdings))
    holdings[first]["weight"] = round(holdings[first]["weight"] + diff, 2)
    return {"asset_classes": {k: v for k, v in mix.items() if v > 0}, "holdings": holdings}


def equity_share(weights_by_class: dict[str, float]) -> float:
    return sum(w for cls, w in weights_by_class.items() if cls.startswith("equit"))


def risk_profile_issues(targets: Targets, cfg: Config) -> list[str]:
    """Plain-language mismatches between the target mix and the stated risk profile/horizon."""
    from .config import class_weights

    issues = []
    eq = equity_share(class_weights(targets))
    lo, hi = PROFILES[cfg.risk_profile]["equity_range"]
    if not lo <= eq <= hi:
        issues.append(
            f"Target equity share is {eq:.0%}, outside the {lo:.0%}-{hi:.0%} range usual for a "
            f"'{cfg.risk_profile}' profile."
        )
    if cfg.horizon_years < SHORT_HORIZON_YEARS and eq > SHORT_HORIZON_MAX_EQUITY:
        issues.append(
            f"Investment horizon is {cfg.horizon_years:g} years but target equity is {eq:.0%}; "
            f"with under {SHORT_HORIZON_YEARS} years a large fall may not have time to recover."
        )
    return issues
