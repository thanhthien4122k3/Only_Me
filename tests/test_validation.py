import numpy as np
import pandas as pd
import pytest

from data import TF_MS
from validation import (break_even_cost, compare_exits, cost_sensitivity,
                        gate1_checklist, hold_out, param_sweep, walk_forward,
                        window_metrics)

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
        "stops": {"ema_exit_enabled": True, "initial_stop_enabled": False,
                  "take_profit_r": None, "trailing_enabled": False,
                  "trailing_atr_mult": 2.5, "time_stop_bars": None},
    }


def make_features(n_bars, seed=11):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.05, 0.8, n_bars)))  # small drift: no exp overflow
    ts = [BASE + i * H1 for i in range(n_bars)]
    v = rng.uniform(800, 1200, n_bars)
    v[::23] *= 3
    return pd.DataFrame({
        "timestamp": pd.to_datetime(ts, unit="ms", utc=True),
        "open": np.concatenate([[100.0], close[:-1]]),
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": v,
        "atr": [2.0] * n_bars,
        "adx": [30.0] * n_bars,
        "signal": [False] * n_bars,
        "exit_trend": [False] * n_bars,
    })


def with_signals(df):
    # cheap way to produce some signals: breakout bars get signal=True
    prior = df["high"].shift(1).rolling(20).max()
    df = df.copy()
    df["signal"] = (df["close"] > prior) & (df["volume"] > 1.2 * df["volume"].rolling(20).mean())
    return df


def test_window_metrics_basic():
    trades = pd.DataFrame({
        "net_pnl": [10.0, -4.0],
        "entry_fee": [0.1, 0.1], "exit_fee": [0.1, 0.1],
        "entry_time": pd.to_datetime([BASE, BASE + H1], unit="ms", utc=True),
        "exit_time": pd.to_datetime([BASE + 10 * H1, BASE + 11 * H1], unit="ms", utc=True),
        "entry_price": [100.0, 100.0], "exit_price": [110.0, 96.0],
        "quantity": [1.0, 1.0], "initial_stop_price": [97.0, 97.0],
        "realized_pnl": [10.0, -4.0],
    })
    m = window_metrics(trades, make_cfg())
    assert m["profit_factor"] == pytest.approx(2.5)
    assert m["n_trades"] == 2
    assert m["net_return_pct"] == pytest.approx(0.6)


def test_walk_forward_windows_and_trade_membership():
    cfg = make_cfg()
    frames = {"X/USDT": with_signals(make_features(6000))}
    wf, pooled = walk_forward(frames, cfg)
    assert len(wf) >= 2
    assert (wf["window_start"].diff().dropna() > pd.Timedelta(0)).all()
    assert wf["window_end"].iloc[-1] == frames["X/USDT"]["timestamp"].iloc[-1]
    assert (wf["n_trades"] >= 0).all()
    # one continuous run: windows partition all trades by entry_time
    assert pooled["n_trades"] == wf["n_trades"].sum()
    assert "expectancy_ci_90_lo" in pooled


def test_hold_out_split_and_filtering():
    cfg = make_cfg()
    frames = {"X/USDT": with_signals(make_features(6000))}
    m, split, summary = hold_out(frames, cfg)
    total = frames["X/USDT"]["timestamp"].iloc[-1] - frames["X/USDT"]["timestamp"].iloc[0]
    expected = frames["X/USDT"]["timestamp"].iloc[0] + 0.8 * total
    assert abs((split - expected).total_seconds()) < 3 * 3600
    assert m["n_trades"] >= 0
    assert "n_open_at_end" in m


def test_gate1_pass_and_fail_and_warning():
    full_pass = {"expectancy_usdt": 1.0, "n_trades": 120, "max_drawdown_pct": 3.0,
                 "profit_factor": 1.5}
    ho_pass = {"n_trades": 20, "profit_factor": 1.4}
    wf_pass = pd.DataFrame({"n_trades": [5, 5, 5], "profit_factor": [1.3, 1.2, 0.9]})
    checks, warnings = gate1_checklist(full_pass, ho_pass, wf_pass)
    assert (checks["result"] == "PASS").all()
    assert warnings == []

    full_fail = {"expectancy_usdt": -0.5, "n_trades": 40, "max_drawdown_pct": 10.0,
                 "profit_factor": 0.8}
    ho_fail = {"n_trades": 0, "profit_factor": 0.0}
    wf_fail = pd.DataFrame({"n_trades": [1, 1, 1, 1], "profit_factor": [0.5, 0.6, 0.7, 0.8]})
    checks2, warnings2 = gate1_checklist(full_fail, ho_fail, wf_fail)
    assert (checks2["result"] == "FAIL").all()

    full_warn = {"expectancy_usdt": 2.0, "n_trades": 150, "max_drawdown_pct": 4.0,
                 "profit_factor": 3.1}
    _, warnings3 = gate1_checklist(full_warn, ho_pass, wf_pass)
    assert any("2.5" in w for w in warnings3)


def test_compare_exits_runs_all_4():
    cfg = make_cfg()
    frames = {"X/USDT": with_signals(make_features(1500))}
    df = compare_exits(frames, cfg)
    assert len(df) == 4
    assert list(df["config"].str[0]) == ["A", "B", "C", "D"]
    for col in ["trades", "win rate", "PF", "expectancy USDT", "net return %",
                "max drawdown %", "max_single_loss_pct_of_equity",
                "realized_vs_planned_risk", "total costs"]:
        assert col in df.columns


def test_cost_sensitivity_12_rows_and_grid():
    cfg = make_cfg()
    frames = {"X/USDT": with_signals(make_features(1500))}
    df = cost_sensitivity(frames, cfg)
    assert len(df) == 12
    assert set(zip(df["fee"], df["slippage"])) == {
        (f, s) for f in [0.05, 0.08, 0.10, 0.15] for s in [0.02, 0.05, 0.10]}
    for col in ["trades", "PF", "expectancy USDT", "net return %",
                "max drawdown %", "expectancy CI90 low"]:
        assert col in df.columns
    assert cfg["backtest"]["fee_pct"] == 0.1  # config gốc không bị đổi


def test_break_even_cost_line():
    df = pd.DataFrame({"fee": [0.05, 0.15], "slippage": [0.02, 0.10],
                       "expectancy USDT": [1.0, -0.5]})
    assert "fee=0.05" in break_even_cost(df)
    all_neg = pd.DataFrame({"fee": [0.15], "slippage": [0.10],
                            "expectancy USDT": [-1.0]})
    assert "không có kịch bản" in break_even_cost(all_neg)


def test_param_sweep_returns_all_combos():
    cfg = make_cfg()
    frames = {"X/USDT": with_signals(make_features(1500))}
    df = param_sweep(frames, cfg, [("risk.notional_cap", [200, 400])])
    assert len(df) == 3  # baseline + 2 variants
    assert df.iloc[0]["config"] == "baseline"
    labels = set(df["config"])
    assert "risk.notional_cap=200" in labels
    assert "risk.notional_cap=400" in labels
    assert cfg["risk"]["notional_cap"] == 300  # original config untouched


def test_param_sweep_two_params():
    cfg = make_cfg()
    frames = {"X/USDT": with_signals(make_features(1500))}
    df = param_sweep(frames, cfg, [("risk.notional_cap", [200, 400]),
                                   ("stops.time_stop_bars", [12, 24])])
    assert len(df) == 1 + 2 * 2
