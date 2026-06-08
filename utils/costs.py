"""
Transaction cost model for NSE equity trades.

Costs modelled:
  - Brokerage: ₹20 flat per order (Zerodha model)
  - STT (Securities Transaction Tax): 0.025% on sell-side turnover
  - Slippage: 0.05% on execution price, applied to both buy and sell
    (accounts for bid-ask spread and partial fill drift)

Not modelled (minor, can add later):
  - NSE exchange charges (~0.00322%)
  - SEBI turnover fees (~0.0001%)
  - GST on brokerage (18%)
  - Stamp duty (0.015% on buy side)
"""

BROKERAGE_PER_ORDER = 20.0   # ₹ flat
STT_RATE = 0.00025            # 0.025% on sell turnover
SLIPPAGE_RATE = 0.0005        # 0.05% on execution price


def apply_slippage(price: float, side: str) -> float:
    """
    Adjust price for slippage.
    Buy orders fill slightly higher; sell orders fill slightly lower.
    """
    if side == "buy":
        return price * (1 + SLIPPAGE_RATE)
    elif side == "sell":
        return price * (1 - SLIPPAGE_RATE)
    raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")


def transaction_costs(price: float, quantity: int, side: str) -> float:
    """
    Total friction cost for one order.

    Args:
        price:    execution price per share (after slippage is already applied)
        quantity: number of shares
        side:     'buy' or 'sell'

    Returns:
        Total cost in ₹ (always positive — a deduction from cash).
    """
    turnover = price * quantity
    brokerage = BROKERAGE_PER_ORDER
    stt = turnover * STT_RATE if side == "sell" else 0.0
    return brokerage + stt
