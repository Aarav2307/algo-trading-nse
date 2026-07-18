#!/bin/bash
# Run this on the SERVER before any SCP that touches paper_trading/
# Usage: ssh -i ~/.ssh/LightsailDefaultKey-ap-south-1.pem ubuntu@13.205.133.169 \
#          "bash ~/algo-trading/paper_trading/backup_state.sh"
set -euo pipefail
BACKUP_DIR="/home/ubuntu/algo-trading/paper_trading/state_backups"
STATE_FILE="/home/ubuntu/algo-trading/paper_trading/portfolio_state.json"
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/portfolio_state_$TIMESTAMP.json"
cp "$STATE_FILE" "$BACKUP_PATH"
echo "✅ Backup saved: $BACKUP_PATH"
echo "   Size: $(wc -c < "$BACKUP_PATH") bytes"
echo "   Cash: $(python3 -c "import json; s=json.load(open('$STATE_FILE')); print(f'Rs{s[\"cash\"]:,.2f}')")"
echo "   ETF:  $(python3 -c "import json; s=json.load(open('$STATE_FILE')); print(f'{s[\"etf_shares\"]} units')")"
echo "   Trades: $(python3 -c "import json; s=json.load(open('$STATE_FILE')); print(s['total_trades'])")"
