"""Public OKX spot OHLCV download via ccxt (no API key)."""
from pathlib import Path
import time

import ccxt
import numpy as np
import pandas as pd
import yaml

TF_MS = {"1h": 3_600_000, "4h": 14_400_000}


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_exchange():
    return ccxt.okx({"enableRateLimit": True, "timeout": 30_000})


def fetch_range(exchange, symbol, timeframe, since_ms, until_ms, limit=300):
    """Paginate OKX history-candles. `since` verified to mean 'candles >= since', ascending."""
    out = []
    cursor = since_ms
    tf_ms = TF_MS[timeframe]
    while cursor < until_ms:
        batch = None
        for attempt in range(3):
            try:
                batch = exchange.fetch_ohlcv(
                    symbol, timeframe, since=cursor, limit=limit,
                    params={"type": "HistoryCandles"},
                )
                break
            except (ccxt.NetworkError, ccxt.RateLimitExceeded):
                if attempt == 2:
                    raise
                time.sleep(2 + 2 * attempt)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1][0] + tf_ms
        if len(batch) < limit:
            break
    return out


def candles_to_df(candles, timeframe, now_ms):
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="ts", keep="last").sort_values("ts").reset_index(drop=True)
    tf_ms = TF_MS[timeframe]
    df = df[df["ts"] + tf_ms <= now_ms]  # keep only closed candles
    df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["timestamp", "ts", "open", "high", "low", "close", "volume"]]


def file_path(data_dir, symbol, timeframe):
    return Path(data_dir) / f"{symbol.replace('/', '-')}_{timeframe}.parquet"


def download_one(exchange, symbol, timeframe, config):
    tf_ms = TF_MS[timeframe]
    now_ms = exchange.fetch_time()
    months = config["data"]["months_back"]
    since_ms = now_ms - int(months * 30.44 * 86_400_000)
    since_ms -= since_ms % tf_ms  # align to bar boundary
    candles = fetch_range(exchange, symbol, timeframe, since_ms, now_ms)
    df = candles_to_df(candles, timeframe, now_ms)
    path = file_path(config["data"]["dir"], symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df, path, since_ms, now_ms


def quality_report(df, timeframe, since_ms, now_ms):
    """Count rows, detect missing candles and coverage truncation."""
    tf_ms = TF_MS[timeframe]
    if df.empty:
        return {
            "rows": 0, "first": None, "last": None, "expected": 0,
            "missing_bars": 0, "gap_count": 0, "largest_gaps": [],
            "warnings": ["empty dataframe"],
        }
    first, last = int(df["ts"].iloc[0]), int(df["ts"].iloc[-1])
    warnings = []
    if first > since_ms + 2 * tf_ms:
        warnings.append("data starts late: possible truncated download")
    last_closed_open = (now_ms // tf_ms - 1) * tf_ms
    if last < last_closed_open - 2 * tf_ms:
        warnings.append("data ends early")
    expected = (last - first) // tf_ms + 1
    full = np.arange(first, last + 1, tf_ms)
    missing = np.setdiff1d(full, df["ts"].values)
    gaps = []
    for ts in missing:
        if gaps and ts == gaps[-1][1] + tf_ms:
            gaps[-1][1] = ts
        else:
            gaps.append([ts, ts])
    gaps = sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)
    return {
        "rows": len(df),
        "first": pd.to_datetime(first, unit="ms", utc=True),
        "last": pd.to_datetime(last, unit="ms", utc=True),
        "expected": expected,
        "missing_bars": len(missing),
        "gap_count": len(gaps),
        "largest_gaps": [
            (pd.to_datetime(g[0], unit="ms", utc=True),
             pd.to_datetime(g[1], unit="ms", utc=True),
             (g[1] - g[0]) // tf_ms + 1)
            for g in gaps[:5]
        ],
        "warnings": warnings,
    }


def load_ohlcv(symbol, timeframe, data_dir):
    path = file_path(data_dir, symbol, timeframe)
    df = pd.read_parquet(path)
    df = df.sort_values("ts").reset_index(drop=True)
    df["close_time"] = df["timestamp"] + pd.to_timedelta(TF_MS[timeframe], unit="ms")
    return df
