"""
Strategy: Simple Moving Average (SMA) Crossover

Logic:
  - BUY  (+1) when the fast SMA (default 50-day) crosses ABOVE the slow SMA (200-day).
    This is called a "Golden Cross" — historically bullish.
  - SELL (-1) when the fast SMA crosses BELOW the slow SMA.
    This is called a "Death Cross" — historically bearish.
  - HOLD  (0) on all other bars.

This module is purely signal logic. It takes a DataFrame and returns a signal
Series. It knows nothing about orders, cash, or portfolio state — that's the
backtester's job. This makes it easy to test in isolation and reuse with any
data source.

Why SMA crossover as a first strategy?
  - Simple and well-understood
  - Trend-following: works in sustained bull/bear markets
  - Weaknesses: whipsaws in sideways/choppy markets, lags by design
"""

import numpy as np
import pandas as pd


def generate_signals(df: pd.DataFrame, fast_period: int = 50, slow_period: int = 200) -> pd.Series:
    """
    Compute SMA crossover signals on OHLCV data.

    Args:
        df:           DataFrame with at least a 'close' column, DatetimeIndex
        fast_period:  lookback for the fast (short) moving average
        slow_period:  lookback for the slow (long) moving average

    Returns:
        pd.Series of floats indexed like df:
            +1  → enter long (buy)
            -1  → exit long (sell)
             0  → no action (SMA valid, no crossover today)
            NaN → not yet evaluable (slow SMA still warming up)

    Note: The first (slow_period - 1) rows have NaN SMAs and emit NaN, not 0
    — a real "no signal, valid data" HOLD is distinguishable from "can't
    evaluate yet." This makes first_valid_index() (used by engine/backtester.py
    to anchor the benchmark's buy-and-hold start) return the first bar the
    strategy could actually have acted on, not always index 0.
    """
    if slow_period <= fast_period:
        raise ValueError("slow_period must be greater than fast_period")
    if len(df) < slow_period:
        raise ValueError(
            f"Not enough data: need at least {slow_period} rows, got {len(df)}"
        )

    fast_sma = df["close"].rolling(window=fast_period).mean()
    slow_sma = df["close"].rolling(window=slow_period).mean()

    # Boolean: is fast SMA currently above slow SMA?
    fast_above = fast_sma > slow_sma

    # A crossover happens when today's relationship differs from yesterday's.
    # shift(1) gives yesterday's value; compare element-wise.
    crossed_above = fast_above & ~fast_above.shift(1, fill_value=False)   # golden cross
    crossed_below = ~fast_above & fast_above.shift(1, fill_value=False)   # death cross

    signals = pd.Series(np.nan, index=df.index, name="signal")
    signals[slow_sma.notna()] = 0
    signals[crossed_above] = 1
    signals[crossed_below] = -1

    n_buys = (signals == 1).sum()
    n_sells = (signals == -1).sum()
    print(f"[strategy] SMA({fast_period}/{slow_period}): {n_buys} buy signals, {n_sells} sell signals")

    return signals
