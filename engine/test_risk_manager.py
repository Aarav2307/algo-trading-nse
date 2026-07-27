"""
engine/test_risk_manager.py — Tests for RiskManager.to_state()/resume_from_state(),
added during the Jul 21 architecture review to replace paper_trading/signal_runner.py's
direct private-attribute pokes (rm._entry_price, rm._bars_since_entry, ...) that were
the only way to resume a position mid-trade across a process restart.
"""
import math

import pandas as pd

from engine.risk_manager import RiskManager

_CONFIG = {
    "hard_stop_pct":           -0.20,
    "atr_period":               5,
    "atr_multiplier":           3.0,
    "max_bars_held":            60,
    "round_number_offset_pct":  0.01,
    "enable_layer_1":           True,
    "enable_layer_2":           True,
    "enable_layer_3":           True,
    "enable_layer_4":           False,   # keep chandelier math simple/predictable
}


def _make_history(closes: list[float]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    return pd.DataFrame({
        "open":  closes,
        "high":  [c * 1.01 for c in closes],
        "low":   [c * 0.99 for c in closes],
        "close": closes,
    })


def test_to_state_round_trips_through_resume_from_state():
    rm = RiskManager(_CONFIG)
    rm.on_position_open(entry_price=100.0, entry_date="2026-06-01",
                         ohlcv_history=_make_history([100.0]))

    hist = _make_history([100.0])
    for c in [101, 102, 103, 104, 105, 106]:
        hist = pd.concat([hist, _make_history([c])], ignore_index=True)
        rm.check_exit(hist.iloc[-1], hist)

    state   = rm.to_state()
    resumed = RiskManager.resume_from_state(_CONFIG, **state)

    assert resumed._entry_price              == rm._entry_price
    assert resumed._entry_date               == rm._entry_date
    assert resumed._bars_since_entry          == rm._bars_since_entry
    assert resumed._highest_high_since_entry  == rm._highest_high_since_entry
    assert resumed._chandelier_stop           == rm._chandelier_stop


def test_resume_from_state_does_not_reset_bars_since_entry():
    """The whole point of resume_from_state(): unlike on_position_open(), it
    must NOT reset bars_since_entry to 0 — that's what made the private-
    attribute workaround necessary in the first place."""
    resumed = RiskManager.resume_from_state(
        _CONFIG,
        entry_price=100.0,
        entry_date="2026-06-01",
        bars_since_entry=11,
        highest_high_since_entry=110.0,
        chandelier_stop=95.0,
    )
    assert resumed._bars_since_entry == 11


def test_chandelier_stop_none_round_trips_to_negative_infinity():
    """JSON null (chandelier not yet computed — ATR warm-up) must map to
    -inf internally, matching a freshly-opened position, and back to None
    via to_state()."""
    resumed = RiskManager.resume_from_state(
        _CONFIG,
        entry_price=100.0,
        entry_date="2026-06-01",
        bars_since_entry=1,
        highest_high_since_entry=101.0,
        chandelier_stop=None,
    )
    assert math.isinf(resumed._chandelier_stop)
    assert resumed.to_state()["chandelier_stop"] is None


def test_resumed_risk_manager_continues_check_exit_correctly():
    """A RiskManager resumed mid-trade must behave identically to one that
    never left memory — the actual bug class this guards against is a
    process restart silently resetting RM state."""
    rm = RiskManager(_CONFIG)
    rm.on_position_open(entry_price=100.0, entry_date="2026-06-01",
                         ohlcv_history=_make_history([100.0]))

    hist = _make_history([100.0])
    for c in [99, 98, 97, 96, 95]:
        hist = pd.concat([hist, _make_history([c])], ignore_index=True)
        rm.check_exit(hist.iloc[-1], hist)

    # Simulate a process restart: serialize, then rebuild a fresh instance.
    state   = rm.to_state()
    resumed = RiskManager.resume_from_state(_CONFIG, **state)

    # Continue both on one more bar — they must agree exactly.
    hist = pd.concat([hist, _make_history([94])], ignore_index=True)
    decision_original = rm.check_exit(hist.iloc[-1], hist)
    decision_resumed  = resumed.check_exit(hist.iloc[-1], hist)

    assert decision_original == decision_resumed
    assert rm.to_state() == resumed.to_state()
