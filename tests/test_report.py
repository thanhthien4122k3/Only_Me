import json
import re

import pandas as pd

from report import (compare_markets, make_run_id, sha256_file,
                    write_run_metadata)


def test_make_run_id_format():
    rid = make_run_id()
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", rid)


def test_make_run_id_unique():
    assert make_run_id() != make_run_id()


def test_sha256_deterministic_and_content_sensitive(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("same content")
    b.write_text("different content")
    assert sha256_file(a) == sha256_file(a)
    assert sha256_file(a) != sha256_file(b)


def test_write_run_metadata(tmp_path):
    cfg = {"backtest": {"initial_capital": 1000.0, "fee_pct": 0.1, "slippage_pct": 0.05}}
    summary = {"n_trades": 5, "final_equity": 1234.5}
    metrics = {"profit_factor": 1.5, "n_trades": 5}
    src = tmp_path / "data.parquet"
    src.write_bytes(b"x" * 10)
    out = write_run_metadata(tmp_path, cfg, summary, metrics, [src], "backtest --market spot")
    assert out.exists()
    meta = json.loads(out.read_text(encoding="utf-8"))
    assert meta["config"]["backtest"]["fee_pct"] == 0.1
    assert meta["metrics"]["n_trades"] == 5
    assert meta["data_fingerprints"][0]["sha256"] == sha256_file(src)
    assert meta["assumptions"]


def test_compare_markets_table():
    m_spot = {"net_return_pct": 5.9, "final_equity": 1059.0, "n_trades": 58,
              "win_rate_pct": 29.3, "profit_factor": 1.15, "expectancy_usdt": 1.03,
              "max_drawdown_pct": 13.9, "total_costs": 49.4, "total_funding": 0.0}
    m_fut = {"net_return_pct": 2.3, "final_equity": 1022.9, "n_trades": 45,
             "win_rate_pct": 33.3, "profit_factor": 1.09, "expectancy_usdt": 0.51,
             "max_drawdown_pct": 14.6, "total_costs": 30.0, "total_funding": 0.78}
    t = compare_markets(m_spot, m_fut)
    assert list(t["thị trường"]) == ["spot", "futures"]
    assert "profit factor" in t.columns
    assert t[t["thị trường"] == "spot"]["số lệnh"].iloc[0] == 58
