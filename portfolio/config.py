"""Load and validate config.yaml and targets.yaml.

YAML files use percentages written as percent (5 = 5%). Everything returned
from here uses fractions (0.05) so the rest of the code never has to guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CASH = "CASH"
SUPPORTED_BASES = ("GBP", "EUR")
RISK_PROFILES = ("cautious", "balanced", "growth", "aggressive")
TOLERANCE = 0.001  # 0.1 pp slack when checking that weights sum to 100%


class ConfigError(ValueError):
    """Raised when config.yaml or targets.yaml is invalid."""


@dataclass
class Config:
    base_currency: str = "GBP"
    risk_profile: str = "balanced"
    horizon_years: float = 10
    risk_free_rate: float = 0.04
    abs_drift: float = 0.05
    rel_drift: float = 0.25
    allow_sells: bool = True
    fractional_shares: bool = False
    max_position: float = 0.25
    min_trade_size: float = 100.0
    cash_max: float = 0.05
    fixed_fee: float = 0.0
    commission_bps: float = 0.0
    spread_bps: float = 10.0
    corr_threshold: float = 0.90
    corr_lookback_days: int = 365
    underperf_lookback_days: int = 365
    underperf_threshold: float = 0.15
    offline: bool = False
    cache_dir: Path = Path(".cache/prices")
    max_age_hours: float = 12


@dataclass
class Target:
    ticker: str
    weight: float  # fraction of total portfolio value
    asset_class: str
    region: str = ""
    currency: str | None = None  # quote currency hint, e.g. "GBp"
    name: str = ""


@dataclass
class Targets:
    holdings: dict[str, Target]
    asset_classes: dict[str, float] = field(default_factory=dict)

    @property
    def cash_weight(self) -> float:
        cash = self.holdings.get(CASH)
        return cash.weight if cash else 0.0

    @property
    def tickers(self) -> list[str]:
        """Investable tickers (everything except the CASH line)."""
        return [t for t in self.holdings if t != CASH]


def _pct(value, name: str) -> float:
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number, got {value!r}") from None


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    reb = raw.get("rebalancing", {}) or {}
    cash = raw.get("cash", {}) or {}
    costs = raw.get("costs", {}) or {}
    corr = raw.get("correlation", {}) or {}
    under = raw.get("underperformance", {}) or {}
    prices = raw.get("prices", {}) or {}
    d = Config()

    cfg = Config(
        base_currency=str(raw.get("base_currency", d.base_currency)).upper(),
        risk_profile=str(raw.get("risk_profile", d.risk_profile)).lower(),
        horizon_years=float(raw.get("investment_horizon_years", d.horizon_years)),
        risk_free_rate=_pct(raw.get("risk_free_rate_pct", 4.0), "risk_free_rate_pct"),
        abs_drift=_pct(reb.get("absolute_drift_pp", 5), "absolute_drift_pp"),
        rel_drift=_pct(reb.get("relative_drift_pct", 25), "relative_drift_pct"),
        allow_sells=bool(reb.get("allow_sells", d.allow_sells)),
        fractional_shares=bool(reb.get("fractional_shares", d.fractional_shares)),
        max_position=_pct(raw.get("max_position_pct", 25), "max_position_pct"),
        min_trade_size=float(raw.get("min_trade_size", d.min_trade_size)),
        cash_max=_pct(cash.get("max_pct", 5), "cash.max_pct"),
        fixed_fee=float(costs.get("fixed_fee", d.fixed_fee)),
        commission_bps=float(costs.get("commission_bps", d.commission_bps)),
        spread_bps=float(costs.get("spread_bps", d.spread_bps)),
        corr_threshold=float(corr.get("threshold", d.corr_threshold)),
        corr_lookback_days=int(corr.get("lookback_days", d.corr_lookback_days)),
        underperf_lookback_days=int(under.get("lookback_days", d.underperf_lookback_days)),
        underperf_threshold=_pct(under.get("threshold_pp", 15), "underperformance.threshold_pp"),
        offline=bool(prices.get("offline", d.offline)),
        cache_dir=Path(prices.get("cache_dir", d.cache_dir)),
        max_age_hours=float(prices.get("max_age_hours", d.max_age_hours)),
    )

    if cfg.base_currency not in SUPPORTED_BASES:
        raise ConfigError(f"base_currency must be one of {SUPPORTED_BASES}, got {cfg.base_currency}")
    if cfg.risk_profile not in RISK_PROFILES:
        raise ConfigError(f"risk_profile must be one of {RISK_PROFILES}, got {cfg.risk_profile}")
    if not 0 < cfg.max_position <= 1:
        raise ConfigError("max_position_pct must be between 0 and 100")
    if cfg.abs_drift <= 0 or cfg.rel_drift <= 0:
        raise ConfigError("drift thresholds must be positive")
    if not cfg.cache_dir.is_absolute():
        cfg.cache_dir = Path(path).parent / cfg.cache_dir
    return cfg


def load_targets(path: Path) -> Targets:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    holdings_raw = raw.get("holdings") or {}
    if not holdings_raw:
        raise ConfigError("targets.yaml has no holdings")

    holdings: dict[str, Target] = {}
    for ticker, spec in holdings_raw.items():
        spec = spec or {}
        ticker = str(ticker).strip()
        holdings[ticker] = Target(
            ticker=ticker,
            weight=_pct(spec.get("weight"), f"weight of {ticker}"),
            asset_class=str(spec.get("asset_class", "cash" if ticker == CASH else "")).lower(),
            region=str(spec.get("region", "")),
            currency=spec.get("currency"),
            name=str(spec.get("name", "")),
        )
    classes = {str(k).lower(): _pct(v, f"asset class {k}") for k, v in (raw.get("asset_classes") or {}).items()}
    targets = Targets(holdings=holdings, asset_classes=classes)
    validate_targets(targets)
    return targets


def validate_targets(targets: Targets) -> None:
    for t in targets.holdings.values():
        if t.weight < 0:
            raise ConfigError(f"target weight of {t.ticker} is negative")
        if not t.asset_class:
            raise ConfigError(f"{t.ticker} has no asset_class")

    total = sum(t.weight for t in targets.holdings.values())
    if abs(total - 1.0) > TOLERANCE:
        raise ConfigError(f"holding weights sum to {total:.2%}, they must sum to 100%")

    if targets.asset_classes:
        class_total = sum(targets.asset_classes.values())
        if abs(class_total - 1.0) > TOLERANCE:
            raise ConfigError(f"asset_classes sum to {class_total:.2%}, they must sum to 100%")
        implied = class_weights(targets)
        for cls in set(implied) | set(targets.asset_classes):
            a, b = implied.get(cls, 0.0), targets.asset_classes.get(cls, 0.0)
            if abs(a - b) > TOLERANCE:
                raise ConfigError(
                    f"asset class '{cls}': holdings add up to {a:.2%} but asset_classes says {b:.2%}"
                )


def class_weights(targets: Targets) -> dict[str, float]:
    """Target weight per asset class, derived from the holding targets."""
    out: dict[str, float] = {}
    for t in targets.holdings.values():
        out[t.asset_class] = out.get(t.asset_class, 0.0) + t.weight
    return out
