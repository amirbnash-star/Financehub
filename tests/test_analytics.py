import numpy as np
import pandas as pd
import pytest

from portfolio import analytics as A
from portfolio.config import Config, Target, Targets
from portfolio.prices import PriceBook
from portfolio.valuation import value_portfolio

from conftest import FakeSource, make_tx


def test_xirr_matches_excel_documentation_example():
    # Example from Microsoft's XIRR documentation: result 0.373362535
    flows = [("2008-01-01", -10000), ("2008-03-01", 2750), ("2008-10-30", 4250),
             ("2009-02-15", 3250), ("2009-04-01", 2750)]
    assert A.xirr(flows) == pytest.approx(0.373362535, abs=1e-6)


def test_xirr_simple_and_degenerate():
    r = A.xirr([("2020-01-01", -1000), ("2021-01-01", 1100)])
    assert r == pytest.approx(1.1 ** (365 / 366) - 1, abs=1e-8)
    assert A.xirr([("2020-01-01", -1000)]) is None
    assert A.xirr([("2020-01-01", 100), ("2021-01-01", 100)]) is None


def test_twr_removes_deposit_effect():
    idx = pd.bdate_range("2024-01-01", periods=4)
    daily = pd.DataFrame({"value": [100, 110, 210, 231], "flow": [100, 0, 100, 0]}, index=idx)
    r = A.daily_twr_returns(daily)
    assert list(r.round(10)) == [0.1, 0.0, 0.1]
    assert A.total_return(r) == pytest.approx(0.21)


def test_max_drawdown_and_series():
    r = pd.Series([0.1, -0.5, 0.2], index=pd.bdate_range("2024-01-01", periods=3))
    dd = A.max_drawdown(r)
    assert dd.max_drawdown == pytest.approx(-0.5)
    assert dd.peak == r.index[0] and dd.trough == r.index[1]


def test_volatility_and_sharpe_formula():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0005, 0.01, 500))
    assert A.volatility(r) == pytest.approx(r.std() * np.sqrt(252))
    rf = 0.02
    rfd = (1 + rf) ** (1 / 252) - 1
    expected = (r - rfd).mean() / (r - rfd).std() * np.sqrt(252)
    assert A.sharpe_ratio(r, rf) == pytest.approx(expected)
    assert A.volatility(r[:5]) is None  # too short to be meaningful


def test_hhi_and_effective_n():
    assert A.hhi(pd.Series([0.5, 0.5])) == pytest.approx(0.5)
    assert A.hhi(pd.Series([0.25] * 4)) == pytest.approx(0.25)
    assert A.hhi(pd.Series([0.3, 0.3, 0.0])) == pytest.approx(0.5)  # renormalised, zeros ignored


def test_high_correlations():
    corr = pd.DataFrame([[1, 0.95, 0.1], [0.95, 1, 0.2], [0.1, 0.2, 1]], index=list("ABC"), columns=list("ABC"))
    assert A.high_correlations(corr, 0.9) == [("A", "B", 0.95)]


def _setup(tmp_path):
    idx = pd.bdate_range("2023-01-02", "2024-06-28")
    aaa = pd.Series(np.linspace(50, 60, len(idx)), index=idx)  # GBP
    bbb = pd.Series(20.0, index=idx)  # USD
    usd = pd.Series(0.8, index=idx)
    src = FakeSource({"AAA": aaa, "BBB": bbb, "USDGBP=X": usd}, {"AAA": "GBP", "BBB": "USD"})
    book = PriceBook(tmp_path, "GBP", source=src)
    targets = Targets({
        "AAA": Target("AAA", 0.6, "equity"),
        "BBB": Target("BBB", 0.3, "equity"),
        "CASH": Target("CASH", 0.1, "cash"),
    })
    tx = make_tx([
        ("2023-01-02", "", "deposit", 1000, 1, "GBP", 0),
        ("2023-01-02", "AAA", "buy", 10, 50, "GBP", 0),
        ("2023-01-03", "BBB", "buy", 5, 20, "USD", 0),  # no USD cash -> implicit deposit
    ])
    return tx, targets, book


def test_valuation_multi_currency(tmp_path):
    tx, targets, book = _setup(tmp_path)
    val = value_portfolio(tx, targets, Config(), book, as_of="2024-06-28")
    t = val.table
    assert t.loc["AAA", "value"] == pytest.approx(600)
    assert t.loc["BBB", "value"] == pytest.approx(5 * 20 * 0.8)
    assert val.cash == pytest.approx(500)
    assert val.total == pytest.approx(1180)
    assert t["weight"].sum() == pytest.approx(1)
    assert t.loc["AAA", "drift"] == pytest.approx(600 / 1180 - 0.6)
    assert any("implicit deposit" in w for w in val.warnings)
    # The implicit USD deposit is an external flow, so it must not count as a return.
    assert val.daily["flow"].sum() == pytest.approx(1000 + 80)


def test_analyse_end_to_end(tmp_path):
    tx, targets, book = _setup(tmp_path)
    val = value_portfolio(tx, targets, Config(), book, as_of="2024-06-28")
    a = A.analyse(val, targets, Config())
    # Only AAA moved: +100 GBP on 1080 invested.
    assert a.twr_total == pytest.approx(100 / 1080, rel=0.02)
    assert a.xirr > 0
    assert a.largest[0] == "AAA"
    assert a.holding_xirr["BBB"] == pytest.approx(0, abs=1e-6)
    assert a.class_table.loc["equity", "target"] == pytest.approx(0.9)
