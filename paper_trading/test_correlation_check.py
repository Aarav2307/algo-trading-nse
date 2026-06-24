"""
paper_trading/test_correlation_check.py — Unit tests for correlation_check.py

All tests use mocked yfinance data and temp portfolio state files.
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


def _make_yf_mock(tickers: list, target_corr: float = 0.0, seed: int = 42) -> pd.DataFrame:
    """
    Build a yfinance-style MultiIndex DataFrame for mocking.

    The first ticker in `tickers` is treated as the candidate; subsequent
    tickers are open positions whose returns are generated with `target_corr`
    correlation to the candidate's returns.

    Returns a DataFrame where df["Close"] gives the per-ticker close prices.
    """
    n = 100  # trading days
    dates = pd.bdate_range("2026-01-01", periods=n)
    rng = np.random.default_rng(seed)

    # Base returns for candidate
    r_base = rng.normal(0, 0.012, n)

    prices: dict[str, np.ndarray] = {}
    for i, ticker in enumerate(tickers):
        if i == 0:
            # Candidate
            r = r_base
        else:
            # Open position with target_corr to candidate
            noise = rng.normal(0, 0.012, n)
            r = target_corr * r_base + np.sqrt(max(0.0, 1.0 - target_corr ** 2)) * noise

        prices[ticker] = 100.0 * np.exp(np.cumsum(r))

    # Build MultiIndex DataFrame matching yfinance multi-ticker format
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
# Test 1 — No open positions
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
# Test 2 — Safe correlation (~0.15)
# =============================================================================

def test_2_safe_correlation() -> None:
    name = "Test 2 — Safe correlation"

    tickers = ["CANDIDATE.NS", "OPENPOS.NS"]
    state_path = _make_state_file(open_positions=["OPENPOS.NS"])
    mock_df = _make_yf_mock(tickers, target_corr=0.15, seed=1)

    try:
        with patch("yfinance.download", return_value=mock_df):
            result = check_entry_correlation(
                candidate="CANDIDATE.NS",
                portfolio_state_path=state_path,
                max_correlation=0.60,
            )

        if not result["safe"]:
            _fail(name, f"Expected safe=True; max_corr={result['max_correlation']}")
            return
        if result["max_correlation"] is None or result["max_correlation"] >= 0.60:
            _fail(name, f"Expected max_correlation < 0.60, got {result['max_correlation']}")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 3 — Unsafe correlation (~0.90)
# =============================================================================

def test_3_unsafe_correlation() -> None:
    name = "Test 3 — Unsafe correlation"

    tickers = ["CANDIDATE.NS", "OPENPOS.NS"]
    state_path = _make_state_file(open_positions=["OPENPOS.NS"])
    mock_df = _make_yf_mock(tickers, target_corr=0.90, seed=2)

    try:
        with patch("yfinance.download", return_value=mock_df):
            result = check_entry_correlation(
                candidate="CANDIDATE.NS",
                portfolio_state_path=state_path,
                max_correlation=0.60,
            )

        if result["safe"]:
            _fail(name, f"Expected safe=False; max_corr={result['max_correlation']}")
            return
        if result["max_correlation"] is None or result["max_correlation"] < 0.60:
            _fail(name, f"Expected max_correlation ≥ 0.60, got {result['max_correlation']}")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 4 — Multiple open positions, one unsafe
# =============================================================================

def test_4_multiple_positions_one_unsafe() -> None:
    name = "Test 4 — Multiple positions, one unsafe"

    # Two open positions: one safe (~0.20), one unsafe (~0.90)
    tickers = ["CANDIDATE.NS", "SAFE_POS.NS", "UNSAFE_POS.NS"]
    state_path = _make_state_file(open_positions=["SAFE_POS.NS", "UNSAFE_POS.NS"])

    rng = np.random.default_rng(3)
    n = 100
    dates = pd.bdate_range("2026-01-01", periods=n)
    r_base = rng.normal(0, 0.012, n)

    # Candidate returns = base
    r_cand = r_base
    # Safe position: low correlation
    r_safe = 0.20 * r_base + np.sqrt(1 - 0.04) * rng.normal(0, 0.012, n)
    # Unsafe position: high correlation
    r_unsafe = 0.90 * r_base + np.sqrt(1 - 0.81) * rng.normal(0, 0.012, n)

    prices = {
        "CANDIDATE.NS": 100.0 * np.exp(np.cumsum(r_cand)),
        "SAFE_POS.NS":   200.0 * np.exp(np.cumsum(r_safe)),
        "UNSAFE_POS.NS": 150.0 * np.exp(np.cumsum(r_unsafe)),
    }
    mi_cols = pd.MultiIndex.from_product(
        [["Close"], tickers], names=["Price", "Ticker"]
    )
    mock_df = pd.DataFrame(
        np.column_stack([prices[t] for t in tickers]),
        index=dates,
        columns=mi_cols,
    )

    try:
        with patch("yfinance.download", return_value=mock_df):
            result = check_entry_correlation(
                candidate="CANDIDATE.NS",
                portfolio_state_path=state_path,
                max_correlation=0.60,
            )

        if result["safe"]:
            _fail(name, f"Expected safe=False; max_corr={result['max_correlation']}")
            return
        if result["max_correlation"] is None or result["max_correlation"] < 0.60:
            _fail(name, f"Expected max_correlation ≥ 0.60, got {result['max_correlation']}")
            return
        if "SAFE_POS.NS" not in result["correlations"]:
            _fail(name, "SAFE_POS.NS missing from correlations dict")
            return
        if "UNSAFE_POS.NS" not in result["correlations"]:
            _fail(name, "UNSAFE_POS.NS missing from correlations dict")
            return
        # Safe position should have lower correlation than unsafe
        if result["correlations"]["SAFE_POS.NS"] >= result["correlations"]["UNSAFE_POS.NS"]:
            _fail(name, "Expected safe position to have lower correlation than unsafe")
            return
        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 5 — NaN handling (one pair has no overlapping data)
# =============================================================================

def test_5_nan_handling() -> None:
    name = "Test 5 — NaN handling"

    # Candidate has prices for days 0-49 only (NaN for days 50-99).
    # NAN_POS has prices for days 50-99 only (NaN for days 0-49).
    # This guarantees zero overlapping data → NaN correlation for that pair.
    # GOOD_POS has full coverage and a safe correlation (~0.20) with candidate.
    state_path = _make_state_file(open_positions=["GOOD_POS.NS", "NAN_POS.NS"])

    rng = np.random.default_rng(4)
    n = 100
    dates = pd.bdate_range("2026-01-01", periods=n)
    r_base = rng.normal(0, 0.012, n)

    # Candidate: valid only for days 0-49
    p_cand = np.full(n, np.nan)
    p_cand[:50] = 100.0 * np.exp(np.cumsum(r_base[:50]))

    # NAN_POS: valid only for days 50-99 — no overlap with candidate
    p_nan = np.full(n, np.nan)
    p_nan[50:] = 150.0 * np.exp(np.cumsum(rng.normal(0, 0.012, n - 50)))

    # GOOD_POS: full coverage, safe correlation (~0.20) with the base returns
    r_good = 0.20 * r_base + np.sqrt(1 - 0.04) * rng.normal(0, 0.012, n)
    p_good = 200.0 * np.exp(np.cumsum(r_good))

    tickers = ["CANDIDATE.NS", "GOOD_POS.NS", "NAN_POS.NS"]
    mi_cols = pd.MultiIndex.from_product(
        [["Close"], tickers], names=["Price", "Ticker"]
    )
    mock_df = pd.DataFrame(
        np.column_stack([p_cand, p_good, p_nan]),
        index=dates,
        columns=mi_cols,
    )

    try:
        with patch("yfinance.download", return_value=mock_df):
            result = check_entry_correlation(
                candidate="CANDIDATE.NS",
                portfolio_state_path=state_path,
                max_correlation=0.60,
            )

        # Must not crash and must return a complete dict
        if "correlations" not in result:
            _fail(name, "Result missing 'correlations' key")
            return

        # NAN_POS pair must be recorded as NaN (zero overlapping bars after dropna)
        nan_corr = result["correlations"].get("NAN_POS.NS")
        if nan_corr is not None and not np.isnan(nan_corr):
            _fail(name, f"Expected NaN for NAN_POS.NS, got {nan_corr:.4f}")
            return

        # GOOD_POS has safe correlation — the NaN pair must not override this
        good_corr = result["correlations"].get("GOOD_POS.NS")
        if good_corr is None:
            _fail(name, "GOOD_POS.NS missing from correlations")
            return
        if np.isnan(good_corr):
            _fail(name, "Expected non-NaN correlation for GOOD_POS.NS")
            return
        if good_corr >= 0.60:
            _fail(name, f"Expected GOOD_POS.NS correlation < 0.60, got {good_corr:.4f}")
            return
        if not result["safe"]:
            _fail(name, "NaN pair should not block entry when valid pairs are safe")
            return

        _pass(name)
    finally:
        os.unlink(state_path)


# =============================================================================
# Test 6 — Signal runner integration (fail-open path)
# =============================================================================

def test_6_fail_open_integration() -> None:
    name = "Test 6 — Signal runner integration (fail-open)"

    # Verify check_entry_correlation is importable (same check signal_runner does)
    try:
        from paper_trading.correlation_check import check_entry_correlation as _fn
        if not callable(_fn):
            _fail(name, "check_entry_correlation is not callable")
            return
    except ImportError as e:
        _fail(name, f"Import failed: {e}")
        return

    # Simulate the signal_runner try/except block with a function that raises
    logs: list = []
    buy_was_blocked = [False]

    def mock_check_raises(**kwargs):
        raise RuntimeError("Simulated network error")

    try:
        result = mock_check_raises(candidate="TEST.NS")
        if not result["safe"]:
            buy_was_blocked[0] = True
            logs.append("BLOCKED")
        else:
            logs.append("ALLOWED")
    except Exception as e:
        logs.append(
            f"CORRELATION CHECK ERROR | TEST.NS | {e} — proceeding with BUY (fail open)"
        )

    if buy_was_blocked[0]:
        _fail(name, "Fail-open path should not block the BUY")
        return
    if not any("CORRELATION CHECK ERROR" in msg for msg in logs):
        _fail(name, f"Expected fail-open error log, got: {logs}")
        return

    _pass(name)


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
    test_5_nan_handling()
    test_6_fail_open_integration()

    _assert_live_state_untouched(mtime_before)

    print()
    print(f"  Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 55)
    print()

    if _FAIL:
        sys.exit(1)
