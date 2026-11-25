"""Performance metrics with documented formulas. All results are net of modelled costs."""
import numpy as np
import pandas as pd

BARS_PER_YEAR_1H = 365 * 24  # hourly bars


def max_drawdown(equity):
    """Max peak-to-trough decline. Returns (drawdown_usdt <= 0, drawdown_pct <= 0)."""
    peak = equity.cummax()
    dd = equity - peak
    i = dd.idxmin()
    pct = dd[i] / peak[i] * 100 if len(equity) else 0.0
    return float(dd[i]), float(pct)


def sharpe_ratio(equity, bars_per_year=BARS_PER_YEAR_1H):
    """Mean bar return / std of bar returns, annualized by sqrt(bars_per_year)."""
    r = equity.pct_change().dropna()
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * np.sqrt(bars_per_year))


def sortino_ratio(equity, bars_per_year=BARS_PER_YEAR_1H):
    """Like Sharpe but std only over negative bar returns."""
    r = equity.pct_change().dropna()
    downside = r[r < 0]
    if len(downside) < 2 or downside.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / downside.std(ddof=1) * np.sqrt(bars_per_year))


def bootstrap_expectancy_ci(net_pnl, n_resamples=1000, alpha=0.10, seed=42):
    """90% bootstrap CI of mean net PnL (USDT per trade)."""
    if len(net_pnl) == 0:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    vals = net_pnl.to_numpy()
    means = rng.choice(vals, size=(n_resamples, len(vals)), replace=True).mean(axis=1)
    return (float(np.percentile(means, 100 * alpha / 2)),
            float(np.percentile(means, 100 * (1 - alpha / 2))))


def trade_metrics(trades):
    n = len(trades)
    zero = {"n_trades": 0, "n_wins": 0, "n_losses": 0, "win_rate_pct": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "largest_win": 0.0, "largest_loss": 0.0,
            "profit_factor": 0.0, "expectancy_usdt": 0.0, "expectancy_r": 0.0,
            "avg_holding_hours": 0.0, "total_fees": 0.0}
    if n == 0:
        return zero
    wins = trades[trades["net_pnl"] > 0]
    losses = trades[trades["net_pnl"] <= 0]
    gross_profit = wins["net_pnl"].sum()
    gross_loss = -losses["net_pnl"].sum()
    # Profit factor = gross profit / gross loss (net of fees); inf if no losing trades
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    # R multiple = net_pnl / planned risk, where planned risk = qty * stop distance
    risk = trades["quantity"] * (trades["entry_price"] - trades["initial_stop_price"])
    r_mult = (trades["net_pnl"] / risk.replace(0, np.nan)).dropna()
    holding = (trades["exit_time"] - trades["entry_time"]).dt.total_seconds() / 3600.0
    return {
        "n_trades": n,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate_pct": len(wins) / n * 100,
        "avg_win": float(wins["net_pnl"].mean()),
        "avg_loss": float(losses["net_pnl"].mean()),
        "largest_win": float(wins["net_pnl"].max()),
        "largest_loss": float(losses["net_pnl"].min()),
        "profit_factor": float(pf),
        "expectancy_usdt": float(trades["net_pnl"].mean()),
        "expectancy_r": float(r_mult.mean()) if len(r_mult) else 0.0,
        "avg_holding_hours": float(holding.mean()),
        "total_fees": float(trades["entry_fee"].sum() + trades["exit_fee"].sum()),
    }


def by_symbol(trades):
    if len(trades) == 0:
        return pd.DataFrame(columns=["symbol", "n_trades", "net_pnl", "win_rate_pct"])
    g = trades.groupby("symbol").agg(
        n_trades=("net_pnl", "size"),
        net_pnl=("net_pnl", "sum"),
        win_rate_pct=("net_pnl", lambda s: (s > 0).mean() * 100),
    ).reset_index()
    return g


def by_quarter(trades):
    if len(trades) == 0:
        return pd.DataFrame(columns=["quarter", "n_trades", "net_pnl", "win_rate_pct"])
    q = trades["exit_time"].dt.tz_localize(None).dt.to_period("Q")
    g = trades.assign(quarter=q).groupby("quarter").agg(
        n_trades=("net_pnl", "size"),
        net_pnl=("net_pnl", "sum"),
        win_rate_pct=("net_pnl", lambda s: (s > 0).mean() * 100),
    ).reset_index()
    return g


def compute_costs(trades, orders):
    """Fees (entry+exit) plus slippage cost, where slippage = qty * |ref - fill| per order."""
    fees = float(trades["entry_fee"].sum() + trades["exit_fee"].sum()) if len(trades) else 0.0
    slip = 0.0
    if len(orders):
        filled = orders[orders["fill_price"].notna()]
        slip = float((filled["qty"] * (filled["ref_price"] - filled["fill_price"]).abs()).sum())
    return fees, slip


def compute_metrics(trades, orders, equity, cfg):
    bt = cfg["backtest"]
    initial = bt["initial_capital"]
    final = float(equity["equity"].iloc[-1]) if len(equity) else initial
    m = {"initial_capital": initial,
         "final_equity": final,
         "net_profit": final - initial,
         "net_return_pct": (final / initial - 1) * 100}
    if len(equity):
        mdd, mdd_pct = max_drawdown(equity["equity"])
        m["max_drawdown_usdt"] = -mdd
        m["max_drawdown_pct"] = -mdd_pct
        m["sharpe_ratio"] = sharpe_ratio(equity["equity"])
        m["sortino_ratio"] = sortino_ratio(equity["equity"])
        m["exposure_time_pct"] = (equity["exposure_pct"] > 0).mean() * 100
        m["avg_exposure_pct"] = equity["exposure_pct"].mean()
    else:
        m.update({"max_drawdown_usdt": 0.0, "max_drawdown_pct": 0.0,
                  "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
                  "exposure_time_pct": 0.0, "avg_exposure_pct": 0.0})
    fees, slip = compute_costs(trades, orders)
    m["total_fees"] = fees
    m["total_slippage"] = slip
    m["total_costs"] = fees + slip
    m["total_funding"] = (float(trades["funding_fee"].sum())
                          if len(trades) and "funding_fee" in trades.columns else 0.0)
    # gross profit = realized pnl (after slippage, before fees) of winning trades
    gross = float(trades[trades["realized_pnl"] > 0]["realized_pnl"].sum()) if len(trades) else 0.0
    m["costs_share_of_gross_profit_pct"] = (fees + slip) / gross * 100 if gross > 0 else 0.0
    m.update(trade_metrics(trades))
    if len(trades):
        lo, hi = bootstrap_expectancy_ci(trades["net_pnl"])
        losses = trades[trades["net_pnl"] <= 0]
        if len(losses):
            # largest single loss relative to initial capital
            m["max_single_loss_pct_of_equity"] = float(losses["net_pnl"].min()) / initial * 100
            # mean of -net_pnl / planned_risk over losing trades
            planned = losses["quantity"] * (losses["entry_price"] - losses["initial_stop_price"])
            ratio = (-losses["net_pnl"] / planned.replace(0, np.nan)).dropna()
            m["realized_vs_planned_risk"] = float(ratio.mean()) if len(ratio) else 0.0
        else:
            m["max_single_loss_pct_of_equity"] = 0.0
            m["realized_vs_planned_risk"] = 0.0
    else:
        lo, hi = 0.0, 0.0
        m["max_single_loss_pct_of_equity"] = 0.0
        m["realized_vs_planned_risk"] = 0.0
    m["expectancy_ci_90_lo"], m["expectancy_ci_90_hi"] = lo, hi
    return m


def compute_benchmarks(features_by_symbol, cfg):
    """Buy & hold per coin and equal-weight, net of one round trip of fee+slippage."""
    fee = cfg["backtest"]["fee_pct"] / 100.0
    slip = cfg["backtest"]["slippage_pct"] / 100.0
    rows = []
    for sym, df in features_by_symbol.items():
        first, last = df["close"].iloc[0], df["close"].iloc[-1]
        ret = (last * (1 - fee - slip)) / (first * (1 + fee + slip)) - 1
        rows.append({"symbol": sym, "buy_hold_ret_pct": ret * 100})
    b = pd.DataFrame(rows)
    eq = pd.DataFrame([{"symbol": "EQUAL_WEIGHT",
                        "buy_hold_ret_pct": b["buy_hold_ret_pct"].mean()}])
    return pd.concat([b, eq], ignore_index=True)
