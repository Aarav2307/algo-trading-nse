#!/bin/bash
# paper_trading/run_screen.sh
# Cron wrapper for bi-weekly universe expansion screener.
# Runs Wednesday and Sunday at 6 PM IST (12:30 UTC).
#
# Cron entry (add via: crontab -e):
#   30 12 * * 0,3 /home/ubuntu/algo-trading/paper_trading/run_screen.sh

LOG_DIR="/home/ubuntu/algo-trading/paper_trading/logs"
DATE=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/screen_${DATE}.log"

mkdir -p "$LOG_DIR"

echo "======================================" >> "$LOG_FILE"
echo "Screen run started: $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"

cd /home/ubuntu/algo-trading || exit 1
source venv/bin/activate

python screener/auto_screener.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "" >> "$LOG_FILE"
echo "Screen run completed: $(date) | Exit code: $EXIT_CODE" >> "$LOG_FILE"

exit $EXIT_CODE
