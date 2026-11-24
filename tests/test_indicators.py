import numpy as np
import pandas as pd

from indicators import adx, atr, ema, rsi


def test_ema_matches_ewm():
    s = pd.Series(np.linspace(1, 100, 100))
    out = ema(s, 20)
    pd.testing.assert_series_equal(out, s.ewm(span=20, adjust=False, min_periods=20).mean())
    assert out.iloc[:19].isna().all()
    assert out.iloc[19:].notna().all()


def test_rsi_100_on_rising_and_bounded():
    rising = pd.Series(np.arange(1.0, 200.0))
    out = rsi(rising, 14)
    assert out.iloc[:14].isna().all()
    assert (out.iloc[14:] == 100.0).all()  # strictly rising: no losses -> RSI 100
    rng = np.random.default_rng(7)
    mixed = pd.Series(100 + np.cumsum(rng.normal(0, 1, 500)))
    vals = rsi(mixed, 14).dropna()
    assert ((vals >= 0) & (vals <= 100)).all()


def test_atr_constant_range():
    n = 100
    df = pd.DataFrame({
        "high": np.arange(n, dtype=float) + 10.0,
        "low": np.arange(n, dtype=float),
        "close": np.arange(n, dtype=float) + 5.0,
    })
    out = atr(df, 14)
    assert out.iloc[:13].isna().all()
    np.testing.assert_allclose(out.iloc[13:], 10.0)  # every bar's TR is 10


def test_adx_bounds():
    rng = np.random.default_rng(3)
    n = 400
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({
        "high": close + rng.uniform(0.5, 3, n),
        "low": close - rng.uniform(0.5, 3, n),
        "close": close,
    })
    out = adx(df, 14)
    assert out.iloc[:13].isna().all()
    vals = out.dropna()
    assert (vals >= 0).all()
    assert (vals <= 100 + 1e-9).all()


def test_warmup_nan_all_indicators():
    rng = np.random.default_rng(1)
    n = 300
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({
        "high": close + rng.uniform(0.5, 2, n),
        "low": close - rng.uniform(0.5, 2, n),
        "close": close,
    })
    assert ema(df["close"], 20).iloc[:19].isna().all()
    assert rsi(df["close"], 14).iloc[:13].isna().all()
    assert atr(df, 14).iloc[:13].isna().all()
    assert adx(df, 14).iloc[:13].isna().all()
