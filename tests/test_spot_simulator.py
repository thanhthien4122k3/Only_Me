import pytest

from markets.spot import buy_fill, market_value, new_account, sell_fill


def test_buy_deducts_cash_with_fee():
    acc = new_account(1000)
    buy_fill(acc, "BTC/USDT", 1.0, 100.0, 0.001)
    assert acc["cash"] == pytest.approx(1000 - 100 * 1.001)
    assert acc["assets"]["BTC/USDT"] == 1.0


def test_sell_adds_cash_net_fee():
    acc = new_account(1000)
    buy_fill(acc, "BTC/USDT", 1.0, 100.0, 0.001)
    sell_fill(acc, "BTC/USDT", 0.4, 120.0, 0.001)
    assert acc["cash"] == pytest.approx(1000 - 100 * 1.001 + 0.4 * 120 * 0.999)
    assert acc["assets"]["BTC/USDT"] == pytest.approx(0.6)


def test_insufficient_balance_rejected():
    with pytest.raises(ValueError):
        buy_fill(new_account(50), "BTC/USDT", 1.0, 100.0, 0.001)


def test_insufficient_asset_rejected():
    acc = new_account(1000)
    with pytest.raises(ValueError):
        sell_fill(acc, "BTC/USDT", 1.0, 100.0, 0.001)


def test_market_value_cash_plus_assets():
    acc = new_account(1000)
    buy_fill(acc, "BTC/USDT", 2.0, 100.0, 0.0)
    assert market_value(acc, {"BTC/USDT": 110.0}) == pytest.approx(1000 - 200 + 220)
