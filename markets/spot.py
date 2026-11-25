"""Spot accounting: quote cash + base asset balances. No leverage, no shorting."""


def new_account(cash):
    return {"cash": float(cash), "assets": {}}


def buy_fill(acc, symbol, qty, price, fee_rate):
    """Cash is reduced by qty*price*(1+fee); base asset increases by qty."""
    cost = qty * price * (1 + fee_rate)
    if cost > acc["cash"] + 1e-9:
        raise ValueError("insufficient cash for buy")
    acc["cash"] -= cost
    acc["assets"][symbol] = acc["assets"].get(symbol, 0.0) + qty
    return acc


def sell_fill(acc, symbol, qty, price, fee_rate):
    """Cash increases by qty*price*(1-fee); base asset decreases by qty."""
    held = acc["assets"].get(symbol, 0.0)
    if qty > held + 1e-9:
        raise ValueError("insufficient asset for sell")
    acc["assets"][symbol] = held - qty
    acc["cash"] += qty * price * (1 - fee_rate)
    return acc


def market_value(acc, prices):
    """Equity = cash + sum(asset qty * last known price)."""
    return acc["cash"] + sum(qty * prices.get(sym, 0.0) for sym, qty in acc["assets"].items())
