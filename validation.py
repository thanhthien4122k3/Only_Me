"""Validation: walk-forward, hold-out, Gate 1 checklist, small parameter sweeps."""
from copy import deepcopy
from itertools import product

import numpy as np
import pandas as pd

from engine import run_backtest
from metrics import trade_metrics


def _timeline(frames_by_symbol):
    return (pd.concat([df["timestamp"] for df in frames_by_symbol.values()])
            .drop_duplicates().sort_values().reset_index(drop=True))


def _slice(frames_by_symbol, pre, end):
    return {sym: df[(df["timestamp"] >= pre) & (df["timestamp"] <= end)].reset_index(drop=True)
            for sym, df in frames_by_symbol.items()}


def window_metrics(trades, cfg):
    """PF / expectancy / net return over a window, computed from its trades only."""
    m = trade_metrics(trades)
    net = float(trades["net_pnl"].sum()) if len(trades) else 0.0
    m["net_pnl"] = net
    m["net_return_pct"] = net / cfg["backtest"]["initial_capital"] * 100
    return m


def walk_forward(frames_by_symbol, cfg, market_type="spot", window_months=3,
                 warmup_months=2, **engine_kwargs):
    """Fixed-parameter rolling 3-month test windows. Each window is an independent run
    with fresh capital; features are preloaded warmup_months back so indicators are warm
    at the window start. Only trades with entry_time >= window start are counted."""
    ts = _timeline(frames_by_symbol)
    start, end = ts.iloc[0], ts.iloc[-1]
    w = pd.Timedelta(days=int(window_months * 30.44))
    warm = pd.Timedelta(days=int(warmup_months * 30.44))
    rows = []
    ws = start
    while ws < end:
        we = min(ws + w, end)
        trades, _, _, _, _ = run_backtest(_slice(frames_by_symbol, ws - warm, we), cfg,
                                          market_type=market_type, **engine_kwargs)
        w_trades = trades[trades["entry_time"] >= ws] if len(trades) else trades
        m = window_metrics(w_trades, cfg)
        rows.append({"window_start": ws, "window_end": we,
                     "n_trades": m["n_trades"],
                     "profit_factor": m["profit_factor"],
                     "win_rate_pct": round(m["win_rate_pct"], 2),
                     "expectancy_usdt": round(m["expectancy_usdt"], 4),
                     "net_pnl": round(m["net_pnl"], 2),
                     "net_return_pct": round(m["net_return_pct"], 2)})
        ws = we
    return pd.DataFrame(rows)


def hold_out(frames_by_symbol, cfg, market_type="spot", pct=0.20, warmup_months=2,
             **engine_kwargs):
    """Evaluate the last pct of the timeline ONCE. Returns (metrics, split_ts, summary)."""
    ts = _timeline(frames_by_symbol)
    split = ts.iloc[int(len(ts) * (1 - pct))]
    warm = pd.Timedelta(days=int(warmup_months * 30.44))
    trades, orders, equity, skipped, summary = run_backtest(
        _slice(frames_by_symbol, split - warm, ts.iloc[-1]), cfg,
        market_type=market_type, **engine_kwargs)
    h_trades = trades[trades["entry_time"] >= split] if len(trades) else trades
    return window_metrics(h_trades, cfg), split, summary


def _pf_str(pf):
    if np.isinf(pf):
        return "inf (no losing trades)"
    return f"{pf:.3f}"


def gate1_checklist(full_metrics, holdout_metrics, wf_df):
    """Original Stage-3 Gate 1 criteria. Returns (checks DataFrame, warnings list)."""
    checks = []

    def chk(name, passed, detail):
        checks.append({"check": name, "result": "PASS" if passed else "FAIL",
                       "detail": detail})

    pf_ho = holdout_metrics["profit_factor"]
    if holdout_metrics["n_trades"] == 0:
        chk("Hold-out profit factor > 1.2 sau chi phí", False, "không có lệnh trong hold-out")
    else:
        chk("Hold-out profit factor > 1.2 sau chi phí",
            bool(np.isinf(pf_ho) or pf_ho > 1.2), f"PF={_pf_str(pf_ho)}")
    chk("Expectancy > 0 (toàn kỳ)", full_metrics["expectancy_usdt"] > 0,
        f"expectancy={full_metrics['expectancy_usdt']:.4f} USDT/lệnh")
    chk("Có ít nhất 100 lệnh toàn kỳ", full_metrics["n_trades"] >= 100,
        f"số lệnh={full_metrics['n_trades']}")
    chk("Drawdown tối đa <= 5% vốn", full_metrics["max_drawdown_pct"] <= 5.0,
        f"maxDD={full_metrics['max_drawdown_pct']:.2f}%")
    wf_traded = wf_df[wf_df["n_trades"] > 0]
    if len(wf_traded):
        ratio = (wf_traded["profit_factor"] > 1).mean()
        chk("PF > 1 trong >= 60% cửa sổ walk-forward", ratio >= 0.6,
            f"{ratio * 100:.0f}% ({int((wf_traded['profit_factor'] > 1).sum())}/"
            f"{len(wf_traded)} cửa sổ có lệnh; "
            f"{len(wf_df) - len(wf_traded)} cửa sổ không có lệnh bị loại)")
    else:
        chk("PF > 1 trong >= 60% cửa sổ walk-forward", False,
            "không có lệnh trong bất kỳ cửa sổ walk-forward nào")
    warnings = []
    for name, pf in [("toàn kỳ", full_metrics["profit_factor"]),
                     ("hold-out", holdout_metrics["profit_factor"])]:
        if pf and np.isfinite(pf) and pf > 2.5:
            warnings.append(f"CẢNH BÁO: profit factor {name} = {pf:.2f} > 2.5 - "
                            f"nhiều khả năng có bug hoặc look-ahead!")
    return pd.DataFrame(checks), warnings


def param_sweep(frames_by_symbol, cfg, param_lists, market_type="spot", **engine_kwargs):
    """Tiny sweeps only: param_lists = [(dotted_path, [v1, v2, ...])], max 2 params x 3 values.
    ALL results are returned - nothing is selected automatically."""
    rows = []

    def run_one(label, c):
        trades, _, _, _, _ = run_backtest(frames_by_symbol, c, market_type=market_type,
                                          **engine_kwargs)
        m = trade_metrics(trades)
        net = float(trades["net_pnl"].sum()) if len(trades) else 0.0
        pf = m["profit_factor"]
        rows.append({"config": label, "n_trades": m["n_trades"],
                     "win_rate_pct": round(m["win_rate_pct"], 2),
                     "profit_factor": round(pf, 3) if np.isfinite(pf) else "inf",
                     "expectancy_usdt": round(m["expectancy_usdt"], 4),
                     "net_pnl": round(net, 2)})

    run_one("baseline", cfg)
    paths = [p for p, _ in param_lists]
    values = [v for _, v in param_lists]
    for combo in product(*values):
        c = deepcopy(cfg)
        label = []
        for path, val in zip(paths, combo):
            sec, key = path.split(".")
            c[sec][key] = val
            label.append(f"{path}={val}")
        run_one(", ".join(label), c)
    return pd.DataFrame(rows)
