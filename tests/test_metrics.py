import numpy as np
import pandas as pd
import pytest

from metrics import (bootstrap_expectancy_ci, by_quarter, by_symbol,
                     compute_costs, compute_metrics, max_drawdown, trade_metrics)

BASE = 1_700_000_000_000


def make_trades(net_pnls):
    n = len(net_pnls)
    entry = pd.to_datetime([BASE + i * 3_600_000 for i in range(n)], unit="ms", utc=True)
    exit_ = entry + pd.to_timedelta(10, unit="h")
    return pd.DataFrame({
        "symbol": "X/USDT",
        "entry_time": entry,
        "exit_time": exit_,
        "entry_price": [100.0] * n,
        "exit_price": [100.0] * n,
        "quantity": [1.0] * n,
        "initial_stop_price": [97.0] * n,
        "entry_fee": [0.1] * n,
        "exit_fee": [0.1] * n,
        "realized_pnl": [p + 0.2 for p in net_pnls],
        "net_pnl": net_pnls,
        "exit_reason": ["ema_exit"] * n,
    })


def test_profit_factor_win_rate_expectancy():
    m = trade_metrics(make_trades([10.0, -5.0, 2.0]))
    assert m["profit_factor"] == pytest.approx(12.0 / 5.0)
    assert m["win_rate_pct"] == pytest.approx(2 / 3 * 100)
    assert m["expectancy_usdt"] == pytest.approx(7.0 / 3.0)
    assert m["avg_holding_hours"] == pytest.approx(10.0)


def test_expectancy_r_uses_planned_risk():
    m = trade_metrics(make_trades([6.0, -3.0]))  # risk = 1 * (100-97) = 3 USDT
    assert m["expectancy_r"] == pytest.approx((2.0 + (-1.0)) / 2.0)


def test_max_drawdown():
    eq = pd.Series([100.0, 120.0, 90.0, 110.0])
    mdd, pct = max_drawdown(eq)
    assert mdd == pytest.approx(-30.0)
    assert pct == pytest.approx(-25.0)


def test_bootstrap_ci_contains_mean():
    pnl = pd.Series(np.random.default_rng(3).normal(2.0, 5.0, 200))
    lo, hi = bootstrap_expectancy_ci(pnl)
    assert lo <= pnl.mean() <= hi


def test_by_symbol_and_quarter():
    trades = make_trades([10.0, -5.0])
    s = by_symbol(trades)
    assert s.iloc[0]["n_trades"] == 2
    assert s.iloc[0]["net_pnl"] == pytest.approx(5.0)
    q = by_quarter(trades)
    assert q.iloc[0]["n_trades"] == 2


def test_compute_costs_fees_and_slippage():
    trades = make_trades([1.0, 2.0])
    orders = pd.DataFrame({
        "symbol": ["X/USDT", "X/USDT"],
        "side": ["buy", "sell"],
        "qty": [1.0, 1.0],
        "ref_price": [100.0, 105.0],
        "fill_price": [100.05, 104.9475],  # 0.05% slippage on each
        "status": ["FILLED", "FILLED"],
        "reason": ["signal", "ema_exit"],
        "fee": [0.1, 0.1],
    })
    fees, slip = compute_costs(trades, orders)
    assert fees == pytest.approx(0.4)  # 2 trades x (entry 0.1 + exit 0.1)
    assert slip == pytest.approx(1.0 * 0.05 + 1.0 * 0.0525)


def test_compute_metrics_end_to_end():
    trades = make_trades([10.0, -4.0])
    orders = pd.DataFrame({
        "symbol": ["X/USDT"] * 4, "side": ["buy", "sell", "buy", "sell"],
        "qty": [1.0] * 4, "ref_price": [100.0, 110.0, 100.0, 96.0],
        "fill_price": [100.05, 109.945, 100.05, 95.952],
        "status": ["FILLED"] * 4, "reason": [""] * 4, "fee": [0.1] * 4,
    })
    eq = pd.DataFrame({
        "timestamp": pd.to_datetime([BASE + i * 3_600_000 for i in range(5)], unit="ms", utc=True),
        "equity": [1000.0, 1005.0, 1010.0, 1008.0, 1006.0],
        "cash": [1000.0] * 5,
        "exposure_pct": [50.0] * 5,
    })
    cfg = {"backtest": {"initial_capital": 1000.0, "fee_pct": 0.1, "slippage_pct": 0.05}}
    m = compute_metrics(trades, orders, eq, cfg)
    assert m["final_equity"] == pytest.approx(1006.0)
    assert m["net_return_pct"] == pytest.approx(0.6)
    assert m["total_fees"] == pytest.approx(0.4)  # 2 trades x 2 fills x 0.1
    assert m["total_costs"] > m["total_fees"]
    assert m["n_trades"] == 2


def test_empty_trades_no_crash():
    trades = pd.DataFrame()
    orders = pd.DataFrame()
    equity = pd.DataFrame()
    cfg = {"backtest": {"initial_capital": 1000.0, "fee_pct": 0.1, "slippage_pct": 0.05}}
    m = compute_metrics(trades, orders, equity, cfg)
    assert m["n_trades"] == 0
    assert m["final_equity"] == 1000.0
