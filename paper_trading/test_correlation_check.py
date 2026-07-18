"""
paper_trading/test_correlation_check.py — Unit tests for correlation_check.py

Tests 1, 5-6 test edge cases and the yfinance fallback path.
Tests 2-4  test the primary price_data path (used by signal_runner).
The live portfolio_state.json is never touched.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.correlation_check import check_entry_correlation

LIVE_STATE = Path("paper_trading/portfolio_state.json")
_PASS = 0
_FAIL = 0


# =============================================================================
# Helpers
# =============================================================================

def _make_state_file(open_positions: list, entry_price: float = 1000.0) -> str:
    """Write a minimal portfolio_state.json to a temp file. Returns the path."""
    positions = {}
    for ticker in open_positions:
        positions[ticker] = {
            "shares": 10,
            "entry_price": entry_price,
            "pending_buy": False,
        }
    # Add a few flat positions so the state looks realistic
    for flat in ["FLAT_A.NS", "FLAT_B.NS"]:
        if flat not in positions:
            positions[flat] = {"shares": 0, "entry_price": 0.0, "pending_buy": False}

    state = {
        "cash": 50000.0,
        "initial_capital": 100000.0,
        "positions": positions,
        "cooldown_state": {},
        "total_trades": 0,
        "trade_log": [],
        "etf_shares": 0,
        "etf_avg_price": 0.0,
        "etf_tier": 0,
    }
    tmp = tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", delete=False
    )
    json.dump(state, tmp)
    tmp.close()
    return tmp.name


def make_price_df(prices) -> pd.DataFrame:
    """Build a minimal OHLCV-style DataFrame with a 'close' column."""
    dates = pd.date_range("2026-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({"close": prices}, index=dates)


def _make_correlated_prices(
    target_corr: float, seed: int = 42, n: int = 100
) -> tuple:
    """
    Return (prices_candidate, prices_position) as numpy arrays.
    The two series have approximately target_corr correlation on log returns.
    """
    rng = np.random.default_rng(seed)
    r_base = rng.normal(0, 0.012, n)
    noise  = rng.normal(0, 0.012, n)
    r_pos  = target_corr * r_base + np.sqrt(max(0.0, 1.0 - target_corr ** 2)) * noise
    return (
        100.0 * np.exp(np.cumsum(r_base)),
        100.0 * np.exp(np.cumsum(r_pos)),
    )


def _make_yf_mock(tickers: list, target_corr: float = 0.0, seed: int = 42) -> pd.DataFrame:
    """
    Build a yfinance-style MultiIndex DataFrame for mocking (CLI path tests).
    Returns a DataFrame where df["Close"] gives per-ticker close prices.
    """
    n = 100
    dates = pd.bdate_range("2026-01-01", periods=n)
    rng = np.random.default_rng(seed)
    r_base = rng.normal(0, 0.012, n)

    prices: dict[str, np.ndarray] = {}
    for i, ticker in enumerate(tickers):
        if i == 0:
            r = r_base
        else:
            noise = rng.normal(0, 0.012, n)
            r = target_corr * r_base + np.sqrt(max(0.0, 1.0 - target_corr ** 2)) * noise
        prices[ticker] = 100.0 * np.exp(np.cumsum(r))

    mi_cols = pd.MultiIndex.from_product(
        [["Close"], tickers], names=["Price", "Ticker"]
    )
    data = np.column_stack([prices[t] for t in tickers])
    return pd.DataFrame(data, index=dates, columns=mi_cols)


def _pass(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  PASS  {name}")


def _fail(name: str, msg: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  FAIL  {name}: {msg}")


# =============================================================================
# Test 1 — No open positions (no price fetch needed on either path)
# =============================================================================

def test_1_no_open_positions() -> None:
    name = "Test 1 — No open positions"

    state_path = _make_state_file(open_positions=[])

    try:
        result = check_entry_correlation(
            candidate="CANDIDATE.NS",
            portfolio_state_path=state_path,
        )
        if not result["safe"]:
            _fail(name, f"Expected safe=True, got False")
            return
        if "No open positions" not in result["reason"]:
            _fail(name, f"Expected 'No open positions' in reason, got: {result['reason']}")
            return
        if result["open_positions"]:
            _fail(name, f"Expected empty open_positions, got: {result['open_positions']}")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 2 — Safe correlation via price_data (primary path, ~0.15 corr)
# =============================================================================

def test_2_safe_correlation() -> None:
    name = "Test 2 — Safe correlation (price_data path)"

    p_cand, p_open = _make_correlated_prices(target_corr=0.15, seed=1)
    mock_data = {
        "CANDIDATE.NS": make_price_df(p_cand),
        "OPENPOS.NS":   make_price_df(p_open),
    }
    state_path = _make_state_file(open_positions=["OPENPOS.NS"])

    try:
        result = check_entry_correlation(
            candidate="CANDIDATE.NS",
            portfolio_state_path=state_path,
            max_correlation=0.60,
            price_data=mock_data,
        )
        if not result["safe"]:
            _fail(name, f"Expected safe=True; max_corr={result['max_correlation']}")
            return
        if result["max_correlation"] is None or result["max_correlation"] >= 0.60:
            _fail(name, f"Expected max_correlation < 0.60, got {result['max_correlation']}")
            return
        if "OPENPOS.NS" not in result["correlations"]:
            _fail(name, "OPENPOS.NS missing from correlations dict")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 3 — Unsafe correlation via price_data (~0.90 corr)
# =============================================================================

def test_3_unsafe_correlation() -> None:
    name = "Test 3 — Unsafe correlation (price_data path)"

    p_cand, p_open = _make_correlated_prices(target_corr=0.90, seed=2)
    mock_data = {
        "CANDIDATE.NS": make_price_df(p_cand),
        "OPENPOS.NS":   make_price_df(p_open),
    }
    state_path = _make_state_file(open_positions=["OPENPOS.NS"])

    try:
        result = check_entry_correlation(
            candidate="CANDIDATE.NS",
            portfolio_state_path=state_path,
            max_correlation=0.60,
            price_data=mock_data,
        )
        if result["safe"]:
            _fail(name, f"Expected safe=False; max_corr={result['max_correlation']}")
            return
        if result["max_correlation"] is None or result["max_correlation"] < 0.60:
            _fail(name, f"Expected max_correlation >= 0.60, got {result['max_correlation']}")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 4 — Multiple positions, one unsafe via price_data
# =============================================================================

def test_4_multiple_positions_one_unsafe() -> None:
    name = "Test 4 — Multiple positions, one unsafe (price_data path)"

    rng = np.random.default_rng(3)
    n = 100
    r_base   = rng.normal(0, 0.012, n)
    r_safe   = 0.20 * r_base + np.sqrt(1 - 0.04)  * rng.normal(0, 0.012, n)
    r_unsafe = 0.90 * r_base + np.sqrt(1 - 0.81)  * rng.normal(0, 0.012, n)

    mock_data = {
        "CANDIDATE.NS":  make_price_df(100.0 * np.exp(np.cumsum(r_base))),
        "SAFE_POS.NS":   make_price_df(200.0 * np.exp(np.cumsum(r_safe))),
        "UNSAFE_POS.NS": make_price_df(150.0 * np.exp(np.cumsum(r_unsafe))),
    }
    state_path = _make_state_file(open_positions=["SAFE_POS.NS", "UNSAFE_POS.NS"])

    try:
        result = check_entry_correlation(
            candidate="CANDIDATE.NS",
            portfolio_state_path=state_path,
            max_correlation=0.60,
            price_data=mock_data,
        )
        if result["safe"]:
            _fail(name, f"Expected safe=False; max_corr={result['max_correlation']}")
            return
        if result["max_correlation"] is None or result["max_correlation"] < 0.60:
            _fail(name, f"Expected max_correlation >= 0.60, got {result['max_correlation']}")
            return
        if "SAFE_POS.NS" not in result["correlations"]:
            _fail(name, "SAFE_POS.NS missing from correlations dict")
            return
        if "UNSAFE_POS.NS" not in result["correlations"]:
            _fail(name, "UNSAFE_POS.NS missing from correlations dict")
            return
        safe_corr   = result["correlations"]["SAFE_POS.NS"]
        unsafe_corr = result["correlations"]["UNSAFE_POS.NS"]
        if not (isinstance(safe_corr, float) and isinstance(unsafe_corr, float)):
            _fail(name, f"Expected numeric correlations, got {safe_corr}, {unsafe_corr}")
            return
        if safe_corr >= unsafe_corr:
            _fail(name, "Expected safe position to have lower correlation than unsafe")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 5 — Missing ticker in price_data (graceful NaN handling)
# =============================================================================

def test_5_missing_ticker_in_price_data() -> None:
    name = "Test 5 — Missing ticker in price_data (graceful handling)"

    p_cand, p_good = _make_correlated_prices(target_corr=0.15, seed=5)
    # MISSING_POS.NS intentionally absent from price_data
    mock_data = {
        "CANDIDATE.NS": make_price_df(p_cand),
        "GOOD_POS.NS":  make_price_df(p_good),
    }
    state_path = _make_state_file(open_positions=["GOOD_POS.NS", "MISSING_POS.NS"])

    try:
        result = check_entry_correlation(
            candidate="CANDIDATE.NS",
            portfolio_state_path=state_path,
            max_correlation=0.60,
            price_data=mock_data,
        )
        # Must not crash and must return a complete dict
        if "correlations" not in result:
            _fail(name, "Result missing 'correlations' key")
            return

        # MISSING_POS.NS not in price_data → should be recorded as NaN
        miss_corr = result["correlations"].get("MISSING_POS.NS")
        if miss_corr is not None and not np.isnan(miss_corr):
            _fail(name, f"Expected NaN for MISSING_POS.NS, got {miss_corr}")
            return

        # GOOD_POS.NS has data → correlation should be computed
        good_corr = result["correlations"].get("GOOD_POS.NS")
        if good_corr is None:
            _fail(name, "GOOD_POS.NS missing from correlations")
            return
        if np.isnan(good_corr):
            _fail(name, "Expected non-NaN correlation for GOOD_POS.NS")
            return

        # Low-corr GOOD_POS.NS must not block entry
        if not result["safe"]:
            _fail(name, "NaN/missing pair should not block entry when valid pair is safe")
            return

        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 6 — price_data=None falls back to yfinance (CLI path)
# =============================================================================

def test_6_yfinance_fallback() -> None:
    name = "Test 6 — price_data=None falls back to yfinance (CLI path)"

    tickers   = ["CANDIDATE.NS", "OPENPOS.NS"]
    state_path = _make_state_file(open_positions=["OPENPOS.NS"])
    mock_df    = _make_yf_mock(tickers, target_corr=0.15, seed=6)

    try:
        with patch("yfinance.download", return_value=mock_df) as mock_dl:
            result = check_entry_correlation(
                candidate="CANDIDATE.NS",
                portfolio_state_path=state_path,
                max_correlation=0.60,
                # price_data intentionally omitted → yfinance fallback
            )

        if not mock_dl.called:
            _fail(name, "Expected yfinance.download to be called (CLI fallback path)")
            return
        if not result["safe"]:
            _fail(name, f"Expected safe=True for low-corr mock; max_corr={result['max_correlation']}")
            return
        if result["max_correlation"] is None or result["max_correlation"] >= 0.60:
            _fail(name, f"Expected max_correlation < 0.60, got {result['max_correlation']}")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Guard: live state file must not be modified
# =============================================================================

def _assert_live_state_untouched(mtime_before: float) -> None:
    if LIVE_STATE.exists():
        mtime_after = os.path.getmtime(LIVE_STATE)
        if mtime_after != mtime_before:
            print(f"\n  CRITICAL: {LIVE_STATE} was modified during tests!")
            sys.exit(2)


# =============================================================================
# Runner
# =============================================================================

if __name__ == "__main__":
    print()
    print("=" * 55)
    print("  Correlation Check Unit Tests")
    print("=" * 55)

    mtime_before = os.path.getmtime(LIVE_STATE) if LIVE_STATE.exists() else 0.0

    test_1_no_open_positions()
    test_2_safe_correlation()
    test_3_unsafe_correlation()
    test_4_multiple_positions_one_unsafe()
    test_5_missing_ticker_in_price_data()
    test_6_yfinance_fallback()

    _assert_live_state_untouched(mtime_before)

    print()
    print(f"  Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 55)
    print()

    if _FAIL:
        sys.exit(1)
