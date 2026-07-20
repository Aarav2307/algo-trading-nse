#!/bin/bash
# Nightly pre-trade risk monitor — runs after market close
# Cron: 0 13 * * 1-5 (7 PM IST = 13:00 UTC, Mon-Fri)
set -euo pipefail
PROJECT_ROOT="/home/ubuntu/algo-trading"
source "$PROJECT_ROOT/venv/bin/activate"
cd "$PROJECT_ROOT"
LOG_FILE="$PROJECT_ROOT/paper_trading/logs/$(date +%Y-%m-%d)_news.log"
echo "News monitor started: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
python3 utils/news_monitor.py >> "$LOG_FILE" 2>&1
echo "News monitor completed: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
