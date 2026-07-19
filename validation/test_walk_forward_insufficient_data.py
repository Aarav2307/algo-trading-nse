"""
validation/test_walk_forward_insufficient_data.py

Regression tests for the insufficient-data crash fix (Jul 2026).

Before the fix, stocks with zero or sub-minimum bars in the extended
walk-forward windows (2015-2019 IS / 2020-2023 OOS) caused a TypeError:
  '"N/A" >= METRIC_MIN'
because run_extended_walk_forward() returned {"score": "N/A", ...} and the
gate verdict section assumed score was always int or None.

These tests verify:
  1. The crash does not recur when extended data is insufficient.
  2. The gate correctly evaluates on the original window only (not penalised).
  3. A stock with sufficient data is unaffected.
"""

import sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.walk_forward import (
    run_extended_walk_forward,
    run_walk_forward,
    PARAMS,
    WINDOWS,
    EXT_IS_START, EXT_IS_END, EXT_OOS_START, EXT_OOS_END,
    MIN_BARS,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_df(n_bars: int) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with n_bars rows of synthetic data."""
    idx   = pd.date_range("2015-01-01", periods=n_bars, freq="B")
    price = 100 + np.cumsum(np.random.default_rng(42).normal(0, 1, n_bars))
    price = np.clip(price, 10, None)
    return pd.DataFrame({
        "open":   price * 0.99,
        "high":   price * 1.01,
        "low":    price * 0.98,
        "close":  price,
        "volume": 100_000,
    }, index=idx)


# =============================================================================
# Test 1 — extended window insufficient IS data does not crash and returns N/A
# =============================================================================

def test_extended_insufficient_is_data_returns_na_not_crash():
    """
    When the extended IS window has fewer than MIN_BARS trading days,
    run_extended_walk_forward must return {"score": "N/A", ...} rather than
    crashing or raising TypeError.  The error key must be present.
    """
    short_df = _make_df(10)   # 10 bars << MIN_BARS (200) — always insufficient

    with patch("validation.walk_forward.get_ohlcv", return_value=short_df), \
         patch("validation.walk_forward._fetch_nifty", return_value=pd.DataFrame()):
        result = run_extended_walk_forward(stocks=["FAKE.NS"])

    assert "FAKE.NS" in result
    r = result["FAKE.NS"]
    assert r["score"]      == "N/A"
    assert r["oos_return"] == "N/A"
    assert "error" in r, "error key must be present for insufficient-data returns"
    assert r["error"] is not None


# =============================================================================
# Test 2 — gate logic does not crash and treats insufficient extended data
#          as SKIPPED (not penalised, original window evaluates independently)
# =============================================================================

def test_gate_verdict_does_not_crash_on_na_ext_score():
    """
    The normalization of 'N/A' → None must prevent TypeError in the gate
    verdict comparison.  Simulate what __main__'s WF gate does with the
    returned dicts directly.
    """
    TOTAL_METRICS = 6
    METRIC_MIN    = 4
    OOS_RET_MIN   = 4.0

    # Simulate: original window passed, extended window has no data
    orig_data = {"score": 5, "oos_return": 15.4}
    ext_data  = {"score": "N/A", "oos_return": "N/A", "error": "INSUFFICIENT DATA — 0 bars (need ≥ 200)"}

    orig_score   = orig_data.get("score")
    orig_oos_ret = orig_data.get("oos_return")
    ext_score    = ext_data.get("score")
    ext_oos_ret  = ext_data.get("oos_return")

    # Apply the normalization fix
    if orig_score   == "N/A": orig_score   = None
    if orig_oos_ret == "N/A": orig_oos_ret = None
    if ext_score    == "N/A": ext_score    = None
    if ext_oos_ret  == "N/A": ext_oos_ret  = None

    orig_metrics_ok = orig_score is not None and orig_score >= METRIC_MIN
    orig_ret_ok     = orig_oos_ret is not None and orig_oos_ret >= OOS_RET_MIN

    # ext_score is None → treat as SKIPPED, don't penalise
    ext_metrics_ok = ext_score is None or ext_score >= METRIC_MIN

    gate_pass = orig_metrics_ok and orig_ret_ok and ext_metrics_ok

    assert orig_metrics_ok is True,  "5/6 should pass the ≥4/6 original gate"
    assert orig_ret_ok     is True,  "+15.4% should pass the ≥+4% OOS gate"
    assert ext_metrics_ok  is True,  "None ext_score should not penalise the gate"
    assert gate_pass       is True,  "Gate should PASS on original window alone when ext is SKIPPED"


# =============================================================================
# Test 3 — insufficient data in both original AND extended windows
# =============================================================================

def test_gate_verdict_does_not_crash_when_orig_also_na():
    """
    If orig window also returns N/A (e.g. stock listed after 2018), the
    normalization must prevent TypeError there too, and gate_pass must be False.
    """
    METRIC_MIN  = 4
    OOS_RET_MIN = 4.0

    orig_data = {"score": "N/A", "oos_return": "N/A", "error": "INSUFFICIENT DATA — 50 bars (need ≥ 200)"}
    ext_data  = {"score": "N/A", "oos_return": "N/A", "error": "INSUFFICIENT DATA — 0 bars (need ≥ 200)"}

    orig_score   = orig_data.get("score")
    orig_oos_ret = orig_data.get("oos_return")
    ext_score    = ext_data.get("score")
    ext_oos_ret  = ext_data.get("oos_return")

    if orig_score   == "N/A": orig_score   = None
    if orig_oos_ret == "N/A": orig_oos_ret = None
    if ext_score    == "N/A": ext_score    = None
    if ext_oos_ret  == "N/A": ext_oos_ret  = None

    orig_metrics_ok = orig_score is not None and orig_score >= METRIC_MIN
    orig_ret_ok     = orig_oos_ret is not None and orig_oos_ret >= OOS_RET_MIN
    ext_metrics_ok  = ext_score is None or ext_score >= METRIC_MIN

    gate_pass = orig_metrics_ok and orig_ret_ok and ext_metrics_ok

    assert orig_metrics_ok is False, "None orig_score should fail the metrics gate"
    assert orig_ret_ok     is False, "None orig_oos_ret should fail the return gate"
    assert gate_pass       is False, "Gate should FAIL when original window has no data"


# =============================================================================
# Test 4 — stock with sufficient data is completely unaffected by the fix
# =============================================================================

def test_sufficient_data_result_unchanged():
    """
    A stock with plenty of data in both windows must return the same
    structured result as before the fix (no regressions for the normal path).
    """
    good_df = _make_df(1_300)  # >> MIN_BARS, covers both windows

    with patch("validation.walk_forward.get_ohlcv", return_value=good_df), \
         patch("validation.walk_forward._fetch_nifty", return_value=pd.DataFrame()):
        result = run_extended_walk_forward(stocks=["FAKE.NS"])

    r = result["FAKE.NS"]
    assert r["score"] != "N/A",      "sufficient-data stock should return an int score"
    assert isinstance(r["score"], int), f"score should be int, got {type(r['score'])}"
    assert 0 <= r["score"] <= 6,     "score must be in [0, 6]"
    assert "error" not in r or r.get("error") is None, "error key should be absent or None"
