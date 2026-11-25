"""CLI entry point. Usage: python run.py download|signals|backtest|validate|sweep|snapshot"""
import argparse
import json
import math
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from data import (check_data, check_features, download_futures, download_one,
                  file_path, funding_file_path, futures_file_path, load_config,
                  load_ohlcv, make_exchange, quality_report)
from engine import run_backtest, validate_config
from metrics import compute_benchmarks, compute_metrics
from report import (compare_markets, make_run_id, write_compare, write_report,
                    write_run_metadata)
from strategy import FEATURE_COLS, build_features, funnel
from validation import compare_exits, gate1_checklist, hold_out, param_sweep, walk_forward


def cmd_download(config, args):
    if args.market in ("futures", "all"):
        download_futures(config)
        print("\nTải futures xong (OHLCV + funding + instruments.json).")
    if args.market in ("spot", "all"):
        exchange = make_exchange()
        print("Đang tải OHLCV spot OKX (dữ liệu công khai, không cần API key)...")
        warnings_found = False
        for symbol in config["data"]["symbols"]:
            for tf in config["data"]["timeframes"]:
                t0 = time.time()
                df, path, since_ms, now_ms = download_one(exchange, symbol, tf, config)
                rep = quality_report(df, tf, since_ms, now_ms)
                print(f"\n=== {path}  ({time.time() - t0:.0f}s)")
                print(f"dòng={rep['rows']}  đầu={rep['first']}  cuối={rep['last']}")
                print(f"kỳ vọng={rep['expected']}  nến thiếu={rep['missing_bars']}  "
                      f"số gap={rep['gap_count']}")
                for start, end, n in rep["largest_gaps"]:
                    print(f"  gap {start} -> {end} : thiếu {n} nến")
                for w in rep["warnings"]:
                    print(f"CẢNH BÁO: {w}")
                    warnings_found = True
        print("\nTải xong. Giờ UTC máy chủ:",
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
        print(f"\nsố tín hiệu: {len(sig)}")
        per_q = sig["timestamp"].dt.tz_localize(None).dt.to_period("Q").value_counts().sort_index()
        print("tín hiệu theo quý:")
        print(per_q.to_string())
        print("5 tín hiệu đầu tiên:")
        for t in sig["timestamp"].iloc[:5]:
            print(" ", t.strftime("%Y-%m-%d %H:%M UTC"))
        print("5 tín hiệu cuối cùng:")
        for t in sig["timestamp"].iloc[-5:]:
            print(" ", t.strftime("%Y-%m-%d %H:%M UTC"))
        out = Path(data_dir) / f"features_{symbol.replace('/', '-')}_1h.parquet"
        features.to_parquet(out, index=False)
        print(f"đã lưu {out}")


def _ctx(config, symbol, market):
    """Build (frames, engine_kwargs, symbols, data_sources) for a market."""
    data_dir = config["data"]["dir"]
    if market == "futures":
        symbols = ([symbol] if symbol
                   else config["data"].get("futures_symbols",
                                           [s + ":USDT" for s in config["data"]["symbols"]]))
        inst = {}
        meta_path = Path(data_dir) / "futures" / "instruments.json"
        if meta_path.exists():
            inst = json.loads(meta_path.read_text(encoding="utf-8"))
        contract_meta = {}
        for sym in symbols:
            inst_id = f"{sym.split('/')[0]}-{sym.split('/')[1].split(':')[0]}-SWAP"
            if inst_id in inst:
                contract_meta[sym] = inst[inst_id]
            else:
                print(f"CẢNH BÁO: thiếu metadata cho {inst_id}; chạy 'download --market futures'")
        frames = {}
        for sym in symbols:
            frames[sym] = build_features(load_ohlcv(sym, "1h", data_dir, "futures"),
                                         load_ohlcv(sym, "4h", data_dir, "futures"), config)
        funding = {}
        sources = [meta_path]
        for sym in symbols:
            fp = funding_file_path(data_dir, sym)
            if fp.exists():
                fd = pd.read_parquet(fp)
                if len(fd):
                    funding[sym] = fd
                    sources.append(fp)
                    print(f"funding {sym}: {len(fd)} kỳ, "
                          f"{fd['time'].iloc[0]} -> {fd['time'].iloc[-1]}")
            else:
                print(f"CẢNH BÁO: thiếu dữ liệu funding cho {sym} - funding bị bỏ qua")
            for tf in config["data"]["timeframes"]:
                sources.append(futures_file_path(data_dir, sym, tf))
        return frames, {"funding_by_symbol": funding or None,
                        "contract_meta": contract_meta}, symbols, sources
    symbols = [symbol] if symbol else config["data"]["symbols"]
    frames = {sym: build_features(load_ohlcv(sym, "1h", data_dir),
                                  load_ohlcv(sym, "4h", data_dir), config)
              for sym in symbols}
    sources = [file_path(data_dir, sym, tf)
               for sym in symbols for tf in config["data"]["timeframes"]]
    return frames, {}, symbols, sources


def _data_gate(config, market, frames):
    """Run check_data on raw OHLCV + check_features on features.
    Returns (issues, has_critical)."""
    issues = []
    data_dir = config["data"]["dir"]
    is_fut = market == "futures"
    symbols = (config["data"].get("futures_symbols",
                                  [s + ":USDT" for s in config["data"]["symbols"]])
               if is_fut else config["data"]["symbols"])
    for sym in symbols:
        for tf in ["1h", "4h"]:
            df = load_ohlcv(sym, tf, data_dir, "futures" if is_fut else "spot")
            for i in check_data(df, tf, config):
                issues.append({"file": f"{sym}_{tf}", **i})
    for sym, feat in frames.items():
        for i in check_features(feat, FEATURE_COLS):
            issues.append({"file": f"features_{sym}", **i})
    return issues, any(i["severity"] == "CRITICAL" for i in issues)


def _print_gate(issues, exploratory):
    for i in issues:
        print(f"  [{i['severity']}] {i['file']}: {i['message']}")
    has_critical = any(i["severity"] == "CRITICAL" for i in issues)
    if has_critical and not exploratory:
        print("DỮ LIỆU KHÔNG ĐẠT GATE (CRITICAL) - dừng. Dùng --exploratory để chạy "
              "với nhãn EXPLORATORY.")
        sys.exit(3)
    return has_critical


def _run_one(config, market, symbol, out_dir, exploratory=False):
    frames, ekw, symbols, sources = _ctx(config, symbol, market)
    issues, _ = _data_gate(config, market, frames)
    has_critical = _print_gate(issues, exploratory)
    trades, orders, equity, skipped, summary = run_backtest(
        frames, config, market_type=market, **ekw)
    m = compute_metrics(trades, orders, equity, config)
    b = compute_benchmarks(frames, config)
    write_report(config, trades, orders, equity, skipped, summary, m, b,
                 out_dir=out_dir, exploratory=exploratory or has_critical)
    write_run_metadata(out_dir, config, summary, m, sources, " ".join(sys.argv[1:]),
                       exploratory=exploratory or has_critical)
    print(f"  {market} -> {out_dir}  "
          f"lợi nhuận={m['net_return_pct']:.2f}%  lệnh={m['n_trades']}  PF={m['profit_factor']:.3f}")
    return m


def cmd_backtest(config, args):
    run_id = make_run_id()
    out_root = Path("results") / run_id
    if args.market == "all":
        m_spot = _run_one(config, "spot", args.symbol, out_root / "spot",
                          exploratory=args.exploratory)
        fut_symbol = args.symbol + ":USDT" if args.symbol else None
        m_fut = _run_one(config, "futures", fut_symbol, out_root / "futures",
                         exploratory=args.exploratory)
        table = compare_markets(m_spot, m_fut)
        print("\n=== So sánh spot vs futures (cùng chiến lược, chỉ long) ===")
        print(table.to_string(index=False))
        write_compare(out_root, table)
        print(f"kết quả trong {out_root}/ (compare.md + spot/ + futures/)")
    else:
        _run_one(config, args.market, args.symbol, out_root, exploratory=args.exploratory)
        print(f"benchmark (mua & giữ %): xem {out_root / 'summary.md'}")
    print(f"run id: {run_id}")


def cmd_validate(config, args):
    if args.market == "all":
        print("validate hỗ trợ từng market riêng: spot hoặc futures.")
        sys.exit(2)
    frames, ekw, _, _ = _ctx(config, args.symbol, args.market)
    issues, _ = _data_gate(config, args.market, frames)
    _print_gate(issues, args.exploratory)
    trades, orders, equity, skipped, summary = run_backtest(
        frames, config, market_type=args.market, **ekw)
    m = compute_metrics(trades, orders, equity, config)
    wf, pooled = walk_forward(frames, config, market_type=args.market, **ekw)
    ho_m, split, _ = hold_out(frames, config, market_type=args.market, **ekw)
    checks, warnings = gate1_checklist(m, ho_m, wf)
    print("\n=== Walk-forward (1 lần chạy liên tục, tham số cố định, cửa sổ 3 tháng theo entry_time) ===")
    print(wf.to_string(index=False))
    print(f"\nPooled out-of-sample (gộp toàn bộ cửa sổ): "
          f"expectancy={pooled['expectancy_usdt']:.4f} USDT, "
          f"CI90=[{pooled['expectancy_ci_90_lo']:.4f}, {pooled['expectancy_ci_90_hi']:.4f}], "
          f"PF={pooled['profit_factor']:.3f}")
    print(f"\n=== Hold-out (20% cuối cùng của dữ liệu, từ {split}) ===")
    for k, v in ho_m.items():
        print(f"  {k}: {v}")
    print("\n=== Danh sách kiểm tra Gate 1 ===")
    for _, r in checks.iterrows():
        print(f"  [{r['result']}] {r['check']}  ({r['detail']})")
    for w in warnings:
        print(w)
    out = Path("results")
    out.mkdir(parents=True, exist_ok=True)
    wf.to_csv(out / "walk_forward.csv", index=False)
    (out / "gate1.md").write_text(
        "\n".join([f"- [{r['result']}] {r['check']} ({r['detail']})"
                   for _, r in checks.iterrows()] + [""] + warnings) + "\n",
        encoding="utf-8")
    print(f"đã lưu results/gate1.md + results/walk_forward.csv")


def cmd_sweep(config, args):
    if args.market == "all":
        print("sweep hỗ trợ từng market riêng: spot hoặc futures.")
        sys.exit(2)
    frames, ekw, _, _ = _ctx(config, args.symbol, args.market)
    param_lists = []
    for p in args.param or []:
        path, vals_s = p.split("=", 1)
        vals = [float(v) if "." in v else int(v) for v in vals_s.split(",")]
        param_lists.append((path, vals))
    if len(param_lists) > 2 or any(len(v) > 3 for _, v in param_lists):
        print("Giữ sweep nhỏ: tối đa 2 tham số x 3 giá trị.")
        sys.exit(2)
    df = param_sweep(frames, config, param_lists, market_type=args.market, **ekw)
    show = df.rename(columns={"config": "cấu hình", "n_trades": "số lệnh",
                              "win_rate_pct": "tỷ lệ thắng %", "profit_factor": "PF",
                              "expectancy_usdt": "expectancy (USDT)", "net_pnl": "PnL ròng"})
    print("\n=== Quét tham số (in TẤT CẢ kết quả, không chọn tự động) ===")
    print(show.to_string(index=False))
    out = Path("results")
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "sweep.csv", index=False)
    print("đã lưu results/sweep.csv")


def cmd_snapshot(config, args):
    """Spot config B (ema_exit + initial_stop) headline metrics -> results/snapshot_<label>.csv."""
    frames, _, _, _ = _ctx(config, None, "spot")
    cfg = deepcopy(config)
    cfg["stops"].update({"ema_exit_enabled": True, "initial_stop_enabled": True,
                         "take_profit_r": None, "trailing_enabled": False,
                         "trailing_atr_mult": 2.5, "time_stop_bars": None})
    trades, orders, equity, skipped, summary = run_backtest(frames, cfg, market_type="spot")
    m = compute_metrics(trades, orders, equity, cfg)
    pf = m["profit_factor"]
    row = pd.DataFrame([{
        "label": args.label,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trades": m["n_trades"],
        "PF": round(pf, 4) if math.isfinite(pf) else "inf",
        "expectancy": round(m["expectancy_usdt"], 4),
        "net_return": round(m["net_return_pct"], 4),
        "max_dd": round(m["max_drawdown_pct"], 4),
    }])
    out = Path("results") / f"snapshot_{args.label}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    row.to_csv(out, index=False)
    print(row.to_string(index=False))
    print(f"đã lưu {out}")


def cmd_compare_exits(config, args):
    if args.market == "all":
        print("compare-exits hỗ trợ từng market riêng: spot hoặc futures.")
        sys.exit(2)
    frames, ekw, _, _ = _ctx(config, args.symbol, args.market)
    df = compare_exits(frames, config, market_type=args.market, **ekw)
    print("\n=== So sánh 4 cấu hình thoát lệnh (cùng dữ liệu, in TẤT CẢ kết quả) ===")
    print(df.to_string(index=False))
    out = Path("results")
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "compare_exits.csv", index=False)
    print("đã lưu results/compare_exits.csv")
    print("\nLưu ý: KHÔNG chọn hàng có lợi nhuận cao nhất - hãy chọn theo drawdown và "
          "độ ổn định, sau đó xác nhận lại bằng walk-forward.")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="run.py")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("download", help="Fetch OKX OHLCV into data/*.parquet")
    d.add_argument("--market", choices=["spot", "futures", "all"], default="spot")
    sub.add_parser("signals", help="Build features/signals, save data/features_*_1h.parquet")
    p = sub.add_parser("backtest", help="Run backtest (spot or futures)")
    p.add_argument("--market", choices=["spot", "futures", "all"], default="spot")
    p.add_argument("--symbol", default=None, help="e.g. BTC/USDT (default: all in config)")
    p.add_argument("--exploratory", action="store_true",
                   help="chạy dù data gate CRITICAL, đánh nhãn EXPLORATORY")
    v = sub.add_parser("validate", help="Walk-forward + hold-out + Gate 1 checklist")
    v.add_argument("--market", choices=["spot", "futures", "all"], default="spot")
    v.add_argument("--symbol", default=None)
    v.add_argument("--exploratory", action="store_true",
                   help="chạy dù data gate CRITICAL, đánh nhãn EXPLORATORY")
    s = sub.add_parser("sweep", help="Tiny parameter sweep (max 2 params x 3 values)")
    s.add_argument("--market", choices=["spot", "futures", "all"], default="spot")
    s.add_argument("--symbol", default=None)
    s.add_argument("--param", action="append", metavar="SECTION.KEY=v1,v2,v3",
                   help="repeatable, e.g. --param risk.notional_cap=200,300,500")
    ce = sub.add_parser("compare-exits", help="Compare 4 fixed exit configs on the same data")
    ce.add_argument("--market", choices=["spot", "futures", "all"], default="spot")
    ce.add_argument("--symbol", default=None)
    sn = sub.add_parser("snapshot",
                        help="Spot config B headline metrics -> results/snapshot_<label>.csv")
    sn.add_argument("--label", required=True)
    args = parser.parse_args()
    config = load_config("config.yaml")
    try:
        validate_config(config)
    except ValueError as e:
        print(f"Lỗi config: {e}")
        sys.exit(2)
    if args.command == "download":
        cmd_download(config, args)
    elif args.command == "signals":
        cmd_signals(config)
    elif args.command == "backtest":
        cmd_backtest(config, args)
    elif args.command == "validate":
        cmd_validate(config, args)
    elif args.command == "sweep":
        cmd_sweep(config, args)
    elif args.command == "compare-exits":
        cmd_compare_exits(config, args)
    elif args.command == "snapshot":
        cmd_snapshot(config, args)


if __name__ == "__main__":
    main()
