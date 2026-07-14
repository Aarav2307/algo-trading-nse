"""
screener/test_candidates_file.py

Tests for _write_candidates_file() in auto_screener.py.

No live API calls — all Kite access mocked.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from screener.auto_screener import _write_candidates_file, CANDIDATES_FILE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_results(adds=None, watches=None):
    """Minimal run_screen() result dict with the fields _write_candidates_file reads."""
    return {
        "adds":    adds    or [],
        "watches": watches or [],
        "monitors": [],
        "removes": [],
        "meta": {},
    }


# ---------------------------------------------------------------------------
# Test 1 — happy path: correct JSON schema and field values
# ---------------------------------------------------------------------------

def test_writes_correct_schema(tmp_path):
    """Written file must have the exact expected JSON schema."""
    import screener.auto_screener as asc

    results = _sample_results(
        adds=[{"ticker": "HAL.NS",   "hurst": 0.51, "adx": 28.0, "gap_short": -1.2, "gap_long": -3.4, "avg_corr": 0.3}],
        watches=[{"ticker": "ITC.NS", "hurst": 0.49, "adx": 25.5, "gap_short": -5.6, "gap_long": -7.8, "avg_corr": 0.2}],
    )

    out = tmp_path / "latest_candidates.json"
    with patch.object(asc, "CANDIDATES_FILE", out):
        _write_candidates_file(results)

    assert out.exists()
    data = json.loads(out.read_text())

    assert "screen_date" in data
    assert "run_time_ist" in data
    assert data["add_tickers"] == ["HAL.NS"]
    assert data["watchlist_tickers"] == ["ITC.NS"]


# ---------------------------------------------------------------------------
# Test 2 — atomic write: no .tmp file left behind after success
# ---------------------------------------------------------------------------

def test_no_tmp_file_left_after_success(tmp_path):
    """The .tmp file must be cleaned up (via rename) after a successful write."""
    import screener.auto_screener as asc

    out = tmp_path / "latest_candidates.json"
    tmp_sentinel = tmp_path / "latest_candidates.json.tmp"

    results = _sample_results(adds=[{"ticker": "X.NS", "hurst": 0.5, "adx": 26.0, "gap_short": -2.0, "gap_long": -4.0, "avg_corr": 0.3}])

    with patch.object(asc, "CANDIDATES_FILE", out):
        _write_candidates_file(results)

    assert out.exists(),            "Final file must exist after successful write"
    assert not tmp_sentinel.exists(), ".tmp file must NOT exist after rename completes"


# ---------------------------------------------------------------------------
# Test 3 — fail-open: write failure does NOT propagate
# ---------------------------------------------------------------------------

def test_write_failure_does_not_propagate(tmp_path):
    """A failure during the atomic write must be swallowed, not raised.
    The screener run must continue regardless."""
    import screener.auto_screener as asc

    results = _sample_results(adds=[{"ticker": "X.NS", "hurst": 0.5, "adx": 26.0, "gap_short": -2.0, "gap_long": -4.0, "avg_corr": 0.3}])

    out = tmp_path / "latest_candidates.json"
    with patch.object(asc, "CANDIDATES_FILE", out), \
         patch("builtins.open", side_effect=OSError("disk full")):
        # Must not raise — fail-open
        _write_candidates_file(results)

    assert not out.exists(), "File should not exist when write failed"


# ---------------------------------------------------------------------------
# Test 4 — empty lists: zero candidates must not crash
# ---------------------------------------------------------------------------

def test_empty_adds_and_watches(tmp_path):
    """Screener run that finds zero candidates must produce a valid file with
    empty lists, not crash."""
    import screener.auto_screener as asc

    results = _sample_results(adds=[], watches=[])
    out = tmp_path / "latest_candidates.json"

    with patch.object(asc, "CANDIDATES_FILE", out):
        _write_candidates_file(results)

    data = json.loads(out.read_text())
    assert data["add_tickers"] == []
    assert data["watchlist_tickers"] == []
