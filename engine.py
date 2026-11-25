"""Shared backtest engine: order/position lifecycle, fills, equity. Market rules come from the simulator modules."""
from dataclasses import dataclass, fields
from typing import Optional

import numpy as np
import pandas as pd

from markets import futures as futures_market
from markets import spot as spot_market

REQUIRED_COLS = ["timestamp", "open", "high", "low", "close", "atr", "exit_trend", "signal", "adx"]


@dataclass
class Order:
    """Order lifecycle record. v1: market orders with full fill (limit/partial fill later)."""
    symbol: str
    side: str                    # "buy" | "sell"
    qty: float
    ref_price: float             # reference price before slippage
    status: str = "PENDING"      # PENDING | PARTIALLY_FILLED | FILLED | CANCELED | REJECTED
    fill_price: Optional[float] = None
    fill_time: Optional[pd.Timestamp] = None
    reason: str = ""             # fill / rejection reason
    fee: float = 0.0
    order_id: str = ""


@dataclass
class Position:
    """One position lifecycle. Futures-only fields default to 0 for spot."""
    symbol: str
    market_type: str = "spot"
    direction: str = "long"
    entry_time: Optional[pd.Timestamp] = None
    entry_price: float = 0.0
    quantity: float = 0.0
    notional_value: float = 0.0
    initial_stop_price: float = 0.0
    current_stop_price: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    funding_fee: float = 0.0
    realized_pnl: float = 0.0
    net_pnl: float = 0.0
    status: str = "open"         # "open" | "closed"
    highest_close: float = 0.0
    bars_held: int = 0
    exit_reason: str = ""
    pending_exit: str = ""       # close-based exit decided; fills at next bar open
    open_at_end: bool = False    # True when execution_end set and position still open
    position_id: str = ""
    leverage: float = 1.0
    initial_margin: float = 0.0
    liq_price: float = 0.0


def validate_config(cfg):
    """Essential params must exist and be non-null. Optional exit params may be null (= off)."""
    def need(section, key):
        v = cfg.get(section, {}).get(key)
        if v is None:
            raise ValueError(f"config.{section}.{key} is required and must not be null")
        return v

    for s, k in [("backtest", "initial_capital"), ("backtest", "fee_pct"),
                 ("backtest", "slippage_pct"), ("risk", "risk_pct"),
                 ("risk", "notional_cap"), ("risk", "max_positions"),
                 ("risk", "stop_atr_mult"), ("stops", "ema_exit_enabled"),
                 ("spot", "min_qty")]:
        need(s, k)
    return cfg


def run_backtest(features_by_symbol, cfg, market_type="spot", funding_by_symbol=None,
                 contract_meta=None, execution_start=None, execution_end=None):
    """Chronological bar loop over all symbols.

    Signal on CLOSED bar t -> entry at bar t+1 OPEN (fill_policy=next_bar_open).
    Intrabar exits use the conservative rule: if stop and take-profit are both inside
    one candle, the STOP is assumed to hit first; an open below the stop fills at the open.
    Close-based exits (time_stop, trailing, ema) are decided at the bar close and - with
    backtest.exit_fill=next_open (default) - fill at the NEXT bar open; with exit_fill=close
    they fill at the deciding bar's close (legacy). Stop/tp/gap/liquidation stay intrabar.
    Futures: liquidation (simplified isolated model) is checked before stop/tp;
    funding payments are applied at funding timestamps using the close of the bar
    that closes at the funding time as the mark-price proxy. Where no real funding data
    exists, the fallback rate is applied at 00/08/16 UTC (counted separately as assumed).

    execution_start / execution_end (optional Timestamps): bars before execution_start are
    warm-up only - no entries and no exits are processed there, and equity/capital starts
    fresh at execution_start. A signal on the last warm-up bar may fill at the first bar
    >= execution_start (its open). With execution_end set, positions still open there are
    marked to market and reported OPEN (open_at_end flag), not force-closed.
    """
    validate_config(cfg)
    bt, rk, st = cfg["backtest"], cfg["risk"], cfg["stops"]
    sp = cfg.get("spot", {})
    is_futures = market_type == "futures"
    fee_key = "fee_pct_futures" if is_futures else "fee_pct_spot"
    fee_pct = bt.get(fee_key, bt.get("fee_pct"))
    if fee_pct is None:
        raise ValueError(f"config.backtest.{fee_key} (hoặc fee_pct) là bắt buộc")
    fee = fee_pct / 100.0
    slip = bt["slippage_pct"] / 100.0
    exit_fill = bt.get("exit_fill", "next_open")
    if exit_fill not in ("next_open", "close"):
        raise ValueError("config.backtest.exit_fill must be 'next_open' or 'close'")

    funding_enabled = False
    fallback_rate = 0.0
    if is_futures:
        fu = cfg.get("futures") or {}
        lev = fu.get("leverage")
        mmr = fu.get("maintenance_margin_rate")
        if not lev or lev <= 0:
            raise ValueError("config.futures.leverage must be > 0")
        if mmr is None or not (0 < mmr < 1):
            raise ValueError("config.futures.maintenance_margin_rate must be in (0, 1)")
        if contract_meta is None or any(s not in contract_meta for s in features_by_symbol):
            raise ValueError("contract_meta required for all futures symbols")
        funding_enabled = bool(fu.get("funding_enabled", True))
        fallback_rate = float(fu.get("funding_fallback_rate_per_8h", 0.0001))
        acc = futures_market.new_account(bt["initial_capital"])
    else:
        acc = spot_market.new_account(bt["initial_capital"])

    frames = {}
    for sym, df in features_by_symbol.items():
        f = df[REQUIRED_COLS].copy()
        f["sig_prev"] = f["signal"].shift(1).fillna(False)
        f["adx_prev"] = f["adx"].shift(1)
        f["atr_prev"] = f["atr"].shift(1)
        frames[sym] = f.set_index("timestamp")

    timeline = sorted(set().union(*(f.index for f in frames.values())))
    if execution_start is not None:
        timeline = [ts for ts in timeline if ts >= execution_start]
    if execution_end is not None:
        timeline = [ts for ts in timeline if ts <= execution_end]
    positions, trades, orders, skipped, equity_rows = {}, [], [], [], []
    last_close, last_equity = {}, bt["initial_capital"]

    # funding events per symbol: real downloaded rates + fallback at 00/08/16 UTC marks
    funding_events, funding_ptr = {}, {}
    n_funding = 0
    n_funding_fallback = 0
    fallback_usdt = 0.0
    if funding_enabled:
        H8 = 8 * 3_600_000
        t0_ms = min(int(f.index.min().value) // 1_000_000 for f in frames.values())
        t1_ms = max(int(f.index.max().value) // 1_000_000 for f in frames.values())
        schedule = np.arange((t0_ms // H8) * H8, t1_ms + 1, H8)
        fallback_on = fallback_rate > 0
        for sym in frames:
            real = (funding_by_symbol or {}).get(sym)
            rate_map = {}
            if real is not None and len(real):
                rate_map = dict(zip(real["ts"].astype("int64"), real["rate"]))
            rows = [{"ts_ns": int(t) * 1_000_000, "rate": float(r), "fallback": False}
                    for t, r in rate_map.items()]
            if fallback_on:
                rows += [{"ts_ns": int(t) * 1_000_000, "rate": fallback_rate,
                          "fallback": True}
                         for t in schedule if t not in rate_map]
            ev = pd.DataFrame(rows)
            funding_events[sym] = (ev.sort_values("ts_ns").reset_index(drop=True)
                                   if len(ev) else ev)
        funding_ptr = {sym: 0 for sym in funding_events}

    tp_r = st.get("take_profit_r")
    time_stop = st.get("time_stop_bars")
    trail_mult = st.get("trailing_atr_mult")
    stop_mult = rk["stop_atr_mult"]
    skip_ratio = bt.get("skip_cost_ratio") or 0.0
    stop_enabled = bool(st.get("initial_stop_enabled") or st.get("trailing_enabled"))
    trailing_enabled = bool(st.get("trailing_enabled") and trail_mult)
    tp_enabled = bool(tp_r and tp_r > 0)
    time_enabled = bool(time_stop and time_stop > 0)
    ema_enabled = bool(st.get("ema_exit_enabled"))

    def close_pos(pos, price, ts, reason):
        fill = price * (1 - slip)
        orders.append(Order(pos.symbol, "sell", pos.quantity, price, "FILLED", fill, ts,
                            reason, pos.quantity * fill * fee))
        if is_futures:
            acc["cash"] += (fill - pos.entry_price) * pos.quantity - pos.quantity * fill * fee
        else:
            spot_market.sell_fill(acc, pos.symbol, pos.quantity, fill, fee)
        pos.exit_time, pos.exit_price = ts, fill
        pos.exit_fee = pos.quantity * fill * fee
        pos.realized_pnl = (fill - pos.entry_price) * pos.quantity
        pos.net_pnl = pos.realized_pnl - pos.entry_fee - pos.exit_fee - pos.funding_fee
        pos.status, pos.exit_reason = "closed", reason
        trades.append(pos)

    def check_bar(pos, row):
        """Returns (reason, price) if the bar closes the position, else None."""
        if is_futures:
            # liquidation takes priority; a gap below liq price fills at the open (worse)
            if row["open"] < pos.liq_price:
                return "liquidation", row["open"]
            if row["low"] <= pos.liq_price:
                return "liquidation", pos.liq_price
        stop_price = pos.current_stop_price if stop_enabled else None
        tp_price = (pos.entry_price + tp_r * (pos.entry_price - pos.initial_stop_price)
                    if tp_enabled else None)
        o, h, l = row["open"], row["high"], row["low"]
        if stop_price is not None and o < stop_price:
            return "gap_open", o
        if stop_price is not None and l <= stop_price:
            if tp_price is not None and h >= tp_price:
                return "stop_first", stop_price  # conservative: stop assumed first
            return "stop", stop_price
        if tp_price is not None and h >= tp_price:
            return "tp", tp_price
        pos.bars_held += 1
        if row["close"] > pos.highest_close:
            pos.highest_close = row["close"]
        if time_enabled and pos.bars_held >= time_stop:
            if exit_fill == "close":
                return "time_stop", row["close"]
            pos.pending_exit = "time_stop"
            return None
        if (trailing_enabled and pd.notna(row["atr"])
                and row["close"] <= pos.highest_close - trail_mult * row["atr"]):
            if exit_fill == "close":
                return "trailing", row["close"]
            pos.pending_exit = "trailing"
            return None
        if ema_enabled and row["exit_trend"]:
            if exit_fill == "close":
                return "ema_exit", row["close"]
            pos.pending_exit = "ema_exit"
            return None
        if trailing_enabled and pd.notna(row["atr"]):
            pos.current_stop_price = pos.highest_close - trail_mult * row["atr"]
        return None

    prev_ts_ns = -1
    for ts in timeline:
        # funding payments at funding timestamps inside (prev_ts, ts]
        if funding_enabled:
            ts_ns = ts.value
            for sym, ev in funding_events.items():
                ptr = funding_ptr[sym]
                while ptr < len(ev) and ev["ts_ns"].iloc[ptr] <= ts_ns:
                    pos = positions.get(sym)
                    if ev["ts_ns"].iloc[ptr] > prev_ts_ns and pos is not None:
                        # mark proxy = close of the bar that closes at the funding time
                        price = last_close.get(sym, pos.entry_price)
                        payment = pos.quantity * price * float(ev["rate"].iloc[ptr])
                        acc["cash"] -= payment
                        pos.funding_fee += payment
                        if bool(ev["fallback"].iloc[ptr]):
                            n_funding_fallback += 1
                            fallback_usdt += payment
                        else:
                            n_funding += 1
                    ptr += 1
                funding_ptr[sym] = ptr
            prev_ts_ns = ts_ns
        # exits for positions open before this bar
        for sym in list(positions):
            if ts not in frames[sym].index:
                continue  # no data: cannot evaluate, position kept (conservative)
            row = frames[sym].loc[ts]
            pos = positions[sym]
            if pos.pending_exit:
                # close-based exit decided at the previous bar close -> fill at this open
                close_pos(pos, row["open"], ts, pos.pending_exit)
                del positions[sym]
                continue
            res = check_bar(pos, row)
            if res:
                close_pos(pos, res[1], ts, res[0])
                del positions[sym]
        # entries at this bar's open, from signals on the previous CLOSED bar
        candidates = []
        for sym, f in frames.items():
            if sym in positions or ts not in f.index:
                continue
            row = f.loc[ts]
            if not row["sig_prev"]:
                continue
            stop_distance = stop_mult * row["atr_prev"]
            if pd.isna(stop_distance) or stop_distance <= 0:
                skipped.append({"symbol": sym, "time": ts, "reason": "no_stop_reference"})
                continue
            stop_dist_pct = stop_distance / row["open"] * 100
            cost_pct = 2 * (fee + slip) * 100
            if skip_ratio and stop_dist_pct > 0 and cost_pct / stop_dist_pct > skip_ratio:
                skipped.append({"symbol": sym, "time": ts, "reason": "cost_ratio"})
                continue
            candidates.append((sym, row, stop_distance))
        free_slots = rk["max_positions"] - len(positions)
        if free_slots <= 0:
            for sym, row, sd in candidates:
                skipped.append({"symbol": sym, "time": ts, "reason": "slot_full"})
            candidates = []
        elif len(candidates) > free_slots:
            candidates.sort(key=lambda c: -(c[1]["adx_prev"] if pd.notna(c[1]["adx_prev"]) else 0.0))
            for sym, row, sd in candidates[free_slots:]:
                skipped.append({"symbol": sym, "time": ts, "reason": "slot_full"})
            candidates = candidates[:free_slots]
        for sym, row, stop_distance in candidates:
            fill = row["open"] * (1 + slip)
            risk_amount = last_equity * rk["risk_pct"] / 100.0
            qty = risk_amount / stop_distance
            if qty * fill > rk["notional_cap"]:
                qty = rk["notional_cap"] / fill
            entry_fee = qty * fill * fee
            if is_futures:
                qty = futures_market.round_to_contracts(qty, contract_meta[sym])
                if qty <= 0:
                    skipped.append({"symbol": sym, "time": ts, "reason": "size_too_small"})
                    continue
                entry_fee = qty * fill * fee
                margin = qty * fill / lev
                margin_used = sum(p.initial_margin for p in positions.values())
                if margin + entry_fee > acc["cash"] - margin_used:
                    orders.append(Order(sym, "buy", qty, row["open"], "REJECTED", None,
                                        None, "insufficient_margin"))
                    continue
                acc["cash"] -= entry_fee
                liq = futures_market.liquidation_price_long(fill, lev, mmr)
                pos = Position(sym, "futures", "long", ts, fill, qty, qty * fill,
                               fill - stop_distance, fill - stop_distance,
                               entry_fee=entry_fee, highest_close=fill,
                               leverage=lev, initial_margin=margin, liq_price=liq)
            else:
                min_q = sp.get("min_qty")
                qty = np.floor(qty / min_q) * min_q if min_q and min_q > 0 else qty
                if qty <= 0:
                    skipped.append({"symbol": sym, "time": ts, "reason": "size_too_small"})
                    continue
                entry_fee = qty * fill * fee
                cost = qty * fill * (1 + fee)
                if cost > acc["cash"]:
                    orders.append(Order(sym, "buy", qty, row["open"], "REJECTED", None,
                                        None, "insufficient_balance"))
                    continue
                spot_market.buy_fill(acc, sym, qty, fill, fee)
                pos = Position(sym, "spot", "long", ts, fill, qty, qty * fill,
                               fill - stop_distance, fill - stop_distance,
                               entry_fee=entry_fee, highest_close=fill)
            positions[sym] = pos
            orders.append(Order(sym, "buy", qty, row["open"], "FILLED", fill, ts,
                                "signal", entry_fee))
            res = check_bar(pos, row)  # entry bar is live too
            if res:
                close_pos(pos, res[1], ts, res[0])
                del positions[sym]
        # mark to market at this bar's close
        for sym, f in frames.items():
            if ts in f.index:
                last_close[sym] = f.loc[ts, "close"]
        if is_futures:
            eq = acc["cash"] + sum(
                (last_close.get(sym, p.entry_price) - p.entry_price) * p.quantity
                for sym, p in positions.items())
        else:
            eq = acc["cash"] + sum(acc["assets"].get(sym, 0.0) * last_close.get(sym, 0.0)
                                   for sym in acc["assets"])
        notional_open = sum(p.quantity * last_close.get(p.symbol, p.entry_price)
                            for p in positions.values())
        exposure = notional_open / eq * 100 if eq else 0.0
        equity_rows.append({"timestamp": ts, "equity": eq, "cash": acc["cash"],
                            "exposure_pct": exposure})
        last_equity = eq

    # close anything still open at the last available close (flagged in exit_reason)
    n_forced = 0
    n_open = 0
    if timeline:
        if execution_end is not None:
            for sym, pos in list(positions.items()):
                if pos.pending_exit:
                    close_pos(pos, last_close.get(sym, pos.entry_price), timeline[-1],
                              pos.pending_exit + "_eod")
                    n_forced += 1
                else:
                    # marked to market, reported open (not force-closed)
                    pos.open_at_end = True
                    pos.exit_price = last_close.get(sym, pos.entry_price)
                    pos.exit_reason = "open_at_end"
                    pos.status = "open"
                    pos.realized_pnl = 0.0
                    pos.net_pnl = 0.0
                    trades.append(pos)
                    n_open += 1
        else:
            for sym, pos in list(positions.items()):
                reason = pos.pending_exit + "_eod" if pos.pending_exit else "end_of_data"
                close_pos(pos, last_close.get(sym, pos.entry_price), timeline[-1], reason)
                n_forced += 1
            if n_forced:
                equity_rows[-1] = {"timestamp": timeline[-1], "equity": acc["cash"],
                                   "cash": acc["cash"], "exposure_pct": 0.0}

    for i, o in enumerate(orders):
        o.order_id = f"O{i + 1}"
    for i, t in enumerate(trades):
        t.position_id = f"P{i + 1}"
    order_cols = [f.name for f in fields(Order)]
    pos_cols = [f.name for f in fields(Position)]
    trades_df = (pd.DataFrame([t.__dict__ for t in trades], columns=pos_cols)
                 if trades else pd.DataFrame(columns=pos_cols))
    orders_df = (pd.DataFrame([o.__dict__ for o in orders], columns=order_cols)
                 if orders else pd.DataFrame(columns=order_cols))
    equity_df = pd.DataFrame(equity_rows)
    skipped_df = (pd.DataFrame(skipped, columns=["symbol", "time", "reason"])
                  if skipped else pd.DataFrame(columns=["symbol", "time", "reason"]))
    final_equity = acc["cash"]
    summary = {
        "market_type": market_type,
        "funding_included": funding_enabled,
        "n_funding_payments": n_funding,
        "n_funding_fallback": n_funding_fallback,
        "funding_assumed_usdt": fallback_usdt,
        "initial_capital": bt["initial_capital"],
        "final_equity": final_equity,
        "net_profit": final_equity - bt["initial_capital"],
        "net_return_pct": (final_equity / bt["initial_capital"] - 1) * 100,
        "n_trades": len(trades),
        "n_rejected": int((orders_df["status"] == "REJECTED").sum()) if len(orders_df) else 0,
        "n_skipped": len(skipped),
        "n_forced_close": n_forced,
        "n_open_at_end": n_open,
    }
    return trades_df, orders_df, equity_df, skipped_df, summary
