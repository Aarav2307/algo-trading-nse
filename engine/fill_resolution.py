"""
engine/fill_resolution.py — Shared AMO fill-resolution decision logic.

Root problem this closes: engine/backtester.py has no concept of an AMO limit
price at all — every deferred BUY/SELL fills unconditionally at next-day open.
paper_trading/morning_fill_check.py, which runs the actual live AMO orders,
checks each fill against a limit price (AMO_LIMIT_BUFFER_PCT) and classifies a
miss as either a gap-down circuit exit (GAP_EXIT) or a same-side requeue —
logic that used to live only as private functions and inline branching inside
morning_fill_check.py, unreachable by the backtester.

This module extracts exactly the pure decision functions — no I/O, no
portfolio state, no order-log writes — so they can be shared. Today only
morning_fill_check.py consumes them (see that file for how); wiring
engine/backtester.py to the same functions, so walk-forward's OOS numbers
stop assuming a 100% AMO fill rate, is a deliberately separate, larger change
(it would alter historical simulated fills for every currently-validated
stock) and is not part of this extraction.
"""
from typing import Tuple


def check_circuit_breaker(ticker: str, open_price: float, prev_close: float) -> Tuple[bool, str]:
    """
    Check if a stock has hit a circuit breaker at open.
    NSE circuit limits: 2%, 5%, 10%, 20% depending on stock category.

    A circuit breaker is suspected if open price moved >= 20% from prev close
    (lower circuit = stock can't be sold, upper circuit = can't be bought).
    20% is NSE's standard upper/lower circuit band for individual stocks,
    distinguishing a genuine circuit event from an ordinary large overnight gap.

    Returns:
        (True, reason) if circuit breaker suspected
        (False, "") if normal
    """
    if prev_close <= 0:
        return False, ""

    pct_move = abs(open_price - prev_close) / prev_close * 100

    if pct_move >= 20.0:
        direction = "upper" if open_price > prev_close else "lower"
        return True, (
            f"Possible {direction} circuit breaker: open moved {pct_move:.1f}% "
            f"from prev close ₹{prev_close:.2f}"
        )

    return False, ""


def is_fill_hit(order_type: str, limit_price: float, open_price: float) -> bool:
    """
    Would this AMO limit order have filled at today's open?
    BUY  fills if open <= limit (won't pay more than the signal-day limit).
    SELL fills if open >= limit (won't sell for less than the signal-day limit).
    """
    if order_type == "BUY":
        return open_price <= limit_price
    return open_price >= limit_price


def classify_missed_sell(
    limit_price: float,
    open_price: float,
    gap_breaker_threshold: float,
    is_managed_exit: bool,
) -> str:
    """
    Classify a missed SELL AMO (is_fill_hit already returned False) into
    exactly one of three outcomes:

      "GAP_EXIT"        -- gap-down exceeds gap_breaker_threshold: exit
                           immediately at today's open rather than requeue,
                           since holding an unprotected position through a
                           confirmed adverse gap is worse than a worse exit
                           price.
      "REQUEUE"         -- small gap on a managed exit (RM stop or strategy
                           signal): retry tomorrow at an updated limit rather
                           than leaving the position with no active stop.
      "UNMANAGED_MISS"  -- small gap, not a managed exit type (rare edge
                           case) -- no automatic follow-up action.

    gap_magnitude uses limit_price as the denominator (not open_price),
    matching morning_fill_check.py's original inline calculation exactly.
    """
    gap_magnitude = (limit_price - open_price) / limit_price
    if gap_magnitude > gap_breaker_threshold:
        return "GAP_EXIT"
    if is_managed_exit:
        return "REQUEUE"
    return "UNMANAGED_MISS"
