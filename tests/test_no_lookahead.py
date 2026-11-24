from pathlib import Path

import numpy as np
import pandas as pd

from data import TF_MS, candles_to_df, load_config
from strategy import build_features

CFG = load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))


def make_frame(candles, tf, now_ms):
    df = candles_to_df(candles, tf, now_ms)
    df["close_time"] = df["timestamp"] + pd.to_timedelta(TF_MS[tf], unit="ms")
    return df


def make_random_ohlcv(n, step_ms, base, seed, drift=0.3, vol=0.8):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    opn = np.concatenate([[100.0], close[:-1]])
    volume = rng.uniform(800, 1200, n)
    volume[::23] *= 3  # periodic spikes so some volume conditions pass
    return [[base + i * step_ms, o, h, l, c, v]
            for i, (o, h, l, c, v) in enumerate(zip(opn, high, low, close, volume))]


def test_no_lookahead_truncation():
    base = 1_700_000_000_000
    h1 = TF_MS["1h"]
    n1 = 1500
    now_ms = base + (n1 + 10) * h1
    df1 = make_frame(make_random_ohlcv(n1, h1, base, seed=11), "1h", now_ms)
    df4 = make_frame(make_random_ohlcv(400, 4 * h1, base, seed=12), "4h", now_ms)

    full = build_features(df1, df4, CFG)
    assert full["signal"].sum() > 0  # precondition: the dataset has signals

    T = full["close_time"].iloc[1200]
    t1 = df1[df1["close_time"] <= T].reset_index(drop=True)
    t4 = df4[df4["close_time"] <= T].reset_index(drop=True)
    trunc = build_features(t1, t4, CFG)

    cols = ["ema20", "ema50", "rsi", "adx", "atr", "vol_sma", "prior_high",
            "trend4h_ok", "exit_trend", "signal"]
    expected = full[full["close_time"] <= T][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(trunc[cols], expected)


def test_4h_bar_not_visible_before_its_close():
    base = 1_700_000_000_000
    h1 = TF_MS["1h"]
    n4 = 250
    now_ms = base + (n4 * 4 + 10) * h1
    # 4H: flat 100, final bar jumps to 150 -> trend4h_ok True only on the last 4H bar
    c4 = [100.0] * n4
    c4[-1] = 150.0
    candles4 = [[base + i * 4 * h1, 100.0, 101.0, 99.0, c4[i], 1000.0] for i in range(n4)]
    df4 = make_frame(candles4, "4h", now_ms)
    n1 = n4 * 4 + 2
    candles1 = [[base + i * h1, 100.0, 101.0, 99.0, 100.0, 1000.0] for i in range(n1)]
    df1 = make_frame(candles1, "1h", now_ms)

    feat = build_features(df1, df4, CFG)
    last4_close = df4["close_time"].iloc[-1]
    before = feat[feat["close_time"] < last4_close]["trend4h_ok"]
    at = feat[feat["close_time"] == last4_close]["trend4h_ok"]
    after = feat[feat["close_time"] > last4_close]["trend4h_ok"]
    assert (before == False).all()  # noqa: E712
    assert (at == True).all()  # noqa: E712
    assert (after == True).all()  # noqa: E712
