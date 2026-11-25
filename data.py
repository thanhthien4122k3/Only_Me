"""Public OKX spot OHLCV download via ccxt (no API key)."""
import json
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


def load_ohlcv(symbol, timeframe, data_dir, market="spot"):
    path = (futures_file_path(data_dir, symbol, timeframe) if market == "futures"
            else file_path(data_dir, symbol, timeframe))
    df = pd.read_parquet(path)
    df = df.sort_values("ts").reset_index(drop=True)
    df["close_time"] = df["timestamp"] + pd.to_timedelta(TF_MS[timeframe], unit="ms")
    return df


def futures_file_path(data_dir, symbol, timeframe):
    name = symbol.replace("/", "-").replace(":", "-")
    return Path(data_dir) / "futures" / f"{name}_{timeframe}.parquet"


def funding_file_path(data_dir, symbol):
    name = symbol.replace("/", "-").replace(":", "-")
    return Path(data_dir) / "futures" / f"funding_{name}.parquet"


def fetch_funding_history(exchange, symbol, since_ms, until_ms, limit=100):
    """OKX serves only ~3 months of funding history back (verified Nov 2026)."""
    out = []
    cursor = since_ms
    while cursor < until_ms:
        batch = None
        for attempt in range(3):
            try:
                batch = exchange.fetch_funding_rate_history(symbol, since=cursor, limit=limit)
                break
            except (ccxt.NetworkError, ccxt.RateLimitExceeded):
                if attempt == 2:
                    raise
                time.sleep(2 + 2 * attempt)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1]["timestamp"] + 1
        if len(batch) < limit:
            break
    return out


def funding_to_df(rows):
    df = pd.DataFrame(rows)[["timestamp", "fundingRate"]]
    df = df.rename(columns={"timestamp": "ts", "fundingRate": "rate"})
    df = df.drop_duplicates(subset="ts", keep="last").sort_values("ts").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["time", "ts", "rate"]]


def fetch_instrument_meta(exchange, inst_ids):
    """Normalized contract metadata from OKX public instruments (SWAP)."""
    raw = exchange.publicGetPublicInstruments({"instType": "SWAP"})
    out = {}
    for r in raw.get("data", []):
        if r["instId"] in inst_ids:
            out[r["instId"]] = {
                "instId": r["instId"],
                "instType": r["instType"],
                "ctVal": float(r["ctVal"]),
                "ctValCcy": r["ctValCcy"],
                "ctMult": float(r.get("ctMult") or 1),
                "lotSz": float(r["lotSz"]),
                "minSz": float(r["minSz"]),
                "tickSz": float(r["tickSz"]),
                "settleCcy": r["settleCcy"],
            }
    return out


def download_futures(config):
    """Swap OHLCV (1h/4h) + funding rates + instrument metadata -> data/futures/."""
    exchange = make_exchange()
    data_dir = config["data"]["dir"]
    symbols = config["data"].get("futures_symbols",
                                 [s + ":USDT" for s in config["data"]["symbols"]])
    inst_ids = {f"{s.split('/')[0]}-{s.split('/')[1].split(':')[0]}-SWAP" for s in symbols}
    meta = fetch_instrument_meta(exchange, inst_ids)
    meta_path = Path(data_dir) / "futures" / "instruments.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"đã lưu {meta_path}: {sorted(meta)}")
    for symbol in symbols:
        for tf in ["1h", "4h"]:
            now_ms = exchange.fetch_time()
            months = config["data"]["months_back"]
            since_ms = now_ms - int(months * 30.44 * 86_400_000)
            since_ms -= since_ms % TF_MS[tf]
            candles = fetch_range(exchange, symbol, tf, since_ms, now_ms)
            df = candles_to_df(candles, tf, now_ms)
            path = futures_file_path(data_dir, symbol, tf)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=False)
            rep = quality_report(df, tf, since_ms, now_ms)
            print(f"=== {path}: dòng={rep['rows']} đầu={rep['first']} cuối={rep['last']} "
                  f"thiếu={rep['missing_bars']}")
            for w in rep["warnings"]:
                print(f"CẢNH BÁO: {w}")
        now_ms = exchange.fetch_time()
        months = config["data"]["months_back"]
        since_ms = now_ms - int(months * 30.44 * 86_400_000)
        rows = fetch_funding_history(exchange, symbol, since_ms, now_ms)
        fdf = funding_to_df(rows)
        fpath = funding_file_path(data_dir, symbol)
        fdf.to_parquet(fpath, index=False)
        print(f"=== {fpath}: dòng funding={len(fdf)} "
              f"đầu={fdf['time'].iloc[0] if len(fdf) else None} "
              f"cuối={fdf['time'].iloc[-1] if len(fdf) else None} "
              f"(OKX chỉ có lịch sử funding ~3 tháng)")
    return meta


STABLECOIN_BASES = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "PYUSD", "GUSD",
                    "BUSD", "EURT", "AEUR", "USDP", "PAX", "USDJ", "USTC", "LUSD",
                    "FRAX", "USDD", "XUSD"}
WRAPPED_BASES = {"WBTC", "WETH", "WBETH", "STETH", "RETH", "WEETH", "METH"}
LEV_SUFFIXES = ("3L", "3S", "5L", "5S", "2L", "2S", "BULL", "BEAR", "UP", "DOWN")
LEV_PREFIXES = ("2X", "3X", "5X", "10X")


def is_excluded_base(base):
    """Heuristic: stablecoin / wrapped-staked / leveraged token bases."""
    if base in STABLECOIN_BASES or base in WRAPPED_BASES:
        return True
    if base.startswith(LEV_PREFIXES) or base.endswith(LEV_SUFFIXES):
        return True
    return False


def fetch_spot_usdt_tickers(exchange):
    """Public tickers: spot USDT pairs, excluded bases removed, ranked by 24h quote volume
    (top 30). No API key needed."""
    tickers = exchange.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT") or sym.count("/") != 1:
            continue
        if t.get("spot") is False:
            continue
        base = sym.split("/")[0]
        if is_excluded_base(base):
            continue
        qv = t.get("quoteVolume")
        if qv is None:
            qv = (t.get("baseVolume") or 0.0) * (t.get("last") or 0.0)
        rows.append({"symbol": sym, "base": base, "quote_volume": float(qv or 0.0)})
    rows.sort(key=lambda r: -r["quote_volume"])
    return rows[:30]


def first_candle_ts(exchange, symbol, since_ms):
    """First available 1H candle open time (ms) at/after since_ms, or None.
    Note: OKX history-candles has a max lookback (~5 years) - probing further returns empty."""
    try:
        c = exchange.fetch_ohlcv(symbol, "1h", since=since_ms, limit=1,
                                 params={"type": "HistoryCandles"})
    except Exception:
        return None
    return int(c[0][0]) if c else None


def check_data(df, timeframe, cfg):
    """Data quality gate. Returns a list of {"severity": CRITICAL|WARN, "code", "message"}."""
    issues = []
    if df.empty:
        issues.append({"severity": "CRITICAL", "code": "empty", "message": "dataframe rỗng"})
        return issues
    tf_ms = TF_MS[timeframe]
    if df["ts"].duplicated().any() or not df["ts"].is_monotonic_increasing:
        issues.append({"severity": "CRITICAL", "code": "ts_invalid",
                       "message": "ts trùng lặp hoặc không tăng dần"})
    bad = (df["high"] < df[["open", "close"]].max(axis=1)) | \
          (df["low"] > df[["open", "close"]].min(axis=1))
    if bad.any():
        issues.append({"severity": "CRITICAL", "code": "ohlc_inconsistent",
                       "message": f"{int(bad.sum())} nến có high/low mâu thuẫn open/close"})
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        issues.append({"severity": "CRITICAL", "code": "nonpositive_price",
                       "message": "có giá <= 0"})
    months = cfg["data"]["months_back"]
    min_days = months * 30.44 - 7
    coverage_days = (int(df["ts"].iloc[-1]) - int(df["ts"].iloc[0])) / 86_400_000
    if coverage_days < min_days:
        issues.append({"severity": "CRITICAL", "code": "short_coverage",
                       "message": f"độ phủ {coverage_days:.0f} ngày < {min_days:.0f} ngày"})
    first, last = int(df["ts"].iloc[0]), int(df["ts"].iloc[-1])
    expected = (last - first) // tf_ms + 1
    missing = expected - len(df)
    if missing > 0:
        full = np.arange(first, last + 1, tf_ms)
        miss = np.setdiff1d(full, df["ts"].values)
        gaps = []
        for ts_ms in miss:
            if gaps and ts_ms == gaps[-1][1] + tf_ms:
                gaps[-1][1] = ts_ms
            else:
                gaps.append([ts_ms, ts_ms])
        for g in gaps:
            n = (g[1] - g[0]) // tf_ms + 1
            t0 = pd.to_datetime(g[0], unit="ms", utc=True)
            if n > 12:
                issues.append({"severity": "CRITICAL", "code": "large_gap",
                               "message": f"gap {n} nến liên tiếp tại {t0}"})
            else:
                issues.append({"severity": "WARN", "code": "small_gap",
                               "message": f"gap {n} nến tại {t0}"})
        if missing > 0.005 * expected:
            issues.append({"severity": "CRITICAL", "code": "too_many_missing",
                           "message": f"thiếu {missing}/{expected} nến "
                                      f"({missing / expected * 100:.2f}%)"})
    zero_vol = int((df["volume"] == 0).sum())
    if zero_vol:
        issues.append({"severity": "WARN", "code": "zero_volume",
                       "message": f"{zero_vol} nến volume = 0"})
    return issues


def check_features(features_df, required_cols, warmup_bars=250):
    """CRITICAL if any required feature is NaN after the indicator warm-up."""
    issues = []
    for col in required_cols:
        if col not in features_df.columns:
            issues.append({"severity": "CRITICAL", "code": "feature_missing",
                           "message": f"thiếu cột feature {col}"})
            continue
        n = int(features_df[col].iloc[warmup_bars:].isna().sum())
        if n:
            issues.append({"severity": "CRITICAL", "code": "feature_nan",
                           "message": f"{col}: {n} NaN sau warm-up {warmup_bars} nến"})
    return issues
