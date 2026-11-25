from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data import TF_MS, candles_to_df, load_config
from engine import run_backtest, validate_config
from strategy import build_features

BASE = 1_700_000_000_000
H1 = TF_MS["1h"]


def make_cfg():
    return {
        "backtest": {"initial_capital": 1000.0, "fee_pct": 0.1, "slippage_pct": 0.05,
                     "skip_cost_ratio": 0.2, "quote_currency": "USDT",
                     "fill_policy": "next_bar_open"},
        "spot": {"min_qty": 0.00001},
        "risk": {"risk_pct": 0.5, "notional_cap": 300.0, "max_positions": 3,
                 "stop_atr_mult": 1.5},
        "stops": {"ema_exit_enabled": True, "initial_stop_enabled": True,
                  "take_profit_r": None, "trailing_enabled": False,
                  "trailing_atr_mult": 2.5, "time_stop_bars": None},
    }


def make_features(closes, signals=(), exit_trend=(), atr=2.0, adx=30.0,
                  high_pad=1.0, low_pad=1.0, open_shift=0.0):
    n = len(closes)
    ts = [BASE + i * H1 for i in range(n)]
    o = [c + open_shift for c in closes]
    h = [c + high_pad for c in closes]
    l = [c - low_pad for c in closes]
    sig = [False] * n
    for i in signals:
        sig[i] = True
    ext = [False] * n
    for i in exit_trend:
        ext[i] = True
    return pd.DataFrame({
        "timestamp": pd.to_datetime(ts, unit="ms", utc=True),
        "open": o, "high": h, "low": l, "close": closes,
        "atr": [atr] * n, "adx": [adx] * n,
        "signal": sig, "exit_trend": ext,
    })


def test_entry_at_next_bar_open():
    feat = make_features([100.0] * 10, signals=[3])
    trades, orders, _, _, summary = run_backtest({"X/USDT": feat}, make_cfg())
    assert summary["n_trades"] == 1
    t = trades.iloc[0]
    assert t["entry_time"] == feat["timestamp"].iloc[4]  # signal bar 3 -> open bar 4
    assert t["entry_price"] == pytest.approx(feat["open"].iloc[4] * 1.0005)
    buy = orders[(orders["side"] == "buy") & (orders["status"] == "FILLED")]
    assert len(buy) == 1


def test_stop_first_when_both_inside_candle():
    cfg = make_cfg()
    cfg["stops"]["take_profit_r"] = 2.0
    feat = make_features([100.0] * 10, signals=[3])
    feat.loc[5, "high"] = 110.0
    feat.loc[5, "low"] = 95.0  # stop (~97.05) and tp (~106.05) both inside
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, cfg)
    t = trades.iloc[0]
    assert t["exit_reason"] == "stop_first"
    assert t["exit_price"] == pytest.approx((t["entry_price"] - 1.5 * 2.0) * 0.9995)


def test_gap_beyond_stop_fills_at_open():
    cfg = make_cfg()
    cfg["stops"]["take_profit_r"] = 2.0
    feat = make_features([100.0] * 10, signals=[3])
    feat.loc[5, "open"] = 90.0  # opens below the stop
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, cfg)
    t = trades.iloc[0]
    assert t["exit_reason"] == "gap_open"
    assert t["exit_price"] == pytest.approx(90.0 * 0.9995)


def test_close_exit_fills_next_bar_open():
    feat = make_features([100.0] * 10, signals=[3], exit_trend=[6])
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, make_cfg())
    t = trades.iloc[0]
    assert t["exit_reason"] == "ema_exit"
    assert t["exit_time"] == feat["timestamp"].iloc[7]  # decided at bar 6 -> fill bar 7 open
    assert t["exit_price"] == pytest.approx(feat["open"].iloc[7] * 0.9995)


def test_close_exit_at_close_legacy():
    cfg = make_cfg()
    cfg["backtest"]["exit_fill"] = "close"
    feat = make_features([100.0] * 10, signals=[3], exit_trend=[6])
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, cfg)
    t = trades.iloc[0]
    assert t["exit_time"] == feat["timestamp"].iloc[6]
    assert t["exit_price"] == pytest.approx(100.0 * 0.9995)


def test_pending_exit_at_eod_flagged():
    feat = make_features([100.0] * 8, signals=[3], exit_trend=[7])  # decision on last bar
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, make_cfg())
    t = trades.iloc[0]
    assert t["exit_reason"] == "ema_exit_eod"
    assert t["exit_price"] == pytest.approx(100.0 * 0.9995)


def test_time_stop():
    cfg = make_cfg()
    cfg["stops"]["time_stop_bars"] = 3
    feat = make_features([100.0] * 20, signals=[3])
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, cfg)
    t = trades.iloc[0]
    assert t["exit_reason"] == "time_stop"
    # entry bar 4 = 1st held bar; 3rd held bar close is bar 6 -> fill at bar 7 open
    assert t["exit_time"] == feat["timestamp"].iloc[7]
    assert t["exit_price"] == pytest.approx(feat["open"].iloc[7] * 0.9995)


def test_equity_accounting_consistent():
    rng = np.random.default_rng(5)
    closes = 100 + np.cumsum(rng.normal(0, 1, 200))
    feat = make_features(closes, signals=[10, 50, 90], high_pad=2.0, low_pad=2.0)
    trades, orders, equity, _, summary = run_backtest({"X/USDT": feat}, make_cfg())
    assert summary["final_equity"] == pytest.approx(1000.0 + trades["net_pnl"].sum())
    assert (equity["equity"] >= equity["cash"] - 1e-9).all()
    assert equity["equity"].iloc[-1] == pytest.approx(equity["cash"].iloc[-1])
    buys = orders[orders["side"] == "buy"]
    sells = orders[orders["side"] == "sell"]
    assert buys["qty"].sum() == pytest.approx(sells["qty"].sum())  # all closed


def test_insufficient_balance_rejected():
    cfg = make_cfg()
    cfg["risk"]["risk_pct"] = 5000.0
    cfg["risk"]["notional_cap"] = 1e9
    feat = make_features([100.0] * 10, signals=[3])
    trades, orders, _, _, summary = run_backtest({"X/USDT": feat}, cfg)
    assert summary["n_trades"] == 0
    rej = orders[orders["status"] == "REJECTED"]
    assert len(rej) == 1
    assert rej.iloc[0]["reason"] == "insufficient_balance"
    assert summary["final_equity"] == pytest.approx(1000.0)


def test_max_positions_and_adx_ranking():
    cfg = make_cfg()
    cfg["risk"]["max_positions"] = 2
    frames = {
        "A/USDT": make_features([100.0] * 10, signals=[3], adx=40.0),
        "B/USDT": make_features([100.0] * 10, signals=[3], adx=30.0),
        "C/USDT": make_features([100.0] * 10, signals=[3], adx=25.0),
    }
    trades, orders, _, skipped, summary = run_backtest(frames, cfg)
    assert summary["n_trades"] == 2
    entered = sorted(orders[orders["side"] == "buy"]["symbol"].tolist())
    assert entered == ["A/USDT", "B/USDT"]
    slot = skipped[skipped["reason"] == "slot_full"]
    assert len(slot) == 1
    assert slot.iloc[0]["symbol"] == "C/USDT"


def test_no_pyramiding():
    feat = make_features([100.0] * 10, signals=[3, 5])
    trades, orders, _, _, summary = run_backtest({"X/USDT": feat}, make_cfg())
    assert summary["n_trades"] == 1
    assert len(orders[orders["side"] == "buy"]) == 1


def test_skip_rule_cost_ratio():
    feat = make_features([100.0] * 10, signals=[3], atr=0.05)  # tiny stop distance
    trades, _, _, skipped, summary = run_backtest({"X/USDT": feat}, make_cfg())
    assert summary["n_trades"] == 0
    assert len(skipped[skipped["reason"] == "cost_ratio"]) == 1


def test_validate_config_rejects_null_fee():
    bad = make_cfg()
    bad["backtest"]["fee_pct"] = None
    with pytest.raises(ValueError):
        validate_config(bad)


def test_no_entries_before_execution_start():
    feat = make_features([100.0] * 15, signals=[3])  # entry would be bar 4 (warm-up)
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, make_cfg(),
                                      execution_start=feat["timestamp"].iloc[10])
    assert len(trades) == 0


def test_warmup_signal_fills_at_first_test_bar_open():
    feat = make_features([100.0] * 15, signals=[9])  # signal on the last warm-up bar
    start = feat["timestamp"].iloc[10]
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, make_cfg(), execution_start=start)
    t = trades.iloc[0]
    assert t["entry_time"] == start
    assert t["entry_price"] == pytest.approx(feat["open"].iloc[10] * 1.0005)


def test_entry_time_always_gte_execution_start():
    feat = make_features([100.0] * 40, signals=[3, 15, 25])
    start = feat["timestamp"].iloc[20]
    trades, _, _, _, _ = run_backtest({"X/USDT": feat}, make_cfg(), execution_start=start)
    assert len(trades) >= 1
    assert (trades["entry_time"] >= start).all()


def test_capital_fresh_at_execution_start():
    feat = make_features([100.0] * 40, signals=[3, 15, 25])
    start = feat["timestamp"].iloc[20]
    _, _, equity, _, _ = run_backtest({"X/USDT": feat}, make_cfg(), execution_start=start)
    assert equity["timestamp"].iloc[0] == start
    assert equity["equity"].iloc[0] == pytest.approx(1000.0)


def test_open_at_end_flag_with_execution_end():
    feat = make_features([100.0] * 20, signals=[3])
    end = feat["timestamp"].iloc[15]
    trades, _, _, _, summary = run_backtest({"X/USDT": feat}, make_cfg(), execution_end=end)
    t = trades.iloc[0]
    assert bool(t["open_at_end"])
    assert t["exit_reason"] == "open_at_end"
    assert t["exit_time"] is None
    assert t["net_pnl"] == 0.0
    assert summary["n_open_at_end"] == 1


def test_fees_by_market():
    cfg = make_cfg()
    cfg["backtest"]["fee_pct"] = 0.5          # default fallback
    cfg["backtest"]["fee_pct_spot"] = 0.1     # spot uses this
    cfg["backtest"]["fee_pct_futures"] = 0.2  # futures uses this
    cfg["futures"] = {"leverage": 2.0, "maintenance_margin_rate": 0.005,
                      "funding_enabled": True}
    feat = make_features([100.0] * 10, signals=[3])
    t_s = run_backtest({"X/USDT": feat}, cfg)[0].iloc[0]
    assert t_s["entry_fee"] == pytest.approx(t_s["quantity"] * t_s["entry_price"] * 0.001)
    meta = {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
            "settleCcy": "USDT"}
    t_f = run_backtest({"X/USDT": feat}, cfg, market_type="futures",
                       contract_meta={"X/USDT": meta})[0].iloc[0]
    assert t_f["entry_fee"] == pytest.approx(t_f["quantity"] * t_f["entry_price"] * 0.002)


def test_fee_fallback_to_default():
    cfg = make_cfg()
    cfg["backtest"]["fee_pct"] = 0.5
    cfg["backtest"]["skip_cost_ratio"] = 0.0  # tránh skip-rule loại tín hiệu
    feat = make_features([100.0] * 10, signals=[3])
    t = run_backtest({"X/USDT": feat}, cfg)[0].iloc[0]
    assert t["entry_fee"] == pytest.approx(t["quantity"] * t["entry_price"] * 0.005)


def test_engine_no_lookahead():
    cfg = load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))
    n1 = 1500
    now_ms = BASE + (n1 + 10) * H1
    rng1 = np.random.default_rng(11)
    c1 = 100 * np.exp(np.cumsum(rng1.normal(0.3, 0.8, n1)))
    h1_ = c1 * (1 + rng1.uniform(0.001, 0.01, n1))
    l1_ = c1 * (1 - rng1.uniform(0.001, 0.01, n1))
    o1 = np.concatenate([[100.0], c1[:-1]])
    v1 = rng1.uniform(800, 1200, n1)
    v1[::23] *= 3
    candles1 = [[BASE + i * H1, o, h, l, c, v]
                for i, (o, h, l, c, v) in enumerate(zip(o1, h1_, l1_, c1, v1))]
    rng4 = np.random.default_rng(12)
    n4 = 400
    c4 = 100 * np.exp(np.cumsum(rng4.normal(1.2, 1.6, n4)))
    h4 = c4 * (1 + rng4.uniform(0.001, 0.01, n4))
    l4 = c4 * (1 - rng4.uniform(0.001, 0.01, n4))
    o4 = np.concatenate([[100.0], c4[:-1]])
    v4 = rng4.uniform(800, 1200, n4)
    candles4 = [[BASE + i * 4 * H1, o, h, l, c, v]
                for i, (o, h, l, c, v) in enumerate(zip(o4, h4, l4, c4, v4))]

    def frame(candles, tf):
        d = candles_to_df(candles, tf, now_ms)
        d["close_time"] = d["timestamp"] + pd.to_timedelta(TF_MS[tf], unit="ms")
        return d

    df1 = frame(candles1, "1h")
    df4 = frame(candles4, "4h")
    full = build_features(df1, df4, cfg)
    T = full["close_time"].iloc[1200]
    trunc = build_features(df1[df1["close_time"] <= T].reset_index(drop=True),
                           df4[df4["close_time"] <= T].reset_index(drop=True), cfg)

    tf_, _, ef_, _, _ = run_backtest({"X/USDT": full}, cfg)
    tt, _, et, _, _ = run_backtest({"X/USDT": trunc}, cfg)
    # every truncated trade that closed naturally (not end-of-data) must match the full run
    natural = tt[(tt["exit_reason"] != "end_of_data")
                 & ~tt["exit_reason"].str.endswith("_eod")]
    for _, tr in natural.iterrows():
        m = tf_[(tf_["entry_time"] == tr["entry_time"]) & (tf_["exit_time"] == tr["exit_time"])]
        assert len(m) == 1
        pd.testing.assert_series_equal(m.iloc[0], tr)
    last_ts = et["timestamp"].iloc[-1]
    # exclude the last truncated row: it is overwritten by the end-of-data force close
    pd.testing.assert_frame_equal(et.iloc[:-1].reset_index(drop=True),
                                  ef_[ef_["timestamp"] < last_ts].reset_index(drop=True))
