"""USDT-margined perpetual swap accounting (isolated margin, long-only in v1).

Documented simplifications:
- Single-tier maintenance margin rate from config (OKX uses tiered MMR).
- Liquidation (long): when price falls to entry*(1 - (1/leverage - mmr)), fill at that price.
- Quantity stored in BASE units (contracts x ctVal), rounded down to whole contracts.
- Margin is not transferred out of cash: fees and realized PnL settle against cash;
  available margin at entry = cash - sum(initial margins of open positions).
"""
import numpy as np


def new_account(cash):
    return {"cash": float(cash)}


def round_to_contracts(qty_base, meta):
    """Round base quantity down to whole contracts (ctVal units)."""
    ct = meta["ctVal"]
    return max(0.0, np.floor(qty_base / ct) * ct)


def liquidation_price_long(entry, leverage, mmr):
    """Simplified: long is liquidated when the loss consumes initial margin down
    to maintenance margin: price drop of (1/leverage - mmr) relative to entry."""
    return entry * (1.0 - (1.0 / leverage - mmr))
