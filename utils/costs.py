"""
Transaction cost model for NSE equity trades (Zerodha / NSE).

Delivery trades (default, system only does delivery):
  Brokerage:         ₹0 — delivery is free on Zerodha
  STT:               0.1% of turnover, BOTH sides
  Stamp duty:        0.015% of turnover, BUY side only
  DP charge:         ₹15.34 per scrip, SELL side only (CDSL + Zerodha + GST)

Intraday trades:
  Brokerage:         min(₹20, 0.03% of turnover) per order
  STT:               0.025% of turnover, SELL side only
  Stamp duty:        0.003% of turnover, BUY side only
  DP charge:         none

Both trade types:
  SEBI turnover fee: 0.0001% of turnover, both sides
  NSE exchange fee:  0.00335% of turnover, both sides
  GST:               18% of (brokerage + SEBI fee + exchange charge), both sides
  Slippage:          0.05% of execution price, both sides
                     Applied via apply_slippage() BEFORE calling transaction_costs().
                     Shown in the breakdown dict for transparency but NOT included
                     in `total` — it is already embedded in the exec_price argument.

Turnover = execution_price × quantity.

Round-trip on ₹10,000 (1 share × ₹10,000) — delivery:
  BUY  side: ₹0 brokerage + ₹10.00 STT + ₹0.01 SEBI + ₹0.34 exchange + ₹0.06 GST + ₹1.50 stamp = ₹11.91
  SELL side: ₹0 brokerage + ₹10.00 STT + ₹0.01 SEBI + ₹0.34 exchange + ₹0.06 GST + ₹15.34 DP   = ₹25.75
  Round-trip: ₹37.66 (+ slippage already in exec_price on each side)
"""

BROKERAGE_DELIVERY        = 0.0        # ₹0 — delivery is free on Zerodha
BROKERAGE_INTRADAY        = 20.0       # ₹20 flat cap — intraday/F&O
BROKERAGE_INTRADAY_RATE   = 0.0003     # 0.03% rate, capped at ₹20
STT_RATE_DELIVERY         = 0.001      # 0.1% both sides (delivery)
STT_RATE_INTRADAY         = 0.00025    # 0.025% sell-side only (intraday)
SLIPPAGE_RATE             = 0.0005     # 0.05% of execution price — both sides
SEBI_FEE_RATE             = 0.000001   # 0.0001% of turnover — both sides
NSE_EXCHANGE_RATE         = 0.0000335  # 0.00335% of turnover — both sides
GST_RATE                  = 0.18       # 18% of (brokerage + SEBI + exchange) — both sides
STAMP_DUTY_RATE           = 0.00015    # 0.015% of turnover — delivery buy side only
STAMP_DUTY_RATE_INTRADAY  = 0.00003    # 0.003% of turnover — intraday buy side only
DP_CHARGE                 = 15.34      # ₹15.34 per scrip per sell (CDSL + Zerodha + GST)

# Backward-compat alias — callers using BROKERAGE_PER_ORDER as a cash floor guard
# still work; deliberately set to BROKERAGE_INTRADAY so the ₹20 buffer is conservative.
BROKERAGE_PER_ORDER = BROKERAGE_INTRADAY


def apply_slippage(price: float, side: str) -> float:
    """
    Adjust price for market-impact / bid-ask slippage.
    Buy orders fill slightly above the signal price; sell orders fill below.

    Args:
        price: the signal/reference price (e.g. today's close)
        side:  "buy" or "sell"

    Returns:
        Slippage-adjusted execution price.
    """
    if side == "buy":
        return price * (1 + SLIPPAGE_RATE)
    elif side == "sell":
        return price * (1 - SLIPPAGE_RATE)
    raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")


def transaction_costs(price: float, shares: int, side: str, trade_type: str = "delivery") -> float:
    """
    Return the total cost in ₹ for one NSE order (slippage excluded — already in price).

    Args:
        price:      Slippage-adjusted execution price per share.
        shares:     Number of shares.
        side:       "buy" or "sell"
        trade_type: "delivery" (default) or "intraday"

    Returns:
        Total cost as a float. Use transaction_cost_breakdown() for the itemised dict.
    """
    return transaction_cost_breakdown(price, shares, side, trade_type)["total"]


def transaction_cost_breakdown(price: float, shares: int, side: str, trade_type: str = "delivery") -> dict:
    """
    Return a full cost breakdown dict for one NSE order.

    Args:
        price:      Slippage-adjusted execution price per share.
        shares:     Number of shares.
        side:       "buy" or "sell"
        trade_type: "delivery" (default) or "intraday"

    Returns:
        Dict with these keys (all values in ₹, always non-negative):

          brokerage        ₹0 delivery / min(₹20, 0.03% turnover) intraday
          stt              0.1% turnover both sides (delivery); 0.025% sell only (intraday)
          slippage         0.05% × turnover — informational only; already embedded
                           in `price` via apply_slippage(). NOT included in `total`.
          sebi_fee         0.0001% of turnover — both sides
          exchange_charge  0.00335% of turnover — both sides
          gst              18% of (brokerage + SEBI + exchange) — both sides
          stamp_duty       0.015% turnover buy only (delivery); 0.003% buy only (intraday)
          dp_charge        ₹15.34 sell side only (delivery); ₹0 intraday
          total            cash deducted beyond shares × exec_price
                           = brokerage + stt + sebi_fee + exchange_charge
                             + gst + stamp_duty + dp_charge
                           (slippage excluded — already in exec_price)
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")
    if trade_type not in ("delivery", "intraday"):
        raise ValueError(f"trade_type must be 'delivery' or 'intraday', got '{trade_type}'")

    turnover = price * shares

    if trade_type == "delivery":
        brokerage  = BROKERAGE_DELIVERY
        stt        = turnover * STT_RATE_DELIVERY                              # both sides
        stamp_duty = turnover * STAMP_DUTY_RATE      if side == "buy"  else 0.0
        dp_charge  = DP_CHARGE                       if side == "sell" else 0.0
    else:  # intraday
        brokerage  = min(BROKERAGE_INTRADAY, turnover * BROKERAGE_INTRADAY_RATE)
        stt        = turnover * STT_RATE_INTRADAY    if side == "sell" else 0.0
        stamp_duty = turnover * STAMP_DUTY_RATE_INTRADAY if side == "buy" else 0.0
        dp_charge  = 0.0

    slippage        = turnover * SLIPPAGE_RATE    # informational — already in price
    sebi_fee        = turnover * SEBI_FEE_RATE
    exchange_charge = turnover * NSE_EXCHANGE_RATE
    gst             = (brokerage + sebi_fee + exchange_charge) * GST_RATE

    total = brokerage + stt + sebi_fee + exchange_charge + gst + stamp_duty + dp_charge

    return {
        "brokerage":        brokerage,
        "stt":              stt,
        "slippage":         slippage,   # informational only — NOT in total
        "sebi_fee":         sebi_fee,
        "exchange_charge":  exchange_charge,
        "gst":              gst,
        "stamp_duty":       stamp_duty,
        "dp_charge":        dp_charge,
        "total":            total,
    }


def format_cost_breakdown(breakdown: dict) -> str:
    """
    Format a cost breakdown dict as a one-line log string.
    Slippage is shown separately with a note that it is in exec_price.

    Example output:
        brokerage=₹0.00 | STT=₹10.00 | slippage=₹5.00* | SEBI=₹0.01 |
        exchange=₹0.34 | GST=₹0.06 | stamp=₹1.50 | DP=₹0.00 | TOTAL=₹11.91
        (* in exec_price)
    """
    return (
        f"brokerage=₹{breakdown['brokerage']:.2f}"
        f" | STT=₹{breakdown['stt']:.2f}"
        f" | slippage=₹{breakdown['slippage']:.2f}*"
        f" | SEBI=₹{breakdown['sebi_fee']:.2f}"
        f" | exchange=₹{breakdown['exchange_charge']:.2f}"
        f" | GST=₹{breakdown['gst']:.2f}"
        f" | stamp=₹{breakdown['stamp_duty']:.2f}"
        f" | DP=₹{breakdown.get('dp_charge', 0.0):.2f}"
        f" | TOTAL=₹{breakdown['total']:.2f}"
        f"  (* already in exec_price)"
    )
