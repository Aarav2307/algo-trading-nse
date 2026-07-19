"""
validation/test_walk_forward_json_output.py

Tests for the --json output mode added to walk_forward.py (Jul 2026).

Coverage:
  1. --json with full data → valid JSON, correct schema, gate_pass correct
  2. --json with insufficient extended-window data → extended.status == "SKIPPED"
     and extended.error populated
  3. --json + --no-extended → extended.status == "NOT_REQUESTED"
  4. Non-json path unaffected by the presence of the --json argparse flag
"""

import json
import runpy
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# =============================================================================
# Shared helpers
# =============================================================================

def _make_df(n_bars: int, start: str = "2015-01-01") -> pd.DataFrame:
    idx   = pd.date_range(start, periods=n_bars, freq="B")
    price = 100 + np.cumsum(np.random.default_rng(42).normal(0, 1, n_bars))
    price = np.clip(price, 10, None)
    return pd.DataFrame({
        "open":   price * 0.99,
        "high":   price * 1.01,
        "low":    price * 0.98,
        "close":  price,
        "volume": 100_000,
    }, index=idx)


def _run_json_cli(*extra_args: str, df: pd.DataFrame) -> dict:
    """
    Run walk_forward.py as __main__ with --ticker FAKE.NS --json [extra_args],
    capture stdout, and return the parsed JSON dict.

    Patches get_ohlcv and _fetch_nifty so no live API calls are made.
    sys.exit(0) raises SystemExit — we catch it so the test can proceed.
    """
    buf = StringIO()
    argv = ["walk_forward.py", "--ticker", "FAKE.NS", "--json"] + list(extra_args)
    with patch("sys.argv", argv), \
         patch("validation.walk_forward.get_ohlcv", return_value=df), \
         patch("validation.walk_forward._fetch_nifty", return_value=pd.DataFrame()), \
         patch("validation.walk_forward._save_results"), \
         redirect_stdout(buf):
        with pytest.raises(SystemExit):
            runpy.run_path(
                str(_ROOT / "validation" / "walk_forward.py"),
                run_name="__main__",
            )

    raw = buf.getvalue().strip()
    assert raw, "No output captured — JSON line was not emitted"
    lines = [l for l in raw.splitlines() if l.strip()]
    assert len(lines) == 1, f"Expected exactly 1 JSON line on stdout, got {len(lines)}:\n{raw}"
    return json.loads(lines[0])


# =============================================================================
# Test 1 — full data: both windows have enough bars
# =============================================================================

def test_json_output_full_data_valid_schema():
    """
    --json with a stock that has enough bars in both IS/OOS windows must emit
    exactly one parseable JSON line with the correct schema and plausible values.
    """
    good_df = _make_df(2_500)   # > 2000 bars — covers 2015-today comfortably
    data = _run_json_cli(df=good_df)

    # Schema keys present
    assert "ticker"     in data
    assert "original"   in data
    assert "extended"   in data
    assert "gate_pass"  in data
    assert "reasons"    in data

    # Ticker correctly normalised
    assert data["ticker"] == "FAKE.NS"

    # original sub-object — schema presence and valid types (score may be None
    # if the synthetic backtest produces no trades, which is fine for schema tests)
    orig = data["original"]
    assert orig["total_metrics"] == 6
    assert orig["score"] is None or (isinstance(orig["score"], int) and 0 <= orig["score"] <= 6)
    assert orig["oos_return_pct"] is None or isinstance(orig["oos_return_pct"], float)

    # extended sub-object — status must be one of the valid values
    ext = data["extended"]
    assert ext["status"] in {"PASS", "FAIL", "SKIPPED", "NOT_REQUESTED"}

    # gate_pass must be bool
    assert isinstance(data["gate_pass"], bool)

    # reasons must be a list; when gate_pass=True reasons must be empty
    assert isinstance(data["reasons"], list)
    if data["gate_pass"]:
        assert data["reasons"] == []


# =============================================================================
# Test 2 — insufficient extended-window data → extended.status == "SKIPPED"
# =============================================================================

def test_json_output_insufficient_extended_data():
    """
    When the stock has enough data for the original window but too little for
    the extended window, extended.status must be "SKIPPED" and extended.error
    must be a non-empty string.
    """
    # 600 bars starting 2020 → covers original 2018-2022/2023 window but not 2015-2019
    recent_df = _make_df(600, start="2020-01-01")
    data = _run_json_cli(df=recent_df)

    ext = data["extended"]
    assert ext["status"] == "SKIPPED", (
        f"Expected SKIPPED for insufficient extended data, got {ext['status']!r}"
    )
    assert ext["error"] is not None and ext["error"] != "", (
        "extended.error should be a non-empty string when data is insufficient"
    )
    assert ext["score"] is None
    assert ext["oos_return_pct"] is None


# =============================================================================
# Test 3 — --no-extended → extended.status == "NOT_REQUESTED"
# =============================================================================

def test_json_output_no_extended_flag():
    """
    When --no-extended is passed alongside --json, extended.status must be
    "NOT_REQUESTED" and extended.score / extended.oos_return_pct must be null.
    """
    good_df = _make_df(2_500)
    data = _run_json_cli("--no-extended", df=good_df)

    ext = data["extended"]
    assert ext["status"] == "NOT_REQUESTED"
    assert ext["score"] is None
    assert ext["oos_return_pct"] is None


# =============================================================================
# Test 4 — non-json path is unaffected by the --json argparse flag's existence
# =============================================================================

def test_non_json_path_unaffected():
    """
    run_walk_forward() called without --json (i.e. with skip_diagnostics=False)
    must return its normal result dict, confirming the new parameter is a
    no-op when False.
    """
    from validation.walk_forward import run_walk_forward

    good_df = _make_df(2_500)
    with patch("validation.walk_forward.get_ohlcv", return_value=good_df), \
         patch("validation.walk_forward._fetch_nifty", return_value=pd.DataFrame()), \
         patch("validation.walk_forward._print_sharpe_table"), \
         patch("validation.walk_forward._cooldown_sensitivity"), \
         patch("validation.walk_forward._save_results"):
        result = run_walk_forward(
            stocks=["FAKE.NS"],
            skip_diagnostics=False,
        )

    assert "FAKE.NS" in result
    r = result["FAKE.NS"]
    assert "score" in r
    assert "oos_return" in r


# =============================================================================
# Test 5 — skip_diagnostics=True skips the three diagnostic functions
# =============================================================================

def test_skip_diagnostics_prevents_live_calls():
    """
    run_walk_forward(..., skip_diagnostics=True) must NOT call _print_sharpe_table,
    _cooldown_sensitivity, or _save_results.
    """
    from validation.walk_forward import run_walk_forward

    good_df = _make_df(2_500)
    with patch("validation.walk_forward.get_ohlcv", return_value=good_df), \
         patch("validation.walk_forward._fetch_nifty", return_value=pd.DataFrame()), \
         patch("validation.walk_forward._print_sharpe_table") as mock_sharpe, \
         patch("validation.walk_forward._cooldown_sensitivity") as mock_cd, \
         patch("validation.walk_forward._save_results") as mock_save:
        run_walk_forward(stocks=["FAKE.NS"], skip_diagnostics=True)

    mock_sharpe.assert_not_called()
    mock_cd.assert_not_called()
    mock_save.assert_not_called()
