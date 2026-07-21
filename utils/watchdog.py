"""
utils/watchdog.py — Real-time cron-job watchdog.

Why this exists: utils/alerts.py's crash alerting only fires from inside a
Python except block in signal_runner.py, auto_screener.py, and
morning_fill_check.py. It cannot see a failure that happens BEFORE Python
ever runs — a bash wrapper that dies under `set -e` on a bad path (run_daily.sh,
Jul 20 2026), or a script that can't even execute because its executable bit
was stripped (run_screen.sh, Jul 19 2026). Both incidents produced zero log
output and were only found by chance, hours later, while chasing something
else.

This script runs on its own cron schedule, shortly after each of the 4 jobs'
expected fire time, and checks the one thing that's true regardless of HOW a
job failed: today's expected log file exists and ends with the completion
marker the wrapper script itself writes on success. If not, it alerts
immediately via utils/alerts.send_watchdog_alert() instead of waiting for a
human to notice.

Usage (cron):
    python3 utils/watchdog.py --job run_daily
    python3 utils/watchdog.py --job run_morning_check
    python3 utils/watchdog.py --job run_screen
    python3 utils/watchdog.py --job run_news_monitor

Suggested crontab (30-60 min after each job's own cron entry):
    20 4  * * 1-5   python3 /home/ubuntu/algo-trading/utils/watchdog.py --job run_morning_check
    50 10 * * 1-5   python3 /home/ubuntu/algo-trading/utils/watchdog.py --job run_daily
    15 13 * * 0,3   python3 /home/ubuntu/algo-trading/utils/watchdog.py --job run_screen
    35 13 * * 1-5   python3 /home/ubuntu/algo-trading/utils/watchdog.py --job run_news_monitor

What this catches: permission failures (stripped executable bit), path/env
failures inside the wrapper (hardcoded wrong PROJECT_ROOT), and cron failing
to fire at all — none of which reach a Python except block, so none of
which utils/alerts.py's existing crash alerting can see.

What this does NOT catch: a job that runs, writes a completion marker, but
produced wrong RESULTS (e.g. corrupted data that doesn't crash) — that's a
different problem, addressed by validation/system_health_check.py's other
checks and by tests, not by this watchdog. It also does not catch a job that
completes successfully but later than expected (e.g. a manual recovery run
hours after the original failure would itself look "clean" retroactively) —
this watchdog's value is running SOON after the expected time, so it flags
the gap before a late recovery ever happens, not after.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.system_health_check import LOGS_DIR, _last_nonempty_line

# Monday=0 ... Sunday=6, matching date.weekday().
_JOBS = {
    "run_daily": {
        "log_path": lambda d: LOGS_DIR / f"{d.isoformat()}.log",
        "marker":   "[run_daily] Completed:",
        "weekdays": {0, 1, 2, 3, 4},
    },
    "run_morning_check": {
        "log_path": lambda d: LOGS_DIR / f"{d.isoformat()}_morning.log",
        "marker":   "Morning fill check completed:",
        "weekdays": {0, 1, 2, 3, 4},
    },
    "run_screen": {
        "log_path": lambda d: LOGS_DIR / f"screen_{d.isoformat()}.log",
        "marker":   "Screen run completed:",
        "weekdays": {6, 2},  # Sunday, Wednesday
    },
    "run_news_monitor": {
        "log_path": lambda d: LOGS_DIR / f"{d.isoformat()}_news.log",
        "marker":   "News monitor completed:",
        "weekdays": {0, 1, 2, 3, 4},
    },
}


def check_job(job_name: str, today: date = None) -> tuple[bool, str]:
    """Returns (healthy, message). Does not alert -- callers decide what to do."""
    today = today or date.today()
    cfg = _JOBS[job_name]

    if today.weekday() not in cfg["weekdays"]:
        return True, f"{job_name}: not scheduled today ({today}), skipping"

    log_path = cfg["log_path"](today)
    if not log_path.exists():
        return False, (
            f"{job_name}: expected log {log_path.name} does not exist for {today} — "
            "job did not run at all (permission failure, path/env failure in the "
            "wrapper script, or cron did not fire)"
        )

    last_line = _last_nonempty_line(log_path)
    if cfg["marker"] not in last_line:
        return False, (
            f"{job_name}: log {log_path.name} exists but is missing its completion "
            f"marker — job started but did not finish cleanly. Last line: {last_line!r}"
        )

    return True, f"{job_name}: {log_path.name} OK"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cron-job watchdog — alerts if today's expected job didn't complete."
    )
    parser.add_argument("--job", required=True, choices=sorted(_JOBS.keys()))
    args = parser.parse_args()

    healthy, msg = check_job(args.job)
    print(msg)

    if not healthy:
        from utils.alerts import send_watchdog_alert
        send_watchdog_alert(args.job, msg)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
