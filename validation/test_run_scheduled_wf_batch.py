"""
validation/test_run_scheduled_wf_batch.py

Tests for run_scheduled_wf_batch.py.

All file I/O is redirected to tmp_path — no writes to the real project
directory. pipeline_main is always mocked — no subprocess calls, no live
API calls.
"""

import json
import sys
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import validation.run_scheduled_wf_batch as rsb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_str():
    return date.today().isoformat()


def _make_candidates(tmp_path, screen_date=None, adds=None, watches=None):
    """Write a fresh candidates file to tmp_path/screener/."""
    (tmp_path / "screener").mkdir(parents=True, exist_ok=True)
    data = {
        "screen_date":       screen_date or _today_str(),
        "run_time_ist":      "2026-07-15T18:00:00+05:30",
        "add_tickers":       adds    or [],
        "watchlist_tickers": watches or [],
    }
    path = tmp_path / "screener" / "latest_candidates.json"
    path.write_text(json.dumps(data))
    return path


def _patch_paths(stack: ExitStack, tmp_path):
    """Enter all file-path patches into the given ExitStack."""
    (tmp_path / "screener").mkdir(parents=True, exist_ok=True)
    (tmp_path / "validation").mkdir(parents=True, exist_ok=True)
    stack.enter_context(patch.object(rsb, "CANDIDATES_FILE", tmp_path / "screener"   / "latest_candidates.json"))
    stack.enter_context(patch.object(rsb, "STATUS_FILE",     tmp_path / "validation"  / "scheduled_run_status.json"))
    stack.enter_context(patch.object(rsb, "REPORT_DIR",      tmp_path / "validation"))


def _read_status(tmp_path):
    return json.loads((tmp_path / "validation" / "scheduled_run_status.json").read_text())


def _write_fake_report(tmp_path, passed=2, failed=3, errors=1):
    """Write a minimal markdown report to the patched REPORT_DIR so
    _parse_report_counts() has something real to parse."""
    (tmp_path / "validation").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "validation" / f"wf_batch_report_{_today_str()}.md"
    path.write_text(
        f"# WF Batch Report — {_today_str()}\n\n"
        f"## Summary\n"
        f"- Passed: {passed}\n"
        f"- Failed: {failed}\n"
        f"- Errors: {errors}\n"
    )


# ---------------------------------------------------------------------------
# Test 1 — missing candidates file → FAILED, pipeline not called
# ---------------------------------------------------------------------------

def test_missing_candidates_file(tmp_path):
    """When the candidates file does not exist, main() must return non-zero,
    write a FAILED status, and never call pipeline_main."""
    (tmp_path / "validation").mkdir(parents=True, exist_ok=True)
    # Do NOT create the candidates file

    with patch.object(rsb, "CANDIDATES_FILE", tmp_path / "screener" / "latest_candidates.json"), \
         patch.object(rsb, "STATUS_FILE",     tmp_path / "validation" / "scheduled_run_status.json"), \
         patch.object(rsb, "pipeline_main") as mock_pipeline:
        rc = rsb.main()

    assert rc != 0
    status = _read_status(tmp_path)
    assert status["status"] == "FAILED"
    assert "not found" in status["reason"].lower()
    mock_pipeline.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — stale candidates file → FAILED, pipeline not called
# ---------------------------------------------------------------------------

def test_stale_candidates_file(tmp_path):
    """A candidates file with screen_date older than _MAX_STALENESS_DAYS must
    be rejected with FAILED status; pipeline must not run."""
    stale_date = (date.today() - timedelta(days=rsb._MAX_STALENESS_DAYS + 1)).isoformat()
    _make_candidates(tmp_path, screen_date=stale_date, adds=["FAKE.NS"])

    with ExitStack() as stack:
        _patch_paths(stack, tmp_path)
        mock_pipeline = stack.enter_context(patch.object(rsb, "pipeline_main"))
        rc = rsb.main()

    assert rc != 0
    status = _read_status(tmp_path)
    assert status["status"] == "FAILED"
    assert "stale" in status["reason"].lower()
    mock_pipeline.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — fresh file, within ceiling → all processed, skipped=0
# ---------------------------------------------------------------------------

def test_fresh_within_ceiling(tmp_path):
    """A valid fresh file with fewer than MAX_CANDIDATES_PER_RUN tickers must
    process all of them and report candidates_skipped_ceiling=0."""
    adds    = [f"ADD{i}.NS"   for i in range(3)]
    watches = [f"WATCH{i}.NS" for i in range(2)]
    _make_candidates(tmp_path, adds=adds, watches=watches)
    _write_fake_report(tmp_path, passed=2, failed=3, errors=0)

    with ExitStack() as stack:
        _patch_paths(stack, tmp_path)
        mock_pipeline = stack.enter_context(patch.object(rsb, "pipeline_main"))
        rc = rsb.main()

    assert rc == 0
    status = _read_status(tmp_path)
    assert status["status"] == "SUCCESS"
    assert status["candidates_processed"] == 5
    assert status["candidates_skipped_ceiling"] == 0
    mock_pipeline.assert_called_once()
    # Verify all 5 tickers were passed
    call_argv = mock_pipeline.call_args[0][0]
    assert "--tickers" in call_argv
    assert len(call_argv) == 1 + 5  # "--tickers" + 5 tickers


# ---------------------------------------------------------------------------
# Test 4 — exceeds ceiling → first 10 processed, skipped reflects remainder
# ---------------------------------------------------------------------------

def test_exceeds_ceiling(tmp_path):
    """When combined ADD+WATCH count exceeds MAX_CANDIDATES_PER_RUN, exactly
    MAX_CANDIDATES_PER_RUN tickers are processed and the skipped count is correct."""
    adds    = [f"ADD{i}.NS"   for i in range(7)]
    watches = [f"WATCH{i}.NS" for i in range(6)]  # total = 13
    _make_candidates(tmp_path, adds=adds, watches=watches)
    _write_fake_report(tmp_path, passed=4, failed=6, errors=0)

    with ExitStack() as stack:
        _patch_paths(stack, tmp_path)
        mock_pipeline = stack.enter_context(patch.object(rsb, "pipeline_main"))
        rc = rsb.main()

    assert rc == 0
    status = _read_status(tmp_path)
    assert status["status"] == "SUCCESS"
    assert status["candidates_processed"] == rsb.MAX_CANDIDATES_PER_RUN
    assert status["candidates_skipped_ceiling"] == 13 - rsb.MAX_CANDIDATES_PER_RUN
    call_argv = mock_pipeline.call_args[0][0]
    # Only MAX_CANDIDATES_PER_RUN tickers should be in the argv
    ticker_args = [a for a in call_argv if a != "--tickers"]
    assert len(ticker_args) == rsb.MAX_CANDIDATES_PER_RUN


# ---------------------------------------------------------------------------
# Test 5 — correct counts propagated to status file
# ---------------------------------------------------------------------------

def test_counts_in_status(tmp_path):
    """Status file must reflect the passed/failed/errors parsed from the report."""
    _make_candidates(tmp_path, adds=["A.NS", "B.NS", "C.NS"])
    _write_fake_report(tmp_path, passed=1, failed=1, errors=1)

    with ExitStack() as stack:
        _patch_paths(stack, tmp_path)
        stack.enter_context(patch.object(rsb, "pipeline_main"))
        rc = rsb.main()

    assert rc == 0
    status = _read_status(tmp_path)
    assert status["passed"] == 1
    assert status["failed"] == 1
    assert status["errors"] == 1


# ---------------------------------------------------------------------------
# Test 6 — pipeline exception caught, status FAILED, no unhandled traceback
# ---------------------------------------------------------------------------

def test_pipeline_exception_caught(tmp_path):
    """An exception raised by pipeline_main must be caught and converted to
    a FAILED status; the wrapper must not propagate an unhandled traceback."""
    _make_candidates(tmp_path, adds=["CRASH.NS"])

    with ExitStack() as stack:
        _patch_paths(stack, tmp_path)
        stack.enter_context(patch.object(rsb, "pipeline_main", side_effect=RuntimeError("WF subprocess crashed")))
        rc = rsb.main()  # must not raise

    assert rc != 0
    status = _read_status(tmp_path)
    assert status["status"] == "FAILED"
    assert "RuntimeError" in status["reason"]
    assert "WF subprocess crashed" in status["reason"]


# ---------------------------------------------------------------------------
# Test 7 — status file never written to real validation directory
# ---------------------------------------------------------------------------

def test_status_never_written_to_real_dir(tmp_path):
    """Regression: status file must never land in the real validation/
    directory when the test patches STATUS_FILE."""
    real_status = _ROOT / "validation" / "scheduled_run_status.json"
    existed_before = real_status.exists()

    _make_candidates(tmp_path, adds=["FAKE.NS"])
    _write_fake_report(tmp_path)

    with ExitStack() as stack:
        _patch_paths(stack, tmp_path)
        stack.enter_context(patch.object(rsb, "pipeline_main"))
        rsb.main()

    # The real file must not have been created (or if it already existed, unchanged)
    if not existed_before:
        assert not real_status.exists(), (
            "Status file was written to the real validation/ directory!"
        )
