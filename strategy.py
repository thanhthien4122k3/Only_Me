"""Feature building and entry signal logic. No trade simulation here."""
import pandas as pd

from indicators import adx, atr, ema, rsi


def build_features(df_1h, df_4h, cfg):
    s = cfg["strategy"]
    df = df_1h.copy()

    df["ema20"] = ema(df["close"], s["ema_fast"])
    df["ema50"] = ema(df["close"], s["ema_slow"])
    df["rsi"] = rsi(df["close"], s["rsi_period"])
    df["adx"] = adx(df, s["adx_period"])
    df["atr"] = atr(df, s["atr_period"])
    df["vol_sma"] = df["volume"].rolling(s["volume_sma"]).mean()
    # highest high of the PREVIOUS breakout_period bars, excluding the current bar
    df["prior_high"] = df["high"].rolling(s["breakout_period"]).max().shift(1)

    f4 = df_4h.copy()
    f4["ema50"] = ema(f4["close"], s["ema_slow"])
    f4["ema200"] = ema(f4["close"], s["ema_regime"])
    f4["trend4h_ok"] = (f4["close"] > f4["ema200"]) & (
        f4["ema50"] > f4["ema50"].shift(s["slope_lookback"])
    )
    # Causality: a 4H bar must not be visible before it closes. merge_asof with
    # direction="backward" matches each 1H bar's close_time to the latest 4H
    # close_time <= that moment, so a 4H bar becomes visible only to 1H bars
    # closing at or after that 4H bar's close.
    merged = pd.merge_asof(
        df[["close_time"]],
        f4[["close_time", "trend4h_ok"]],
        on="close_time",
        direction="backward",
    )
    df["trend4h_ok"] = merged["trend4h_ok"].fillna(False).astype(bool).values

    df["exit_trend"] = df["ema20"] < df["ema50"]
    conf = (
        (df["ema20"] > df["ema50"])
        & (df["rsi"] > s["rsi_min"])
        & (df["adx"] > s["adx_min"])
    )
    df["signal"] = (
        df["trend4h_ok"]
        & conf
        & (df["close"] > df["prior_high"])
        & (df["volume"] > s["volume_mult"] * df["vol_sma"])
    )
    # signal False wherever any required input is NaN (indicator warm-up)
    needed = df[["ema20", "ema50", "rsi", "adx", "prior_high", "vol_sma"]]
    df["signal"] = df["signal"].where(needed.notna().all(axis=1), False).astype(bool)
    return df


def funnel(features, cfg):
    """Pass-rate per condition and cumulative count after applying them in order."""
    s = cfg["strategy"]
    conds = [
        ("4h_trend", features["trend4h_ok"]),
        ("ema20>ema50", features["ema20"] > features["ema50"]),
        ("rsi>min", features["rsi"] > s["rsi_min"]),
        ("adx>min", features["adx"] > s["adx_min"]),
        ("breakout", features["close"] > features["prior_high"]),
        ("volume", features["volume"] > s["volume_mult"] * features["vol_sma"]),
    ]
    rows = []
    cumulative = pd.Series(True, index=features.index)
    for name, cond in conds:
        valid = cond.notna()
        n_pass = int(cond[valid].sum())
        n_valid = int(valid.sum())
        cumulative = cumulative & cond.fillna(False)
        rows.append({
            "condition": name,
            "pass": n_pass,
            "pct_of_valid": round(100 * n_pass / n_valid, 2) if n_valid else 0.0,
            "cumulative": int(cumulative.sum()),
        })
    return pd.DataFrame(rows)
