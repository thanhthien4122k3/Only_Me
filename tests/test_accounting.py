"""Accounting invariants on a multi-symbol synthetic run. Tests only - no engine changes
expected unless one of these fails."""
import numpy as np
import pandas as pd
import pytest

from data import TF_MS
from engine import run_backtest
from metrics import compute_costs, compute_metrics

BASE = 1_700_000_000_000
H1 = TF_MS["1h"]


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
    }


def make_features(closes, signals=(), exit_trend=()):
    n = len(closes)
    ts = [BASE + i * H1 for i in range(n)]
    return pd.DataFrame({
        "timestamp": pd.to_datetime(ts, unit="ms", utc=True),
        "open": [100.0] + closes[:-1],
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "atr": [2.0] * n,
        "adx": [30.0] * n,
        "signal": [i in signals for i in range(n)],
        "exit_trend": [i in exit_trend for i in range(n)],
    })


def multi_run():
    cfg = make_cfg()
    frames = {}
    rng = np.random.default_rng(21)
    for sym, seed in [("A/USDT", 1), ("B/USDT", 2), ("C/USDT", 3)]:
        r = np.random.default_rng(seed)
        closes = 100 + np.cumsum(r.normal(0, 1, 400))
        frames[sym] = make_features(closes.tolist(), signals=[50, 150, 250, 350])
    return frames, cfg


def test_final_equity_identity():
    frames, cfg = multi_run()
    trades, orders, equity, skipped, summary = run_backtest(frames, cfg)
    assert summary["n_trades"] > 0
    assert summary["final_equity"] == pytest.approx(
        cfg["backtest"]["initial_capital"] + trades["net_pnl"].sum())


def test_equity_mark_to_market_consistency():
    """At every bar: equity == cash + sum(open qty x close). This also covers continuity
    across trade closes: on the exit bar the position leaves the sum and cash settles."""
    frames, cfg = multi_run()
    trades, orders, equity, skipped, summary = run_backtest(frames, cfg)
    close_by_sym = {sym: f.set_index("timestamp")["close"] for sym, f in frames.items()}
    for ts, eq, cash in zip(equity["timestamp"], equity["equity"], equity["cash"]):
        open_pos = trades[(trades["entry_time"] <= ts) & (trades["exit_time"] > ts)]
        assets = sum(t["quantity"] * close_by_sym[t["symbol"]].loc[ts]
                     for _, t in open_pos.iterrows())
        assert eq == pytest.approx(cash + assets, abs=1e-6)


def test_total_fees_match_metrics():
    frames, cfg = multi_run()
    trades, orders, equity, skipped, summary = run_backtest(frames, cfg)
    m = compute_metrics(trades, orders, equity, cfg)
    assert m["total_fees"] == pytest.approx(
        trades["entry_fee"].sum() + trades["exit_fee"].sum())


def test_slippage_per_order_formula():
    frames, cfg = multi_run()
    trades, orders, equity, skipped, summary = run_backtest(frames, cfg)
    slip_rate = cfg["backtest"]["slippage_pct"] / 100.0
    filled = orders[orders["fill_price"].notna()]
    assert len(filled) > 0
    for _, o in filled.iterrows():
        # every fill is ref*(1+slip) for buys or ref*(1-slip) for sells
        assert o["qty"] * abs(o["ref_price"] - o["fill_price"]) == pytest.approx(
            o["qty"] * o["ref_price"] * slip_rate)
    fees, slip = compute_costs(trades, orders)
    assert slip == pytest.approx(
        (filled["qty"] * (filled["ref_price"] - filled["fill_price"]).abs()).sum())
