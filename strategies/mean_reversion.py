"""
Strategy: RSI + Bollinger Band Mean Reversion

Logic:
  - BUY  (+1) when RSI < 30 AND close <= lower Bollinger Band on the same bar.
    Both conditions must fire simultaneously — this identifies a stock that is
    oversold by momentum (RSI) AND statistically extended below its rolling mean
    (BB). The intersection is a high-conviction "exhaustion" signal.

  - SELL (-1) when RSI > 70 AND close >= upper Bollinger Band on the same bar.
    Mirror condition: overbought by momentum AND price stretched above the mean.

  - HOLD  (0) on all other bars.

  Signals fire only on the TRANSITION day — the first bar the combined condition
  becomes True after it was False. This prevents the same entry/exit zone from
  emitting dozens of consecutive signals during an extended extreme episode.

Why require BOTH conditions simultaneously?
  Either condition alone produces too many noise signals:
    - RSI < 30 on a trending stock just means it's falling hard — price keeps going.
    - Price below lower BB can persist for weeks in a genuine downtrend.
  Their intersection filters for short-term exhaustion rather than momentum
  continuation: the price has moved so fast that BOTH a momentum indicator AND a
  statistical band are simultaneously saturated. That combination historically
  precedes a snap-back toward the mean on low-beta, mean-reverting stocks.

Ideal market regime: range-bound or slow-drifting stocks with low beta.
Weakness: in a strong downtrend the RSI+BB entry fires early and price keeps
falling — the strategy can stack up repeated small losses before the bounce.
"""

import pandas as pd
import numpy as np

# ── Strategy parameters ───────────────────────────────────────────────────────
# Change values here; do not modify the logic below.
PARAMS: dict = {
    "rsi_period":     14,   # Wilder's standard RSI lookback in trading days
    "bb_period":      20,   # Bollinger Band rolling window (days)
    "bb_std_dev":      2,   # Band width in standard deviations (~95% of price action)
    "rsi_oversold":   30,   # RSI level below which a stock is considered oversold
    "rsi_overbought": 70,   # RSI level above which a stock is considered overbought
}


# ── Indicator helpers (pure functions, no side-effects) ───────────────────────

def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """
    Compute Wilder's RSI (Relative Strength Index).

    Step-by-step:
      1. delta[t]    = close[t] - close[t-1]       daily price change
      2. gain[t]     = max(delta[t], 0)             only up-days count
      3. loss[t]     = max(-delta[t], 0)            only down-days count (positive)
      4. avg_gain/loss via Wilder's EWM: alpha=1/period, adjust=False
         This is mathematically identical to the recursive formula:
         avg[t] = (avg[t-1] × (period-1) + value[t]) / period
      5. RS          = avg_gain / avg_loss
      6. RSI         = 100 − (100 / (1 + RS))

    Result range: 0–100.
    RSI < 30 → oversold (too many down-days relative to up-days recently).
    RSI > 70 → overbought (too many up-days).
    """
    delta = close.diff()                          # NaN on first row by design

    gain = delta.clip(lower=0)                    # negative changes become 0
    loss = (-delta).clip(lower=0)                 # flip sign; positive changes become 0

    # min_periods=period keeps NaN until we have a full window of data
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    # Replace 0 avg_loss with NaN to avoid division-by-zero on pure up-streaks;
    # those bars will have RSI = 100 after forward-fill below
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    # Pure up-streak: avg_loss=0 → RS=inf → RSI=100 (cap it cleanly)
    rsi = rsi.fillna(100)
    return rsi


def _compute_bollinger_bands(
    close: pd.Series, period: int, n_std: float
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute Bollinger Bands (John Bollinger, 1983).

    Middle band = rolling mean(close, period)            ← the "fair value" anchor
    Upper band  = middle + n_std × rolling stddev        ← ~2σ above mean
    Lower band  = middle − n_std × rolling stddev        ← ~2σ below mean

    With period=20 and n_std=2:
      - The bands span ≈ ±2 standard deviations of the last 20 closes.
      - Under a normal distribution, ~95 % of price action falls inside.
      - A close outside the band is a statistically rare event (≈ 5 % of bars).

    Bands widen during high-volatility periods and contract during low-volatility
    periods (unlike fixed-percentage envelopes), so they self-adjust to market
    conditions.

    Returns: (upper_band, middle_band, lower_band) — all pd.Series, same index.
    """
    mid   = close.rolling(window=period).mean()   # 20-day SMA = middle band
    std   = close.rolling(window=period).std()    # 20-day rolling standard deviation

    upper = mid + n_std * std                     # upper band
    lower = mid - n_std * std                     # lower band

    return upper, mid, lower


# ── Public signal generator ───────────────────────────────────────────────────

def generate_signals(df: pd.DataFrame, params: dict = None) -> pd.Series:
    """
    Compute RSI + Bollinger Band mean reversion signals on OHLCV data.

    Matches the same interface as strategies/sma_crossover.py so the backtester
    engine can call either strategy identically.

    Args:
        df:     DataFrame with at least a 'close' column and a DatetimeIndex.
                Must come from data.fetcher (columns lowercase).
        params: Optional dict to override any keys in module-level PARAMS.
                E.g. {"rsi_oversold": 25} tightens the entry threshold.

    Returns:
        pd.Series of integers indexed identically to df:
            +1  → enter long (RSI < oversold AND close <= lower BB)
            -1  → exit long  (RSI > overbought AND close >= upper BB)
             0  → hold / no action

        Signals fire only on the FIRST bar a combined condition becomes True
        (transition), not on every bar it remains True.
    """
    p = PARAMS if params is None else {**PARAMS, **params}

    min_bars = max(p["rsi_period"], p["bb_period"]) + 5
    if len(df) < min_bars:
        raise ValueError(
            f"Not enough data: need at least {min_bars} bars, got {len(df)}"
        )

    close = df["close"]

    # ── Compute indicators ────────────────────────────────────────────────
    rsi                     = _compute_rsi(close, p["rsi_period"])
    bb_upper, _, bb_lower   = _compute_bollinger_bands(
                                  close, p["bb_period"], p["bb_std_dev"]
                              )

    # ── Combined condition booleans ───────────────────────────────────────
    # True on every bar where BOTH sub-conditions hold simultaneously.
    in_oversold_zone   = (rsi < p["rsi_oversold"])   & (close <= bb_lower)
    in_overbought_zone = (rsi > p["rsi_overbought"]) & (close >= bb_upper)

    # ── Transition detection ──────────────────────────────────────────────
    # Fire +1 only on the day the combined oversold zone is ENTERED:
    #   today is in the zone  AND  yesterday was NOT in the zone.
    # shift(1) gets yesterday's boolean; fillna(False) handles the first bar.
    just_entered_oversold   = in_oversold_zone   & (~in_oversold_zone.shift(1).fillna(False))
    just_entered_overbought = in_overbought_zone & (~in_overbought_zone.shift(1).fillna(False))

    signals = pd.Series(0, index=df.index, name="signal")
    signals[just_entered_oversold]   =  1
    signals[just_entered_overbought] = -1

    n_buys  = (signals ==  1).sum()
    n_sells = (signals == -1).sum()
    print(
        f"[strategy] RSI({p['rsi_period']})+BB({p['bb_period']},{p['bb_std_dev']}σ): "
        f"{n_buys} buy signals, {n_sells} sell signals"
    )

    return signals
