"""
utils/alerts.py — Crash alerting for unattended cron jobs.

Paper trading: writes to utils/alerts.log and stderr.
Before going live: extend send_crash_alert() to email via SENDGRID_API_KEY in .env.
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

_ROOT     = Path(__file__).parent.parent
ALERT_LOG = _ROOT / "utils" / "alerts.log"


def send_crash_alert(script_name: str, exc: Exception) -> None:
    """
    Record a crash alert. Does NOT swallow the exception — caller must re-raise.

    Safe to call from except-blocks: OSError on log write is silenced so this
    function never masks the original exception.
    """
    detail = traceback.format_exc()
    now    = datetime.now().isoformat(timespec="seconds")
    msg    = (
        f"\n{'='*70}\n"
        f"CRASH ALERT [{script_name}] — {now}\n"
        f"{'='*70}\n"
        f"{detail}\n"
    )
    try:
        with open(ALERT_LOG, "a") as fh:
            fh.write(msg)
    except OSError:
        pass
    print(msg, file=sys.stderr)
