"""Performance and risk analytics. Each function names its method and source;
the full citations live in methods.py and are printed in every report."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import CASH, Config, Targets
from .valuation import Valuation

TRADING_DAYS = 252
MIN_OBSERVATIONS = 20  # fewer daily returns than this -> risk metrics are not meaningful


# --- Returns ----------------------------------------------------------------

def daily_twr_returns(daily: pd.DataFrame) -> pd.Series:
    """Daily time-weighted returns, removing the effect of deposits/withdrawals.

    Method: TWR by chain-linking sub-period returns (CFA Institute, GIPS 2020,
    provision 2.A.32; Bacon, "Practical Portfolio Performance Measurement", 2008, ch.2).
    Flows are assumed to arrive at the end of the day, so
        r_t = (V_t - F_t) / V_{t-1} - 1.
    Days with no prior value (before the first deposit) are skipped.
    """
    v, f = daily["value"], daily["flow"]
    prev = v.shift(1)
    r = (v - f) / prev - 1
    return r[prev > 0].replace([np.inf, -np.inf], np.nan).dropna()


def total_return(returns: pd.Series) -> float:
    return float((1 + returns).prod() - 1) if len(returns) else 0.0


def annualise(total: float, days: float) -> float | None:
    """Geometric annualisation of a total return over `days` calendar days."""
    if days < 365 or total <= -1:  # annualising less than a year exaggerates
        return None
    return (1 + total) ** (365.25 / days) - 1


def xirr(cashflows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """Money-weighted return: the annual rate r that makes the NPV of cash flows zero,
        sum_i CF_i / (1 + r) ** (days_i / 365) = 0
    (the XIRR definition used by Excel / LibreOffice; CFA Institute, "Investment
    Performance Measurement", MWR). Investor outflows are negative, inflows and the
    final value positive. Solved by bisection, which always converges once a sign
    change is bracketed (Burden & Faires, "Numerical Analysis", §2.1).
    """
    flows = [(pd.Timestamp(d), float(a)) for d, a in cashflows if abs(a) > 1e-9]
    if len(flows) < 2 or all(a >= 0 for _, a in flows) or all(a <= 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)
    years = np.array([(d - t0).days / 365.0 for d, _ in flows])
    amounts = np.array([a for _, a in flows])
    if years.max() < 1 / 365:
        return None

    def npv(rate: float) -> float:
        return float(np.sum(amounts / (1.0 + rate) ** years))

    lo, hi = -0.9999, 1.0
    while npv(hi) > 0 and hi < 1e6:  # widen the bracket for very high returns
        hi *= 2
    if np.sign(npv(lo)) == np.sign(npv(hi)):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if np.sign(npv(mid)) == np.sign(npv(lo)):
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2


def _fx_on(val: Valuation, currency: str, date) -> float:
    return float(val.fx[currency].asof(pd.offsets.BDay().rollforward(date)))


def portfolio_cashflows(val: Valuation) -> list[tuple[pd.Timestamp, float]]:
    """Deposits (negative for the investor), withdrawals (positive), plus today's value."""
    ext = val.tx[val.tx["action"].isin(["deposit", "withdrawal"])]
    flows = []
    for r in ext.to_dict("records"):
        # Fees on a deposit/withdrawal are a portfolio cost, so the investor's flow is the amount.
        sign = -1 if r["action"] == "deposit" else 1
        flows.append((r["date"], sign * r["amount"] * _fx_on(val, r["currency"], r["date"])))
    flows.append((val.as_of, val.total))
    return flows


def holding_cashflows(val: Valuation, ticker: str) -> list[tuple[pd.Timestamp, float]]:
    """Buys (negative), sells and dividends (positive), plus the holding's value today."""
    rows = val.tx[val.tx["ticker"] == ticker]
    flows = []
    for r in rows.to_dict("records"):
        rate = _fx_on(val, r["currency"], r["date"])
        if r["action"] == "buy":
            flows.append((r["date"], -(r["amount"] + r["fees"]) * rate))
        elif r["action"] in ("sell", "dividend"):
            flows.append((r["date"], (r["amount"] - r["fees"]) * rate))
    value = float(val.table.loc[ticker, "value"]) if ticker in val.table.index else 0.0
    flows.append((val.as_of, value))
    return flows


# --- Risk -------------------------------------------------------------------

def volatility(returns: pd.Series) -> float | None:
    """Annualised standard deviation of daily returns, scaled by sqrt(252)
    (Hull, "Options, Futures and Other Derivatives", ch.15: volatility scales with sqrt(time))."""
    if len(returns) < MIN_OBSERVATIONS:
        return None
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))


def sharpe_ratio(returns: pd.Series, risk_free_rate: float) -> float | None:
    """Annualised Sharpe ratio = mean excess daily return / std of daily returns x sqrt(252)
    (Sharpe, "The Sharpe Ratio", Journal of Portfolio Management, 1994)."""
    if len(returns) < MIN_OBSERVATIONS:
        return None
    rf_daily = (1 + risk_free_rate) ** (1 / TRADING_DAYS) - 1
    excess = returns - rf_daily
    sd = excess.std(ddof=1)
    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else None


def drawdown_series(returns: pd.Series) -> pd.Series:
    """Percentage fall from the running peak of the TWR growth index.

    Using TWR (not raw portfolio value) means deposits do not hide losses and
    withdrawals do not look like drawdowns (Magdon-Ismail & Atiya, "Maximum
    Drawdown", Risk Magazine, 2004)."""
    growth = (1 + returns).cumprod()
    return growth / growth.cummax() - 1


@dataclass
class Drawdown:
    max_drawdown: float
    peak: pd.Timestamp | None
    trough: pd.Timestamp | None


def max_drawdown(returns: pd.Series) -> Drawdown | None:
    if len(returns) < 2:
        return None
    dd = drawdown_series(returns)
    trough = dd.idxmin()
    growth = (1 + returns).cumprod()
    peak = growth.loc[:trough].idxmax()
    return Drawdown(float(dd.min()), peak, trough)


def correlation_matrix(prices_base: pd.DataFrame, tickers: list[str], lookback_days: int) -> pd.DataFrame:
    """Pearson correlation of daily returns in base currency over the lookback window
    (Markowitz, "Portfolio Selection", Journal of Finance, 1952: diversification depends
    on correlations; pandas.DataFrame.corr)."""
    cols = [t for t in tickers if t in prices_base and prices_base[t].notna().sum() > MIN_OBSERVATIONS]
    if len(cols) < 2:
        return pd.DataFrame()
    window = prices_base[cols].loc[prices_base.index[-1] - pd.Timedelta(days=lookback_days):]
    returns = window.pct_change(fill_method=None).dropna(how="all")
    return returns.corr(min_periods=MIN_OBSERVATIONS)


def high_correlations(corr: pd.DataFrame, threshold: float) -> list[tuple[str, str, float]]:
    pairs = []
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            c = corr.loc[a, b]
            if pd.notna(c) and c >= threshold:
                pairs.append((a, b, float(c)))
    return sorted(pairs, key=lambda p: -p[2])


def hhi(weights: pd.Series) -> float:
    """Herfindahl-Hirschman index: sum of squared weights of the invested positions
    (Herfindahl 1950; Hirschman 1964). 1/HHI is the 'effective number' of equally
    sized positions (Adelman, JASA, 1969)."""
    w = weights[weights > 0]
    if w.sum() <= 0:
        return 0.0
    w = w / w.sum()
    return float((w ** 2).sum())


def period_return(prices_base: pd.DataFrame, ticker: str, lookback_days: int) -> float | None:
    """Simple price return in base currency over the lookback window."""
    if ticker not in prices_base:
        return None
    s = prices_base[ticker].dropna()
    if s.empty:
        return None
    start = s.index[-1] - pd.Timedelta(days=lookback_days)
    if s.index[0] > start + pd.Timedelta(days=7):
        return None  # not enough history
    past = s.asof(start)
    return float(s.iloc[-1] / past - 1) if past and past > 0 else None


# --- Summary ----------------------------------------------------------------

@dataclass
class Analytics:
    twr_total: float
    twr_annual: float | None
    xirr: float | None
    volatility: float | None
    sharpe: float | None
    drawdown: Drawdown | None
    hhi: float
    effective_n: float | None
    largest: tuple[str, float] | None
    class_table: pd.DataFrame  # index asset_class: weight, target, drift
    holding_xirr: dict[str, float | None]
    holding_return: dict[str, float | None]  # lookback price return
    corr: pd.DataFrame
    corr_pairs: list[tuple[str, str, float]]
    returns: pd.Series
    days: int


def analyse(val: Valuation, targets: Targets, cfg: Config) -> Analytics:
    returns = daily_twr_returns(val.daily)
    days = (val.daily.index[-1] - val.daily.index[0]).days if len(val.daily) else 0
    twr_total = total_return(returns)
    invested = val.table.drop(index=CASH, errors="ignore")
    held = invested[invested["value"] > 0]
    h = hhi(held["weight"])
    largest = (held["weight"].idxmax(), float(held["weight"].max())) if not held.empty else None

    classes = val.table.groupby("asset_class")[["weight", "target"]].sum()
    classes["drift"] = classes["weight"] - classes["target"]

    corr = correlation_matrix(val.prices_base, list(held.index), cfg.corr_lookback_days)
    return Analytics(
        twr_total=twr_total,
        twr_annual=annualise(twr_total, days),
        xirr=xirr(portfolio_cashflows(val)),
        volatility=volatility(returns),
        sharpe=sharpe_ratio(returns, cfg.risk_free_rate),
        drawdown=max_drawdown(returns),
        hhi=h,
        effective_n=1 / h if h > 0 else None,
        largest=largest,
        class_table=classes,
        holding_xirr={t: xirr(holding_cashflows(val, t)) for t in held.index},
        holding_return={t: period_return(val.prices_base, t, cfg.underperf_lookback_days) for t in invested.index},
        corr=corr,
        corr_pairs=high_correlations(corr, cfg.corr_threshold),
        returns=returns,
        days=days,
    )
