"""
utils/test_news_monitor.py — Unit tests for news_monitor.py and
signal_runner's load_news_flags().

Run: python utils/test_news_monitor.py
All 16 must pass.
"""

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from utils.news_monitor import (
    fetch_earnings_flags,
    fetch_surveillance_flags,
    run_monitor,
    _get_universe,
    _log_crash,
    ERROR_LOG_FILE,
    NEWS_FLAGS_FILE,
    MANUAL_BLOCKS_FILE,
)

_PASS = 0
_FAIL = 0


def _pass(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  PASS  {name}")


def _fail(name: str, msg: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  FAIL  {name}: {msg}")


def _mock_get_response(text: str, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response-like object."""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# =============================================================================
# Test 1 — Surveillance detection from sec_list CSV data
# =============================================================================

def test_1_surveillance_detection() -> None:
    name = "Test 1 — Surveillance detection from sec_list data"

    csv_text = (
        "Symbol,Series,Security Name,Band,Remarks\n"
        "BAND2STOCK,EQ,Band Two Ltd,2,Short Term ASM\n"
        "CLEANSTOCK,EQ,Clean Stock Ltd,20,\n"
        "GSMSTOCK,EQ,GSM Stage Ltd,No Band,GSM Stage 3\n"
        "BAND20,EQ,Normal Band Ltd,20,Normal surveillance\n"
    )
    mock_resp = _mock_get_response(csv_text)

    universe = [
        "BAND2STOCK.NS", "CLEANSTOCK.NS", "GSMSTOCK.NS", "BAND20.NS"
    ]

    with patch("requests.get", return_value=mock_resp):
        flags = fetch_surveillance_flags(universe)

    # Band='2' → should be flagged
    if "BAND2STOCK.NS" not in flags:
        _fail(name, "BAND2STOCK.NS (Band=2) should be flagged as SURVEILLANCE")
        return
    if flags["BAND2STOCK.NS"]["type"] != "SURVEILLANCE":
        _fail(name, f"Expected type=SURVEILLANCE, got {flags['BAND2STOCK.NS']['type']}")
        return
    if not flags["BAND2STOCK.NS"]["auto_block"]:
        _fail(name, "Surveillance flag must have auto_block=True")
        return

    # Band='20' with no GSM → should NOT be flagged
    if "CLEANSTOCK.NS" in flags:
        _fail(name, "CLEANSTOCK.NS (Band=20, no GSM) should NOT be flagged")
        return
    if "BAND20.NS" in flags:
        _fail(name, "BAND20.NS (Band=20) should NOT be flagged")
        return

    # GSM in Remarks → should be flagged
    if "GSMSTOCK.NS" not in flags:
        _fail(name, "GSMSTOCK.NS (GSM in Remarks) should be flagged as SURVEILLANCE")
        return

    _pass(name)


# =============================================================================
# Test 2 — Surveillance fails open on network error
# =============================================================================

def test_2_surveillance_fails_open() -> None:
    name = "Test 2 — Surveillance fails open on network error"

    with patch("requests.get", side_effect=Exception("Connection refused")):
        flags = fetch_surveillance_flags(["BAJAJ-AUTO.NS", "HCLTECH.NS"])

    if flags != {}:
        _fail(name, f"Expected empty dict on network error, got {flags}")
        return

    _pass(name)


# =============================================================================
# Test 3 — Earnings flag detection (results meeting within 5 trading days)
# =============================================================================

def test_3_earnings_flag_detection() -> None:
    name = "Test 3 — Earnings flag detection"

    # Board meeting 3 calendar days ahead (will include trading days)
    meeting_date = date.today() + timedelta(days=3)
    meeting_str  = meeting_date.strftime("%d-%b-%Y")

    api_response = [
        {
            "bm_symbol": "BAJAJ-AUTO",
            "bm_date":   meeting_str,
            "bm_desc":   "Board Meeting to consider Unaudited Financial Results for Q1",
        }
    ]

    mock_session = MagicMock()
    mock_session.get.return_value = _mock_get_response(json.dumps(api_response))
    # First call (homepage for cookie) should also succeed
    mock_session.get.side_effect = None

    api_resp  = MagicMock()
    api_resp.raise_for_status = MagicMock()
    api_resp.json.return_value = api_response

    homepage_resp = MagicMock()
    homepage_resp.raise_for_status = MagicMock()

    mock_session.get.side_effect = [homepage_resp, api_resp]

    with patch("requests.Session", return_value=mock_session):
        flags = fetch_earnings_flags(["BAJAJ-AUTO.NS"], trading_days_ahead=5)

    if "BAJAJ-AUTO.NS" not in flags:
        _fail(name, f"BAJAJ-AUTO.NS should be flagged for earnings; got flags={flags}")
        return
    flag = flags["BAJAJ-AUTO.NS"]
    if flag["type"] != "EARNINGS_RISK":
        _fail(name, f"Expected type=EARNINGS_RISK, got {flag['type']}")
        return
    if flag["auto_block"]:
        _fail(name, "Earnings risk must NOT auto-block (auto_block=False)")
        return
    if flag["meeting_date"] != meeting_str:
        _fail(name, f"Expected meeting_date={meeting_str}, got {flag['meeting_date']}")
        return

    _pass(name)


# =============================================================================
# Test 4 — Earnings flag skips non-results meetings (AGM only)
# =============================================================================

def test_4_earnings_skips_non_results() -> None:
    name = "Test 4 — Earnings flag skips non-results meetings"

    meeting_date = date.today() + timedelta(days=2)
    api_response = [
        {
            "bm_symbol": "BAJAJ-AUTO",
            "bm_date":   meeting_date.strftime("%d-%b-%Y"),
            "bm_desc":   "Annual General Meeting to transact ordinary business",
        }
    ]

    api_resp = MagicMock()
    api_resp.raise_for_status = MagicMock()
    api_resp.json.return_value = api_response

    homepage_resp = MagicMock()
    homepage_resp.raise_for_status = MagicMock()

    mock_session = MagicMock()
    mock_session.get.side_effect = [homepage_resp, api_resp]

    with patch("requests.Session", return_value=mock_session):
        flags = fetch_earnings_flags(["BAJAJ-AUTO.NS"], trading_days_ahead=5)

    if "BAJAJ-AUTO.NS" in flags:
        _fail(name, "AGM-only meeting should NOT be flagged as earnings risk")
        return

    _pass(name)


# =============================================================================
# Test 5 — Earnings fails open on network error
# =============================================================================

def test_5_earnings_fails_open() -> None:
    name = "Test 5 — Earnings fails open on network error"

    mock_session = MagicMock()
    mock_session.get.side_effect = Exception("NSE API timeout")

    with patch("requests.Session", return_value=mock_session):
        flags = fetch_earnings_flags(["BAJAJ-AUTO.NS"])

    if flags != {}:
        _fail(name, f"Expected empty dict on network error, got {flags}")
        return

    _pass(name)


# =============================================================================
# Test 6 — Manual block loaded and applied
# =============================================================================

def test_6_manual_block_applied() -> None:
    name = "Test 6 — Manual block loaded and applied"

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    manual   = {
        "HCLTECH.NS": {
            "blocked_until": tomorrow,
            "reason":        "Earnings risk — manually blocking before Q1 results",
        }
    }

    tmp_manual = tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", delete=False
    )
    json.dump(manual, tmp_manual)
    tmp_manual.close()

    tmp_flags = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp_flags.close()
    tmp_flags_path = Path(tmp_flags.name)

    try:
        # Mock out the network calls so only manual block logic is tested
        with (
            patch("utils.news_monitor.fetch_surveillance_flags", return_value={}),
            patch("utils.news_monitor.fetch_earnings_flags",     return_value={}),
            patch("utils.news_monitor.MANUAL_BLOCKS_FILE",       Path(tmp_manual.name)),
            patch("utils.news_monitor.NEWS_FLAGS_FILE",          tmp_flags_path),
        ):
            result = run_monitor(["HCLTECH.NS", "COLPAL.NS"])

        flags = result.get("flags", {})
        if "HCLTECH.NS" not in flags:
            _fail(name, "HCLTECH.NS should be in flags as MANUAL_BLOCK")
            return
        flag = flags["HCLTECH.NS"]
        if flag["type"] != "MANUAL_BLOCK":
            _fail(name, f"Expected type=MANUAL_BLOCK, got {flag['type']}")
            return
        if not flag["auto_block"]:
            _fail(name, "Manual block must have auto_block=True")
            return
        if "COLPAL.NS" in flags:
            _fail(name, "COLPAL.NS should not be in flags (no block)")
            return

        _pass(name)
    finally:
        os.unlink(tmp_manual.name)
        if tmp_flags_path.exists():
            os.unlink(tmp_flags_path)


# =============================================================================
# Test 7 — Expired manual block ignored
# =============================================================================

def test_7_expired_manual_block_ignored() -> None:
    name = "Test 7 — Expired manual block ignored"

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    manual    = {
        "HCLTECH.NS": {
            "blocked_until": yesterday,
            "reason":        "Old block — should be ignored",
        }
    }

    tmp_manual = tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", delete=False
    )
    json.dump(manual, tmp_manual)
    tmp_manual.close()

    tmp_flags = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp_flags.close()
    tmp_flags_path = Path(tmp_flags.name)

    try:
        with (
            patch("utils.news_monitor.fetch_surveillance_flags", return_value={}),
            patch("utils.news_monitor.fetch_earnings_flags",     return_value={}),
            patch("utils.news_monitor.MANUAL_BLOCKS_FILE",       Path(tmp_manual.name)),
            patch("utils.news_monitor.NEWS_FLAGS_FILE",          tmp_flags_path),
        ):
            result = run_monitor(["HCLTECH.NS"])

        flags = result.get("flags", {})
        if "HCLTECH.NS" in flags:
            _fail(name, "Expired manual block should NOT appear in flags")
            return

        _pass(name)
    finally:
        os.unlink(tmp_manual.name)
        if tmp_flags_path.exists():
            os.unlink(tmp_flags_path)


# =============================================================================
# Test 8 — signal_runner load_news_flags() fails open when file missing
# =============================================================================

def test_8_load_news_flags_fails_open() -> None:
    name = "Test 8 — load_news_flags() fails open when file missing"

    try:
        from paper_trading.signal_runner import load_news_flags, NEWS_FLAGS_FILE as SIG_FLAGS_FILE
    except Exception as e:
        _fail(name, f"Import failed: {e}")
        return

    nonexistent = Path(tempfile.mktemp(suffix=".json"))
    assert not nonexistent.exists()

    with patch("paper_trading.signal_runner.NEWS_FLAGS_FILE", nonexistent):
        flags = load_news_flags()

    if flags != {}:
        _fail(name, f"Expected empty dict when file missing, got {flags}")
        return

    _pass(name)


# =============================================================================
# Test 9 — signal_runner load_news_flags() warns on stale file
# =============================================================================

def test_9_load_news_flags_warns_on_stale() -> None:
    name = "Test 9 — load_news_flags() warns on stale file"

    try:
        from paper_trading.signal_runner import load_news_flags
    except Exception as e:
        _fail(name, f"Import failed: {e}")
        return

    stale_time = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    stale_data = {
        "generated_at":     stale_time,
        "universe_checked": ["BAJAJ-AUTO.NS"],
        "flags": {
            "BAJAJ-AUTO.NS": {
                "type":       "SURVEILLANCE",
                "detail":     "Band=2, Remarks=ASM",
                "auto_block": True,
                "source":     "NSE sec_list.csv",
            }
        },
    }

    tmp = tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", delete=False
    )
    json.dump(stale_data, tmp)
    tmp.close()
    tmp_path = Path(tmp.name)

    captured = []
    original_print = print

    def capturing_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args)
        captured.append(msg)
        original_print(*args, **kwargs)

    try:
        import builtins
        with (
            patch("paper_trading.signal_runner.NEWS_FLAGS_FILE", tmp_path),
            patch("builtins.print", side_effect=capturing_print),
        ):
            flags = load_news_flags()

        # Must return the flags despite staleness
        if "BAJAJ-AUTO.NS" not in flags:
            _fail(name, "Stale flags file should still return its flags")
            return

        # Must have printed a staleness warning
        stale_warned = any("stale" in msg.lower() or "old" in msg.lower() for msg in captured)
        if not stale_warned:
            _fail(name, f"Expected staleness warning in output; got: {captured}")
            return

        _pass(name)
    finally:
        os.unlink(tmp.name)


# =============================================================================
# Tests 10-13 — weekday-aware staleness logic in _expected_last_news_monitor_date
# =============================================================================

from paper_trading.signal_runner import _expected_last_news_monitor_date as _exp_date


def test_10_monday_expects_friday_run():
    """On a Monday, a flags file from the prior Friday must be fresh."""
    monday = date(2026, 7, 6)   # weekday() == 0 (Monday)
    friday = date(2026, 7, 3)   # weekday() == 4 (Friday)
    assert monday.weekday() == 0
    assert friday.weekday() == 4
    assert _exp_date(monday) == friday


def test_11_tuesday_expects_monday_run():
    """On a Tuesday, only yesterday's (Monday) run counts as fresh."""
    tuesday = date(2026, 7, 7)  # weekday() == 1 (Tuesday)
    monday  = date(2026, 7, 6)  # weekday() == 0 (Monday)
    assert tuesday.weekday() == 1
    assert monday.weekday() == 0
    assert _exp_date(tuesday) == monday


def test_12_monday_flags_from_thursday_is_stale():
    """A flags file older than the expected Friday run must warn on Monday."""
    monday   = date(2026, 7, 6)
    thursday = date(2026, 7, 2)  # Thursday — older than expected Friday Jul 3
    assert thursday.weekday() == 3
    assert thursday < _exp_date(monday)  # Thursday is before expected Friday


def test_13_wednesday_flags_from_monday_is_stale():
    """On a Wednesday, a flags file from Monday (2 days old) should warn,
    since Tuesday's run should have refreshed it."""
    wednesday = date(2026, 7, 8)  # weekday() == 2 (Wednesday)
    monday    = date(2026, 7, 6)  # weekday() == 0 (Monday)
    tuesday   = date(2026, 7, 7)  # weekday() == 1 (Tuesday) — what Wednesday expects
    assert wednesday.weekday() == 2
    assert _exp_date(wednesday) == tuesday
    assert monday < tuesday  # Monday is before expected Tuesday → stale


# =============================================================================
# Test 14 — integration: Monday reading Friday's flags must NOT warn
# =============================================================================

def test_14_load_news_flags_no_warning_on_monday_reading_friday_flags() -> None:
    """
    Integration-level regression test for the original bug: a Monday run
    reading the preceding Friday's flags file must NOT print a staleness
    warning, even though the file is ~65 hours old by clock time.
    """
    name = "test_14_load_news_flags_no_warning_on_monday_reading_friday_flags"
    try:
        import paper_trading.signal_runner as sr
    except Exception as e:
        _fail(name, f"Import failed: {e}")
        return

    friday_generated_at = "2026-07-03 18:30"  # a real Friday
    monday_today = date(2026, 7, 6)            # a real Monday
    assert monday_today.weekday() == 0  # sanity check it's really Monday

    flags_data = {
        "generated_at":     friday_generated_at,
        "universe_checked": ["BAJAJ-AUTO.NS"],
        "flags":            {},
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(flags_data, tmp)
        tmp_path = tmp.name

    try:
        with patch.object(sr, "NEWS_FLAGS_FILE", Path(tmp_path)), \
             patch("paper_trading.signal_runner.date") as mock_date:
            mock_date.today.return_value = monday_today
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            captured = io.StringIO()
            with redirect_stdout(captured):
                result = sr.load_news_flags()

            output = captured.getvalue()
            if "WARNING" in output and "stale" in output.lower():
                _fail(name, f"Expected no staleness warning, got: {output!r}")
                return
    finally:
        os.unlink(tmp_path)

    _pass(name)


# =============================================================================
# Test 15 — universe source: _get_universe() must equal signal_runner.STOCKS
# =============================================================================

def test_15_universe_is_signal_runner_stocks() -> None:
    """_get_universe() must return signal_runner.STOCKS, not portfolio_state positions."""
    name = "test_15_universe_is_signal_runner_stocks"
    try:
        from paper_trading.signal_runner import STOCKS
        result = _get_universe()
        expected = list(STOCKS)
        if result != expected:
            _fail(name, f"Got {result!r}, expected {expected!r}")
            return
    except Exception as e:
        _fail(name, f"Exception: {e}")
        return
    _pass(name)


# =============================================================================
# Test 16 — crash logging: _log_crash() writes traceback to ERROR_LOG_FILE
# =============================================================================

def test_16_crash_is_logged_to_error_file(tmp_path) -> None:
    """_log_crash() must write the exception traceback to ERROR_LOG_FILE.
    Verified by patching ERROR_LOG_FILE to a tmp_path location."""
    name = "test_16_crash_is_logged_to_error_file"
    log_path = tmp_path / "news_monitor_errors.log"
    import utils.news_monitor as nm
    try:
        with patch.object(nm, "ERROR_LOG_FILE", log_path):
            try:
                raise RuntimeError("simulated news_monitor crash")
            except RuntimeError as exc:
                nm._log_crash(exc)

        if not log_path.exists():
            _fail(name, "ERROR_LOG_FILE was not created")
            return
        content = log_path.read_text()
        if "RuntimeError" not in content:
            _fail(name, f"Expected 'RuntimeError' in log, got: {content!r}")
            return
        if "simulated news_monitor crash" not in content:
            _fail(name, f"Expected crash message in log, got: {content!r}")
            return
    except Exception as e:
        _fail(name, f"Exception: {e}")
        return
    _pass(name)


# =============================================================================
# Runner
# =============================================================================

if __name__ == "__main__":
    print()
    print("=" * 55)
    print("  News Monitor Unit Tests")
    print("=" * 55)

    test_1_surveillance_detection()
    test_2_surveillance_fails_open()
    test_3_earnings_flag_detection()
    test_4_earnings_skips_non_results()
    test_5_earnings_fails_open()
    test_6_manual_block_applied()
    test_7_expired_manual_block_ignored()
    test_8_load_news_flags_fails_open()
    test_9_load_news_flags_warns_on_stale()
    test_10_monday_expects_friday_run()
    test_11_tuesday_expects_monday_run()
    test_12_monday_flags_from_thursday_is_stale()
    test_13_wednesday_flags_from_monday_is_stale()
    test_14_load_news_flags_no_warning_on_monday_reading_friday_flags()
    test_15_universe_is_signal_runner_stocks()
    # test_16 uses pytest tmp_path fixture — runs via pytest only

    print()
    print(f"  Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 55)
    print()

    if _FAIL:
        sys.exit(1)
