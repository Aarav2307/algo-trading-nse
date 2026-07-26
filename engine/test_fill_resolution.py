"""
engine/test_fill_resolution.py — Regression tests for engine/fill_resolution.py.

Root problem this closes: the AMO fill-or-miss test and the GAP_EXIT-vs-requeue
classification used to live only as inline logic inside
paper_trading/morning_fill_check.py's big processing loop -- not even a
function of their own. paper_trading/test_gap_breaker.py's tests 7 and 8
had to hand-copy that inline logic into the test itself to exercise it,
meaning they tested a copy, not the real code path. engine/backtester.py,
which has no fill-limit concept at all, had no way to reuse any of it.

These tests exercise the extracted pure functions directly with plain
scalars -- no portfolio, no CSV, no I/O.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from engine.fill_resolution import check_circuit_breaker, is_fill_hit, classify_missed_sell


# =============================================================================
# is_fill_hit()
# =============================================================================

def test_buy_fills_when_open_at_or_below_limit():
    assert is_fill_hit("BUY", limit_price=100.0, open_price=99.0) is True
    assert is_fill_hit("BUY", limit_price=100.0, open_price=100.0) is True   # boundary: exactly at limit fills


def test_buy_misses_when_open_above_limit():
    assert is_fill_hit("BUY", limit_price=100.0, open_price=100.01) is False


def test_sell_fills_when_open_at_or_above_limit():
    assert is_fill_hit("SELL", limit_price=100.0, open_price=101.0) is True
    assert is_fill_hit("SELL", limit_price=100.0, open_price=100.0) is True   # boundary: exactly at limit fills


def test_sell_misses_when_open_below_limit():
    assert is_fill_hit("SELL", limit_price=100.0, open_price=99.99) is False


# =============================================================================
# classify_missed_sell()
# =============================================================================

def test_large_gap_classifies_as_gap_exit():
    # limit=3680, open=3400 -> gap_magnitude = 7.6% > 3% threshold
    result = classify_missed_sell(
        limit_price=3680.0, open_price=3400.0,
        gap_breaker_threshold=0.03, is_managed_exit=True,
    )
    assert result == "GAP_EXIT"


def test_large_gap_is_gap_exit_regardless_of_managed_status():
    """GAP_EXIT fires on gap size alone -- a large gap on an unmanaged exit
    still must not be silently left with no automatic action."""
    result = classify_missed_sell(
        limit_price=3680.0, open_price=3400.0,
        gap_breaker_threshold=0.03, is_managed_exit=False,
    )
    assert result == "GAP_EXIT"


def test_small_gap_managed_exit_requeues():
    # limit=3680, open=3620 -> gap_magnitude = 1.63% < 3% threshold
    result = classify_missed_sell(
        limit_price=3680.0, open_price=3620.0,
        gap_breaker_threshold=0.03, is_managed_exit=True,
    )
    assert result == "REQUEUE"


def test_small_gap_unmanaged_exit_is_unmanaged_miss():
    result = classify_missed_sell(
        limit_price=3680.0, open_price=3620.0,
        gap_breaker_threshold=0.03, is_managed_exit=False,
    )
    assert result == "UNMANAGED_MISS"


def test_gap_exactly_at_threshold_does_not_trigger_gap_exit():
    """Strictly > threshold required to fire GAP_EXIT (mirrors the original
    inline `if gap_magnitude > GAP_BREAKER_THRESHOLD`) -- exactly-at-threshold
    must fall through to the managed/unmanaged classification instead."""
    limit_price = 100.0
    open_price  = 97.0   # gap_magnitude = 3.0 / 100.0 = exactly 0.03
    result = classify_missed_sell(
        limit_price=limit_price, open_price=open_price,
        gap_breaker_threshold=0.03, is_managed_exit=True,
    )
    assert result == "REQUEUE", "gap exactly at threshold must not classify as GAP_EXIT"


def test_gap_just_above_threshold_triggers_gap_exit():
    limit_price = 100.0
    open_price  = 96.99   # gap_magnitude = 3.01% > 3%
    result = classify_missed_sell(
        limit_price=limit_price, open_price=open_price,
        gap_breaker_threshold=0.03, is_managed_exit=True,
    )
    assert result == "GAP_EXIT"


# =============================================================================
# check_circuit_breaker()
# =============================================================================

def test_circuit_breaker_below_20_percent_not_flagged():
    flagged, msg = check_circuit_breaker("TEST", open_price=119.9, prev_close=100.0)
    assert flagged is False
    assert msg == ""


def test_circuit_breaker_upper_at_exactly_20_percent():
    flagged, msg = check_circuit_breaker("TEST", open_price=120.0, prev_close=100.0)
    assert flagged is True
    assert "upper" in msg


def test_circuit_breaker_lower_direction():
    flagged, msg = check_circuit_breaker("TEST", open_price=80.0, prev_close=100.0)
    assert flagged is True
    assert "lower" in msg


def test_circuit_breaker_zero_prev_close_guard():
    flagged, msg = check_circuit_breaker("TEST", open_price=100.0, prev_close=0.0)
    assert flagged is False
    assert msg == ""
