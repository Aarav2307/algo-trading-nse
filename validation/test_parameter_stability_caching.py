"""
validation/test_parameter_stability_caching.py

Regression test for run_parameter_stability_test()'s data caching (2026-08-07).

Before the fix, each of the 9 SMA/ATR combinations called
run_extended_walk_forward(), which fetched fresh IS/OOS data per stock via
get_ohlcv() and fresh NIFTY data via _fetch_nifty() — 9x more Kite calls than
necessary, since sma_fast/sma_slow/atr_multiplier only affect signal
generation and risk logic applied on top of the price data, not the price
data itself. This test proves get_ohlcv/_fetch_nifty are now called exactly
once per (stock, window) and once per NIFTY window, regardless of how many
parameter combinations are tested.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.walk_forward import run_parameter_stability_test


def _make_df(n_bars: int, start: str = "2015-01-01") -> pd.DataFrame:
    idx   = pd.date_range(start, periods=n_bars, freq="B")
    price = 100 + np.cumsum(np.random.default_rng(7).normal(0, 1, n_bars))
    price = np.clip(price, 10, None)
    return pd.DataFrame({
        "open":   price * 0.99,
        "high":   price * 1.01,
        "low":    price * 0.98,
        "close":  price,
        "volume": 100_000,
    }, index=idx)


def test_data_fetched_once_per_stock_not_once_per_combo():
    """
    get_ohlcv must be called exactly 2 times per stock (IS + OOS windows),
    not 18 (9 combos x 2 windows) — proving the fetch is cached and reused
    across all 9 SMA/ATR combinations, not repeated per combination.
    """
    stocks = ["FAKE1.NS", "FAKE2.NS"]
    df = _make_df(1_500)

    with patch("validation.walk_forward.get_ohlcv", return_value=df) as mock_ohlcv, \
         patch("validation.walk_forward._fetch_nifty", return_value=pd.DataFrame()) as mock_nifty, \
         patch("validation.walk_forward.time.sleep"):
        results = run_parameter_stability_test(stocks, cooldown_bars=15, nifty_regime_filter=True)

    # 2 stocks x 2 windows (IS, OOS) = 4 total, regardless of 9 combos tested
    assert mock_ohlcv.call_count == 4, (
        f"Expected exactly 4 get_ohlcv calls (2 stocks x IS/OOS), got "
        f"{mock_ohlcv.call_count} — data is being re-fetched per combination"
    )

    # NIFTY fetched once per window (IS, OOS), not once per combo
    assert mock_nifty.call_count == 2, (
        f"Expected exactly 2 _fetch_nifty calls (IS/OOS), got {mock_nifty.call_count}"
    )

    # Still produces one result per SMA/ATR combination
    assert len(results) == 9, "Expected 9 SMA/ATR combination results"
    for r in results:
        assert "total_score" in r
        assert "avg_oos_return" in r
        assert "per_stock" in r


def test_nifty_not_fetched_when_regime_filter_disabled():
    """nifty_regime_filter=False must skip _fetch_nifty entirely, same as
    run_extended_walk_forward()'s existing behavior."""
    stocks = ["FAKE1.NS"]
    df = _make_df(1_500)

    with patch("validation.walk_forward.get_ohlcv", return_value=df), \
         patch("validation.walk_forward._fetch_nifty") as mock_nifty, \
         patch("validation.walk_forward.time.sleep"):
        run_parameter_stability_test(stocks, cooldown_bars=15, nifty_regime_filter=False)

    mock_nifty.assert_not_called()


def test_results_match_uncached_baseline():
    """
    The cached path (_run_one_from_df + params) must produce identical scores
    to the uncached path (_run_one + params) for the same data and params —
    proving the caching refactor changed only *when* data is fetched, not
    *what* is computed from it.
    """
    from validation.walk_forward import _run_one_from_df, _run_one, EXT_IS_START, EXT_IS_END

    df = _make_df(1_500)
    test_params = {
        "sma_fast": 15, "sma_slow": 40, "atr_period": 22,
        "atr_multiplier": 2.5, "hard_stop_pct": -0.20, "max_bars_held": 60,
        "risk_per_trade": 0.015, "max_position": 0.20,
    }

    cached_result = _run_one_from_df(df, cooldown_bars=15, nifty_df=None, params=test_params)

    with patch("validation.walk_forward.get_ohlcv", return_value=df), \
         patch("validation.walk_forward.time.sleep"):
        uncached_result = _run_one(
            "FAKE.NS", EXT_IS_START, EXT_IS_END, nifty_df=None, params=test_params
        )

    assert cached_result["metrics"]["total_ret"] == pytest.approx(uncached_result["metrics"]["total_ret"])
    assert cached_result["metrics"]["n_trades"] == uncached_result["metrics"]["n_trades"]
