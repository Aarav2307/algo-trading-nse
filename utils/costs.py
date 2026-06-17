"""
Transaction cost model for NSE equity trades (Zerodha / NSE).

All real exchange and regulatory costs are modelled:

  Brokerage:         ₹20 flat per order (Zerodha equity delivery/intraday)
  STT:               0.025% of turnover on SELL side only
  Slippage:          0.05% of execution price, both sides
                     Applied via apply_slippage() BEFORE calling transaction_costs().
                     Shown in the breakdown dict for transparency but NOT included
                     in `total` — it is already embedded in the exec_price argument.
  SEBI turnover fee: 0.0001% of turnover, both sides
  NSE exchange fee:  0.00335% of turnover, both sides
  GST on brokerage:  18% of brokerage, both sides
  Stamp duty:        0.015% of turnover, BUY side only

Turnover = execution_price × quantity.

Round-trip cost on a ₹1,00,000 position (100 shares × ₹1,000):
  BUY  side: ₹20 brokerage + ₹0.10 SEBI + ₹3.35 exchange + ₹3.60 GST + ₹15.00 stamp = ₹42.05
  SELL side: ₹20 brokerage + ₹25.00 STT + ₹0.10 SEBI + ₹3.35 exchange + ₹3.60 GST   = ₹52.05
  Round-trip: ₹94.10 (+ ₹100 slippage already in exec_price on each side)
"""

BROKERAGE_PER_ORDER  = 20.0        # ₹ flat per order
STT_RATE             = 0.00025     # 0.025% of turnover — sell side only
SLIPPAGE_RATE        = 0.0005      # 0.05% of execution price — both sides
SEBI_FEE_RATE        = 0.000001    # 0.0001% of turnover — both sides
NSE_EXCHANGE_RATE    = 0.0000335   # 0.00335% of turnover — both sides
GST_RATE             = 0.18        # 18% of brokerage — both sides
STAMP_DUTY_RATE      = 0.00015     # 0.015% of turnover — buy side only


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


def transaction_costs(price: float, quantity: int, side: str) -> dict:
    """
    Compute a full cost breakdown for one NSE order.

    Args:
        price:    Execution price per share. This should be the slippage-adjusted
                  price returned by apply_slippage() — slippage has already been
                  applied to the fill price before this function is called.
        quantity: Number of shares.
        side:     "buy" or "sell"

    Returns:
        Dict with these keys (all values in ₹, always non-negative):

          brokerage        ₹20 flat per order
          stt              0.025% of turnover — sell side only, else 0
          slippage         0.05% × turnover — informational only; already embedded
                           in `price` via apply_slippage(). NOT included in `total`.
          sebi_fee         0.0001% of turnover — both sides
          exchange_charge  0.00335% of turnover — both sides
          gst              18% of brokerage — both sides
          stamp_duty       0.015% of turnover — buy side only, else 0
          total            cash deducted beyond shares × exec_price
                           = brokerage + stt + sebi_fee + exchange_charge + gst + stamp_duty
                           (slippage excluded — already in exec_price)
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")

    turnover        = price * quantity

    brokerage       = BROKERAGE_PER_ORDER
    stt             = turnover * STT_RATE         if side == "sell" else 0.0
    slippage        = turnover * SLIPPAGE_RATE    # informational — already in price
    sebi_fee        = turnover * SEBI_FEE_RATE
    exchange_charge = turnover * NSE_EXCHANGE_RATE
    gst             = brokerage * GST_RATE
    stamp_duty      = turnover * STAMP_DUTY_RATE  if side == "buy"  else 0.0

    # total = real cash deductions on top of exec_price × shares
    total = brokerage + stt + sebi_fee + exchange_charge + gst + stamp_duty

    return {
        "brokerage":        brokerage,
        "stt":              stt,
        "slippage":         slippage,   # informational only — NOT in total
        "sebi_fee":         sebi_fee,
        "exchange_charge":  exchange_charge,
        "gst":              gst,
        "stamp_duty":       stamp_duty,
        "total":            total,
    }


def format_cost_breakdown(breakdown: dict) -> str:
    """
    Format a cost breakdown dict as a one-line log string.
    Slippage is shown separately with a note that it is in exec_price.

    Example output:
        brokerage=₹20.00 | STT=₹0.00 | slippage=₹5.00* | SEBI=₹0.01 |
        exchange=₹0.34 | GST=₹3.60 | stamp=₹1.50 | TOTAL=₹25.45
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
        f" | TOTAL=₹{breakdown['total']:.2f}"
        f"  (* already in exec_price)"
    )
