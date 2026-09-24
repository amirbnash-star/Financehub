import pytest

from portfolio.holdings import rebuild_holdings
from portfolio.transactions import TransactionError, append_transaction, load_transactions, validate_row

from conftest import make_tx


def test_average_cost_and_cash():
    tx = make_tx([
        ("2024-01-01", "", "deposit", 10_000, 1, "GBP", 0),
        ("2024-01-02", "AAA", "buy", 100, 10, "GBP", 5),
        ("2024-02-01", "AAA", "buy", 100, 20, "GBP", 5),
        ("2024-03-01", "AAA", "sell", 50, 30, "GBP", 5),
        ("2024-04-01", "AAA", "dividend", 150, 0.2, "GBP", 0),
    ])
    h = rebuild_holdings(tx)
    pos = h.positions["AAA"]
    # Pool: 200 shares cost 3010 -> avg 15.05; sell 50 removes 752.50
    assert pos.quantity == 150
    assert pos.average_cost == pytest.approx(15.05)
    assert pos.cost == pytest.approx(2257.5)
    assert pos.realised == pytest.approx(50 * 30 - 5 - 752.5)
    assert pos.dividends == pytest.approx(30)
    assert h.cash["GBP"] == pytest.approx(10_000 - 1005 - 2005 + 1495 + 30)


def test_pence_are_converted_to_pounds():
    tx = make_tx([("2024-01-02", "IGLT.L", "buy", 10, 1050, "GBp", 0)])
    assert tx.loc[0, "currency"] == "GBP"
    assert tx.loc[0, "price"] == pytest.approx(10.5)
    assert rebuild_holdings(tx).cash["GBP"] == pytest.approx(-105)


def test_as_of_filters_future_rows():
    tx = make_tx([
        ("2024-01-02", "AAA", "buy", 10, 10, "GBP", 0),
        ("2024-06-02", "AAA", "buy", 10, 10, "GBP", 0),
    ])
    assert rebuild_holdings(tx, as_of="2024-03-01").positions["AAA"].quantity == 10


def test_cannot_sell_more_than_held():
    tx = make_tx([
        ("2024-01-02", "AAA", "buy", 10, 10, "GBP", 0),
        ("2024-01-03", "AAA", "sell", 11, 10, "GBP", 0),
    ])
    with pytest.raises(TransactionError, match="only 10 held"):
        rebuild_holdings(tx)


def test_full_sale_closes_position():
    tx = make_tx([
        ("2024-01-02", "AAA", "buy", 10, 10, "GBP", 0),
        ("2024-01-03", "AAA", "sell", 10, 12, "GBP", 0),
    ])
    h = rebuild_holdings(tx)
    assert h.open_positions() == {}
    assert h.positions["AAA"].realised == pytest.approx(20)


@pytest.mark.parametrize("row, msg", [
    ({"date": "2024-01-01", "ticker": "", "action": "buy", "quantity": 1, "price": 1, "currency": "GBP"}, "needs a ticker"),
    ({"date": "2024-01-01", "ticker": "X", "action": "deposit", "quantity": 1, "currency": "GBP"}, "must not have a ticker"),
    ({"date": "nope", "ticker": "X", "action": "buy", "quantity": 1, "price": 1, "currency": "GBP"}, "bad date"),
    ({"date": "2024-01-01", "ticker": "X", "action": "short", "quantity": 1, "price": 1, "currency": "GBP"}, "action"),
    ({"date": "2024-01-01", "ticker": "X", "action": "buy", "quantity": -1, "price": 1, "currency": "GBP"}, "quantity"),
])
def test_invalid_rows(row, msg):
    with pytest.raises(TransactionError, match=msg):
        validate_row(row)


def test_append_and_reload(tmp_path):
    path = tmp_path / "transactions.csv"
    append_transaction(path, {"date": "2024-01-01", "action": "deposit", "quantity": 500, "price": 1, "currency": "GBP"})
    append_transaction(path, {"date": "2024-01-02", "ticker": "aaa", "action": "buy", "quantity": 2, "price": 100, "currency": "GBP", "fees": 1})
    tx = load_transactions(path)
    assert list(tx["action"]) == ["deposit", "buy"]
    assert tx.loc[1, "ticker"] == "AAA"
    assert rebuild_holdings(tx).cash["GBP"] == pytest.approx(299)


def test_missing_file_is_empty(tmp_path):
    assert load_transactions(tmp_path / "none.csv").empty
