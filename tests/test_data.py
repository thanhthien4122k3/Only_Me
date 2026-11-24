import pandas as pd

from data import candles_to_df, load_ohlcv, quality_report

TF_MS = {"1h": 3_600_000, "4h": 14_400_000}


def make_candles(n, start_ts=1_700_000_000_000, step=3_600_000):
    return [[start_ts + i * step, 100.0 + i, 110.0 + i, 95.0 + i, 105.0 + i, 1000.0 + i]
            for i in range(n)]


def test_candles_to_df_drops_unclosed_and_dedups():
    step = TF_MS["1h"]
    base = 1_700_000_000_000
    candles = make_candles(6, base)
    now_ms = base + 6 * step + 30 * 60_000  # 30 min into the 7th bar
    candles.append([base + 6 * step, 200, 210, 190, 205, 500])  # unclosed bar
    candles.append([base + 2 * step, 99, 111, 94, 104, 999])  # duplicate ts
    df = candles_to_df(candles, "1h", now_ms)
    assert len(df) == 6
    assert df["ts"].is_unique
    assert df["ts"].max() == base + 5 * step


def test_quality_report_detects_gap():
    step = TF_MS["1h"]
    base = 1_700_000_000_000
    df = candles_to_df(make_candles(10, base), "1h", base + 10 * step)
    df = df.drop(df.index[3:6]).reset_index(drop=True)  # remove 3 consecutive bars
    rep = quality_report(df, "1h", base, base + 10 * step)
    assert rep["missing_bars"] == 3
    assert rep["gap_count"] == 1
    assert rep["largest_gaps"][0][2] == 3


def test_quality_report_warns_late_start():
    step = TF_MS["1h"]
    base = 1_700_000_000_000
    df = candles_to_df(make_candles(10, base + 5 * step), "1h", base + 15 * step)
    rep = quality_report(df, "1h", base, base + 15 * step)
    assert any("starts late" in w for w in rep["warnings"])


def test_quality_report_empty_df():
    rep = quality_report(pd.DataFrame(), "1h", 1_700_000_000_000, 1_700_100_000_000)
    assert "empty dataframe" in rep["warnings"]


def test_load_ohlcv_close_time(tmp_path):
    step = TF_MS["1h"]
    base = 1_700_000_000_000
    df = candles_to_df(make_candles(5, base), "1h", base + 5 * step)
    df.to_parquet(tmp_path / "TEST-USDT_1h.parquet", index=False)
    out = load_ohlcv("TEST/USDT", "1h", tmp_path)
    assert out["ts"].is_monotonic_increasing
    assert (out["close_time"] - out["timestamp"] == pd.to_timedelta(step, unit="ms")).all()
