"""Validation: walk-forward, hold-out, Gate 1 checklist, small parameter sweeps."""
from copy import deepcopy
from itertools import product

import numpy as np
import pandas as pd

from engine import run_backtest
from metrics import compute_metrics, trade_metrics


def _timeline(frames_by_symbol):
    return (pd.concat([df["timestamp"] for df in frames_by_symbol.values()])
            .drop_duplicates().sort_values().reset_index(drop=True))


def window_metrics(trades, cfg):
    """PF / expectancy / net return over a window, computed from its trades only."""
    m = trade_metrics(trades)
    net = float(trades["net_pnl"].sum()) if len(trades) else 0.0
    m["net_pnl"] = net
    m["net_return_pct"] = net / cfg["backtest"]["initial_capital"] * 100
    return m


def walk_forward(frames_by_symbol, cfg, market_type="spot", window_months=3,
                 **engine_kwargs):
    """ONE continuous run over the full data (fixed params, nothing refit), then trades
    are assigned to 3-month windows by entry_time. Because parameters are never fitted,
    every window is out-of-sample. Returns (windows_df, pooled_metrics) where pooled
    metrics include the bootstrap 90% CI of expectancy."""
    ts = _timeline(frames_by_symbol)
    start, end = ts.iloc[0], ts.iloc[-1]
    trades, orders, equity, skipped, summary = run_backtest(frames_by_symbol, cfg,
                                                            market_type=market_type,
                                                            **engine_kwargs)
    w = pd.Timedelta(days=int(window_months * 30.44))
    rows = []
    ws = start
    while ws < end:
        we = min(ws + w, end)
        last_window = we == end
        if len(trades):
            mask = (trades["entry_time"] >= ws) & ((trades["entry_time"] <= we)
                                                   if last_window
                                                   else (trades["entry_time"] < we))
            w_trades = trades[mask]
        else:
            w_trades = trades
        m = window_metrics(w_trades, cfg)
        rows.append({"window_start": ws, "window_end": we,
                     "n_trades": m["n_trades"],
                     "profit_factor": m["profit_factor"],
                     "win_rate_pct": round(m["win_rate_pct"], 2),
                     "expectancy_usdt": round(m["expectancy_usdt"], 4),
                     "net_pnl": round(m["net_pnl"], 2),
                     "net_return_pct": round(m["net_return_pct"], 2)})
        ws = we
    windows = pd.DataFrame(rows)
    pooled = compute_metrics(trades, orders, equity, cfg)
    return windows, pooled


def hold_out(frames_by_symbol, cfg, market_type="spot", pct=0.20, **engine_kwargs):
    """Evaluate the last pct of the timeline ONCE: capital starts fresh at the split and
    only bars before the split serve as warm-up. Positions still open at the end are
    marked to market and reported open (open_at_end flag); metrics exclude them but
    report their count. Returns (metrics, split_ts, summary)."""
    ts = _timeline(frames_by_symbol)
    split = ts.iloc[int(len(ts) * (1 - pct))]
    trades, orders, equity, skipped, summary = run_backtest(
        frames_by_symbol, cfg, market_type=market_type,
        execution_start=split, **engine_kwargs)
    closed = trades[~trades["open_at_end"]] if len(trades) else trades
    m = window_metrics(closed, cfg)
    m["n_open_at_end"] = int(trades["open_at_end"].sum()) if len(trades) else 0
    return m, split, summary


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


def compare_exits(frames_by_symbol, cfg, market_type="spot", **engine_kwargs):
    """4 fixed exit configs on the same data. ALL results returned - nothing selected."""
    base = deepcopy(cfg)
    base["stops"].update({"ema_exit_enabled": True, "initial_stop_enabled": False,
                          "take_profit_r": None, "trailing_enabled": False,
                          "trailing_atr_mult": 2.5, "time_stop_bars": None})
    b = deepcopy(base)
    b["stops"]["initial_stop_enabled"] = True
    c = deepcopy(b)
    c["stops"]["trailing_enabled"] = True
    d = deepcopy(b)
    d["stops"]["take_profit_r"] = 2.0
    d["stops"]["time_stop_bars"] = 24
    variants = [("A: ema_exit", base), ("B: ema + ATR stop", b),
                ("C: B + trailing 2.5", c), ("D: B + TP 2R + time 24", d)]
    rows = []
    for label, c_ in variants:
        trades, orders, equity, skipped, summary = run_backtest(
            frames_by_symbol, c_, market_type=market_type, **engine_kwargs)
        m = compute_metrics(trades, orders, equity, c_)
        pf = m["profit_factor"]
        rows.append({
            "config": label,
            "trades": m["n_trades"],
            "win rate": round(m["win_rate_pct"], 2),
            "PF": round(pf, 3) if np.isfinite(pf) else "inf",
            "expectancy USDT": round(m["expectancy_usdt"], 4),
            "net return %": round(m["net_return_pct"], 2),
            "max drawdown %": round(m["max_drawdown_pct"], 2),
            "max_single_loss_pct_of_equity": round(m["max_single_loss_pct_of_equity"], 2),
            "realized_vs_planned_risk": round(m["realized_vs_planned_risk"], 3),
            "total costs": round(m["total_costs"], 2),
        })
    return pd.DataFrame(rows)


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
