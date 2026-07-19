#!/bin/bash
# paper_trading/run_morning_check.sh — Morning AMO fill checker cron wrapper.
#
# Runs at 9:30 AM IST (4:00 AM UTC) every weekday to resolve yesterday's AMO orders.
#
# CRON SETUP (add via: crontab -e):
# ────────────────────────────────────────────────────────────
#   0 4 * * 1-5 /home/ubuntu/algo-trading/paper_trading/run_morning_check.sh
#   └── 4:00 AM UTC = 9:30 AM IST, weekdays only
#
# To also apply fills to portfolio state automatically, change the python line
# below to add the --apply flag. Leave it off until you've confirmed the fill
# logic is correct for at least a few sessions.

set -euo pipefail

PROJECT_ROOT="/home/ubuntu/algo-trading"
source "$PROJECT_ROOT/venv/bin/activate"
cd "$PROJECT_ROOT"

LOG_FILE="$PROJECT_ROOT/paper_trading/logs/$(date +%Y-%m-%d)_morning.log"
mkdir -p "$(dirname "$LOG_FILE")"

echo "Morning fill check started: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"

# Refresh token — best-effort. Don't skip fill check if login fails: the
# existing token may still be valid, and morning_fill_check has its own
# TokenException guard that will fail loudly and write to alerts.log.
if ! python auth/auto_login.py >> "$LOG_FILE" 2>&1; then
    echo "[morning_fill_check] WARNING: auto_login.py failed — attempting fill check with existing token" >> "$LOG_FILE"
fi

# --apply flag: pass it to update portfolio state with actual fill prices.
# Remove the flag to run in dry-run mode (report only, no state changes).
python paper_trading/morning_fill_check.py --apply >> "$LOG_FILE" 2>&1

echo "Morning fill check completed: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
