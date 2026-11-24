"""CLI entry point. Usage: python run.py download"""
import argparse
import time
from datetime import datetime, timezone

from data import download_one, load_config, make_exchange, quality_report


def cmd_download(config):
    exchange = make_exchange()
    print("Downloading OKX spot OHLCV (public, no API key)...")
    for symbol in config["data"]["symbols"]:
        for tf in config["data"]["timeframes"]:
            t0 = time.time()
            df, path = download_one(exchange, symbol, tf, config)
            rep = quality_report(df, tf)
            print(f"\n=== {path}  ({time.time() - t0:.0f}s)")
            print(f"rows={rep['rows']}  first={rep['first']}  last={rep['last']}")
            print(f"expected={rep['expected']}  missing_bars={rep['missing_bars']}  "
                  f"gap_count={rep['gap_count']}")
            for start, end, n in rep["largest_gaps"]:
                print(f"  gap {start} -> {end} : {n} bars missing")
    print("\nDownload complete. Server UTC now:",
          datetime.fromtimestamp(exchange.milliseconds() / 1000, tz=timezone.utc))


def main():
    parser = argparse.ArgumentParser(prog="run.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download", help="Fetch OKX spot OHLCV into data/*.parquet")
    args = parser.parse_args()
    config = load_config("config.yaml")
    if args.command == "download":
        cmd_download(config)


if __name__ == "__main__":
    main()
