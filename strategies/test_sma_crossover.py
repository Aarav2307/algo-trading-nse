"""
strategies/test_sma_crossover.py

Tests for generate_signals() in strategies/sma_crossover.py.

Regression coverage (2026-08-07): signals used to be initialized with 0
instead of NaN, so first_valid_index() (used by engine/backtester.py to
anchor the benchmark's buy-and-hold start date) always trivially returned
the very first row, regardless of whether the SMA was actually valid there.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from strategies.sma_crossover import generate_signals


def _trending_df(n=30, start="2020-01-01"):
    """Rises then falls — produces one golden cross, then one death cross."""
    idx = pd.date_range(start, periods=n, freq="B")
    up   = list(range(100, 100 + n // 2))
    down = list(range(100 + n // 2 - 1, 100 + n // 2 - 1 - (n - n // 2), -1))
    price = up + down
    return pd.DataFrame({"close": price[:n]}, index=idx)


def test_warmup_rows_are_nan_not_zero():
    """Rows before slow_sma is valid (first slow_period-1 rows) must be NaN,
    not 0 — that's the actual bug: 0 is indistinguishable from a real HOLD."""
    df = _trending_df(n=30)
    signals = generate_signals(df, fast_period=3, slow_period=10)

    warmup = signals.iloc[:9]   # slow_period=10 → first 9 rows have NaN slow_sma
    assert warmup.isna().all(), "warm-up rows must be NaN, not a numeric HOLD value"


def test_first_valid_index_is_not_always_row_zero():
    """first_valid_index() must reflect where the slow SMA actually becomes
    valid, not trivially index 0 (the bug this test guards against)."""
    df = _trending_df(n=30)
    signals = generate_signals(df, fast_period=3, slow_period=10)

    assert signals.first_valid_index() == df.index[9], (
        "first_valid_index() should be the 10th bar (0-indexed 9), where the "
        "10-period slow SMA first becomes computable"
    )
    assert signals.first_valid_index() != df.index[0]


def test_post_warmup_hold_rows_are_zero_not_nan():
    """Once slow_sma is valid, a non-crossover bar must still read 0 (a real
    HOLD), not NaN — NaN is reserved for the genuine warm-up period only."""
    df = _trending_df(n=30)
    signals = generate_signals(df, fast_period=3, slow_period=10)

    post_warmup = signals.iloc[9:]
    assert post_warmup.notna().all(), "every bar once SMA is valid must have a real signal value"


def test_crossover_values_unaffected():
    """The actual crossover detection (+1 golden cross, -1 death cross) must
    be byte-identical to before — this fix only changes the warm-up rows."""
    df = _trending_df(n=30)
    signals = generate_signals(df, fast_period=3, slow_period=10)

    assert (signals == 1).sum() == 1, "expected exactly one golden cross"
    assert (signals == -1).sum() == 1, "expected exactly one death cross"


def test_last_row_always_numeric_when_data_sufficient():
    """paper_trading/signal_runner.py does int(signals.iloc[-1]) unconditionally
    whenever generate_signals() doesn't raise — the last row must never be NaN
    when len(df) >= slow_period (generate_signals raises ValueError otherwise)."""
    df = _trending_df(n=30)
    signals = generate_signals(df, fast_period=3, slow_period=10)

    assert pd.notna(signals.iloc[-1])
    assert int(signals.iloc[-1]) in (-1, 0, 1)


def test_insufficient_data_still_raises():
    """Unrelated to this fix — confirm the pre-existing insufficient-data
    guard is untouched."""
    df = _trending_df(n=5)
    with pytest.raises(ValueError, match="Not enough data"):
        generate_signals(df, fast_period=3, slow_period=10)


def test_flat_price_all_hold_after_warmup():
    """A perfectly flat price series (fast == slow always) must never cross —
    every post-warmup row should be 0, none NaN, none +-1."""
    idx = pd.date_range("2020-01-01", periods=20, freq="B")
    df = pd.DataFrame({"close": [100.0] * 20}, index=idx)
    signals = generate_signals(df, fast_period=3, slow_period=10)

    assert signals.iloc[:9].isna().all()
    assert (signals.iloc[9:] == 0).all()
