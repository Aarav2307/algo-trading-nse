"""
screener/regime_classifier.py — Professional Regime Classifier

Classifies NSE stocks into one of four market regimes using two independent,
industry-standard metrics:

    Hurst Exponent (H) — long-range memory of the price series (3-year window)
        H < 0.5  →  mean-reverting  (price oscillates around a central value)
        H ≈ 0.5  →  random walk     (no exploitable pattern)
        H > 0.5  →  trending        (price tends to continue in the same direction)

    ADX — Average Directional Index (14-period, Wilder, full-window median)
        ADX < 20  →  no trend / choppy / ranging
        ADX 25–40 →  clear trend present
        ADX > 40  →  strong trend, often near exhaustion

Regime grid (2×2):
    ┌──────────────────┬───────────────┬──────────────────────────────────┐
    │ Hurst            │ ADX           │ Regime → Strategy                │
    ├──────────────────┼───────────────┼──────────────────────────────────┤
    │ H > 0.5          │ ADX > 25      │ TRENDING_STRONG  → sma_crossover │
    │ H < 0.5          │ ADX < 20      │ MEAN_REVERTING   → mean_reversion│
    │ H > 0.5          │ ADX < 20      │ COMPOUNDER       → buy_and_hold  │
    │ any other combo  │               │ UNCLASSIFIED     → manual review │
    └──────────────────┴───────────────┴──────────────────────────────────┘

API surface (pure functions, no I/O — ready for FastAPI / Flask endpoint):
    compute_hurst(prices)           → float
    compute_adx(df, period)         → pd.Series
    compute_regime_metrics(df)      → dict
    classify_regime(hurst, adx)     → tuple[str, str]   (regime, strategy)
    classify_universe(...)          → pd.DataFrame
    print_results(df)               → None
    validate_expected(df)           → None
"""

import io
import os
import sys
import warnings
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

# ── Path setup: allow running from project root OR from screener/ ─────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the shared universe list and fetch helpers from sma_screener.
# This avoids duplicating the 73-stock list and the yfinance wrapper logic.
try:
    from screener.sma_screener import UNIVERSE, _fetch_quiet, _fetch_benchmark
except ImportError:
    from sma_screener import UNIVERSE, _fetch_quiet, _fetch_benchmark


# ── Constants ─────────────────────────────────────────────────────────────────

ADX_PERIOD         = 14     # Wilder's standard ADX period
ADX_TREND_THRESH   = 25     # ADX ≥ this → trend present
ADX_NOTCH_THRESH   = 20     # ADX < this → no trend / ranging
HURST_TREND_THRESH = 0.50   # H above this → persistent / trending
# ADX_REGIME_WINDOW removed: previously used tail(252) to create a 1-year ADX
# subset while Hurst used the full window — a deliberate mismatch.
# Now both metrics use the full shared window defined in screener/config.py.

REGIMES = {
    "TRENDING_STRONG": "sma_crossover",
    "MEAN_REVERTING":  "mean_reversion",
    "COMPOUNDER":      "buy_and_hold",
    "UNCLASSIFIED":    "manual_review",
}

# Stocks we've already backtested — expected regime vs empirical result
VALIDATION_CASES: dict[str, str] = {
    "BHEL.NS":     "TRENDING_STRONG",   # +366% on SMA 20/50 in our test
    "VEDL.NS":     "TRENDING_STRONG",   # high vol, deep DD, commodity cycle
    "PNB.NS":      "TRENDING_STRONG",   # PSU bank, β1.46, cyclical
    "TMPV.NS":     "TRENDING_STRONG",   # SMA 20/50 returned +366% vs -5% MR
    "MARICO.NS":   "MEAN_REVERTING",    # MR returned +95%, 100% win rate
    "NESTLEIND.NS":"MEAN_REVERTING",    # MR returned +79%, 100% win rate
    "RELIANCE.NS": "COMPOUNDER",        # slow grind, SMA barely fired
    "HDFCBANK.NS": "COMPOUNDER",        # SMA returned +9% vs B&H +92%
}


# ── Hurst Exponent ─────────────────────────────────────────────────────────────

def compute_hurst(prices: pd.Series) -> float:
    """
    Compute the Hurst Exponent via Rescaled Range (R/S) Analysis.

    ── What the Hurst Exponent measures ─────────────────────────────────────────
    The Hurst Exponent H quantifies the LONG-RANGE MEMORY of a time series —
    whether past moves predict future moves over extended horizons:

        H < 0.5  →  Mean-reverting (anti-persistent)
                     A large up-move tends to be followed by a down-move.
                     The series has a "memory" of reverting to a central level.
                     Example: oscillating FMCG stocks like Marico, Nestle.

        H ≈ 0.5  →  Random walk (no exploitable memory)
                     Past moves give no information about future direction.
                     This is what the Efficient Market Hypothesis predicts.

        H > 0.5  →  Trending (persistent)
                     A large up-move tends to be followed by another up-move.
                     The series "remembers" its direction over long horizons.
                     Example: BHEL during a government capex supercycle.

    ── Algorithm: Rescaled Range (R/S) Analysis ─────────────────────────────────
    Introduced by Harold Edwin Hurst (1951) studying Nile River flood data.

    We apply R/S to LOG RETURNS (not raw prices) because:
      - Raw prices are non-stationary (they drift upward indefinitely)
      - Log returns r[t] = log(P[t]/P[t-1]) are approximately stationary
      - The scaling theory assumes stationarity for H = 0.5 to hold under
        a null hypothesis of random walk

    For a given lag size n, the algorithm is:

      Step 1 — Divide the return series into K = floor(N/n) non-overlapping
               sub-series of length n each.

      Step 2 — For each sub-series [r_1, r_2, ..., r_n]:
               a) Compute the mean:  μ = mean(r_1 .. r_n)
               b) Centre the series: e[i] = r[i] − μ   (mean-adjusted returns)
               c) Build cumulative sums: Y[i] = Σ e[1..i]
                  (Y represents the "path" of cumulative excess return)
               d) R = max(Y) − min(Y)   ← range of the cumulative path
                  (How far did the path wander from start to farthest extreme?)
               e) S = std(r_1 .. r_n)  ← scale (standard deviation)
               f) RS = R / S            ← rescaled range (dimensionless)

      Step 3 — Average RS across all K sub-series of the same length n:
               <RS(n)> = mean of all K RS values

      Step 4 — Repeat Steps 1–3 for many different lag sizes n (log-spaced).

    The key mathematical property:
               <RS(n)>  ∝  n^H

    Step 5 — Take logarithms of both sides:
               log(<RS(n)>) = H × log(n) + constant

    Step 6 — Fit a line to the (log(n), log(<RS>)) scatter plot.
               The slope of this line is the Hurst Exponent H.

    Args:
        prices:  pd.Series of closing prices (not returns), DatetimeIndex.
                 Should span at least 1 year; 3 years recommended for stability.

    Returns:
        Hurst Exponent as a float in (0, 1). Returns 0.5 if calculation fails.
    """
    # Convert prices to log returns: r[t] = log(P[t] / P[t-1])
    # Using log ensures symmetry: a +10% and −10% are equal in magnitude.
    log_returns = np.log(prices / prices.shift(1)).dropna().values
    n_total = len(log_returns)

    if n_total < 100:
        return 0.5   # insufficient data → default to random walk

    # ── Choose lag sizes (log-spaced from 10 to N/4) ─────────────────────────
    # We need enough sub-series at each lag for a stable estimate (at least 2),
    # so the maximum lag is N/4 (gives at least 4 sub-series of that length).
    # Log-spacing ensures we sample small AND large scales equally.
    n_min = 10
    n_max = n_total // 4
    if n_max < n_min:
        return 0.5

    lags = np.unique(
        np.logspace(np.log10(n_min), np.log10(n_max), 20).astype(int)
    )

    log_rs_list  = []   # log(<RS(n)>) for each valid lag
    log_lag_list = []   # log(n)       for each valid lag

    for n in lags:
        n_windows = n_total // n   # number of non-overlapping sub-series
        if n_windows < 2:
            continue

        rs_per_window = []
        for w in range(n_windows):
            sub = log_returns[w * n : (w + 1) * n]   # sub-series of length n

            mu  = sub.mean()                           # Step 2a: sub-series mean
            e   = sub - mu                             # Step 2b: mean-adjusted
            Y   = np.cumsum(e)                         # Step 2c: cumulative sum
            R   = Y.max() - Y.min()                    # Step 2d: range
            S   = sub.std(ddof=1)                      # Step 2e: std deviation

            if S > 1e-10 and R > 0:                    # guard against flat windows
                rs_per_window.append(R / S)            # Step 2f: RS value

        if len(rs_per_window) >= 2:
            avg_rs = np.mean(rs_per_window)            # Step 3: average RS
            log_rs_list.append(np.log(avg_rs))         # Step 5: log(<RS>)
            log_lag_list.append(np.log(n))             # Step 5: log(n)

    if len(log_lag_list) < 4:
        return 0.5   # too few points for a reliable regression

    # ── Step 6: OLS regression slope = H ─────────────────────────────────────
    # np.polyfit(x, y, deg=1) returns [slope, intercept]
    slope, _ = np.polyfit(log_lag_list, log_rs_list, 1)
    return float(np.clip(slope, 0.0, 1.0))   # H ∈ (0, 1) by construction


# ── ADX (Average Directional Index) ───────────────────────────────────────────

def compute_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    """
    Compute ADX (Average Directional Index) using Wilder's original method.

    ── What ADX measures ────────────────────────────────────────────────────────
    ADX measures TREND STRENGTH — not direction. A high ADX means the price is
    trending (up OR down) with conviction. A low ADX means it's ranging/choppy.

    Developed by J. Welles Wilder Jr. in "New Concepts in Technical Trading
    Systems" (1978). Still the gold standard for trend-strength measurement.

        ADX < 20   →  No trend, choppy/sideways market
        ADX 20–25  →  Weak or emerging trend (borderline)
        ADX 25–40  →  Clear trend — ideal for trend-following strategies
        ADX > 40   →  Strong trend — often at extremes, may be nearing end

    ── Step 1: True Range (TR) ───────────────────────────────────────────────────
    TR captures the FULL price range for the day, including gaps from the prior
    close. A gap-up open is a bullish move that simple High−Low misses.

        TR[t] = max(
                    High[t] − Low[t],           ← pure intraday range
                    |High[t] − Close[t−1]|,     ← range if gap-up occurred
                    |Low[t]  − Close[t−1]|      ← range if gap-down occurred
                )

    ── Step 2: Directional Movement (+DM and −DM) ───────────────────────────────
    Captures how much today's range extends BEYOND yesterday's range.

        up_move   = High[t]  − High[t−1]    (how far today beat yesterday's high)
        down_move = Low[t−1] − Low[t]       (how far today undercut yesterday's low)

        +DM[t] = up_move   if (up_move > down_move) AND (up_move > 0)   else 0
        −DM[t] = down_move if (down_move > up  move) AND (down_move > 0) else 0

    If today's range is entirely inside yesterday's (inside bar), both = 0.
    If up_move == down_move, both = 0 (no directional bias).

    ── Step 3: Wilder's Smoothing ────────────────────────────────────────────────
    Wilder used a special recursive EMA (NOT a simple SMA). The formula is:

        Smoothed_0 = sum of first `period` raw values   (seed with first N bars)
        Smoothed[t] = Smoothed[t−1] − (Smoothed[t−1] / period) + Value[t]

    This is mathematically identical to pandas EWM with:
        alpha = 1 / period,  min_periods = period,  adjust = False

    Why not a simple SMA? Wilder wanted more weight on recent data but smoother
    than a standard EMA. His formula gives a fixed 1/period decay per bar.

    ── Step 4: Directional Indicators ───────────────────────────────────────────
        +DI = 100 × (Smoothed_+DM / Smoothed_TR)
        −DI = 100 × (Smoothed_−DM / Smoothed_TR)

    +DI > −DI → uptrend. −DI > +DI → downtrend.
    The GAP between +DI and −DI measures how one-sided the trend is.

    ── Step 5: Directional Movement Index (DX) ──────────────────────────────────
        DX = 100 × |+DI − −DI| / (+DI + −DI)

    DX = 0   when +DI = −DI (balanced, no trend).
    DX = 100 when one DI is zero (pure directional movement).
    DX is volatile bar-to-bar; ADX is its smoothed version.

    ── Step 6: ADX = Wilder's smoothing of DX ───────────────────────────────────
    ADX = Wilder's EWM applied to DX with the same period.
    This is why ADX LAGS — it takes ~period bars after a trend starts for
    ADX to climb above 25. By the time ADX is high, the trend is confirmed
    (not just beginning).

    Args:
        df:      OHLCV DataFrame with columns 'high', 'low', 'close'.
        period:  Wilder's smoothing period (default 14, the standard).

    Returns:
        pd.Series of ADX values, same index as df. NaN for first (2×period) bars.
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    # ── Step 1: True Range ────────────────────────────────────────────────────
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,                 # intraday range
        (high - prev_close).abs(),  # gap-up extension
        (low  - prev_close).abs(),  # gap-down extension
    ], axis=1).max(axis=1)          # TR = largest of the three

    # ── Step 2: Directional Movement ──────────────────────────────────────────
    up_move   = high - high.shift(1)     # today's high vs yesterday's high
    down_move = low.shift(1) - low       # yesterday's low vs today's low

    # +DM: up_move wins AND is positive
    plus_dm  = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm  = pd.Series(plus_dm,  index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    # ── Step 3: Wilder's smoothing ────────────────────────────────────────────
    # pandas EWM with alpha=1/period and adjust=False exactly matches
    # Wilder's recursive formula: W[t] = W[t-1] − W[t-1]/period + X[t]
    wilder_kwargs = dict(alpha=1 / period, min_periods=period, adjust=False)

    tr_smooth  =  tr.ewm(**wilder_kwargs).mean()
    pdm_smooth = plus_dm.ewm(**wilder_kwargs).mean()
    mdm_smooth = minus_dm.ewm(**wilder_kwargs).mean()

    # ── Step 4: Directional Indicators ────────────────────────────────────────
    plus_di  = 100 * pdm_smooth / tr_smooth
    minus_di = 100 * mdm_smooth / tr_smooth

    # ── Step 5: DX ────────────────────────────────────────────────────────────
    di_sum  = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = np.where(di_sum > 0, 100 * di_diff / di_sum, 0.0)
    dx = pd.Series(dx, index=df.index)

    # ── Step 6: ADX = Wilder's smoothing of DX ───────────────────────────────
    adx = dx.ewm(**wilder_kwargs).mean()
    return adx


# ── Regime classification (pure functions) ────────────────────────────────────

def classify_regime(hurst: float, adx_value: float) -> tuple[str, str]:
    """
    Map (Hurst, ADX) pair to a regime label and recommended strategy.

    Uses the 2×2 grid defined at the top of this module. The UNCLASSIFIED bucket
    catches the two "contradictory" combinations:
      - H < 0.5 (mean-reverting price memory) BUT ADX > 25 (strong current trend):
        typically a stock in a one-off momentum spike that will likely revert.
      - H ≈ 0.5 borderline stocks: Hurst is unreliable close to 0.5.

    Args:
        hurst:     Hurst exponent (0 < H < 1)
        adx_value: Median ADX over the classification window

    Returns:
        (regime, strategy) tuple of strings.
    """
    trending   = hurst     >= HURST_TREND_THRESH
    trend_now  = adx_value >= ADX_TREND_THRESH
    range_now  = adx_value <  ADX_NOTCH_THRESH

    if     trending and trend_now:   return ("TRENDING_STRONG", "sma_crossover")
    if not trending and range_now:   return ("MEAN_REVERTING",  "mean_reversion")
    if     trending and range_now:   return ("COMPOUNDER",      "buy_and_hold")
    return ("UNCLASSIFIED", "manual_review")


def compute_regime_metrics(df: pd.DataFrame) -> Optional[dict]:
    """
    Compute Hurst and ADX for a single stock's OHLCV DataFrame.

    This is the function to expose as a single-stock API endpoint.

    Args:
        df: OHLCV DataFrame from data.fetcher (columns: open, high, low, close, volume)

    Returns:
        Dict with keys: hurst, adx_series (full Series), adx_regime (scalar used
        for classification), regime, strategy. None if data is insufficient.
    """
    if len(df) < ADX_PERIOD * 3:
        return None

    hurst      = compute_hurst(df["close"])
    adx_series = compute_adx(df)

    # Use the median of the FULL window's ADX values so that Hurst and ADX
    # are measured over the identical date range (both now come from config.py).
    # Previously this used tail(252) — a 1-year subset — while Hurst used the
    # full window, creating an internal mismatch inside this function itself.
    recent_adx = adx_series.dropna()
    if len(recent_adx) < 30:
        return None
    adx_regime_val = float(recent_adx.median())

    regime, strategy = classify_regime(hurst, adx_regime_val)

    return {
        "hurst":      hurst,
        "adx_series": adx_series,
        "adx_regime": adx_regime_val,
        "regime":     regime,
        "strategy":   strategy,
    }


# ── Universe classifier (orchestrator) ────────────────────────────────────────

def classify_universe(
    universe: dict[str, str],
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    Download data for every ticker, compute regime metrics, and return a
    classified DataFrame. No printing occurs here — purely data pipeline.

    Args:
        universe:  {ticker: sector} dict
        start:     ISO date string, start of window (inclusive)
        end:       ISO date string, end of window (exclusive)

    Returns:
        DataFrame indexed by ticker with columns:
            sector, hurst, adx_regime, regime, strategy
        Sorted by regime then by hurst descending within each regime.
    """
    total   = len(universe)
    records = []
    skipped = []

    for idx, (ticker, sector) in enumerate(universe.items(), 1):
        label = f"[{idx:>2}/{total}] {ticker:<18} ({sector:<14})"
        print(f"  {label}", end="  ", flush=True)

        df = _fetch_quiet(ticker, start, end)
        if df is None:
            print("✗  download failed")
            skipped.append(ticker)
            continue

        metrics = compute_regime_metrics(df)
        if metrics is None:
            print("✗  insufficient data")
            skipped.append(ticker)
            continue

        print(
            f"✓  H={metrics['hurst']:.3f}  ADX={metrics['adx_regime']:>5.1f}"
            f"  →  {metrics['regime']}"
        )
        records.append({
            "ticker":     ticker,
            "sector":     sector,
            "hurst":      metrics["hurst"],
            "adx_regime": metrics["adx_regime"],
            "regime":     metrics["regime"],
            "strategy":   metrics["strategy"],
        })

    if skipped:
        print(f"\n  Skipped {len(skipped)}: {', '.join(skipped)}")

    if not records:
        raise RuntimeError("No stocks produced valid metrics.")

    df_out = pd.DataFrame(records).set_index("ticker")

    # Sort: regime category first (consistent ordering), then Hurst desc within
    regime_order = {"TRENDING_STRONG": 0, "COMPOUNDER": 1,
                    "MEAN_REVERTING": 2, "UNCLASSIFIED": 3}
    df_out["_order"] = df_out["regime"].map(regime_order).fillna(4)
    df_out = (df_out
              .sort_values(["_order", "hurst"], ascending=[True, False])
              .drop(columns="_order"))

    return df_out


# ── Output formatting ─────────────────────────────────────────────────────────

_W = 92   # table width

_REGIME_STYLE = {
    "TRENDING_STRONG": ("▲", "── TRENDING_STRONG  →  strategy: sma_crossover ──"),
    "MEAN_REVERTING":  ("◄►", "── MEAN_REVERTING   →  strategy: mean_reversion ──"),
    "COMPOUNDER":      ("→", "── COMPOUNDER       →  strategy: buy_and_hold ──"),
    "UNCLASSIFIED":    ("?", "── UNCLASSIFIED      →  strategy: manual_review ──"),
}

_HEADER = (
    f"  {'Ticker':<18}  {'Sector':<14}  "
    f"{'Hurst':>7}  {'ADX':>6}  {'Regime':<18}  Strategy"
)


def print_results(df: pd.DataFrame) -> None:
    """
    Print the classified universe grouped by regime, then print a summary
    of regime counts and dominant sectors.
    """
    print("\n" + "=" * _W)
    print("  MARKET REGIME CLASSIFIER — NSE / NIFTY 500 UNIVERSE")
    print("  Hurst Exponent (R/S, 3yr) + ADX-14 (1yr median)")
    print("=" * _W)

    for regime in ["TRENDING_STRONG", "COMPOUNDER", "MEAN_REVERTING", "UNCLASSIFIED"]:
        subset = df[df["regime"] == regime]
        if subset.empty:
            continue

        marker, label = _REGIME_STYLE[regime]
        print(f"\n  {marker}  {label}  ({len(subset)} stocks)")
        print("  " + "─" * (_W - 2))
        print(_HEADER)
        print("  " + "─" * (_W - 2))

        for ticker, row in subset.iterrows():
            # Flag borderline Hurst values (close to 0.5) with a warning marker
            h_flag = " !" if abs(row["hurst"] - 0.5) < 0.03 else "  "
            print(
                f"  {ticker:<18}  {row['sector']:<14}  "
                f"{row['hurst']:>6.3f}{h_flag}  {row['adx_regime']:>6.1f}"
                f"  {row['regime']:<18}  {row['strategy']}"
            )

    # ── Summary: counts ───────────────────────────────────────────────────────
    print("\n" + "─" * _W)
    print("  REGIME SUMMARY")
    print("─" * _W)
    counts = df["regime"].value_counts()
    total  = len(df)
    for regime in ["TRENDING_STRONG", "COMPOUNDER", "MEAN_REVERTING", "UNCLASSIFIED"]:
        n = counts.get(regime, 0)
        bar = "█" * int(n / total * 40)
        print(f"  {regime:<20}  {n:>3} stocks  {bar}  ({n/total*100:.0f}%)")

    # ── Summary: sector patterns ──────────────────────────────────────────────
    print("\n" + "─" * _W)
    print("  SECTOR BREAKDOWN BY REGIME")
    print("─" * _W)
    pivot = (
        df.groupby(["sector", "regime"])
        .size()
        .unstack(fill_value=0)
    )
    # Ensure all regime columns are present
    for col in ["TRENDING_STRONG", "COMPOUNDER", "MEAN_REVERTING", "UNCLASSIFIED"]:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot[["TRENDING_STRONG", "COMPOUNDER", "MEAN_REVERTING", "UNCLASSIFIED"]]

    # Order sectors by their dominant regime
    pivot["dominant"] = pivot.idxmax(axis=1)
    dom_order = {"TRENDING_STRONG": 0, "COMPOUNDER": 1,
                 "MEAN_REVERTING": 2, "UNCLASSIFIED": 3}
    pivot["_o"] = pivot["dominant"].map(dom_order)
    pivot = pivot.sort_values("_o").drop(columns=["dominant", "_o"])

    sec_h = f"  {'Sector':<16}  {'TRENDING':>9}  {'COMPOUNDER':>11}  {'MEAN_REV':>9}  {'UNCLASS':>8}"
    print(sec_h)
    print("  " + "─" * (_W - 2))
    for sector, row in pivot.iterrows():
        print(
            f"  {sector:<16}  {int(row['TRENDING_STRONG']):>9}"
            f"  {int(row['COMPOUNDER']):>11}"
            f"  {int(row['MEAN_REVERTING']):>9}"
            f"  {int(row['UNCLASSIFIED']):>8}"
        )

    print("=" * _W)


def validate_expected(df: pd.DataFrame) -> None:
    """
    Check whether stocks with known empirical backtest results were classified
    into the regime the backtest evidence suggests they belong to.

    'Expected' regimes come from VALIDATION_CASES — they were set based on
    which strategy won in our prior backtests, not from the classifier.
    A match means the Hurst+ADX signal independently agrees with the empirical
    strategy performance — validating that the classifier has predictive value.
    """
    print("\n" + "─" * _W)
    print("  VALIDATION — EMPIRICAL BACKTEST RESULTS vs CLASSIFIER")
    print("─" * _W)
    print(f"  {'Ticker':<18}  {'Expected':<18}  {'Classified':<18}  {'H':>6}  {'ADX':>6}  Result")
    print("  " + "─" * (_W - 2))

    matches    = 0
    mismatches = 0

    for ticker, expected in VALIDATION_CASES.items():
        if ticker not in df.index:
            print(f"  {ticker:<18}  {expected:<18}  {'NOT IN UNIVERSE':<18}  —  —  ✗ missing")
            mismatches += 1
            continue

        row        = df.loc[ticker]
        classified = row["regime"]
        match      = (classified == expected)

        result_str = "✓ MATCH" if match else "✗ MISMATCH"
        if match:
            matches += 1
        else:
            mismatches += 1

        print(
            f"  {ticker:<18}  {expected:<18}  {classified:<18}"
            f"  {row['hurst']:>6.3f}  {row['adx_regime']:>6.1f}  {result_str}"
        )

    total = matches + mismatches
    pct   = matches / total * 100 if total > 0 else 0
    print("  " + "─" * (_W - 2))
    print(f"  Validation: {matches}/{total} correct ({pct:.0f}%)")

    if matches == total:
        print("  ✓ All validation stocks classified correctly.")
        print("    The Hurst + ADX regime signal is consistent with empirical backtest results.")
    else:
        print("  ! Some mismatches detected — review borderline H values (marked with !)")
        print("    Note: Hurst is sensitive to the exact 3-year window; ADX to market phase.")

    print("─" * _W)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Use the shared fixed window from config.py so this classifier and
    # sma_screener.py measure over an identical date range.
    try:
        from screener.config import START_DATE, END_DATE
    except ImportError:
        from config import START_DATE, END_DATE

    print(f"\nRegime Classifier  |  {len(UNIVERSE)} stocks  |  {START_DATE} → {END_DATE}")
    print(f"Hurst: R/S method, full window  |  ADX-{ADX_PERIOD}, full-window median")
    print("─" * _W)
    print("Computing metrics...\n")

    results = classify_universe(UNIVERSE, START_DATE, END_DATE)
    print_results(results)
    validate_expected(results)
