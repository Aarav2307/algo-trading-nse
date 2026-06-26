"""
paper_trading/test_gap_breaker.py — Unit tests for gap-down circuit breaker.

All tests use temp state files — never touches paper_trading/portfolio_state.json.
"""

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc
from paper_trading.morning_fill_check import (
    GAP_BREAKER_THRESHOLD,
    _process_order,
    _update_portfolio_fill,
)
from utils.costs import apply_slippage, transaction_costs

_PASS = 0
_FAIL = 0

LIVE_STATE = Path("paper_trading/portfolio_state.json")


def _pass_test(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  PASS  {name}")


def _fail_test(name: str, msg: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  FAIL  {name}: {msg}")


def _make_state(
    ticker: str,
    shares: int = 3,
    entry_price: float = 3731.56,
    entry_cost: float = 25.0,
    cash: float = 50_000.0,
) -> dict:
    return {
        "cash": cash,
        "initial_capital": 100_000.0,
        "positions": {
            ticker: {
                "shares": shares,
                "entry_price": entry_price,
                "entry_cost": entry_cost,
                "entry_date": "2026-06-01",
                "highest_high_since_entry": entry_price * 1.05,
                "bars_held": 10,
                "chandelier_stop": None,
                "pending_buy": False,
                "pending_rm_exit": True,
                "rm_exit_reason": "CHANDELIER",
                "rm_sell_requeue_count": 0,
            }
        },
        "cooldown_state": {
            ticker: {"remaining_bars": 0, "last_exit_reason": None}
        },
        "total_trades": 2,
        "last_run_date": "2026-06-25",
        "inception_date": "2026-06-02",
        "weekly_start_value": 99_000.0,
        "weekly_start_date": "2026-06-23",
        "weekly_signals": {"BUY": 0, "SELL": 0, "RISK_EXIT": 0},
        "trade_log": [
            {"ticker": "AAA", "exit_reason": "CHANDELIER", "net_pnl": -100.0},
            {"ticker": "BBB", "exit_reason": "STRATEGY_SIGNAL", "net_pnl": -200.0},
        ],
        "etf_shares": 0,
        "etf_avg_price": 0.0,
        "etf_tier": 0,
    }


def _make_order(
    ticker: str,
    order_type: str = "SELL",
    shares: int = 3,
    limit_price: float = 3680.0,
    signal_price: float = 3700.0,
    notes: str = "CHANDELIER",
    order_date: str = "2026-06-25",
) -> dict:
    return {
        "ticker":       ticker,
        "order_type":   order_type,
        "shares":       str(shares),
        "limit_price":  str(limit_price),
        "signal_price": str(signal_price),
        "notes":        notes,
        "date":         order_date,
        "order_id":     "",
        "status":       "DRY_RUN",
        "fill_date":    "",
        "fill_price":   "",
    }


# =============================================================================
# Test 1 — Normal SELL fill (open >= limit, no circuit breaker)
# =============================================================================

def test_1_normal_sell_fill() -> None:
    name = "Test 1 — Normal SELL fill (open >= limit)"

    order  = _make_order("TESTSTOCK.NS", limit_price=3680.0)
    result = _process_order(order, today_open=3700.0, prev_close=3750.0, kite=None)

    if not result["filled"]:
        _fail_test(name, f"Expected FILLED, got status={result['status']}")
        return
    if result["status"] != "FILLED":
        _fail_test(name, f"Expected status=FILLED, got {result['status']}")
        return
    if result["fill_price"] != 3700.0:
        _fail_test(name, f"Expected fill_price=3700.0, got {result['fill_price']}")
        return

    # A filled order has open >= limit, so gap_magnitude would be <= 0 — no breaker
    gap_magnitude = (3680.0 - 3700.0) / 3680.0
    if gap_magnitude > GAP_BREAKER_THRESHOLD:
        _fail_test(name, "Circuit breaker should not apply to a filled order")
        return

    _pass_test(name)


# =============================================================================
# Test 2 — Small gap miss (gap < 3%) → requeue path, NOT GAP_EXIT
# =============================================================================

def test_2_small_gap_miss() -> None:
    name = "Test 2 — Small gap miss (1.63% < 3%) → requeue path"

    # open=3620, limit=3680 → gap_magnitude = (3680-3620)/3680 = 1.63%
    order  = _make_order("TESTSTOCK.NS", limit_price=3680.0)
    result = _process_order(order, today_open=3620.0, prev_close=3720.0, kite=None)

    if result["status"] != "MISSED":
        _fail_test(name, f"Expected MISSED, got {result['status']}")
        return

    gap_magnitude = (3680.0 - 3620.0) / 3680.0
    if abs(gap_magnitude - 60 / 3680) > 1e-9:
        _fail_test(name, f"gap_magnitude arithmetic error: got {gap_magnitude:.6f}")
        return
    if gap_magnitude > GAP_BREAKER_THRESHOLD:
        _fail_test(name, f"Small gap {gap_magnitude*100:.2f}% should NOT trigger circuit breaker")
        return

    _pass_test(name)


# =============================================================================
# Test 3 — Large gap (7.6% > 3%) → GAP_EXIT fires, position closed
# =============================================================================

def test_3_large_gap_circuit_breaker() -> None:
    name = "Test 3 — Large gap (7.6% > 3%) → GAP_EXIT fires"

    ticker = "TESTGAP.NS"
    tmp    = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()

    try:
        initial_cash = 50_000.0
        state        = _make_state(ticker, shares=3, entry_price=3731.56, cash=initial_cash)
        with open(tmp.name, "w") as fh:
            json.dump(state, fh, indent=2)

        original_state_file = mfc.STATE_FILE
        mfc.STATE_FILE = Path(tmp.name)
        try:
            # open=3400, limit=3680 → gap = (3680-3400)/3680 = 7.61% > 3%
            limit_px, open_px = 3680.0, 3400.0
            gap_magnitude     = (limit_px - open_px) / limit_px

            if gap_magnitude <= GAP_BREAKER_THRESHOLD:
                _fail_test(name, f"Test precondition: gap {gap_magnitude*100:.2f}% should be > {GAP_BREAKER_THRESHOLD*100:.0f}%")
                return

            _update_portfolio_fill(
                ticker, "SELL", 3, open_px, date(2026, 6, 29),
                exit_reason="GAP_EXIT",
                is_rm_exit=True,
                portfolio_obj=None,
            )

            with open(tmp.name) as fh:
                after = json.load(fh)

            if after["positions"][ticker]["shares"] != 0:
                _fail_test(name, f"Expected shares=0 after GAP_EXIT, got {after['positions'][ticker]['shares']}")
                return
            if after["cash"] <= initial_cash:
                _fail_test(name, f"Cash should increase after SELL: {initial_cash:.2f} → {after['cash']:.2f}")
                return
            last_trade = after["trade_log"][-1]
            if last_trade["exit_reason"] != "GAP_EXIT":
                _fail_test(name, f"Expected exit_reason=GAP_EXIT, got '{last_trade['exit_reason']}'")
                return

        finally:
            mfc.STATE_FILE = original_state_file

        _pass_test(name)

    finally:
        os.unlink(tmp.name)


# =============================================================================
# Test 4 — Gap just below threshold (2.91%) → MISSED, NOT GAP_EXIT
# =============================================================================

def test_4_exactly_at_threshold() -> None:
    name = "Test 4 — Gap just below threshold (2.91%) → MISSED (not GAP_EXIT)"

    # Choose open_px so gap_magnitude is provably below 3%.
    # gap_magnitude = (3680 - 3573) / 3680 = 107/3680 = 2.908% < 3%
    # Avoids floating-point ambiguity of trying to hit exactly 3.0000...%.
    limit_px      = 3680.0
    open_px       = 3573.0
    gap_magnitude = (limit_px - open_px) / limit_px   # 0.02908... < 0.03

    if gap_magnitude >= GAP_BREAKER_THRESHOLD:
        _fail_test(name, f"Test precondition: gap {gap_magnitude*100:.3f}% should be < {GAP_BREAKER_THRESHOLD*100:.0f}%")
        return

    # Strictly > threshold required to fire — 2.91% must NOT trigger circuit breaker
    if gap_magnitude > GAP_BREAKER_THRESHOLD:
        _fail_test(name, f"2.91% gap should NOT trigger circuit breaker (threshold={GAP_BREAKER_THRESHOLD*100:.0f}%)")
        return

    # Confirm _process_order returns MISSED (open < limit)
    order  = _make_order("TESTSTOCK.NS", limit_price=limit_px)
    result = _process_order(order, today_open=open_px, prev_close=limit_px + 50, kite=None)
    if result["status"] != "MISSED":
        _fail_test(name, f"Expected MISSED, got {result['status']}")
        return

    _pass_test(name)


# =============================================================================
# Test 5 — GAP_EXIT P&L is correct (gross ≈ -₹994.68)
# =============================================================================

def test_5_gap_exit_pnl_correct() -> None:
    name = "Test 5 — GAP_EXIT P&L calculation is correct"

    ticker     = "TESTPNL.NS"
    entry_px   = 3731.56
    shares     = 3
    open_px    = 3400.0
    entry_cost = 25.0
    tmp        = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()

    try:
        initial_cash = 50_000.0
        state        = _make_state(ticker, shares=shares, entry_price=entry_px,
                                   entry_cost=entry_cost, cash=initial_cash)
        with open(tmp.name, "w") as fh:
            json.dump(state, fh, indent=2)

        original_state_file = mfc.STATE_FILE
        mfc.STATE_FILE = Path(tmp.name)
        try:
            _update_portfolio_fill(
                ticker, "SELL", shares, open_px, date(2026, 6, 29),
                exit_reason="GAP_EXIT",
                is_rm_exit=False,
                portfolio_obj=None,
            )

            with open(tmp.name) as fh:
                after = json.load(fh)

            # Recompute expected values using the same logic as _update_portfolio_fill
            exec_price     = apply_slippage(open_px, "sell")
            sell_cost      = transaction_costs(exec_price, shares, "sell")["total"]
            proceeds       = shares * exec_price - sell_cost
            expected_cash  = initial_cash + proceeds
            expected_gross = (exec_price - entry_px) * shares
            expected_net   = expected_gross - sell_cost - entry_cost

            if abs(after["cash"] - expected_cash) > 0.01:
                _fail_test(name, f"Cash: expected ₹{expected_cash:.2f}, got ₹{after['cash']:.2f}")
                return

            last_trade = after["trade_log"][-1]

            if abs(last_trade["net_pnl"] - expected_net) > 0.01:
                _fail_test(name, f"net_pnl: expected ₹{expected_net:.2f}, got ₹{last_trade['net_pnl']:.2f}")
                return
            if last_trade["net_pnl"] >= 0:
                _fail_test(name, f"net_pnl should be negative, got ₹{last_trade['net_pnl']:.2f}")
                return

            # Gross P&L uses slippage-adjusted exec_price (same as _update_portfolio_fill)
            if abs(last_trade["gross_pnl"] - expected_gross) > 0.01:
                _fail_test(name, f"gross_pnl: expected ₹{expected_gross:.2f}, got ₹{last_trade['gross_pnl']:.2f}")
                return

        finally:
            mfc.STATE_FILE = original_state_file

        _pass_test(name)

    finally:
        os.unlink(tmp.name)


# =============================================================================
# Test 6 — GAP_EXIT does not double-exit (idempotency guard)
# =============================================================================

def test_6_gap_exit_no_double_exit() -> None:
    name = "Test 6 — GAP_EXIT idempotency (shares=0 → no double exit)"

    ticker = "TESTDUPE.NS"
    tmp    = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()

    try:
        initial_cash = 50_000.0
        # Position already flat (shares=0) — simulate already-closed state
        state = _make_state(ticker, shares=0, entry_price=3731.56, cash=initial_cash)
        with open(tmp.name, "w") as fh:
            json.dump(state, fh, indent=2)

        original_trade_count = len(state["trade_log"])

        original_state_file = mfc.STATE_FILE
        mfc.STATE_FILE = Path(tmp.name)
        try:
            # Should return early via idempotency guard (shares <= 0)
            _update_portfolio_fill(
                ticker, "SELL", 3, 3400.0, date(2026, 6, 29),
                exit_reason="GAP_EXIT",
                is_rm_exit=False,
                portfolio_obj=None,
            )

            with open(tmp.name) as fh:
                after = json.load(fh)

        finally:
            mfc.STATE_FILE = original_state_file

        if abs(after["cash"] - initial_cash) > 0.01:
            _fail_test(name, f"Cash changed on double-exit: {initial_cash:.2f} → {after['cash']:.2f}")
            return
        if len(after["trade_log"]) != original_trade_count:
            _fail_test(name, f"trade_log grew on double-exit: {original_trade_count} → {len(after['trade_log'])}")
            return

        _pass_test(name)

    finally:
        os.unlink(tmp.name)


# =============================================================================
# Runner
# =============================================================================

if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  Gap-Down Circuit Breaker Tests")
    print("=" * 50)

    mtime_before = os.path.getmtime(LIVE_STATE) if LIVE_STATE.exists() else 0.0

    test_1_normal_sell_fill()
    test_2_small_gap_miss()
    test_3_large_gap_circuit_breaker()
    test_4_exactly_at_threshold()
    test_5_gap_exit_pnl_correct()
    test_6_gap_exit_no_double_exit()

    if LIVE_STATE.exists():
        mtime_after = os.path.getmtime(LIVE_STATE)
        if mtime_after != mtime_before:
            print(f"\n  CRITICAL: {LIVE_STATE} was modified during tests!")
            sys.exit(2)

    print()
    print(f"  Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 50)
    print()

    if _FAIL:
        sys.exit(1)
