"""
Tests for utils/watchdog.py — the real-time cron-job watchdog.

Root incident this closes: run_daily.sh (Jul 20 2026, hardcoded Mac path)
and run_screen.sh (Jul 19 2026, stripped executable bit) both failed with
zero log output and went undetected for hours because utils/alerts.py's
crash alerting only fires from inside a Python except block — neither
failure ever reached Python. system_health_check.py's existing
check_run_completion() also can't help here: it only runs on-demand and
only ever sees "log missing" retroactively, long after the fact.

These tests use a real tmp_path as LOGS_DIR (patched) and real date objects
rather than mocking check_job() itself, so the weekday-scheduling logic is
genuinely exercised, not assumed.
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import utils.watchdog as watchdog


# A known Monday and a known Saturday in the system's date range, for
# weekday-vs-weekend scheduling assertions.
_A_MONDAY   = date(2026, 7, 20)
_A_SATURDAY = date(2026, 7, 18)
_A_SUNDAY   = date(2026, 7, 19)
_A_WEDNESDAY = date(2026, 7, 15)
_A_TUESDAY  = date(2026, 7, 21)


def test_check_job_healthy_when_log_exists_with_marker(tmp_path):
    log_dir = tmp_path
    (log_dir / f"{_A_MONDAY.isoformat()}.log").write_text(
        f"Run started...\n[run_daily] Completed: {_A_MONDAY} 10:15:20\n"
    )
    with patch.object(watchdog, "LOGS_DIR", log_dir):
        healthy, msg = watchdog.check_job("run_daily", today=_A_MONDAY)
    assert healthy is True
    assert "OK" in msg


def test_check_job_unhealthy_when_log_missing_entirely(tmp_path):
    """This is exactly the run_daily.sh Jul 20 / run_screen.sh Jul 19 signature:
    cron fired, the wrapper failed before ever writing a log, nothing exists."""
    log_dir = tmp_path  # empty — no log written at all
    with patch.object(watchdog, "LOGS_DIR", log_dir):
        healthy, msg = watchdog.check_job("run_daily", today=_A_MONDAY)
    assert healthy is False
    assert "does not exist" in msg
    assert "run_daily" in msg


def test_check_job_unhealthy_when_log_exists_but_no_completion_marker(tmp_path):
    """A different failure mode: the job started (log exists) but crashed
    mid-run before reaching its own completion line."""
    log_dir = tmp_path
    (log_dir / f"{_A_MONDAY.isoformat()}.log").write_text("Run started...\nTraceback...\n")
    with patch.object(watchdog, "LOGS_DIR", log_dir):
        healthy, msg = watchdog.check_job("run_daily", today=_A_MONDAY)
    assert healthy is False
    assert "missing its completion marker" in msg


def test_check_job_skips_non_scheduled_weekday_for_weekday_jobs(tmp_path):
    """run_daily/run_morning_check/run_news_monitor are Mon-Fri only — a
    Saturday with no log must NOT be reported unhealthy."""
    log_dir = tmp_path  # empty
    with patch.object(watchdog, "LOGS_DIR", log_dir):
        healthy, msg = watchdog.check_job("run_daily", today=_A_SATURDAY)
    assert healthy is True
    assert "not scheduled today" in msg


def test_check_job_run_screen_scheduled_sunday_and_wednesday_only(tmp_path):
    log_dir = tmp_path
    with patch.object(watchdog, "LOGS_DIR", log_dir):
        # Sunday and Wednesday: scheduled, log missing -> unhealthy
        healthy_sun, _ = watchdog.check_job("run_screen", today=_A_SUNDAY)
        healthy_wed, _ = watchdog.check_job("run_screen", today=_A_WEDNESDAY)
        # Tuesday: not scheduled -> healthy regardless of missing log
        healthy_tue, msg_tue = watchdog.check_job("run_screen", today=_A_TUESDAY)

    assert healthy_sun is False
    assert healthy_wed is False
    assert healthy_tue is True
    assert "not scheduled today" in msg_tue


def test_check_job_run_screen_healthy_when_log_exists_on_scheduled_day(tmp_path):
    log_dir = tmp_path
    (log_dir / f"screen_{_A_SUNDAY.isoformat()}.log").write_text(
        f"Screen run started...\nScreen run completed: {_A_SUNDAY} | Exit code: 0\n"
    )
    with patch.object(watchdog, "LOGS_DIR", log_dir):
        healthy, msg = watchdog.check_job("run_screen", today=_A_SUNDAY)
    assert healthy is True


def test_main_fires_watchdog_alert_and_exits_nonzero_on_missing_log(tmp_path, monkeypatch):
    """End-to-end: main() must call send_watchdog_alert and return a nonzero
    exit code when a scheduled job's log is missing."""
    log_dir = tmp_path
    alert_calls = []

    def _fake_alert(job_name, detail):
        alert_calls.append((job_name, detail))

    monkeypatch.setattr(sys, "argv", ["watchdog.py", "--job", "run_daily"])
    with patch.object(watchdog, "LOGS_DIR", log_dir), \
         patch.object(watchdog, "check_job", return_value=(False, "run_daily: missing")), \
         patch("utils.alerts.send_watchdog_alert", side_effect=_fake_alert):
        exit_code = watchdog.main()

    assert exit_code == 1
    assert len(alert_calls) == 1
    assert alert_calls[0][0] == "run_daily"


def test_main_does_not_alert_when_healthy(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["watchdog.py", "--job", "run_daily"])
    with patch.object(watchdog, "check_job", return_value=(True, "run_daily: OK")), \
         patch("utils.alerts.send_watchdog_alert") as mock_alert:
        exit_code = watchdog.main()

    assert exit_code == 0
    mock_alert.assert_not_called()
