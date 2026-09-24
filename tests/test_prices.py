import os
import time

import pandas as pd
import pytest

from portfolio.prices import PriceBook, fx_ticker, normalise_currency

from conftest import FakeSource

IDX = pd.bdate_range("2024-01-01", "2024-01-31")


def series(value):
    return pd.Series(float(value), index=IDX)


def test_normalise_currency():
    assert normalise_currency("GBp") == ("GBP", 0.01)
    assert normalise_currency("GBX") == ("GBP", 0.01)
    assert normalise_currency("GBP") == ("GBP", 1.0)
    assert normalise_currency("usd") == ("USD", 1.0)
    assert fx_ticker("USD", "GBP") == "USDGBP=X"


def test_closes_convert_pence_and_cache(tmp_path):
    src = FakeSource({"IGLT.L": series(1050)}, {"IGLT.L": "GBp"})
    book = PriceBook(tmp_path, "GBP", source=src)
    df = book.closes(["IGLT.L"], IDX[0], IDX[-1])
    assert df["IGLT.L"].iloc[-1] == pytest.approx(10.5)
    assert book.currencies["IGLT.L"] == "GBP"
    assert (tmp_path / "IGLT.L.csv").exists()
    # Second call within max_age uses the cache, not the source.
    book.closes(["IGLT.L"], IDX[0], IDX[-1])
    assert src.calls == ["IGLT.L"]


def test_falls_back_to_cache_when_download_fails(tmp_path):
    PriceBook(tmp_path, "GBP", source=FakeSource({"AAA": series(5)}, {"AAA": "GBP"})).closes(["AAA"], IDX[0], IDX[-1])
    old = time.time() - 48 * 3600
    os.utime(tmp_path / "AAA.csv", (old, old))  # make the cache stale
    book = PriceBook(tmp_path, "GBP", source=FakeSource({}, fail=True))
    df = book.closes(["AAA"], IDX[0], IDX[-1])
    assert df["AAA"].iloc[-1] == 5
    assert any("Could not download AAA" in w for w in book.warnings)


def test_missing_ticker_warns_and_is_nan(tmp_path):
    book = PriceBook(tmp_path, "GBP", source=None)
    df = book.closes(["ZZZ"], IDX[0], IDX[-1])
    assert df["ZZZ"].isna().all()
    assert any("ZZZ" in w for w in book.warnings)


def test_fx_base_is_one_and_gaps_forward_fill(tmp_path):
    usd = series(0.8).drop(IDX[5:8])  # missing days are carried forward
    book = PriceBook(tmp_path, "GBP", source=FakeSource({"USDGBP=X": usd}))
    fx = book.fx({"GBP", "USD"}, IDX[0], IDX[-1])
    assert (fx["GBP"] == 1).all()
    assert fx["USD"].notna().all() and fx["USD"].iloc[6] == 0.8
