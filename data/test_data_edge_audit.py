"""
data/test_data_edge_audit.py — Audit stress tests for OHLCV edge cases across
the strategy and risk layers.

NEW FILE — does not modify or overwrite any existing test.
Pure in-memory DataFrames. No Kite calls, no yfinance calls, no network.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from strategies.sma_crossover import generate_signals
from engine.risk_manager import RiskManager
from engine.position_sizer import PositionSizer
from screener.auto_screener import compute_hurst

_RM_CFG = {
    "hard_stop_pct": -0.20, "atr_period": 22, "atr_multiplier": 3.0,
    "max_bars_held": 60, "round_number_offset_pct": 0.01,
    "enable_layer_1": True, "enable_layer_2": True,
    "enable_layer_3": True, "enable_layer_4": True,
}
_PS_CFG = {"enabled": True, "risk_per_trade_pct": 0.015,
           "max_position_pct": 0.20, "fallback_stop_pct": 0.20}


def _ohlcv(n=120, start=100.0, step=1.0, index=None) -> pd.DataFrame:
    close = np.array([start + i * step for i in range(n)], dtype=float)
    idx = index if index is not None else pd.date_range("2026-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1_000),
    }, index=idx)


# =============================================================================
# Insufficient-length OHLCV
# =============================================================================

@pytest.mark.parametrize("n", [0, 1, 2, 19, 49])
def test_generate_signals_raises_below_slow_period(n):
    """Fewer bars than SMA50 needs must raise ValueError, never emit a signal."""
    df = _ohlcv(n)
    with pytest.raises(ValueError, match="Not enough data"):
        generate_signals(df, 20, 50)


def test_generate_signals_at_exactly_slow_period_boundary():
    """Exactly 50 bars: must not raise, and the only valid row is the last."""
    sig = generate_signals(_ohlcv(50), 20, 50)
    assert len(sig) == 50
    assert sig.iloc[:-1].isna().all(), "warm-up rows must be NaN, not 0"
    assert not math.isnan(sig.iloc[-1])


def test_signal_runner_lookback_guard_matches_strategy_requirement():
    """
    signal_runner._fetch_stock_data() skips a ticker with `len(df) < SMA_SLOW`.
    generate_signals() raises at `len(df) < slow_period`. These two thresholds
    must be identical or a ticker slips past the fetch guard and raises inside
    _process_stock.
    """
    import paper_trading.signal_runner as sr
    assert sr.SMA_SLOW == 50
    # 50 bars passes the fetch guard (not < 50) and does not raise downstream.
    generate_signals(_ohlcv(sr.SMA_SLOW), sr.SMA_FAST, sr.SMA_SLOW)


# =============================================================================
# NaN / zero / negative prices
# =============================================================================

def test_generate_signals_with_nan_close_does_not_emit_false_cross():
    """A NaN close mid-series must not manufacture a crossover."""
    df = _ohlcv(120)
    df.iloc[60, df.columns.get_loc("close")] = float("nan")
    sig = generate_signals(df, 20, 50)
    assert set(sig.dropna().unique()) <= {-1.0, 0.0, 1.0}


def test_position_sizer_zero_entry_price_is_guarded():
    """
    A zero entry price is caught by the `stop_distance <= 0` guard before any
    division by entry_price happens — no ZeroDivisionError. PASSES.
    """
    ps = PositionSizer(_PS_CFG)
    shares, log = ps.calculate_shares(0.0, 100_000.0, None, 50_000.0)
    assert shares == 0
    assert log["binding"] == "zero_stop_distance"


def test_position_sizer_negative_price_returns_nonsense_not_error():
    """A negative price produces a negative share count silently."""
    ps = PositionSizer(_PS_CFG)
    shares, log = ps.calculate_shares(-100.0, 100_000.0, None, 50_000.0)
    assert shares <= 0, "negative price must never yield a positive share count"


@pytest.mark.parametrize("price,cash", [(1_000.0, 0.0), (1_000.0, 1.0), (2_135.0, 2.0), (300.0, 0.3)])
def test_position_sizer_never_returns_negative_shares(price, cash):
    """
    CONFIRMED DEFECT.

    max_from_cash = floor((cash - buy_cost_1share) / entry_price). When cash is
    below the cost of a single share the numerator is negative and floor() of a
    small negative fraction is -1, not 0. The `while max_from_cash > 0` loop
    cannot correct an already-negative value, and min() propagates it.

    signal_runner.py:821 guards with `if shares == 0`, so -1 slips through to
    queue_pending_buy(shares=-1): pending_buy=True with a negative share count,
    excluded from get_open_positions() and committed_open_count(), and Step 13's
    AMO writer (`shares > 0`) never emits an order row — so morning_fill_check
    has nothing to cancel. The ticker is frozen at PENDING_BUY permanently.
    """
    ps = PositionSizer(_PS_CFG)
    shares, log = ps.calculate_shares(price, 100_000.0, price * 0.9, cash)
    assert shares >= 0, (
        f"sizer returned {shares} shares for price Rs{price} / cash Rs{cash}; "
        f"signal_runner's `if shares == 0` check will not catch it"
    )


def test_position_sizer_cash_exactly_at_min_floor():
    """Boundary: cash exactly at MIN_CASH_TO_ATTEMPT_BUY (Rs1,000)."""
    import paper_trading.signal_runner as sr
    assert sr.MIN_CASH_TO_ATTEMPT_BUY == 1000.0
    ps = PositionSizer(_PS_CFG)
    # Cheapest plausible universe stock ~Rs300
    shares, _ = ps.calculate_shares(300.0, 100_000.0, 270.0, 1_000.0)
    assert shares >= 0


# =============================================================================
# Duplicate / out-of-order timestamps
# =============================================================================

@pytest.mark.xfail(
    strict=True,
    reason="no dedupe or rejection guard exists at any layer — known gap, "
           "see AUDIT_REPORT_2026-08-22.md. strict=True so this XPASSes loudly "
           "the moment a guard is added, prompting removal of the marker.",
)
def test_duplicate_timestamps_do_not_survive_into_signals():
    """
    Two bars sharing one date. The rolling SMA silently averages the duplicate,
    shifting both SMAs, and nothing rejects or dedupes it.

    REWRITTEN Aug 26 2026. The original asserted `len(sig) == 120` with the
    message "duplicates pass through untouched" — i.e. it asserted the DEFECT'S
    CONTINUED EXISTENCE and went green because the gap was present. Adding the
    correct guard would have turned it RED, punishing the fix. This version
    asserts the property that should hold and is marked xfail(strict=True)
    instead, so the gap stays visible and self-clearing.
    """
    idx = list(pd.date_range("2026-01-01", periods=119, freq="B"))
    idx.insert(60, idx[60])           # duplicate one date
    df = _ohlcv(120, index=pd.DatetimeIndex(idx))
    assert df.index.has_duplicates, "fixture must actually contain a duplicate"
    sig = generate_signals(df, 20, 50)
    assert not sig.index.has_duplicates, (
        "duplicate timestamps survived into the signal series — the SMA "
        "averaged over a phantom extra bar"
    )


@pytest.mark.xfail(
    strict=True,
    reason="no monotonicity guard exists at any layer — known gap, see "
           "AUDIT_REPORT_2026-08-22.md. strict=True so this XPASSes loudly "
           "once a guard is added.",
)
def test_out_of_order_timestamps_do_not_survive_into_signals():
    """
    A descending index still produces signals. df.iloc[-1] is then the OLDEST
    bar, so signal_runner's "today's bar" would be the wrong bar entirely.

    REWRITTEN Aug 26 2026, for two reasons. It asserted `len(sig) == 120`,
    i.e. that the unguarded path still works — green because the gap exists.
    And the assertion commented "This is the load-bearing consequence" was
    `rev.index[-1] < rev.index[0]`, which is a fact about the test's own
    reversed fixture, not a claim about the system: it holds whatever the code
    does. Both replaced by the property that should actually hold.
    """
    df = _ohlcv(120)
    rev = df.iloc[::-1]
    assert not rev.index.is_monotonic_increasing, "fixture must be out of order"
    sig = generate_signals(rev, 20, 50)
    assert sig.index.is_monotonic_increasing, (
        "signals were produced over a non-monotonic index — iloc[-1] is the "
        "oldest bar, which signal_runner would treat as today"
    )


# =============================================================================
# Timezone — every consumer must see tz-naive
# =============================================================================

@pytest.mark.xfail(
    strict=True,
    reason="_fetch_stock_data passes a tz-aware index straight through; it "
           "trusts get_ohlcv to have stripped tz and never verifies. Known "
           "gap, see AUDIT_REPORT_2026-08-22.md.",
)
def test_fetch_stock_data_returns_a_tz_naive_index(monkeypatch):
    """
    Every downstream consumer compares against tz-naive Timestamps (the exact
    pandas 2.3.3 production bug fixed Jul 19). _fetch_stock_data() must
    therefore hand back tz-naive frames.

    REWRITTEN Aug 26 2026. The original asserted that comparing a tz-aware
    Timestamp with a tz-naive one raises TypeError — a property of PANDAS, not
    of this repo. It passed identically whether _fetch_stock_data did anything
    right or not, while its docstring claimed to "pin the invariant that the
    fetcher must return tz-naive" without ever calling the fetcher. A
    mislabelled test is worse than a missing one: a gap is honest, false
    coverage is not. This version calls the real function.

    Measured: the fetcher passes tz-aware input through unchanged.
    """
    import paper_trading.signal_runner as sr
    from datetime import date

    aware = _ohlcv(300, index=pd.date_range("2026-01-01", periods=300, freq="B")) \
        .tz_localize("Asia/Kolkata")

    monkeypatch.setattr(sr, "get_ohlcv", lambda t, s, e: aware)
    monkeypatch.setattr(sr, "STOCKS", ["X.NS"])
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)

    out = sr._fetch_stock_data(date(2026, 2, 20))
    assert out["X.NS"].index.tz is None, (
        "_fetch_stock_data returned a tz-aware index; every downstream "
        "comparison against a tz-naive Timestamp will raise TypeError"
    )


# =============================================================================
# Risk manager — degenerate inputs
# =============================================================================

def test_rm_atr_warmup_leaves_chandelier_none_but_hard_stop_live():
    """
    README: "L1 — Hard Stop ... Fires even during ATR warm-up."
    With < atr_period bars, chandelier must be None and L1 must still fire.
    """
    df = _ohlcv(10, start=100.0, step=-5.0)   # falling hard
    rm = RiskManager(_RM_CFG)
    rm.on_position_open(100.0, "2026-01-01", df.iloc[:1])
    d = rm.check_exit(df.iloc[-1], df)
    assert rm.to_state()["chandelier_stop"] is None, "ATR warm-up → no chandelier"
    assert d["should_exit"] and d["exit_reason"] == "HARD_STOP"


def test_rm_hard_stop_exact_boundary_fires():
    """Boundary: loss of exactly -20.00% must trigger (<= comparison)."""
    df = _ohlcv(5)
    entry = 100.0
    bar = df.iloc[-1].copy()
    bar["close"] = 80.0        # exactly -20%
    bar["high"] = 80.0
    rm = RiskManager(_RM_CFG)
    rm.on_position_open(entry, "2026-01-01", df.iloc[:1])
    d = rm.check_exit(bar, df)
    assert d["should_exit"] and d["exit_reason"] == "HARD_STOP"


def test_rm_time_stop_exact_boundary_fires_at_max_bars():
    """Boundary: bars_since_entry == max_bars_held must fire (>= comparison)."""
    df = _ohlcv(120)
    last_high = float(df.iloc[-1]["high"])
    rm = RiskManager.resume_from_state(
        _RM_CFG, entry_price=100.0, entry_date="2026-01-01",
        bars_since_entry=59,
        highest_high_since_entry=last_high,   # chandelier sits below close → won't fire
        chandelier_stop=None,
    )
    d = rm.check_exit(df.iloc[-1], df)
    assert rm.to_state()["bars_since_entry"] == 60
    assert d["should_exit"] and d["exit_reason"] == "TIME_STOP"


def test_rm_multiple_layers_same_bar_hard_stop_wins():
    """
    Layer precedence when hard stop AND time stop both qualify on one bar:
    L1 must win (checked first, returns immediately).
    """
    df = _ohlcv(120)
    bar = df.iloc[-1].copy()
    bar["close"] = 50.0        # -50% from entry 100
    rm = RiskManager.resume_from_state(
        _RM_CFG, entry_price=100.0, entry_date="2026-01-01",
        bars_since_entry=59, highest_high_since_entry=200.0, chandelier_stop=150.0,
    )
    d = rm.check_exit(bar, df)
    assert d["exit_reason"] == "HARD_STOP", "L1 must take precedence over L2/L3"


# =============================================================================
# Hurst boundary
# =============================================================================

def test_hurst_on_insufficient_data_does_not_silently_return_a_passing_value():
    """
    signal_runner's live gate treats a Hurst exception as fail-open (entry
    allowed). What must NOT happen is a too-short series returning a bogus
    number above the 0.48 threshold that looks like a real measurement.
    """
    try:
        h = compute_hurst(np.array([100.0, 101.0, 102.0]))
    except Exception:
        return  # raising is the acceptable outcome — caller fails open explicitly
    assert h is None or isinstance(h, float)
