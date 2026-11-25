"""Tests for paper.py operational safety: instance lock and live candle store. Offline."""
import numpy as np
import pandas as pd
import pytest

from data import candles_to_df
from paper import InstanceLock, save_candle_store

BASE = 1_700_000_000_000
H1 = 3_600_000


def _candles(n, start_idx=0):
    rng = np.random.default_rng(5)
    c = 1000 + np.cumsum(rng.normal(0.1, 1.0, n))
    rows = [[BASE + (start_idx + i) * H1, c[i] - 0.2, c[i] + 1, c[i] - 1, c[i], 100.0]
            for i in range(n)]
    return candles_to_df(rows, "1h", BASE + (start_idx + n + 10) * H1)


def test_instance_lock_blocks_second_holder(tmp_path):
    first = InstanceLock(tmp_path).acquire()
    try:
        with pytest.raises(RuntimeError):
            InstanceLock(tmp_path).acquire()
    finally:
        first.release()
    # sau khi nhả khóa, tiến trình khác lấy được
    second = InstanceLock(tmp_path).acquire()
    second.release()


def test_instance_lock_context_manager_releases(tmp_path):
    with InstanceLock(tmp_path):
        pass
    with InstanceLock(tmp_path):
        pass  # không ném lỗi: khóa đã được nhả


def test_pid_file_written(tmp_path):
    import os
    with InstanceLock(tmp_path):
        assert (tmp_path / "paper.pid").read_text(encoding="utf-8") == str(os.getpid())


def test_candle_store_merges_and_dedups(tmp_path):
    p1 = save_candle_store(tmp_path, "BTC/USDT", "1h", _candles(100))
    # lần sau chồng lấp 50 nến và thêm 30 nến mới
    p2 = save_candle_store(tmp_path, "BTC/USDT", "1h", _candles(80, start_idx=50))
    assert p1 == p2
    df = pd.read_parquet(p2)
    assert df["ts"].is_unique
    assert len(df) == 130
    assert list(df.columns) == ["timestamp", "ts", "open", "high", "low", "close", "volume"]
    assert df["ts"].is_monotonic_increasing


def test_candle_store_filename_matches_data_convention(tmp_path):
    p = save_candle_store(tmp_path, "ETH/USDT", "4h", _candles(5))
    assert p.name == "ETH-USDT_4h.parquet"  # cùng quy ước với data.file_path (spot)
