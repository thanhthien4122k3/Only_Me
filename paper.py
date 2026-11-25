"""Paper trading (GIẢ LẬP hoàn toàn). Chỉ dùng public OKX data qua ccxt - không API key,
không endpoint private, KHÔNG có bất kỳ hàm đặt lệnh thật nào.

Thiết kế: mỗi chu kỳ tải lại nến đã đóng (warm-up đủ cho indicator), build_features, rồi
chạy run_backtest() với execution_start = ngày bắt đầu paper và execution_end = nến đóng
mới nhất. Vì engine deterministic và causal, chạy lại nhiều lần ra đúng danh sách lệnh cũ
(idempotent) và mất kết nối sẽ tự bù đúng số nến thiếu ở lần chạy sau.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from data import (TF_MS, candles_to_df, check_data, fetch_range, load_config,
                  load_ohlcv, make_exchange)
from engine import run_backtest
from metrics import compute_metrics
from strategy import build_features

H1_MS = TF_MS["1h"]
H4_MS = TF_MS["4h"]
TRADE_COLS = ["symbol", "entry_time", "entry_price", "exit_time", "exit_price",
              "exit_reason", "quantity"]


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_paper_cfg(override_path="paper/paper_config.yaml"):
    base = load_config("config.yaml")
    over = {}
    if override_path and Path(override_path).exists():
        with open(override_path, "r", encoding="utf-8") as f:
            over = yaml.safe_load(f) or {}
    cfg = deep_merge(base, over)
    cfg.setdefault("paper", {})
    cfg["paper"].setdefault("warmup_days", 90)
    cfg["paper"].setdefault("delay_seconds", 60)
    cfg["paper"].setdefault("out_dir", "paper")
    return cfg


def cfg_hash(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]


def log_line(out_dir, msg):
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    with open(p / "run.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


class InstanceLock:
    """Khóa độc quyền cho thư mục live. Hệ điều hành tự nhả khóa khi tiến trình kết thúc,
    nên không bao giờ có lock cũ. Ngăn hai loop cùng ghi một thư mục (đã xảy ra trước đây).
    Dùng: `with InstanceLock(out_dir): ...` - ném RuntimeError nếu đã có tiến trình khác giữ."""

    def __init__(self, out_dir):
        self.dir = Path(out_dir)
        self.path = self.dir / "paper.lock"
        self._fh = None

    def acquire(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise RuntimeError(f"đã có tiến trình paper khác giữ khóa {self.path}")
        self._fh = fh
        (self.dir / "paper.pid").write_text(str(os.getpid()), encoding="utf-8")
        return self

    def release(self):
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


def save_candle_store(store_dir, sym, tf, df):
    """Gộp nến live đã đóng vào store (một file/cặp/khung, cùng tên như data/ để load_ohlcv
    đọc lại được). Replay và backtest dùng store này nên kiểm chứng trên dữ liệu live thật."""
    cols = ["timestamp", "ts", "open", "high", "low", "close", "volume"]
    path = Path(store_dir) / f"{sym.replace('/', '-').replace(':', '-')}_{tf}.parquet"
    new = df[cols]
    if path.exists():
        new = pd.concat([pd.read_parquet(path)[cols], new], ignore_index=True)
    new = new.drop_duplicates(subset="ts", keep="last").sort_values("ts").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(path, index=False)
    return path


def load_state(out_dir):
    sp = Path(out_dir) / "state.json"
    if sp.exists():
        return json.loads(sp.read_text(encoding="utf-8"))
    return None


def save_state(out_dir, state):
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    (p / "state.json").write_text(json.dumps(state, indent=2, default=str),
                                  encoding="utf-8")


def symbols_for(cfg, market):
    return cfg["data"]["futures_symbols" if market == "futures" else "symbols"]


def load_contract_meta(cfg):
    p = Path(cfg["data"]["dir"]) / "futures" / "instruments.json"
    inst = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    meta = {}
    for sym in cfg["data"]["futures_symbols"]:
        iid = f"{sym.split('/')[0]}-{sym.split('/')[1].split(':')[0]}-SWAP"
        if iid in inst:
            meta[sym] = inst[iid]
    return meta


def fetch_ohlcv_live(exchange, symbol, tf, since_ms):
    now_ms = exchange.fetch_time()
    df = candles_to_df(fetch_range(exchange, symbol, tf, since_ms, now_ms), tf, now_ms)
    df["close_time"] = df["timestamp"] + pd.to_timedelta(TF_MS[tf], unit="ms")
    return df


def slice_local(df, since_ms, end_ms):
    return df[(df["ts"] >= since_ms) & (df["ts"] <= end_ms)].reset_index(drop=True)


def build_window_frames(cfg, market, start_ms, end_ms, exchange=None, data_dir=None):
    warmup_days = cfg.get("paper", {}).get("warmup_days", 90)
    since_ms = start_ms - int(warmup_days * 86_400_000)
    frames, raw = {}, {}
    for sym in symbols_for(cfg, market):
        if exchange is not None:
            df1 = fetch_ohlcv_live(exchange, sym, "1h", since_ms)
            df4 = fetch_ohlcv_live(exchange, sym, "4h", since_ms)
        else:
            df1 = slice_local(load_ohlcv(sym, "1h", data_dir, market), since_ms, end_ms)
            df4 = slice_local(load_ohlcv(sym, "4h", data_dir, market), since_ms, end_ms)
        raw[sym] = {"1h": df1, "4h": df4}
        frames[sym] = build_features(df1, df4, cfg)
    return frames, raw


def gate_raw(raw, cfg, out_dir):
    issues = []
    for sym, tfs in raw.items():
        for tf, df in tfs.items():
            for i in check_data(df, tf, cfg):
                issues.append({"file": f"{sym}_{tf}", **i})
    for i in issues:
        log_line(out_dir, f"[{i['severity']}] {i['file']}: [{i['code']}] {i['message']}")
    return issues


def build_signals_df(orders, skipped):
    rows = []
    if len(orders):
        buys = orders[orders["side"] == "buy"]
        for _, o in buys.iterrows():
            if o["status"] == "FILLED":
                rows.append({"symbol": o["symbol"],
                             "signal_time": o["fill_time"] - pd.Timedelta(hours=1),
                             "action_time": o["fill_time"], "action": "ENTER",
                             "reason": o["reason"]})
            elif o["status"] == "REJECTED":
                rows.append({"symbol": o["symbol"], "signal_time": pd.NaT,
                             "action_time": None, "action": "REJECT",
                             "reason": o["reason"]})
    for _, s in skipped.iterrows():
        rows.append({"symbol": s["symbol"],
                     "signal_time": s["time"] - pd.Timedelta(hours=1),
                     "action_time": s["time"], "action": "SKIP",
                     "reason": s["reason"]})
    cols = ["symbol", "signal_time", "action_time", "action", "reason"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).sort_values(["signal_time", "symbol"]).reset_index(drop=True)


def write_outputs(out_dir, trades, orders, equity, skipped):
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    trades.to_csv(p / "trades.csv", index=False)
    orders.to_csv(p / "orders.csv", index=False)
    equity.to_csv(p / "equity.csv", index=False)
    build_signals_df(orders, skipped).to_csv(p / "signals.csv", index=False)


def run_cycle(cfg, market, start_ms, end_ms, exchange=None, data_dir=None,
              out_dir="paper", persist_state=True, skip_gate=False, store_dir=None):
    """Một chu kỳ paper: [start_ms, end_ms] là cửa sổ execution; nến trước start_ms là warm-up."""
    src = "live" if exchange is not None else "local"
    log_line(out_dir, f"cycle {market}: {pd.to_datetime(start_ms, unit='ms', utc=True)} -> "
                      f"{pd.to_datetime(end_ms, unit='ms', utc=True)} (nguồn {src})")
    frames, raw = build_window_frames(cfg, market, start_ms, end_ms,
                                      exchange=exchange, data_dir=data_dir)
    # check_data mặc định áp chuẩn coverage của download (months_back) - với paper chỉ
    # cần cửa sổ warm-up + execution được phủ đủ
    warmup_days = cfg.get("paper", {}).get("warmup_days", 90)
    gate_cfg = dict(cfg)
    gate_cfg["data"] = dict(cfg["data"])
    gate_cfg["data"]["months_back"] = round(warmup_days / 30.44, 2)
    issues = gate_raw(raw, gate_cfg, out_dir)
    critical = [i for i in issues if i["severity"] == "CRITICAL"]
    if critical and not skip_gate:
        raise RuntimeError(f"data gate CRITICAL: {critical[0]['file']} - "
                           f"{critical[0]['message']} (dừng, không tự suy đoán giá)")
    # chỉ lưu nến live SAU khi qua data gate (dữ liệu lỗi không vào store)
    if exchange is not None and store_dir is not None:
        for sym, tfs in raw.items():
            for tf, df in tfs.items():
                save_candle_store(store_dir, sym, tf, df)
    kwargs = {}
    if market == "futures":
        kwargs["contract_meta"] = load_contract_meta(cfg)
        log_line(out_dir, "futures: funding chỉ dùng fallback rate (không có lịch sử "
                          "funding live qua public API)")
    trades, orders, equity, skipped, summary = run_backtest(
        frames, cfg, market_type=market,
        execution_start=pd.to_datetime(start_ms, unit="ms", utc=True),
        execution_end=pd.to_datetime(end_ms, unit="ms", utc=True), **kwargs)
    write_outputs(out_dir, trades, orders, equity, skipped)
    if persist_state:
        old = load_state(out_dir) or {}
        state = {
            "market": market,
            "start_ts": start_ms,
            "last_ts": end_ms,
            "config_hash": cfg_hash(cfg),
            "started_utc": old.get("started_utc") or datetime.now(timezone.utc).isoformat(),
            "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_cycles": int(old.get("n_cycles", 0)) + 1,
        }
        save_state(out_dir, state)
    return trades, orders, equity, skipped, summary


def compare_trades(a, b):
    """Trả về (số lệnh khớp, số lệnh phía a, danh sách khác biệt)."""
    cols = [c for c in TRADE_COLS if c in a.columns and c in b.columns]
    if len(a) == 0 and len(b) == 0:
        return 0, 0, []
    j = a[cols].merge(b[cols], on=["symbol", "entry_time"], suffixes=("_a", "_b"),
                      how="outer", indicator=True)
    match, diffs = 0, []

    def same_val(x, y):
        if pd.isna(x) and pd.isna(y):
            return True  # NaT == NaT là False trong pandas
        return x == y

    for _, r in j.iterrows():
        if r["_merge"] != "both":
            diffs.append((str(r.get("symbol")), str(r.get("entry_time")), "missing_one_side"))
            continue
        same = all(same_val(r[c + "_a"], r[c + "_b"])
                   for c in cols if c not in ("symbol", "entry_time"))
        if same:
            match += 1
        else:
            diffs.append((str(r["symbol"]), str(r["entry_time"]), "value_mismatch"))
    return match, len(a), diffs


def last_closed_bar_ms(now_ms):
    return (now_ms // H1_MS - 1) * H1_MS


def run_live_cycle(cfg, args, exchange):
    out_dir = args.out_dir or cfg["paper"].get("out_dir", "paper")
    now_ms = exchange.fetch_time()
    end_ms = last_closed_bar_ms(now_ms)
    state = load_state(out_dir)
    if state and state.get("last_ts", 0) >= end_ms:
        log_line(out_dir, f"chưa có nến mới (last_ts={state['last_ts']}, end={end_ms})")
        return None
    start_ms = state["start_ts"] if state else None
    if args.start:
        start_ms = int(pd.Timestamp(args.start, tz="UTC").value // 1_000_000)
    if not start_ms:
        start_ms = end_ms
        log_line(out_dir, f"bắt đầu paper từ nến {pd.to_datetime(start_ms, unit='ms', utc=True)}")
    elif state and end_ms - state["last_ts"] > H1_MS:
        log_line(out_dir, f"bù {(end_ms - state['last_ts']) // H1_MS} nến bị thiếu")
    if state and state.get("config_hash") != cfg_hash(cfg):
        log_line(out_dir, "CẢNH BÁO: config đã đổi so với lúc bắt đầu paper")
    log_line(out_dir, f"cycle {args.market}: {pd.to_datetime(start_ms, unit='ms', utc=True)} -> "
                      f"{pd.to_datetime(end_ms, unit='ms', utc=True)}")
    # store nến live chỉ cho spot (futures dùng cấu trúc thư mục khác, chưa hỗ trợ store)
    store = Path(out_dir) / "candles" if args.market == "spot" else None
    return run_cycle(cfg, args.market, start_ms, end_ms, exchange=exchange,
                     out_dir=out_dir, persist_state=True, store_dir=store)


def _acquire_or_exit(out_dir):
    try:
        return InstanceLock(out_dir).acquire()
    except RuntimeError as e:
        print(f"DỪNG: {e}")
        sys.exit(2)


def cmd_once(cfg, args):
    out_dir = args.out_dir or cfg["paper"].get("out_dir", "paper")
    lock = _acquire_or_exit(out_dir)
    try:
        exchange = make_exchange()
        run_live_cycle(cfg, args, exchange)
    finally:
        lock.release()


def cmd_backfill(cfg, args):
    """Tải nến live (kèm warm-up) vào store mà KHÔNG chạy engine và KHÔNG đổi state/trades.
    Dùng để lấp store trước chu kỳ đầu, hoặc sau khi store bị mất."""
    out_dir = args.out_dir or cfg["paper"].get("out_dir", "paper")
    lock = _acquire_or_exit(out_dir)
    try:
        exchange = make_exchange()
        now_ms = exchange.fetch_time()
        end_ms = last_closed_bar_ms(now_ms)
        state = load_state(out_dir)
        start_ms = state["start_ts"] if state else end_ms
        warmup_days = cfg.get("paper", {}).get("warmup_days", 90)
        since_ms = start_ms - int(warmup_days * 86_400_000)
        store = Path(out_dir) / "candles"
        for sym in symbols_for(cfg, "spot"):
            for tf in ("1h", "4h"):
                df = fetch_ohlcv_live(exchange, sym, tf, since_ms)
                save_candle_store(store, sym, tf, df)
                log_line(out_dir, f"backfill {sym} {tf}: {len(df)} nến "
                                  f"({pd.to_datetime(int(df['ts'].min()), unit='ms', utc=True)}"
                                  f" -> {pd.to_datetime(int(df['ts'].max()), unit='ms', utc=True)})")
    finally:
        lock.release()


def cmd_loop(cfg, args):
    out_dir = args.out_dir or cfg["paper"].get("out_dir", "paper")
    lock = _acquire_or_exit(out_dir)
    try:
        exchange = make_exchange()
        log_line(out_dir, f"loop mode: market={args.market} (delay "
                          f"{cfg['paper'].get('delay_seconds', 60)}s sau đóng nến, pid {os.getpid()})")
        while True:
            try:
                run_live_cycle(cfg, args, exchange)
            except KeyboardInterrupt:
                log_line(out_dir, "loop dừng bởi người dùng (Ctrl+C)")
                break
            except Exception as e:
                log_line(out_dir, f"ERROR: {e}")
            delay_s = cfg["paper"].get("delay_seconds", 60)
            now_ms = int(time.time() * 1000)
            next_ms = (now_ms // H1_MS + 1) * H1_MS + delay_s * 1000
            time.sleep(max(0.0, (next_ms - now_ms) / 1000.0))
    finally:
        lock.release()


def cmd_replay(cfg, args):
    """Offline: chạy paper engine trên dữ liệu lịch sử local và so sánh từng lệnh với backtest.
    Ghi vào paper/replay/ để KHÔNG đè lên outputs live."""
    out_dir = args.out_dir or str(Path(cfg["paper"].get("out_dir", "paper")) / "replay")
    start_ms = int(pd.Timestamp(args.start, tz="UTC").value // 1_000_000)
    end_ms = int(pd.Timestamp(args.end, tz="UTC").value // 1_000_000)
    data_dir = args.data_dir or cfg["data"]["dir"]
    trades, orders, equity, skipped, summary = run_cycle(
        cfg, args.market, start_ms, end_ms, data_dir=data_dir, out_dir=out_dir,
        persist_state=args.persist)
    bt, *_ = run_backtest(
        build_window_frames(cfg, args.market, start_ms, end_ms, data_dir=data_dir)[0],
        cfg, market_type=args.market,
        execution_start=pd.to_datetime(start_ms, unit="ms", utc=True),
        execution_end=pd.to_datetime(end_ms, unit="ms", utc=True),
        **( {"contract_meta": load_contract_meta(cfg)} if args.market == "futures" else {}))
    match, total, diffs = compare_trades(trades, bt)
    print(f"\nReplay {args.market} {args.start} -> {args.end}: "
          f"{match}/{total} lệnh khớp backtest")
    for d in diffs[:10]:
        print("  LỆCH:", d)
    log_line(out_dir, f"replay: {match}/{total} lệnh khớp backtest, {len(diffs)} lệch")
    if diffs:
        sys.exit(1)


def cmd_report(cfg, args):
    out_dir = args.out_dir or cfg["paper"].get("out_dir", "paper")
    p = Path(out_dir)
    state = load_state(out_dir)
    if not (p / "trades.csv").exists():
        print("chưa có dữ liệu paper (chạy --once hoặc replay trước)")
        return
    trades = pd.read_csv(p / "trades.csv", parse_dates=["entry_time", "exit_time"])
    orders = pd.read_csv(p / "orders.csv", parse_dates=["fill_time"])
    equity = pd.read_csv(p / "equity.csv", parse_dates=["timestamp"])
    m = compute_metrics(trades, orders, equity, cfg)
    # cửa sổ lấy từ equity.csv (viết cùng lúc với trades.csv -> luôn nhất quán),
    # state chỉ dùng khi có (market + clock live)
    w0, w1 = equity["timestamp"].iloc[0], equity["timestamp"].iloc[-1]
    if state is None:
        market = (str(trades["market_type"].iloc[0]) if len(trades) else "spot")
    else:
        market = state["market"]
        if int(w1.value // 1_000_000) != state.get("last_ts"):
            print(f"LƯU Ý: equity.csv kết thúc {w1} khác state.last_ts - "
                  f"thư mục đang chứa dữ liệu của một lần chạy khác (replay?)")
    print(f"\n=== Paper report ({market}, {w0} -> {w1}) ===")
    for k in ["n_trades", "win_rate_pct", "profit_factor", "expectancy_usdt",
              "net_return_pct", "net_return_incl_open_pct", "max_drawdown_pct",
              "total_costs", "total_funding"]:
        v = m[k]
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    print(f"  n_open_at_end: {int(trades['open_at_end'].sum()) if len(trades) else 0}")
    # so sánh với backtest cùng cửa sổ (dùng dữ liệu local)
    start_ms = int(w0.value // 1_000_000)
    last_ms = int(w1.value // 1_000_000)
    try:
        local_max = int(load_ohlcv(cfg["data"]["symbols"][0], "1h",
                                   cfg["data"]["dir"])["ts"].max())
    except Exception:
        local_max = 0
    if last_ms <= local_max:
        print(f"\nSo sánh backtest CÙNG cửa sổ ({w0.date()} -> {w1.date()}):")
        bt, bt_orders, bt_equity, *_ = run_backtest(
            build_window_frames(cfg, market, start_ms, last_ms,
                                data_dir=cfg["data"]["dir"])[0],
            cfg, market_type=market,
            execution_start=pd.to_datetime(start_ms, unit="ms", utc=True),
            execution_end=pd.to_datetime(last_ms, unit="ms", utc=True),
            **( {"contract_meta": load_contract_meta(cfg)} if market == "futures" else {}))
        # dùng equity/orders CỦA BACKTEST (không phải của paper) cho các chỉ số backtest
        bm = compute_metrics(bt, bt_orders, bt_equity, cfg)
        print(f"  trades: paper={m['n_trades']} vs backtest={bm['n_trades']}")
        print(f"  PF: {m['profit_factor']:.3f} vs {bm['profit_factor']:.3f}")
        print(f"  expectancy: {m['expectancy_usdt']:.4f} vs {bm['expectancy_usdt']:.4f}")
        print(f"  maxDD%: {m['max_drawdown_pct']:.2f} vs {bm['max_drawdown_pct']:.2f}")
    else:
        print("\n(không so sánh được: cửa sổ paper ngoài phạm vi dữ liệu backtest local)")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="paper.py",
                                     description="Paper trading GIẢ LẬP - public data only")
    parser.add_argument("--market", choices=["spot", "futures"], default="spot")
    parser.add_argument("--config", default="paper/paper_config.yaml",
                        help="file override (merge lên config.yaml)")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (UTC) bắt đầu paper")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--once", action="store_true", help="chạy đúng 1 chu kỳ")
    parser.add_argument("--loop", action="store_true", help="chạy lặp mỗi giờ")
    sub = parser.add_subparsers(dest="cmd")
    rp = sub.add_parser("replay", help="offline: chạy trên dữ liệu local + so sánh backtest")
    rp.add_argument("--market", choices=["spot", "futures"], default="spot")
    rp.add_argument("--config", default=None)
    rp.add_argument("--out-dir", default=None)
    rp.add_argument("--start", required=True)
    rp.add_argument("--end", required=True)
    rp.add_argument("--persist", action="store_true", help="ghi state.json")
    rp.add_argument("--data-dir", default=None,
                    help="thư mục OHLCV (mặc định data/; dùng paper/candles để replay live store)")
    sub.add_parser("backfill", help="tải nến live vào paper/candles (không đổi state/trades)")
    rp2 = sub.add_parser("report", help="in chỉ số paper + so sánh backtest cùng cửa sổ")
    rp2.add_argument("--config", default=None)
    rp2.add_argument("--out-dir", default=None)
    args = parser.parse_args()
    cfg = load_paper_cfg(args.config or "paper/paper_config.yaml")
    if args.cmd == "replay":
        cmd_replay(cfg, args)
    elif args.cmd == "report":
        cmd_report(cfg, args)
    elif args.cmd == "backfill":
        cmd_backfill(cfg, args)
    elif args.once:
        cmd_once(cfg, args)
    elif args.loop:
        cmd_loop(cfg, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
