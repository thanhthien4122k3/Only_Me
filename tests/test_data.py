import pandas as pd

from data import (candles_to_df, check_data, check_features, is_excluded_base,
                  load_ohlcv, quality_report)

TF_MS = {"1h": 3_600_000, "4h": 14_400_000}

CFG_SHORT = {"data": {"months_back": 1}}       # 30.44 days coverage needed
CFG_LONG = {"data": {"months_back": 25}}


def make_gate_df(n, step=3_600_000):
    return candles_to_df(make_candles(n), "1h", 1_700_000_000_000 + (n + 1) * step)


def codes(issues, severity=None):
    return {i["code"] for i in issues if severity is None or i["severity"] == severity}


def test_gate_duplicate_ts_critical():
    df = make_gate_df(200)
    df = pd.concat([df, df.iloc[[5]]]).sort_values("ts").reset_index(drop=True)
    assert "ts_invalid" in codes(check_data(df, "1h", CFG_SHORT), "CRITICAL")


def test_gate_unsorted_ts_critical():
    df = make_gate_df(200)
    df = df.iloc[::-1].reset_index(drop=True)
    assert "ts_invalid" in codes(check_data(df, "1h", CFG_SHORT), "CRITICAL")


def test_gate_ohlc_inconsistent_and_nonpositive_critical():
    df = make_gate_df(200)
    df.loc[10, "high"] = df.loc[10, "close"] - 1  # high < close
    assert "ohlc_inconsistent" in codes(check_data(df, "1h", CFG_SHORT), "CRITICAL")
    df2 = make_gate_df(200)
    df2.loc[20, "low"] = -5.0
    assert "nonpositive_price" in codes(check_data(df2, "1h", CFG_SHORT), "CRITICAL")


def test_gate_short_coverage_critical():
    df = make_gate_df(200)  # 200h ~ 8 days << 25 months
    assert "short_coverage" in codes(check_data(df, "1h", CFG_LONG), "CRITICAL")


def test_gate_too_many_missing_critical():
    df = make_gate_df(1000)
    df = df.drop(df.index[100:110]).reset_index(drop=True)  # 10/1000 = 1%
    assert "too_many_missing" in codes(check_data(df, "1h", CFG_SHORT), "CRITICAL")


def test_gate_large_gap_critical():
    df = make_gate_df(1000)
    df = df.drop(df.index[100:114]).reset_index(drop=True)  # 14 consecutive bars
    assert "large_gap" in codes(check_data(df, "1h", CFG_SHORT), "CRITICAL")


def test_gate_warns_not_critical():
    df = make_gate_df(1000)
    df = df.drop(df.index[50]).reset_index(drop=True)  # 1 missing bar
    df.loc[5, "volume"] = 0.0
    issues = check_data(df, "1h", CFG_SHORT)
    assert "small_gap" in codes(issues, "WARN")
    assert "zero_volume" in codes(issues, "WARN")
    assert not codes(issues, "CRITICAL")


def test_gate_clean_data_no_issues():
    df = make_gate_df(1000)
    assert check_data(df, "1h", CFG_SHORT) == []


def test_universe_exclusions():
    assert is_excluded_base("USDC") is True
    assert is_excluded_base("BTC3L") is True
    assert is_excluded_base("5LONG") is False  # chỉ loại prefix 2X/3X/5X/10X
    assert is_excluded_base("XTBULL") is True
    assert is_excluded_base("WBTC") is True
    assert is_excluded_base("STETH") is True
    assert is_excluded_base("DOGE") is False
    assert is_excluded_base("ETH") is False


def test_gate_feature_nan_critical():
    df = make_gate_df(400)
    feat = df.copy()
    feat["ema20"] = 1.0
    feat["ema50"] = 1.0
    feat.loc[300, "ema50"] = float("nan")  # NaN after warm-up 250
    issues = check_features(feat, ["ema20", "ema50"])
    assert any(i["severity"] == "CRITICAL" and i["code"] == "feature_nan" for i in issues)
    assert check_features(df.assign(ema20=1.0), ["ema20"]) == []


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
