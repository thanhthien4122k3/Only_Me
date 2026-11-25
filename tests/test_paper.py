"""Offline tests for paper.py: replay-vs-backtest match, idempotency, gap handling.
Không gọi mạng - dùng dữ liệu parquet tổng hợp trong tmp_path."""
import numpy as np
import pandas as pd
import pytest

from data import candles_to_df
from engine import run_backtest
from paper import (build_window_frames, compare_trades, load_paper_cfg,
                   run_cycle)

BASE = 1_700_000_000_000
H1 = 3_600_000


def make_data_dir(tmp_path, n1=1600, n4=400):
    for sym, seed in [("BTC/USDT", 11), ("ETH/USDT", 12)]:
        rng = np.random.default_rng(seed)
        # drift đủ mạnh so với noise để breakout xảy ra
        c = 1000 + np.cumsum(rng.normal(0.6, 2.0, n1))
        prev = np.concatenate([[1000.0], c[:-1]])
        # pad cực nhỏ: high/low sát max/min(open,close) để breakout khả thi
        h = np.maximum(c, prev) * 1.0002 + 0.1
        l = np.minimum(c, prev) * 0.9998 - 0.1
        # nến thật luôn có open trong [low, high]
        o = np.clip(prev, l, h)
        v = rng.uniform(800, 1200, n1)
        v[::23] *= 3
        rows = [[BASE + i * H1, oi, hi, li, ci, vi]
                for i, (oi, hi, li, ci, vi) in enumerate(zip(o, h, l, c, v))]
        candles_to_df(rows, "1h", BASE + (n1 + 10) * H1).to_parquet(
            tmp_path / f"{sym.replace('/', '-')}_1h.parquet", index=False)
        rng4 = np.random.default_rng(seed + 100)
        c4 = 1000 + np.cumsum(rng4.normal(2.4, 4.0, n4))
        prev4 = np.concatenate([[1000.0], c4[:-1]])
        h4 = np.maximum(c4, prev4) * 1.0002 + 0.1
        l4 = np.minimum(c4, prev4) * 0.9998 - 0.1
        o4 = np.clip(prev4, l4, h4)
        v4 = rng4.uniform(800, 1200, n4)
        rows4 = [[BASE + i * 4 * H1, oi, hi, li, ci, vi]
                 for i, (oi, hi, li, ci, vi) in enumerate(zip(o4, h4, l4, c4, v4))]
        candles_to_df(rows4, "4h", BASE + (n4 + 10) * 4 * H1).to_parquet(
            tmp_path / f"{sym.replace('/', '-')}_4h.parquet", index=False)
    return tmp_path


def make_cfg():
    cfg = load_paper_cfg()
    cfg["paper"]["warmup_days"] = 45
    cfg["data"]["months_back"] = 0  # dữ liệu tổng hợp ngắn: bỏ kiểm tra coverage
    cfg["data"]["symbols"] = ["BTC/USDT", "ETH/USDT"]  # test data chỉ có 2 cặp
    # dữ liệu tổng hợp ATR nhỏ -> skip-rule loại mọi tín hiệu; tắt trong test
    cfg["backtest"]["skip_cost_ratio"] = 0.0
    return cfg


def test_replay_matches_backtest(tmp_path):
    data_dir = make_data_dir(tmp_path)
    out_dir = tmp_path / "paper_out"
    cfg = make_cfg()
    start_ms = BASE + 1200 * H1
    end_ms = BASE + 1500 * H1
    trades, orders, equity, skipped, summary = run_cycle(
        cfg, "spot", start_ms, end_ms, data_dir=str(data_dir),
        out_dir=str(out_dir), persist_state=False)
    assert len(trades) > 0  # dữ liệu tổng hợp phải sinh được lệnh
    frames, _ = build_window_frames(cfg, "spot", start_ms, end_ms,
                                    data_dir=str(data_dir))
    bt, *_ = run_backtest(frames, cfg, market_type="spot",
                          execution_start=pd.to_datetime(start_ms, unit="ms", utc=True),
                          execution_end=pd.to_datetime(end_ms, unit="ms", utc=True))
    match, total, diffs = compare_trades(trades, bt)
    assert total == len(bt)
    assert match == total
    assert diffs == []
    for f in ["trades.csv", "signals.csv", "equity.csv", "orders.csv", "run.log"]:
        assert (out_dir / f).exists()


def test_idempotent_rerun_no_new_trades(tmp_path):
    data_dir = make_data_dir(tmp_path)
    out_dir = tmp_path / "paper_out"
    cfg = make_cfg()
    start_ms = BASE + 1200 * H1
    end_ms = BASE + 1500 * H1
    run_cycle(cfg, "spot", start_ms, end_ms, data_dir=str(data_dir),
              out_dir=str(out_dir), persist_state=True)
    t1 = (out_dir / "trades.csv").read_text()
    run_cycle(cfg, "spot", start_ms, end_ms, data_dir=str(data_dir),
              out_dir=str(out_dir), persist_state=True)
    t2 = (out_dir / "trades.csv").read_text()
    assert t1 == t2  # lần chạy thứ hai không tạo lệnh trùng


def test_gap_logged_and_idempotent(tmp_path):
    data_dir = make_data_dir(tmp_path)
    p = data_dir / "BTC-USDT_1h.parquet"
    df = pd.read_parquet(p)
    df = df.drop(df.index[800:803]).reset_index(drop=True)  # thiếu 3 nến liên tiếp
    df.to_parquet(p, index=False)
    out_dir = tmp_path / "paper_out"
    cfg = make_cfg()
    start_ms = BASE + 1200 * H1
    end_ms = BASE + 1500 * H1
    run_cycle(cfg, "spot", start_ms, end_ms, data_dir=str(data_dir),
              out_dir=str(out_dir), persist_state=True)
    log = (out_dir / "run.log").read_text(encoding="utf-8")
    assert "small_gap" in log
    t1 = (out_dir / "trades.csv").read_text()
    run_cycle(cfg, "spot", start_ms, end_ms, data_dir=str(data_dir),
              out_dir=str(out_dir), persist_state=True)
    assert (out_dir / "trades.csv").read_text() == t1


def test_critical_gap_aborts(tmp_path):
    data_dir = make_data_dir(tmp_path)
    p = data_dir / "BTC-USDT_1h.parquet"
    df = pd.read_parquet(p)
    df = df.drop(df.index[600:700]).reset_index(drop=True)  # gap 100 nến -> CRITICAL
    df.to_parquet(p, index=False)
    out_dir = tmp_path / "paper_out"
    cfg = make_cfg()
    with pytest.raises(RuntimeError):
        run_cycle(cfg, "spot", BASE + 1200 * H1, BASE + 1500 * H1,
                  data_dir=str(data_dir), out_dir=str(out_dir), persist_state=False)
    assert "CRITICAL" in (out_dir / "run.log").read_text(encoding="utf-8")
