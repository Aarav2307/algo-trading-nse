"""
validation/run_scheduled_wf_batch.py — Manual WF batch runner for screener candidates.

Reads screener/latest_candidates.json (written after each real screen run),
validates freshness, caps the candidate list to MAX_CANDIDATES_PER_RUN, and
calls post_screener_pipeline.main() to run the WF gate on each candidate.

IMPORTANT: This script is run MANUALLY, not via cron. See CLAUDE_CONTEXT.md for
the reasoning behind keeping the trigger manual (two silent unattended-job failures
this week — a human-in-the-loop trigger is required until the system has a track
record of reliable unattended operation).

Usage:
    python3 validation/run_scheduled_wf_batch.py

Status is written to validation/scheduled_run_status.json after every run
(success OR failure), so the outcome is always inspectable after the fact.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.post_screener_pipeline import main as pipeline_main

# ── File paths (patched in tests via patch.object) ────────────────────────────

CANDIDATES_FILE = _ROOT / "screener"    / "latest_candidates.json"
STATUS_FILE     = _ROOT / "validation"  / "scheduled_run_status.json"
REPORT_DIR      = _ROOT / "validation"

# ── Constants ─────────────────────────────────────────────────────────────────

# Hard ceiling on candidates processed per run. Deliberately NOT a CLI flag —
# the ceiling must not be silently bypassed by a future edit or one-liner.
MAX_CANDIDATES_PER_RUN = 10

# A candidates file is considered stale if screen_date is older than this.
# Allows running the morning after a Wed/Sun evening screen (age = 1 day).
_MAX_STALENESS_DAYS = 1

_IST = timezone(timedelta(hours=5, minutes=30))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_status(data: dict) -> None:
    """Atomically write the status dict to STATUS_FILE. Fail-open."""
    tmp = STATUS_FILE.parent / (STATUS_FILE.name + ".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.rename(STATUS_FILE)
    except OSError as e:
        print(f"WARNING: failed to write status file: {e}", file=sys.stderr)


def _parse_report_counts(report_path: Path) -> tuple[int, int, int]:
    """Parse passed/failed/errors from the WF batch report markdown.
    Returns (0, 0, 0) on any read/parse failure."""
    try:
        text = report_path.read_text()
        passed = int(re.search(r"- Passed: (\d+)", text).group(1))
        failed = int(re.search(r"- Failed: (\d+)", text).group(1))
        errors = int(re.search(r"- Errors: (\d+)",  text).group(1))
        return passed, failed, errors
    except Exception:
        return 0, 0, 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    today    = date.today()
    run_time = datetime.now(_IST).isoformat(timespec="seconds")

    # ── Load candidates file ────────────────────────────────────────────────
    if not CANDIDATES_FILE.exists():
        reason = f"Candidates file not found: {CANDIDATES_FILE}"
        print(f"FAILED: {reason}", file=sys.stderr)
        _write_status({"run_time": run_time, "status": "FAILED", "reason": reason})
        return 1

    try:
        candidates_data  = json.loads(CANDIDATES_FILE.read_text())
        screen_date_str  = candidates_data["screen_date"]
        screen_date      = date.fromisoformat(screen_date_str)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        reason = f"Candidates file malformed: {e}"
        print(f"FAILED: {reason}", file=sys.stderr)
        _write_status({"run_time": run_time, "status": "FAILED", "reason": reason})
        return 1

    # ── Staleness check ─────────────────────────────────────────────────────
    age_days = (today - screen_date).days
    if age_days > _MAX_STALENESS_DAYS:
        reason = (
            f"Candidates file is stale: screen_date={screen_date_str} is "
            f"{age_days} day(s) old (max {_MAX_STALENESS_DAYS}). "
            f"Re-run the screener or wait for the next scheduled screen."
        )
        print(f"FAILED: {reason}", file=sys.stderr)
        _write_status({
            "run_time":             run_time,
            "status":               "FAILED",
            "reason":               reason,
            "candidates_file_date": screen_date_str,
        })
        return 1

    # ── Combine and cap candidates ──────────────────────────────────────────
    add_tickers   = candidates_data.get("add_tickers",       [])
    watch_tickers = candidates_data.get("watchlist_tickers", [])
    all_tickers   = add_tickers + watch_tickers
    skipped       = max(0, len(all_tickers) - MAX_CANDIDATES_PER_RUN)
    capped        = all_tickers[:MAX_CANDIDATES_PER_RUN]

    if skipped > 0:
        print(
            f"[ceiling] {len(all_tickers)} total candidates exceeds "
            f"MAX_CANDIDATES_PER_RUN={MAX_CANDIDATES_PER_RUN}. "
            f"Processing first {len(capped)}, skipping {skipped}."
        )

    if not capped:
        print("No candidates to process this cycle.")
        _write_status({
            "run_time":                  run_time,
            "status":                    "SUCCESS",
            "candidates_file_date":      screen_date_str,
            "candidates_processed":      0,
            "candidates_skipped_ceiling": 0,
            "passed":                    0,
            "failed":                    0,
            "errors":                    0,
            "report_path":               None,
        })
        return 0

    # ── Run WF pipeline ─────────────────────────────────────────────────────
    report_path = REPORT_DIR / f"wf_batch_report_{today.isoformat()}.md"

    try:
        pipeline_main(["--tickers"] + capped)
        passed, failed, errors = _parse_report_counts(report_path)

        _write_status({
            "run_time":                  run_time,
            "status":                    "SUCCESS",
            "candidates_file_date":      screen_date_str,
            "candidates_processed":      len(capped),
            "candidates_skipped_ceiling": skipped,
            "passed":                    passed,
            "failed":                    failed,
            "errors":                    errors,
            "report_path":               str(report_path.relative_to(REPORT_DIR.parent)),
        })
        return 0

    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        print(f"FAILED: {reason}", file=sys.stderr)
        _write_status({
            "run_time":                  run_time,
            "status":                    "FAILED",
            "reason":                    reason,
            "candidates_file_date":      screen_date_str,
            "candidates_processed":      len(capped),
            "candidates_skipped_ceiling": skipped,
        })
        return 1


if __name__ == "__main__":
    sys.exit(main())
