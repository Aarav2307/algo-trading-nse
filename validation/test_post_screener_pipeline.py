"""
validation/test_post_screener_pipeline.py

Tests for post_screener_pipeline.py.

All Kite API calls and WF subprocess calls are mocked — no live network access.
"""

import json
import sys
import tempfile
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import numpy as np
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.post_screener_pipeline import (
    generate_report,
    get_crossover,
    is_within_cache,
    load_history,
    run_wf_gate,
    save_history,
    main,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_ohlcv(n=60) -> pd.DataFrame:
    idx   = pd.date_range("2026-01-01", periods=n, freq="B")
    price = 100 + np.arange(n, dtype=float)  # steadily rising → GOLDEN cross
    return pd.DataFrame({
        "open":   price * 0.99,
        "high":   price * 1.01,
        "low":    price * 0.98,
        "close":  price,
        "volume": 100_000,
    }, index=idx)


def _sample_wf_pass(ticker="FAKE.NS") -> dict:
    return {
        "ticker": ticker,
        "original": {"score": 6, "total_metrics": 6, "oos_return_pct": 8.0},
        "extended": {"status": "SKIPPED", "score": None,
                     "oos_return_pct": None, "error": "insufficient data"},
        "gate_pass": True,
        "reasons": [],
    }


def _sample_wf_fail(ticker="BAD.NS") -> dict:
    return {
        "ticker": ticker,
        "original": {"score": 2, "total_metrics": 6, "oos_return_pct": 1.5},
        "extended": {"status": "FAIL", "score": 1, "oos_return_pct": -3.0, "error": None},
        "gate_pass": False,
        "reasons": ["original metrics 2/6 < 4 required", "OOS return +1.5% < +4% required"],
    }


# =============================================================================
# Cache tests
# =============================================================================

def test_cache_hit_within_window():
    """Ticker tested 1 day ago with retest_after_days=3 → cached."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    history = {"FAKE.NS": {"last_tested": yesterday, "gate_pass": True}}
    assert is_within_cache(history, "FAKE.NS", retest_after_days=3) is True


def test_cache_miss_outside_window():
    """Ticker tested 5 days ago with retest_after_days=3 → re-test."""
    old_date = (date.today() - timedelta(days=5)).isoformat()
    history = {"FAKE.NS": {"last_tested": old_date, "gate_pass": True}}
    assert is_within_cache(history, "FAKE.NS", retest_after_days=3) is False


def test_cache_miss_unknown_ticker():
    assert is_within_cache({}, "UNKNOWN.NS", retest_after_days=3) is False


def test_cache_roundtrip(tmp_path):
    """load_history / save_history round-trip persists entries correctly."""
    history_file = tmp_path / "wf_test_history.json"
    entry = {"FAKE.NS": {"last_tested": "2026-07-12", "gate_pass": True}}

    import validation.post_screener_pipeline as psp
    original_path = psp.HISTORY_FILE
    psp.HISTORY_FILE = history_file
    try:
        save_history(entry)
        loaded = load_history()
        assert loaded == entry
    finally:
        psp.HISTORY_FILE = original_path


def test_load_history_missing_file(tmp_path):
    """load_history returns {} when file does not exist."""
    import validation.post_screener_pipeline as psp
    original_path = psp.HISTORY_FILE
    psp.HISTORY_FILE = tmp_path / "nonexistent.json"
    try:
        assert load_history() == {}
    finally:
        psp.HISTORY_FILE = original_path


# =============================================================================
# run_wf_gate tests
# =============================================================================

def test_run_wf_gate_parses_json():
    """run_wf_gate extracts the JSON line from subprocess stdout."""
    wf_json = _sample_wf_pass()
    mock_result = MagicMock()
    mock_result.stdout = "some preamble noise\n" + json.dumps(wf_json) + "\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_sub:
        result = run_wf_gate("FAKE.NS")

    mock_sub.assert_called_once()
    cmd = mock_sub.call_args[0][0]
    assert "--ticker" in cmd
    assert "FAKE.NS" in cmd
    assert "--json"   in cmd
    assert result == wf_json


def test_run_wf_gate_no_extended_flag():
    """--no-extended is forwarded to the subprocess command."""
    mock_result = MagicMock()
    mock_result.stdout = json.dumps(_sample_wf_pass()) + "\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_sub:
        run_wf_gate("FAKE.NS", no_extended=True)

    cmd = mock_sub.call_args[0][0]
    assert "--no-extended" in cmd


def test_run_wf_gate_raises_on_no_json():
    """RuntimeError raised when subprocess produces no JSON line."""
    mock_result = MagicMock()
    mock_result.stdout = "no json here at all\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="no JSON"):
            run_wf_gate("FAKE.NS")


# =============================================================================
# Crossover tests
# =============================================================================

def test_crossover_golden_cross():
    """Steadily rising prices → SMA20 > SMA50 → GOLDEN."""
    df = _make_ohlcv(n=60)
    with patch("validation.post_screener_pipeline.get_ohlcv", return_value=df):
        result = get_crossover("FAKE.NS")
    assert result["status"] == "GOLDEN"
    assert result["gap_pct"] > 0
    assert result["sma20"] > result["sma50"]


def test_crossover_death_cross():
    """Steadily falling prices → SMA20 < SMA50 → DEATH."""
    idx   = pd.date_range("2026-01-01", periods=60, freq="B")
    price = 200 - np.arange(60, dtype=float)  # falling
    df = pd.DataFrame({
        "open":  price * 0.99, "high": price * 1.01,
        "low":   price * 0.98, "close": price, "volume": 100_000,
    }, index=idx)
    with patch("validation.post_screener_pipeline.get_ohlcv", return_value=df):
        result = get_crossover("FAKE.NS")
    assert result["status"] == "DEATH"
    assert result["gap_pct"] < 0


def test_crossover_error_on_insufficient_data():
    """Fewer than 50 bars → status ERROR."""
    df = _make_ohlcv(n=10)
    with patch("validation.post_screener_pipeline.get_ohlcv", return_value=df):
        result = get_crossover("FAKE.NS")
    assert result["status"] == "ERROR"
    assert result["gap_pct"] is None


# =============================================================================
# Report generation tests
# =============================================================================

def _make_results(passers=1, failers=1, errors=0):
    today = date.today()
    results = []
    for i in range(passers):
        results.append({
            "ticker":    f"PASS{i}.NS",
            "source":    "fresh",
            "wf":        _sample_wf_pass(f"PASS{i}.NS"),
            "crossover": {"status": "GOLDEN", "gap_pct": 1.5, "sma20": 105, "sma50": 103},
            "error":     None,
        })
    for i in range(failers):
        results.append({
            "ticker":    f"FAIL{i}.NS",
            "source":    f"cached {(today - timedelta(days=1)).isoformat()}",
            "wf":        _sample_wf_fail(f"FAIL{i}.NS"),
            "crossover": None,
            "error":     None,
        })
    for i in range(errors):
        results.append({
            "ticker":    f"ERR{i}.NS",
            "source":    "fresh",
            "wf":        {},
            "crossover": None,
            "error":     "subprocess timeout",
        })
    return results


def test_report_has_all_sections():
    today = date.today()
    report = generate_report(today, _make_results(passers=1, failers=1, errors=1))
    assert f"# WF Batch Report — {today.isoformat()}" in report
    assert "## Summary"                                 in report
    assert "## PASSED"                                  in report
    assert "## FAILED"                                  in report
    assert "## ERRORS"                                  in report
    assert "informational only"                         in report


def test_report_summary_counts():
    today   = date.today()
    results = _make_results(passers=2, failers=3, errors=1)
    report  = generate_report(today, results)
    assert "Passed: 2" in report
    assert "Failed: 3" in report
    assert "Errors: 1" in report


def test_report_no_passed_section_when_all_fail():
    today  = date.today()
    report = generate_report(today, _make_results(passers=0, failers=2, errors=0))
    assert "## PASSED" not in report
    assert "## FAILED"  in report


def test_report_pass_row_contains_crossover():
    today  = date.today()
    report = generate_report(today, _make_results(passers=1, failers=0, errors=0))
    assert "GOLDEN" in report
    assert "PASS0.NS" in report


def test_report_fail_row_contains_reasons():
    today  = date.today()
    report = generate_report(today, _make_results(passers=0, failers=1, errors=0))
    assert "original metrics" in report
    assert "OOS return"       in report


def test_report_cached_source_label():
    today  = date.today()
    report = generate_report(today, _make_results(passers=0, failers=1, errors=0))
    assert "cached" in report


# =============================================================================
# Dry-run test — no WF calls, no API calls
# =============================================================================

def test_dry_run_makes_no_calls(capsys, tmp_path):
    """--dry-run must not call subprocess.run or get_ohlcv."""
    import validation.post_screener_pipeline as psp
    original_path = psp.HISTORY_FILE
    psp.HISTORY_FILE = tmp_path / "wf_test_history.json"
    try:
        with patch("subprocess.run") as mock_sub, \
             patch("validation.post_screener_pipeline.get_ohlcv") as mock_ohlcv:
            main(["--tickers", "FAKE.NS", "OTHER.NS", "--dry-run"])
            mock_sub.assert_not_called()
            mock_ohlcv.assert_not_called()
    finally:
        psp.HISTORY_FILE = original_path

    captured = capsys.readouterr()
    assert "DRY RUN"  in captured.out
    assert "FAKE.NS"  in captured.out
    assert "OTHER.NS" in captured.out
    assert "FRESH"    in captured.out


def test_dry_run_shows_cached_entry(capsys, tmp_path):
    """Cache hits are labeled CACHED in dry-run output."""
    import validation.post_screener_pipeline as psp
    original_path = psp.HISTORY_FILE
    psp.HISTORY_FILE = tmp_path / "wf_test_history.json"
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    psp.HISTORY_FILE.write_text(json.dumps(
        {"FAKE.NS": {"last_tested": yesterday, "gate_pass": True}}
    ))
    try:
        with patch("subprocess.run"), \
             patch("validation.post_screener_pipeline.get_ohlcv"):
            main(["--tickers", "FAKE.NS", "--dry-run"])
    finally:
        psp.HISTORY_FILE = original_path

    captured = capsys.readouterr()
    assert "CACHED"  in captured.out
    assert "FAKE.NS" in captured.out


# =============================================================================
# --limit flag
# =============================================================================

def test_limit_truncates_candidates(tmp_path):
    """--limit 1 should run exactly one WF gate call."""
    import validation.post_screener_pipeline as psp
    original_path       = psp.HISTORY_FILE
    original_report_dir = psp.REPORT_DIR
    psp.HISTORY_FILE = tmp_path / "wf_test_history.json"
    psp.REPORT_DIR   = tmp_path

    wf_pass = _sample_wf_pass("FAKE.NS")
    mock_result = MagicMock()
    mock_result.stdout = json.dumps(wf_pass) + "\n"
    mock_result.stderr = ""

    df = _make_ohlcv(n=60)
    try:
        with patch("subprocess.run", return_value=mock_result) as mock_sub, \
             patch("validation.post_screener_pipeline.get_ohlcv", return_value=df), \
             patch("time.sleep"):
            main(["--tickers", "FAKE.NS", "OTHER.NS", "--limit", "1"])

        # Only one subprocess call despite two candidates
        assert mock_sub.call_count == 1
        cmd = mock_sub.call_args[0][0]
        assert "FAKE.NS" in cmd
    finally:
        psp.HISTORY_FILE = original_path
        psp.REPORT_DIR   = original_report_dir


# =============================================================================
# Cache-skip integration
# =============================================================================

def test_cached_ticker_skips_wf_subprocess(tmp_path):
    """A ticker within retest_after_days must NOT trigger a subprocess call."""
    import validation.post_screener_pipeline as psp
    original_path       = psp.HISTORY_FILE
    original_report_dir = psp.REPORT_DIR
    psp.HISTORY_FILE = tmp_path / "wf_test_history.json"
    psp.REPORT_DIR   = tmp_path
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    psp.HISTORY_FILE.write_text(json.dumps({
        "FAKE.NS": {
            "last_tested":    yesterday,
            "gate_pass":      False,
            "original_score": 2,
            "oos_return_pct": 1.5,
            "extended_status": "SKIPPED",
            "extended_score":  None,
            "extended_oos_return_pct": None,
            "extended_error": None,
            "reasons": ["original metrics 2/6 < 4 required"],
        }
    }))
    try:
        with patch("subprocess.run") as mock_sub, \
             patch("validation.post_screener_pipeline.get_ohlcv") as mock_ohlcv, \
             patch("time.sleep"):
            main(["--tickers", "FAKE.NS", "--retest-after-days", "3"])
        mock_sub.assert_not_called()
        mock_ohlcv.assert_not_called()  # gate_pass=False → no crossover check
    finally:
        psp.HISTORY_FILE = original_path
        psp.REPORT_DIR   = original_report_dir


def test_expired_cache_triggers_fresh_run(tmp_path):
    """A ticker outside retest_after_days triggers a fresh WF run."""
    import validation.post_screener_pipeline as psp
    original_path       = psp.HISTORY_FILE
    original_report_dir = psp.REPORT_DIR
    psp.HISTORY_FILE = tmp_path / "wf_test_history.json"
    psp.REPORT_DIR   = tmp_path
    old_date = (date.today() - timedelta(days=7)).isoformat()
    psp.HISTORY_FILE.write_text(json.dumps({
        "FAKE.NS": {"last_tested": old_date, "gate_pass": False,
                    "original_score": 2, "reasons": []}
    }))

    wf_pass = _sample_wf_pass("FAKE.NS")
    mock_result = MagicMock()
    mock_result.stdout = json.dumps(wf_pass) + "\n"
    mock_result.stderr = ""
    df = _make_ohlcv(n=60)

    try:
        with patch("subprocess.run", return_value=mock_result) as mock_sub, \
             patch("validation.post_screener_pipeline.get_ohlcv", return_value=df), \
             patch("time.sleep"):
            main(["--tickers", "FAKE.NS", "--retest-after-days", "3"])
        mock_sub.assert_called_once()
    finally:
        psp.HISTORY_FILE = original_path
        psp.REPORT_DIR   = original_report_dir


# =============================================================================
# Regression: report must never land in the real validation/ directory
# =============================================================================

def test_main_never_writes_report_to_real_validation_dir(tmp_path):
    """
    Regression test: main() must NEVER write a report file into the real
    validation/ directory, even when REPORT_DIR is not explicitly patched by
    the calling test. This guards against exactly the bug found 2026-07-12,
    where three tests wrote real FAKE.NS report files into the actual project
    directory because they patched HISTORY_FILE but not REPORT_DIR.
    """
    import validation.post_screener_pipeline as psp
    real_report_dir = psp._ROOT / "validation"

    # Snapshot existing report files in the REAL directory before the test
    existing_before = set(real_report_dir.glob("wf_batch_report_*.md"))

    original_history_path = psp.HISTORY_FILE
    original_report_dir   = psp.REPORT_DIR
    psp.HISTORY_FILE = tmp_path / "wf_test_history.json"
    psp.REPORT_DIR   = tmp_path  # the actual fix under test

    wf_pass = _sample_wf_pass("FAKE.NS")
    mock_result = MagicMock()
    mock_result.stdout = json.dumps(wf_pass) + "\n"
    mock_result.stderr = ""
    df = _make_ohlcv(n=60)

    try:
        with patch("subprocess.run", return_value=mock_result), \
             patch("validation.post_screener_pipeline.get_ohlcv", return_value=df), \
             patch("time.sleep"):
            main(["--tickers", "FAKE.NS"])

        # Report must exist in tmp_path (the patched location)
        assert list(tmp_path.glob("wf_batch_report_*.md")), (
            "Report was not written to the patched REPORT_DIR"
        )
    finally:
        psp.HISTORY_FILE = original_history_path
        psp.REPORT_DIR   = original_report_dir

    # CRITICAL: confirm no NEW report file appeared in the REAL directory
    existing_after = set(real_report_dir.glob("wf_batch_report_*.md"))
    new_files = existing_after - existing_before
    assert not new_files, (
        f"main() wrote report file(s) into the REAL validation/ directory "
        f"instead of the patched REPORT_DIR: {new_files}"
    )
