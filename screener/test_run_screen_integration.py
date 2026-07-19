"""
screener/test_run_screen_integration.py

Integration regression tests for run_screen().

Unit tests in test_degradation_annotation.py cover _days_since_regime_transition()
in isolation and simulate the annotation logic inline — they do NOT call
run_screen() itself, so they cannot catch scoping bugs inside run_screen()'s
own code.

This file drives run_screen() end-to-end with mocked data to catch exactly
that class of bug: an error only reachable through the real call site, not
through isolated unit calls.

Confirmed production incident: 2026-07-12 Sunday screener cron crashed with
  NameError: name 'today' is not defined
at the _days_since_regime_transition(today) call inside run_screen().
run_screen() only has `today_str` (str) in scope; there is no `date` object
named `today`. The fix was `date.fromisoformat(today_str)`.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from screener.auto_screener import run_screen


# =============================================================================
# Helpers
# =============================================================================

def _low_adx_df(n: int = 800) -> pd.DataFrame:
    """
    Synthetic OHLCV with n bars and near-zero price variance.
    Produces ADX well below ADX_DEGRADE (22.0), triggering is_degraded=True.
    n must be > MIN_BARS (744) so the ticker enters data_cache.
    """
    rng = np.random.default_rng(42)
    prices = 100 + np.cumsum(rng.normal(0, 0.02, n))
    prices = np.clip(prices, 10, None)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open":   prices * 0.999,
        "high":   prices * 1.001,
        "low":    prices * 0.998,
        "close":  prices,
        "volume": 100_000,
    }, index=idx)


def _tracker_with_one_prior_flag(ticker: str, tmp_path: Path) -> Path:
    """
    Write a degradation tracker JSON with one prior flag for ticker.
    On the next run, consecutive_flags becomes 2 → REMOVE fires,
    ensuring the full is_degraded branch (including annotation) executes.
    """
    yesterday = (date.today() - timedelta(days=3)).isoformat()  # 3 days ago — still consecutive
    data = {
        ticker: {
            "consecutive_flags": 1,
            "last_flagged":      yesterday,
            "last_screen_date":  yesterday,
            "flag_history":      [yesterday],
            "flag_annotations":  {},
        },
        "_meta": {
            "last_screen_date": yesterday,
            "screen_count":     1,
            "created":          yesterday,
            "version":          "2",
        },
    }
    path = tmp_path / "degradation_tracker.json"
    path.write_text(json.dumps(data))
    return path


# =============================================================================
# Regression test: NameError 'today' is not defined inside run_screen()
# =============================================================================

def test_run_screen_annotation_path_no_name_error(tmp_path):
    """
    Regression for NameError: 'today' is not defined (bug in commit 3044b88).

    run_screen() called _days_since_regime_transition(today) but `today` (a
    date object) was never defined in run_screen()'s scope — only `today_str`
    (a string) exists.  Fix: date.fromisoformat(today_str).

    This test drives run_screen() end-to-end such that:
      1. A current-universe stock is marked degraded (ADX below threshold)
      2. The annotation branch executes — _days_since_regime_transition is
         called at the real call site with the real (fixed) argument
      3. _days_since_regime_transition is NOT mocked — it must receive a
         date object without crashing

    If the NameError bug regresses, this test fails with NameError before
    reaching any assert.
    """
    ticker = "REGTEST.NS"
    df = _low_adx_df()
    tracker_path = _tracker_with_one_prior_flag(ticker, tmp_path)

    # Minimal portfolio state — no open positions, no regime_transition_date.
    # _days_since_regime_transition will return None (fail-open): annotation
    # won't fire, but the call site executes, which is exactly what we need.
    state_file = tmp_path / "portfolio_state.json"
    state_file.write_text(json.dumps({"positions": {}}))

    with patch("screener.auto_screener.fetch_nifty500",
               return_value=({"REGTEST.NS": "Test Industry"}, False)), \
         patch("screener.auto_screener.get_current_universe",
               return_value=[ticker]), \
         patch("screener.auto_screener.get_ohlcv", return_value=df), \
         patch("screener.auto_screener.PORTFOLIO_STATE", state_file), \
         patch("screener.auto_screener.DEGRADATION_TRACKER", tracker_path), \
         patch("screener.auto_screener.save_degradation_tracker"):
        # Must complete without NameError or any exception
        result = run_screen()

    # Verify the function returned a structurally valid result
    assert "meta" in result
    assert "adds" in result
    assert "removes" in result

    # REGTEST.NS was degraded for 2 consecutive screens → should be in removes
    remove_tickers = [r["ticker"] for r in result["removes"]]
    assert ticker in remove_tickers, (
        f"Expected {ticker} in removes (2 consecutive degraded flags), "
        f"got: {remove_tickers}"
    )


# =============================================================================
# Regression tests: get_current_universe() must return signal_runner.STOCKS
# =============================================================================

def test_get_current_universe_returns_signal_runner_stocks():
    """
    get_current_universe() must return the authoritative STOCKS list from
    signal_runner.py verbatim — not portfolio_state.json's positions dict.

    Production incident 2026-07-12: the screener ran with
    ['SIEMENS.NS', ..., 'JKTYRE.NS', ...] as the current universe (both
    removed weeks earlier) and was missing CHOLAHLDNG.NS and COHANCE.NS
    (added Jul 9-10). Correlation checks and candidate exclusions were
    all computed against this wrong baseline.
    """
    from screener.auto_screener import get_current_universe
    from paper_trading.signal_runner import STOCKS

    result = get_current_universe()

    assert result == list(STOCKS), (
        f"get_current_universe() must equal signal_runner.STOCKS.\n"
        f"  Got:      {result}\n"
        f"  Expected: {list(STOCKS)}"
    )
    # Removed stocks must never appear
    assert "SIEMENS.NS" not in result, "SIEMENS.NS was removed and must not be in universe"
    assert "JKTYRE.NS"  not in result, "JKTYRE.NS was removed and must not be in universe"
    # Recently added stocks must be present
    assert "CHOLAHLDNG.NS" in result, "CHOLAHLDNG.NS was added Jul 9 and must be present"
    assert "COHANCE.NS"    in result, "COHANCE.NS was added Jul 10 and must be present"


def test_get_current_universe_ignores_portfolio_state_json(tmp_path):
    """
    Even when portfolio_state.json contains stale data (removed stocks, missing
    new additions), get_current_universe() must still return signal_runner.STOCKS.

    Patching PORTFOLIO_STATE to a stale file has zero effect — the function no
    longer reads it. If this ever regresses to reading portfolio_state.json again,
    this test will fail because the stale file's keys differ from STOCKS.
    """
    import json
    from unittest.mock import patch
    from screener.auto_screener import get_current_universe
    from paper_trading.signal_runner import STOCKS

    # Stale portfolio state: contains removed stocks, missing new additions
    stale_state = {
        "positions": {
            "SIEMENS.NS": {"shares": 0},   # removed from universe Jun 24
            "JKTYRE.NS":  {"shares": 0},   # removed from universe Jul 7
            # CHOLAHLDNG.NS and COHANCE.NS intentionally absent — never opened
        }
    }
    stale_file = tmp_path / "portfolio_state.json"
    stale_file.write_text(json.dumps(stale_state))

    with patch("screener.auto_screener.PORTFOLIO_STATE", stale_file):
        result = get_current_universe()

    # Must ignore the stale file and return the correct universe
    assert result == list(STOCKS)
    assert "SIEMENS.NS"    not in result
    assert "JKTYRE.NS"     not in result
    assert "CHOLAHLDNG.NS" in result
    assert "COHANCE.NS"    in result
