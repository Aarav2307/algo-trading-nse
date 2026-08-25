"""
validation/test_system_health_check.py — Tests for system_health_check.py.

All file reads use tmp_path; no live server state or real log files needed.
Universe imports (_load_stocks, _load_screener_universe) are mocked.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import validation.system_health_check as shc


# ── Shared fixtures / helpers ─────────────────────────────────────────────────

_TWO_STOCKS = ["BAJAJ-AUTO.NS", "HCLTECH.NS"]

def _screen_log(log_dir: Path, date_str: str, exit_code: int = 0) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"screen_{date_str}.log").write_text(
        f"[screener] Running...\n"
        f"Screen run completed: {date_str} | Exit code: {exit_code}\n"
    )

def _runner_log(log_dir: Path, date_str: str, completed: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    tail = f"[run_daily] Completed: {date_str} 10:00:00\n" if completed else "Error!\n"
    (log_dir / f"{date_str}.log").write_text(f"[run_daily] Running...\n{tail}")

def _news_log(log_dir: Path, date_str: str, completed: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    tail = f"News monitor completed: {date_str} 13:00:00\n" if completed else "Error!\n"
    (log_dir / f"{date_str}_news.log").write_text(f"[news_monitor] Running...\n{tail}")

def _morning_log(log_dir: Path, date_str: str, completed: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    tail = f"Morning fill check completed: {date_str} 03:50:00\n" if completed else "Error!\n"
    (log_dir / f"{date_str}_morning.log").write_text(f"Morning fill check started...\n{tail}")

# Minimal clean tracker (no _meta key needed for tests, but include it)
_TRACKER_CLEAN = {
    "_meta": {"last_screen_date": "2026-07-15", "screen_count": 8},
    "BAJAJ-AUTO.NS": {
        "consecutive_flags": 0, "last_flagged": None,
        "last_screen_date": "2026-07-15", "flag_history": [], "flag_annotations": [],
    },
    "HCLTECH.NS": {
        "consecutive_flags": 0, "last_flagged": None,
        "last_screen_date": "2026-07-15", "flag_history": [], "flag_annotations": [],
    },
}


# ── Check 1: Screener cadence ─────────────────────────────────────────────────

def _recent(days_ago: int = 2) -> str:
    """
    A date inside check_run_completion's rolling window.

    The run-completion tests previously pinned "2026-07-15". That was incidental
    to what they assert (does a missing completion marker produce FAIL?), but
    once _RUN_COMPLETION_WINDOW_DAYS was introduced the fixed date aged out of
    the window and they began passing vacuously against zero logs. Relative
    dates keep the original intent testable indefinitely. No assertion changed.
    """
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_screener_cadence_pass_no_gaps(tmp_path):
    log_dir = tmp_path / "logs"
    # Jun 17 (Wed) and Jun 21 (Sun) 2026 — no expected dates missing between them
    for d in ["2026-06-17", "2026-06-21"]:
        _screen_log(log_dir, d)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_screener_cadence()
    assert r.status == "PASS"
    assert "no gaps" in r.message
    assert len(r.details["dates"]) == 2


def test_screener_cadence_fail_gap(tmp_path):
    log_dir = tmp_path / "logs"
    # Jun 17 (Wed) → Jun 28 (Sun): Jun 21 (Sun) and Jun 24 (Wed) are missing
    for d in ["2026-06-17", "2026-06-28"]:
        _screen_log(log_dir, d)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_screener_cadence()
    assert r.status == "FAIL"
    assert "2026-06-21" in r.details["missing"]
    assert "2026-06-24" in r.details["missing"]


def test_screener_cadence_fail_no_logs(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_screener_cadence()
    assert r.status == "FAIL"
    assert "No screener logs" in r.message


def test_screener_cadence_off_schedule_noted_not_failed(tmp_path):
    """An extra off-Wed/Sun run is noted in the message but does not cause FAIL."""
    log_dir = tmp_path / "logs"
    # Jun 17 (Wed), Jun 19 (Fri — off-schedule), Jun 21 (Sun)
    for d in ["2026-06-17", "2026-06-19", "2026-06-21"]:
        _screen_log(log_dir, d)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_screener_cadence()
    assert r.status == "PASS"
    assert "off-schedule" in r.message


# ── Check 2: Run completion ───────────────────────────────────────────────────

def test_run_completion_pass_all_clean(tmp_path):
    log_dir = tmp_path / "logs"
    _screen_log(log_dir, _recent())
    _runner_log(log_dir, _recent())
    _news_log(log_dir,   _recent())
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "PASS"
    assert "1/1 clean" in r.message  # appears three times (once per log type)


def test_run_completion_fail_screener_nonzero_exit(tmp_path):
    log_dir = tmp_path / "logs"
    _screen_log(log_dir, _recent(), exit_code=1)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "FAIL"
    assert any("Screener" in i for i in r.details["issues"])


def test_run_completion_fail_runner_no_marker(tmp_path):
    log_dir = tmp_path / "logs"
    _runner_log(log_dir, _recent(), completed=False)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "FAIL"
    assert any("Signal-runner" in i for i in r.details["issues"])


def test_run_completion_fail_news_no_marker(tmp_path):
    log_dir = tmp_path / "logs"
    _news_log(log_dir, _recent(), completed=False)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "FAIL"
    assert any("News monitor" in i for i in r.details["issues"])


def test_run_completion_excludes_morning_logs_from_runner_check(tmp_path):
    """Morning logs (YYYY-MM-DD_morning.log) must not be checked as signal-runner logs."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Write a morning log that would fail the runner check — must be silently ignored
    (log_dir / "2026-07-15_morning.log").write_text("Morning fill check completed: 2026-07-15 03:50:00\n")
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "PASS"
    assert "0/0 clean" in r.message or "Signal-runner 0/0" in r.message


def test_run_completion_pass_morning_check_clean(tmp_path):
    """Morning fill check logs with a valid completion marker must count as clean."""
    log_dir = tmp_path / "logs"
    _morning_log(log_dir, _recent())
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "PASS"
    assert "Morning fill check 1/1 clean" in r.message


def test_run_completion_fail_morning_no_marker(tmp_path):
    """Previously untracked entirely: a morning log missing its completion marker
    must now surface as a FAIL, the same way the other three log types already do."""
    log_dir = tmp_path / "logs"
    _morning_log(log_dir, _recent(), completed=False)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "FAIL"
    assert any("Morning fill check" in i for i in r.details["issues"])


# ── Check 3: Universe consistency ─────────────────────────────────────────────

def test_universe_consistency_pass(tmp_path):
    stocks   = ["BAJAJ-AUTO.NS", "HCLTECH.NS", "COLPAL.NS"]
    universe = ["COLPAL.NS", "BAJAJ-AUTO.NS", "HCLTECH.NS"]   # same, different order
    with patch.object(shc, "_load_stocks",            return_value=stocks), \
         patch.object(shc, "_load_screener_universe", return_value=universe):
        r = shc.check_universe_consistency()
    assert r.status == "PASS"
    assert "MATCH" in r.message


def test_universe_consistency_fail_mismatch(tmp_path):
    with patch.object(shc, "_load_stocks",            return_value=["A.NS", "B.NS"]), \
         patch.object(shc, "_load_screener_universe", return_value=["A.NS", "C.NS"]):
        r = shc.check_universe_consistency()
    assert r.status == "FAIL"
    assert "MISMATCH" in r.message
    assert "B.NS" in r.details["only_stocks"]
    assert "C.NS" in r.details["only_universe"]


# ── Check 4: Degradation tracker ─────────────────────────────────────────────

def test_degradation_tracker_pass_clean(tmp_path):
    tracker_file = tmp_path / "degradation_tracker.json"
    tracker_file.write_text(json.dumps(_TRACKER_CLEAN))
    with patch.object(shc, "DEGRADATION_TRACKER", tracker_file), \
         patch.object(shc, "_load_stocks", return_value=_TWO_STOCKS):
        r = shc.check_degradation_tracker()
    assert r.status == "PASS"
    assert r.details["orphaned"] == []
    assert r.details["missing_from_tracker"] == []
    assert r.details["flagged_for_remove"] == []


def test_degradation_tracker_fail_orphaned_entry(tmp_path):
    tracker = json.loads(json.dumps(_TRACKER_CLEAN))
    tracker["OLDSTOCK.NS"] = {
        "consecutive_flags": 0, "last_flagged": None,
        "last_screen_date": "2026-07-15", "flag_history": [], "flag_annotations": [],
    }
    tracker_file = tmp_path / "degradation_tracker.json"
    tracker_file.write_text(json.dumps(tracker))
    with patch.object(shc, "DEGRADATION_TRACKER", tracker_file), \
         patch.object(shc, "_load_stocks", return_value=_TWO_STOCKS):
        r = shc.check_degradation_tracker()
    assert r.status == "FAIL"
    assert "OLDSTOCK.NS" in r.details["orphaned"]


def test_degradation_tracker_fail_consecutive_flags_gte_2(tmp_path):
    tracker = json.loads(json.dumps(_TRACKER_CLEAN))
    tracker["BAJAJ-AUTO.NS"]["consecutive_flags"] = 2
    tracker_file = tmp_path / "degradation_tracker.json"
    tracker_file.write_text(json.dumps(tracker))
    with patch.object(shc, "DEGRADATION_TRACKER", tracker_file), \
         patch.object(shc, "_load_stocks", return_value=_TWO_STOCKS):
        r = shc.check_degradation_tracker()
    assert r.status == "FAIL"
    assert "BAJAJ-AUTO.NS" in r.details["flagged_for_remove"]


def test_degradation_tracker_warn_missing_entry(tmp_path):
    """A ticker in STOCKS but not in the tracker → WARN (expected after a new addition)."""
    tracker_file = tmp_path / "degradation_tracker.json"
    tracker_file.write_text(json.dumps(_TRACKER_CLEAN))
    universe = _TWO_STOCKS + ["NEWSTOCK.NS"]    # NEWSTOCK.NS not in tracker
    with patch.object(shc, "DEGRADATION_TRACKER", tracker_file), \
         patch.object(shc, "_load_stocks", return_value=universe):
        r = shc.check_degradation_tracker()
    assert r.status == "WARN"
    assert "NEWSTOCK.NS" in r.details["missing_from_tracker"]


def test_degradation_tracker_pass_notes_single_flag(tmp_path):
    """A ticker with consecutive_flags == 1 → PASS but noted in message."""
    tracker = json.loads(json.dumps(_TRACKER_CLEAN))
    tracker["HCLTECH.NS"]["consecutive_flags"] = 1
    tracker_file = tmp_path / "degradation_tracker.json"
    tracker_file.write_text(json.dumps(tracker))
    with patch.object(shc, "DEGRADATION_TRACKER", tracker_file), \
         patch.object(shc, "_load_stocks", return_value=_TWO_STOCKS):
        r = shc.check_degradation_tracker()
    assert r.status == "PASS"
    assert "HCLTECH.NS" in str(r.details["watch_1_flag"])


# ── Check 5: Candidates freshness ─────────────────────────────────────────────

def test_candidates_freshness_pass_recent(tmp_path):
    f = tmp_path / "latest_candidates.json"
    f.write_text(json.dumps({
        "screen_date":       (date.today() - timedelta(days=2)).isoformat(),
        "run_time_ist":      "2026-07-15T18:00:00+05:30",
        "add_tickers":       ["NEWSTOCK.NS"],
        "watchlist_tickers": ["WATCHME.NS"],
    }))
    with patch.object(shc, "CANDIDATES_FILE", f):
        r = shc.check_candidates_freshness()
    assert r.status == "PASS"
    assert "2 ADD" in r.message or "1 ADD" in r.message


def test_candidates_freshness_warn_not_found(tmp_path):
    missing = tmp_path / "latest_candidates.json"    # does not exist
    with patch.object(shc, "CANDIDATES_FILE", missing):
        r = shc.check_candidates_freshness()
    assert r.status == "WARN"
    assert "not found" in r.message


def test_candidates_freshness_warn_stale(tmp_path):
    f = tmp_path / "latest_candidates.json"
    f.write_text(json.dumps({
        "screen_date":       (date.today() - timedelta(days=10)).isoformat(),
        "run_time_ist":      "2026-07-01T18:00:00+05:30",
        "add_tickers":       [],
        "watchlist_tickers": [],
    }))
    with patch.object(shc, "CANDIDATES_FILE", f):
        r = shc.check_candidates_freshness()
    assert r.status == "WARN"
    assert "stale" in r.message


# ── Check 6: Relative-path constants ─────────────────────────────────────────

def test_relative_path_scan_pass_clean_file(tmp_path):
    clean = tmp_path / "clean.py"
    clean.write_text(
        '_ROOT = Path(__file__).parent.parent\n'
        'SOME_FILE = _ROOT / "utils" / "something.json"\n'
        'ABS_FILE  = Path("/absolute/path")\n'
    )
    with patch.object(shc, "_discover_scan_files", return_value=[clean]):
        r = shc.check_relative_path_constants()
    assert r.status == "PASS"
    assert "0 relative-path" in r.message


def test_relative_path_scan_warn_finds_relative(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('SOME_FILE = Path("utils/some.json")\n')
    with patch.object(shc, "_discover_scan_files", return_value=[bad]):
        r = shc.check_relative_path_constants()
    assert r.status == "WARN"
    assert len(r.details["hits"]) == 1
    assert "utils/some.json" in r.details["hits"][0]


def test_relative_path_scan_skips_commented_lines(tmp_path):
    f = tmp_path / "commented.py"
    f.write_text(
        '# SOME_FILE = Path("utils/some.json")  <- this is a comment, skip it\n'
        'REAL = Path(__file__).parent / "data"\n'
    )
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        r = shc.check_relative_path_constants()
    assert r.status == "PASS"


def test_relative_path_scan_multiple_hits(tmp_path):
    f = tmp_path / "multi.py"
    f.write_text(
        'FILE_A = Path("data/a.json")\n'
        'FILE_B = Path(\'data/b.json\')\n'
        'FILE_C = _ROOT / "data" / "c.json"\n'   # absolute, should not match
    )
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        r = shc.check_relative_path_constants()
    assert r.status == "WARN"
    assert len(r.details["hits"]) == 2


def test_relative_path_scan_finds_hit_in_non_original_scope_file(tmp_path):
    """A relative-path constant in any file (not just the original 6) is caught."""
    outside = tmp_path / "new_module.py"
    outside.write_text('DATA_FILE = Path("data/cache.json")\n')
    with patch.object(shc, "_discover_scan_files", return_value=[outside]):
        r = shc.check_relative_path_constants()
    assert r.status == "WARN"
    assert "new_module.py" in r.details["hits"][0]


# ── _discover_scan_files exclusion tests ─────────────────────────────────────

def test_discover_scan_files_excludes_venv():
    """No file under a venv/ directory appears in the scan list."""
    files = shc._discover_scan_files()
    venv_files = [f for f in files if "venv" in f.parts]
    assert venv_files == [], f"venv files leaked into scan: {venv_files}"


def test_discover_scan_files_excludes_pycache():
    """No file under __pycache__/ appears in the scan list."""
    files = shc._discover_scan_files()
    cache_files = [f for f in files if "__pycache__" in f.parts]
    assert cache_files == [], f"__pycache__ files leaked into scan: {cache_files}"


def test_discover_scan_files_excludes_test_files():
    """No test_*.py or *_test.py file appears in the scan list."""
    files = shc._discover_scan_files()
    test_files = [
        f for f in files
        if f.name.startswith("test_") or f.name.endswith("_test.py")
    ]
    assert test_files == [], f"test files leaked into scan: {test_files}"


def test_discover_scan_files_returns_nonempty_sorted_list():
    """The function returns a non-empty sorted list covering multiple source dirs."""
    files = shc._discover_scan_files()
    assert len(files) > 0, "Expected source files to be found"
    assert files == sorted(files), "Result must be sorted"
    # Verify files span at least two directories
    dirs = {f.parent for f in files}
    assert len(dirs) >= 2, f"Expected files from multiple dirs, got: {dirs}"


# ── Exception isolation ───────────────────────────────────────────────────────

def test_exception_in_one_check_does_not_crash_others(tmp_path):
    """An exception in check_universe_consistency must not prevent other checks from running."""
    log_dir = tmp_path / "logs"
    _screen_log(log_dir, _recent())
    _runner_log(log_dir, _recent())
    _news_log(log_dir,   _recent())

    tracker_file = tmp_path / "degradation_tracker.json"
    tracker_file.write_text(json.dumps(_TRACKER_CLEAN))

    candidates_missing = tmp_path / "latest_candidates.json"   # does not exist

    clean_py = tmp_path / "clean.py"
    clean_py.write_text("# clean\n")

    def _boom():
        raise RuntimeError("injected failure")

    with patch.object(shc, "LOGS_DIR",             log_dir), \
         patch.object(shc, "DEGRADATION_TRACKER",  tracker_file), \
         patch.object(shc, "CANDIDATES_FILE",      candidates_missing), \
         patch.object(shc, "_discover_scan_files", return_value=[clean_py]), \
         patch.object(shc, "_load_stocks",         return_value=_TWO_STOCKS), \
         patch.object(shc, "_load_screener_universe", side_effect=_boom):

        results = [fn() for fn in [
            shc.check_screener_cadence,
            shc.check_run_completion,
            shc.check_universe_consistency,
            shc.check_degradation_tracker,
            shc.check_candidates_freshness,
            shc.check_relative_path_constants,
        ]]

    by_name = {r.name: r for r in results}

    # The crashing check reports FAIL with the exception detail
    assert by_name["Universe consistency"].status == "FAIL"
    assert "injected failure" in by_name["Universe consistency"].message

    # All other checks ran and completed with meaningful (non-exception) statuses
    assert by_name["Screener cadence"].status == "PASS"
    assert by_name["Run completion"].status == "PASS"
    assert by_name["Degradation tracker"].status == "PASS"
    assert by_name["Candidates file"].status == "WARN"
    assert by_name["Relative-path scan"].status == "PASS"


# =============================================================================
# Rolling window for check_run_completion  (added 2026-08-25)
# =============================================================================
# Log files are immutable, so an all-time scan can never go green again. On
# 2026-08-25 this check was permanently RED on two long-resolved incidents
# (screen_2026-07-12's NameError, 2026-06-08's ModuleNotFoundError). A check
# that can never clear is one people stop reading.

def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def test_run_completion_ignores_failures_older_than_the_window(tmp_path):
    """An ancient, already-resolved failure must not hold the check red forever."""
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    _runner_log(log_dir, _days_ago(shc._RUN_COMPLETION_WINDOW_DAYS + 10), completed=False)
    _runner_log(log_dir, _days_ago(1), completed=True)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "PASS", f"stale failure still red: {r.message}"


def test_run_completion_still_catches_failures_inside_the_window(tmp_path):
    """Narrowing the window must not blind the check to recent breakage."""
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    _runner_log(log_dir, _days_ago(2), completed=False)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert r.status == "FAIL"


def test_run_completion_reports_how_many_logs_it_skipped(tmp_path):
    """
    Out-of-window logs must be COUNTED AND REPORTED. A window that silently
    dropped history could hide a real failure without a trace.
    """
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    for n in (200, 150, 120):
        _runner_log(log_dir, _days_ago(n), completed=True)
    _runner_log(log_dir, _days_ago(1), completed=True)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert "3 log(s) older than" in r.message, r.message
    assert str(shc._RUN_COMPLETION_WINDOW_DAYS) in r.message


def test_run_completion_says_nothing_about_skips_when_none(tmp_path):
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    _runner_log(log_dir, _days_ago(1), completed=True)
    with patch.object(shc, "LOGS_DIR", log_dir):
        r = shc.check_run_completion()
    assert "not scanned" not in r.message


def test_undated_log_filenames_are_kept_not_dropped():
    """A log whose name carries no date must never be silently discarded."""
    assert shc._log_date(Path("weird-name.log")) is None
    keep, skipped = shc._within_window([Path("weird-name.log")])
    assert keep and skipped == 0


def test_log_date_parses_every_log_naming_convention():
    for name, expected in [
        ("2026-08-25.log", date(2026, 8, 25)),
        ("2026-08-25_morning.log", date(2026, 8, 25)),
        ("2026-08-25_news.log", date(2026, 8, 25)),
        ("screen_2026-08-23.log", date(2026, 8, 23)),
    ]:
        assert shc._log_date(Path(name)) == expected, name


# =============================================================================
# Relative-path scanner must not flag its own docstring  (added 2026-08-25)
# =============================================================================

def test_relative_path_scan_skips_matches_inside_a_docstring(tmp_path):
    """
    The exact self-referential WARN: this module's own _discover_scan_files()
    docstring cites Path("relative") as an example of a false positive, and the
    line-based scanner then flagged it — the tool warning about itself, forever.
    """
    f = tmp_path / "mod.py"
    f.write_text(
        'def g():\n'
        '    """Doc.\n\n'
        '    Example of a false positive: FOO = Path("relative/path")\n'
        '    """\n'
        '    return 1\n'
    )
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        r = shc.check_relative_path_constants()
    assert r.status == "PASS", f"docstring flagged as code: {r.details.get('hits')}"


def test_relative_path_scan_skips_matches_inside_a_single_line_string(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text('EXAMPLE = "FOO = Path(\'relative\')"\n')
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        r = shc.check_relative_path_constants()
    assert r.status == "PASS", r.details.get("hits")


def test_relative_path_scan_still_flags_real_code(tmp_path):
    """The masking must not blind the scanner to the bug it exists to find."""
    f = tmp_path / "mod.py"
    f.write_text('import pathlib\nFOO = Path("relative/path")\n')
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        r = shc.check_relative_path_constants()
    assert r.status == "WARN", "real relative-path constant was missed"
    assert any("relative/path" in h for h in r.details["hits"])


def test_relative_path_scan_flags_real_code_on_a_line_that_also_has_a_string(tmp_path):
    """Masking is per-match-position, not per-line — a line can contain both."""
    f = tmp_path / "mod.py"
    f.write_text('FOO = Path("relative/x")  # note: "Path(\'y\')" in a comment\n')
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        r = shc.check_relative_path_constants()
    assert r.status == "WARN", "real code suppressed by a string elsewhere on the line"


def test_relative_path_scan_degrades_gracefully_on_unparseable_file(tmp_path):
    """A syntax error must not crash the check or silence it entirely."""
    f = tmp_path / "broken.py"
    f.write_text('def (((: \nFOO = Path("relative")\n')
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        r = shc.check_relative_path_constants()
    assert r.status in ("WARN", "PASS")   # must not FAIL/raise
