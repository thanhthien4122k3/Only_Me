from copy import deepcopy

import pandas as pd
import pytest

from data import TF_MS
from engine import run_backtest
from markets import futures as f

BASE = 1_700_000_000_000
H1 = TF_MS["1h"]
META = {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1, "settleCcy": "USDT"}


def make_cfg():
    return {
        "backtest": {"initial_capital": 1000.0, "fee_pct": 0.1, "slippage_pct": 0.05,
                     "skip_cost_ratio": 0.0, "quote_currency": "USDT",
                     "fill_policy": "next_bar_open"},
        "spot": {"min_qty": 0.00001},
        "risk": {"risk_pct": 0.5, "notional_cap": 300.0, "max_positions": 3,
                 "stop_atr_mult": 1.5},
        "stops": {"ema_exit_enabled": True, "initial_stop_enabled": True,
                  "take_profit_r": None, "trailing_enabled": False,
                  "trailing_atr_mult": 2.5, "time_stop_bars": None},
        "futures": {"leverage": 2.0, "maintenance_margin_rate": 0.005,
                    "funding_enabled": True, "margin_mode": "isolated"},
    }


def make_features(closes, signals=(), atr=2.0, adx=30.0, high_pad=1.0, low_pad=1.0):
    n = len(closes)
    ts = [BASE + i * H1 for i in range(n)]
    return pd.DataFrame({
        "timestamp": pd.to_datetime(ts, unit="ms", utc=True),
        "open": closes,
        "high": [c + high_pad for c in closes],
        "low": [c - low_pad for c in closes],
        "close": closes,
        "atr": [atr] * n,
        "adx": [adx] * n,
        "signal": [i in signals for i in range(n)],
        "exit_trend": [False] * n,
    })


def test_round_to_contracts():
    assert f.round_to_contracts(0.015, META) == 0.01
    assert f.round_to_contracts(0.02, META) == 0.02
    assert f.round_to_contracts(0.009, META) == 0.0


def test_liquidation_price_long_formula():
    p = f.liquidation_price_long(100.0, leverage=10.0, mmr=0.005)
    assert p == pytest.approx(100.0 * (1.0 - (0.1 - 0.005)))


def test_futures_long_pnl_and_margin():
    feat = make_features([100.0] * 10, signals=[3])
    trades, orders, _, _, _ = run_backtest({"X/USDT": feat}, make_cfg(),
                                           market_type="futures",
                                           contract_meta={"X/USDT": META})
    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["market_type"] == "futures"
    assert t["entry_time"] == feat["timestamp"].iloc[4]
    # entry fill = open*(1+slip); qty rounded to contracts (0.01 BTC)
    expected_fill = 100.0 * 1.0005
    assert t["entry_price"] == pytest.approx(expected_fill)
    assert t["quantity"] % 0.01 == pytest.approx(0.0)
    # initial margin = notional / leverage
    assert t["initial_margin"] == pytest.approx(t["notional_value"] / 2.0)
    # closed at end of data at last close, net = realized - fees
    assert t["realized_pnl"] == pytest.approx(
        (100.0 * 0.9995 - expected_fill) * t["quantity"])
    assert t["net_pnl"] == pytest.approx(
        t["realized_pnl"] - t["entry_fee"] - t["exit_fee"])


def test_futures_insufficient_margin_rejected():
    cfg = make_cfg()
    cfg["risk"]["risk_pct"] = 5000.0
    cfg["risk"]["notional_cap"] = 1e9
    feat = make_features([100.0] * 10, signals=[3])
    trades, orders, _, _, summary = run_backtest({"X/USDT": feat}, cfg,
                                                 market_type="futures",
                                                 contract_meta={"X/USDT": META})
    assert summary["n_trades"] == 0
    rej = orders[orders["status"] == "REJECTED"]
    assert len(rej) == 1
    assert rej.iloc[0]["reason"] == "insufficient_margin"


def test_funding_applied_and_net_pnl_includes_it():
    feat = make_features([100.0] * 12, signals=[3])
    cfg = make_cfg()
    cfg["stops"]["initial_stop_enabled"] = False  # hold through end
    cfg["risk"]["notional_cap"] = 1e9  # no cap: qty purely risk-based
    rate = 0.0005
    funding = pd.DataFrame({
        "ts": [BASE + 6 * H1],  # during bar 6 (held position)
        "rate": [rate],
    })
    trades, _, equity, _, summary = run_backtest(
        {"X/USDT": feat}, cfg, market_type="futures",
        funding_by_symbol={"X/USDT": funding}, contract_meta={"X/USDT": META})
    assert summary["funding_included"] is True
    assert summary["n_funding_payments"] == 1
    t = trades.iloc[0]
    # payment = qty * close of the bar closing at the funding ts (bar 5 close) * rate
    qty = t["quantity"]
    expected_payment = qty * 100.0 * rate
    assert t["funding_fee"] == pytest.approx(expected_payment)
    assert t["net_pnl"] == pytest.approx(
        t["realized_pnl"] - t["entry_fee"] - t["exit_fee"] - t["funding_fee"])
    # equity invariant: final cash = initial + sum(net_pnl)
    assert summary["final_equity"] == pytest.approx(1000.0 + trades["net_pnl"].sum())


def test_liquidation_checked_before_stop():
    feat = make_features([100.0] * 10, signals=[3])
    cfg = make_cfg()
    cfg["futures"]["leverage"] = 5.0
    cfg["stops"]["initial_stop_enabled"] = False
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, cfg, market_type="futures",
                                      contract_meta={"X/USDT": META})
    t = trades.iloc[0]
    # entry fill ~100.0005, liq = entry*(1-(0.2-0.005)) ~ 80.5004
    liq = t["entry_price"] * (1.0 - (1.0 / 5.0 - 0.005))
    # make a later bar dip below liq
    # (re-run with a crafted low to be deterministic)
    feat2 = make_features([100.0] * 10, signals=[3], low_pad=25.0)
    trades2, _, _, _, _ = run_backtest({"X/USDT": feat2}, cfg, market_type="futures",
                                       contract_meta={"X/USDT": META})
    t2 = trades2.iloc[0]
    assert t2["exit_reason"] == "liquidation"
    assert t2["exit_price"] == pytest.approx(t2["liq_price"] * 0.9995)
    assert liq == pytest.approx(t2["liq_price"])


def test_equity_invariant_futures_random():
    import numpy as np
    rng = np.random.default_rng(9)
    closes = 100 + np.cumsum(rng.normal(0, 1, 150))
    feat = pd.DataFrame({
        "timestamp": pd.to_datetime([BASE + i * H1 for i in range(150)], unit="ms", utc=True),
        "open": closes, "high": closes + 2.0, "low": closes - 2.0, "close": closes,
        "atr": [2.0] * 150, "adx": [30.0] * 150,
        "signal": [i in (10, 60, 110) for i in range(150)],
        "exit_trend": [False] * 150,
    })
    trades, _, equity, _, summary = run_backtest({"X/USDT": feat}, make_cfg(),
                                                 market_type="futures",
                                                 contract_meta={"X/USDT": META})
    assert summary["final_equity"] == pytest.approx(1000.0 + trades["net_pnl"].sum())
    assert equity["equity"].iloc[-1] == pytest.approx(equity["cash"].iloc[-1])
