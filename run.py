"""CLI entry point. Usage: python run.py download|signals"""
import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from data import download_one, load_config, load_ohlcv, make_exchange, quality_report
from strategy import build_features, funnel


def cmd_download(config):
    exchange = make_exchange()
    print("Downloading OKX spot OHLCV (public, no API key)...")
    warnings_found = False
    for symbol in config["data"]["symbols"]:
        for tf in config["data"]["timeframes"]:
            t0 = time.time()
            df, path, since_ms, now_ms = download_one(exchange, symbol, tf, config)
            rep = quality_report(df, tf, since_ms, now_ms)
            print(f"\n=== {path}  ({time.time() - t0:.0f}s)")
            print(f"rows={rep['rows']}  first={rep['first']}  last={rep['last']}")
            print(f"expected={rep['expected']}  missing_bars={rep['missing_bars']}  "
                  f"gap_count={rep['gap_count']}")
            for start, end, n in rep["largest_gaps"]:
                print(f"  gap {start} -> {end} : {n} bars missing")
            for w in rep["warnings"]:
                print(f"WARNING: {w}")
                warnings_found = True
    print("\nDownload complete. Server UTC now:",
          datetime.fromtimestamp(exchange.fetch_time() / 1000, tz=timezone.utc))
    if warnings_found:
        sys.exit(1)


def cmd_signals(config):
    data_dir = config["data"]["dir"]
    for symbol in config["data"]["symbols"]:
        df_1h = load_ohlcv(symbol, "1h", data_dir)
        df_4h = load_ohlcv(symbol, "4h", data_dir)
        features = build_features(df_1h, df_4h, config)
        print(f"\n=== {symbol}")
        print(funnel(features, config).to_string(index=False))
        sig = features[features["signal"]]
        print(f"\nsignal count: {len(sig)}")
        per_q = sig["timestamp"].dt.tz_localize(None).dt.to_period("Q").value_counts().sort_index()
        print("signals per quarter:")
        print(per_q.to_string())
        print("first 5 signal timestamps:")
        for t in sig["timestamp"].iloc[:5]:
            print(" ", t.strftime("%Y-%m-%d %H:%M UTC"))
        print("last 5 signal timestamps:")
        for t in sig["timestamp"].iloc[-5:]:
            print(" ", t.strftime("%Y-%m-%d %H:%M UTC"))
        out = Path(data_dir) / f"features_{symbol.replace('/', '-')}_1h.parquet"
        features.to_parquet(out, index=False)
        print(f"saved {out}")


def main():
    parser = argparse.ArgumentParser(prog="run.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download", help="Fetch OKX spot OHLCV into data/*.parquet")
    sub.add_parser("signals", help="Build features/signals, save data/features_*_1h.parquet")
    args = parser.parse_args()
    config = load_config("config.yaml")
    if args.command == "download":
        cmd_download(config)
    elif args.command == "signals":
        cmd_signals(config)


if __name__ == "__main__":
    main()
