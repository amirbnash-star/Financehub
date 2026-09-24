"""Daily closing prices and FX rates, with a local CSV cache.

Prices come from Yahoo Finance via the `yfinance` library
(https://github.com/ranaroussi/yfinance). We use the unadjusted daily close
because holdings are valued as quantity x traded price, and dividends are
recorded separately as cash in transactions.csv. (Adjusted closes would
count dividends twice.) Splits are already applied by Yahoo.

Every download is saved to `cache_dir/<ticker>.csv`. If Yahoo is unreachable
or returns nothing, we fall back to the cache and add a warning to the
report. That way a flaky network never stops a run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

# London prices are often quoted in pence. Convert to the major unit.
_MINOR_UNITS = {"GBP": ("GBP", 1.0), "GBX": ("GBP", 0.01), "ZAC": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def normalise_currency(currency: str) -> tuple[str, float]:
    """Return (major currency, factor) so that price * factor is in the major unit.

    >>> normalise_currency("GBp")
    ('GBP', 0.01)
    """
    c = str(currency).strip()
    if c == "GBp":  # Yahoo's code for pence; case matters here
        return "GBP", 0.01
    return _MINOR_UNITS.get(c.upper(), (c.upper(), 1.0))


def fx_ticker(currency: str, base: str) -> str:
    """Yahoo ticker whose price is 'units of base per 1 unit of currency'."""
    return f"{currency}{base}=X"


class PriceSource(Protocol):
    """Anything that can supply daily closes. Tests use a fake one."""

    def history(self, ticker: str, start: pd.Timestamp) -> tuple[pd.Series, str | None]:
        """Return (daily close series indexed by date, quote currency or None)."""
        ...


class YahooSource:
    """Live prices from Yahoo Finance via yfinance."""

    def history(self, ticker: str, start: pd.Timestamp) -> tuple[pd.Series, str | None]:
        import yfinance as yf  # imported lazily so tests and offline runs never need it

        t = yf.Ticker(ticker)
        df = t.history(start=start.strftime("%Y-%m-%d"), auto_adjust=False, actions=False)
        if df is None or df.empty:
            return pd.Series(dtype=float), None
        close = df["Close"].copy()
        close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
        currency = (getattr(t, "history_metadata", None) or {}).get("currency")
        return close.astype(float), currency


@dataclass
class PriceBook:
    """Fetches, caches and serves closes (in major currency units) and FX rates."""

    cache_dir: Path
    base_currency: str
    source: PriceSource | None = None  # None = offline, cache only
    max_age_hours: float = 12
    currency_hints: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    currencies: dict[str, str] = field(default_factory=dict)  # ticker -> major currency

    # -- cache ---------------------------------------------------------------
    def _path(self, ticker: str) -> Path:
        safe = ticker.replace("/", "_").replace("^", "_").replace("=", "_")
        return Path(self.cache_dir) / f"{safe}.csv"

    def _read_cache(self, ticker: str) -> tuple[pd.Series, str | None]:
        p = self._path(ticker)
        if not p.exists():
            return pd.Series(dtype=float), None
        df = pd.read_csv(p, parse_dates=["date"])
        currency = df["currency"].dropna().iloc[-1] if "currency" in df and df["currency"].notna().any() else None
        return df.set_index("date")["close"].astype(float), currency

    def _write_cache(self, ticker: str, close: pd.Series, currency: str | None) -> None:
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"date": close.index, "close": close.values, "currency": currency or ""})
        df.to_csv(self._path(ticker), index=False, date_format="%Y-%m-%d")

    def _is_fresh(self, ticker: str) -> bool:
        p = self._path(ticker)
        return p.exists() and (time.time() - p.stat().st_mtime) < self.max_age_hours * 3600

    # -- public --------------------------------------------------------------
    def raw_history(self, ticker: str, start: pd.Timestamp) -> tuple[pd.Series, str | None]:
        """Close series in the quote currency, using the cache when possible."""
        cached, currency = self._read_cache(ticker)
        covers_start = not cached.empty and cached.index.min() <= start + pd.Timedelta(days=7)
        if self.source is None or (covers_start and self._is_fresh(ticker)):
            if cached.empty:
                self.warnings.append(f"No price data for {ticker} (offline and not in cache).")
            return cached, currency
        try:
            fetch_from = start if not covers_start else cached.index.max() - pd.Timedelta(days=7)
            fresh, fresh_ccy = self.source.history(ticker, fetch_from)
        except Exception as exc:  # network errors, API changes, etc.
            fresh, fresh_ccy = pd.Series(dtype=float), None
            self.warnings.append(f"Could not download {ticker} ({type(exc).__name__}); using cached prices.")
        if fresh.empty:
            if cached.empty:
                self.warnings.append(f"No price data for {ticker}.")
            return cached, currency
        merged = pd.concat([cached, fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index().dropna()
        currency = fresh_ccy or currency
        self._write_cache(ticker, merged, currency)
        return merged, currency

    def closes(self, tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Business-day closes in each ticker's major currency, forward-filled.

        Also fills `self.currencies` with each ticker's major currency.
        """
        index = pd.bdate_range(start, end)
        out = pd.DataFrame(index=index, dtype=float)
        for ticker in tickers:
            series, quote_ccy = self.raw_history(ticker, start)
            quote_ccy = quote_ccy or self.currency_hints.get(ticker)
            if not quote_ccy:
                quote_ccy = self.base_currency
                if not series.empty:
                    self.warnings.append(f"Unknown currency for {ticker}; assuming {quote_ccy}.")
            major, factor = normalise_currency(quote_ccy)
            self.currencies[ticker] = major
            out[ticker] = _align(series * factor, index)
        return out

    def fx(self, currencies: set[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Units of base currency per one unit of each currency, per business day."""
        index = pd.bdate_range(start, end)
        out = pd.DataFrame(index=index, dtype=float)
        for ccy in sorted(currencies):
            if ccy == self.base_currency:
                out[ccy] = 1.0
                continue
            series, _ = self.raw_history(fx_ticker(ccy, self.base_currency), start)
            out[ccy] = _align(series, index)
        return out


def _align(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Reindex to business days, carrying the last known price forward (and back
    for the first few days so a series that starts slightly late is still usable)."""
    if series.empty:
        return pd.Series(float("nan"), index=index)
    s = series[~series.index.duplicated(keep="last")].sort_index()
    return s.reindex(index.union(s.index)).ffill().reindex(index).bfill(limit=10)
