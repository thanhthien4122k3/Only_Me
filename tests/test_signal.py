from pathlib import Path

import pandas as pd

from data import TF_MS, candles_to_df, load_config
from strategy import build_features

CFG = load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))

BASE = 1_700_000_000_000
H1 = TF_MS["1h"]


def make_frame(candles, tf, now_ms):
    df = candles_to_df(candles, tf, now_ms)
    df["close_time"] = df["timestamp"] + pd.to_timedelta(TF_MS[tf], unit="ms")
    return df


def make_crafted(spike_volume):
    """1H: ~985 whipsaw bars, then 17 up bars (+5 each) ending at bar 1002.
    Only bar 1001 gets the volume spike. 4H: flat 100, last bar jumps to 150,
    so trend4h_ok is True only for 1H bars closing at/after the last 4H close."""
    n4 = 250
    now_ms = BASE + (n4 * 4 + 10) * H1
    c4 = [100.0] * n4
    c4[-1] = 150.0
    candles4 = [[BASE + i * 4 * H1, 100.0, 101.0, 99.0, c4[i], 1000.0] for i in range(n4)]
    df4 = make_frame(candles4, "4h", now_ms)

    closes = [100.0 + 0.5 * ((-1) ** i) for i in range(985)]  # whipsaw, no breakout
    for _ in range(17):  # bars 985..1001, up-trend: +5 each
        closes.append(closes[-1] + 5.0)
    candles1 = []
    for i, c in enumerate(closes):
        v = 3000.0 if (spike_volume and i == 1001) else 1000.0
        candles1.append([BASE + i * H1, c, c + 1.0, c - 1.0, c, v])
    df1 = make_frame(candles1, "1h", now_ms)
    return build_features(df1, df4, CFG)


def test_exactly_one_signal_on_crafted_breakout():
    feat = make_crafted(spike_volume=True)
    assert feat.index[feat["signal"]].tolist() == [1001]


def test_no_signal_without_volume_spike():
    feat = make_crafted(spike_volume=False)
    assert feat["signal"].sum() == 0
