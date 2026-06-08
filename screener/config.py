"""
screener/config.py — Shared measurement window for all screeners.

WHY A SHARED CONFIG EXISTS
───────────────────────────
The SMA screener and regime classifier originally used different time windows:
  - sma_screener used "today minus 3 years" (a rolling window that shifts daily)
  - regime_classifier used a 3-year Hurst window but only a 1-year ADX subset

This produced outputs that couldn't be directly compared: a stock might score
"Strong fit" on the SMA screener using one date range while simultaneously
landing in UNCLASSIFIED on the regime classifier using a different range.
Any disagreement between the two tools was impossible to interpret — it could be
a genuine signal about the stock OR merely an artifact of the measurement windows.

With a single shared config:
  - Every metric (volatility, beta, drawdown, trend duration, MR score,
    Hurst exponent, ADX) is computed over the identical 2023-01-01 → 2026-01-01
    window.
  - Disagreement between the two tools now reflects genuine stock behaviour,
    not window mismatch.
  - compare_screeners.py can make meaningful AGREE / DISAGREE calls.
"""

# ── Measurement window ────────────────────────────────────────────────────────
# Clean, fixed 3-year window. Both screeners and the compare tool use these.
# To re-run on a different period, change only these two constants.
START_DATE = "2023-01-01"
END_DATE   = "2026-01-01"

# ── Benchmark ─────────────────────────────────────────────────────────────────
BENCHMARK_TICKER = "^NSEI"    # Nifty 50 index via yfinance

# ── Borderline bracket ────────────────────────────────────────────────────────
# Stocks whose Hurst falls in (0.50, HURST_BORDERLINE_UPPER] AND whose ADX is
# below ADX_NOTCH_THRESH are "dual candidates": they show a weak trending
# character at the macro level (H barely above 0.5) but no confirmed current
# trend (low ADX). Empirically, both SMA crossover AND mean reversion strategies
# can work on these stocks — they need case-by-case review.
# See the BORDERLINE bucket in compare_screeners.py.
HURST_BORDERLINE_UPPER = 0.54   # H in (0.50, 0.54] → borderline
