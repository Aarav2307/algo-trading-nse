#!/bin/bash
# paper_trading/run_daily.sh — Daily runner for cron / manual execution.
#
# Runs after NSE market close (3:45 PM IST = 10:15 AM UTC).
#
# CRON SETUP (add via: crontab -e)
# ─────────────────────────────────
#   15 10 * * 1-5 /home/ubuntu/algo-trading/paper_trading/run_daily.sh
#   └── 10:15 AM UTC = 3:45 PM IST, weekdays only
#
# ⚠  Mac cron limitation: cron does NOT run while the laptop is asleep.
#    When travelling to the US:
#      Option A — AWS Lightsail Mumbai ($5/month): always-on Linux VM, reliable.
#      Option B — Run manually via SSH into a remote machine.
#    Reference: https://docs.aws.amazon.com/lightsail/latest/userguide/

set -euo pipefail   # exit on error, undefined vars, pipe failures

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT="/home/ubuntu/algo-trading"
VENV_ACTIVATE="$PROJECT_ROOT/venv/bin/activate"
LOG_DIR="$PROJECT_ROOT/paper_trading/logs"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

# ── Ensure log directory exists ────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Activate virtual environment ──────────────────────────────────────────────
# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

cd "$PROJECT_ROOT"

echo "========================================"   >> "$LOG_FILE"
echo "Run started: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================"   >> "$LOG_FILE"

# ── Step 1: Refresh Kite access token ─────────────────────────────────────────
# auth/auto_login.py uses pyotp for automated TOTP-based login.
# If auto_login.py is not available, comment out this block and run
# auth/kite_login.py manually before the cron job fires.
if [ -f "$PROJECT_ROOT/auth/auto_login.py" ]; then
    echo "[run_daily] Refreshing access token via auto_login.py" >> "$LOG_FILE"
    python auth/auto_login.py >> "$LOG_FILE" 2>&1
else
    echo "[run_daily] auth/auto_login.py not found — using existing token." >> "$LOG_FILE"
    echo "  To automate login, create auth/auto_login.py using pyotp." >> "$LOG_FILE"
fi

# ── Step 2: Run the signal runner ─────────────────────────────────────────────
echo "[run_daily] Running signal_runner.py" >> "$LOG_FILE"
python paper_trading/signal_runner.py >> "$LOG_FILE" 2>&1

echo "[run_daily] Completed: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
