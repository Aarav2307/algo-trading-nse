"""
paper_trading/repair_portfolio_state.py — One-time state repair after BAJAJ-AUTO exit.

DO NOT RUN AUTOMATICALLY. Run manually on the server AFTER:
  1. morning_fill_check.py (9:20 AM IST, Jun 26) confirms BAJAJ-AUTO exit fill
  2. Check the morning log: ~/algo-trading/paper_trading/logs/YYYY-MM-DD.log
  3. Confirm total_trades == 4 and BAJAJ-AUTO shares == 0

This script corrects the corrupted portfolio_state.json by:
  - Recomputing correct cash from trade log
  - Allocating 100% to NIFTYBEES ETF (0 positions → D_aggressive tier 1.0)
  - Preserving trade_log and all other fields unchanged

Usage (on server):
    python3 ~/algo-trading/paper_trading/repair_portfolio_state.py
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

STATE_FILE   = _ROOT / "paper_trading" / "portfolio_state.json"
BACKUP_DIR   = _ROOT / "paper_trading" / "state_backups"
INITIAL_CAPITAL = 100_000.0

# Expected trade log entries (±₹5 tolerance)
EXPECTED_TRADES = [
    {"ticker": "WHIRLPOOL.NS", "net_pnl": -188.47},
    {"ticker": "TMPV.NS",      "net_pnl": -1296.72},
    {"ticker": "SIEMENS.NS",   "net_pnl": -157.43},
    # BAJAJ-AUTO: accept whatever morning_fill_check recorded (actual open fill)
]
TOLERANCE = 5.0


def main() -> None:
    print()
    print("=" * 65)
    print("  Portfolio State Repair Script")
    print("  Run ONLY after BAJAJ-AUTO morning fill confirmed (Jun 26)")
    print("=" * 65)

    # ── Load state ────────────────────────────────────────────────────────────
    if not STATE_FILE.exists():
        print(f"ERROR: {STATE_FILE} not found.")
        sys.exit(1)

    with open(STATE_FILE) as fh:
        state = json.load(fh)

    print(f"\n[1/9] Loaded {STATE_FILE}")

    # ── Precondition checks ────────────────────────────────────────────────────
    print("\n[2/9] Checking preconditions...")

    total_trades  = state.get("total_trades", 0)
    trade_log     = state.get("trade_log", [])
    positions     = state.get("positions", {})

    errors = []

    # a. total_trades must be 4
    if total_trades != 4:
        errors.append(f"total_trades={total_trades}, expected 4")

    # b. BAJAJ-AUTO shares must be 0 (position closed by morning fill)
    bajaj_pos = positions.get("BAJAJ-AUTO.NS", {})
    if bajaj_pos.get("shares", -1) != 0:
        errors.append(
            f"BAJAJ-AUTO.NS shares={bajaj_pos.get('shares')}, expected 0 "
            f"(morning fill has not confirmed yet — wait for fill check)"
        )

    # c. All other positions must have shares == 0
    for ticker, pos in positions.items():
        if ticker != "BAJAJ-AUTO.NS" and pos.get("shares", 0) != 0:
            errors.append(f"{ticker} still has shares={pos['shares']} (unexpected open position)")

    if errors:
        print("  ERROR: Preconditions failed — aborting without modifying state:")
        for e in errors:
            print(f"    ✗ {e}")
        sys.exit(1)

    print("  ✓ total_trades == 4")
    print("  ✓ BAJAJ-AUTO.NS shares == 0 (morning fill confirmed)")
    print("  ✓ All other positions are flat")

    # ── Verify known trade log entries ────────────────────────────────────────
    print("\n[3/9] Verifying trade log entries...")

    trade_map = {t["ticker"]: t for t in trade_log}
    bajaj_entry = trade_map.get("BAJAJ-AUTO.NS")

    for expected in EXPECTED_TRADES:
        ticker   = expected["ticker"]
        expected_pnl = expected["net_pnl"]
        entry    = trade_map.get(ticker)
        if entry is None:
            print(f"  ERROR: {ticker} not found in trade_log")
            sys.exit(1)
        actual_pnl = entry["net_pnl"]
        if abs(actual_pnl - expected_pnl) > TOLERANCE:
            print(
                f"  ERROR: {ticker} net_pnl={actual_pnl:.2f}, "
                f"expected ≈{expected_pnl:.2f} (tolerance ±{TOLERANCE})"
            )
            sys.exit(1)
        print(f"  ✓ {ticker}: net_pnl ₹{actual_pnl:.2f} (expected ≈ ₹{expected_pnl:.2f})")

    if bajaj_entry:
        print(f"  ✓ BAJAJ-AUTO.NS: net_pnl ₹{bajaj_entry['net_pnl']:.2f} (accepted as-is from fill)")
    else:
        print("  ERROR: BAJAJ-AUTO.NS not found in trade_log")
        sys.exit(1)

    # ── Compute correct portfolio value ────────────────────────────────────────
    print("\n[4/9] Computing correct portfolio value...")

    total_losses = sum(t["net_pnl"] for t in trade_log)
    correct_total = INITIAL_CAPITAL + total_losses
    print(f"  Initial capital: ₹{INITIAL_CAPITAL:,.2f}")
    print(f"  Sum of trade P&L: ₹{total_losses:,.2f}")
    print(f"  Correct total after 4 trades: ₹{correct_total:,.2f}")

    # ── Fetch NIFTYBEES price ─────────────────────────────────────────────────
    print("\n[5/9] Fetching current NIFTYBEES price from yfinance...")

    try:
        import yfinance as yf
        nb = yf.download(
            "NIFTYBEES.NS",
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        # Handle both flat and MultiIndex columns
        if isinstance(nb.columns, type(nb.columns)) and hasattr(nb.columns, "levels"):
            nb = nb["Close"] if "Close" in nb.columns.get_level_values(0) else nb
        close_series = nb["Close"] if isinstance(nb.columns, type(nb.columns)) else nb
        if hasattr(close_series, "columns"):
            # MultiIndex — flatten
            close_series = close_series.iloc[:, 0]
        niftybees_price = float(close_series.iloc[-1])
    except Exception as exc:
        print(f"  ERROR: Could not fetch NIFTYBEES price: {exc}")
        sys.exit(1)

    print(f"  NIFTYBEES price: ₹{niftybees_price:.2f} (live from yfinance)")

    # ── Compute ETF allocation ────────────────────────────────────────────────
    print("\n[6/9] Computing correct ETF allocation (0 positions → 100% tier)...")

    etf_shares = int(correct_total / niftybees_price)
    etf_value  = etf_shares * niftybees_price
    cash       = correct_total - etf_value

    print(f"  ETF shares:  {etf_shares}  (floor({correct_total:.2f} / {niftybees_price:.2f}))")
    print(f"  ETF value:   ₹{etf_value:.2f}")
    print(f"  Cash:        ₹{cash:.2f}")
    total_check = cash + etf_value
    diff = abs(total_check - correct_total)
    print(f"  Total check: ₹{total_check:.2f}  (diff ₹{diff:.2f} — must be < ₹1)")
    if diff >= 1.0:
        print(f"  ERROR: Total check fails (diff = ₹{diff:.2f} ≥ ₹1)")
        sys.exit(1)

    # ── Timestamped backup ────────────────────────────────────────────────────
    print("\n[7/9] Creating backup before modifying...")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"portfolio_state_repair_{ts}.json"
    with open(backup_path, "w") as fh:
        json.dump(state, fh, indent=2, default=str)
    print(f"  Backup: {backup_path}")

    # ── Apply corrections ─────────────────────────────────────────────────────
    print("\n[8/9] Applying corrections (only cash, etf_shares, etf_avg_price, etf_tier, last_run_date)...")

    state["cash"]           = round(cash, 4)
    state["etf_shares"]     = etf_shares
    state["etf_avg_price"]  = round(niftybees_price, 4)
    state["etf_tier"]       = 1.0
    state["last_run_date"]  = date.today().isoformat()

    tmp_path = str(STATE_FILE) + ".tmp"
    with open(tmp_path, "w") as fh:
        json.dump(state, fh, indent=2, default=str)
    os.replace(tmp_path, str(STATE_FILE))

    # ── Final verification ────────────────────────────────────────────────────
    print("\n[9/9] Final verification:")
    print(f"  Correct total:   ₹{correct_total:.2f}")
    print(f"  NIFTYBEES price: ₹{niftybees_price:.2f} (live from yfinance)")
    print(f"  ETF shares:      {etf_shares}")
    print(f"  ETF value:       ₹{etf_value:.2f}")
    print(f"  Cash:            ₹{cash:.2f}")
    print(f"  Total check:     ₹{total_check:.2f} (must equal correct_total ±₹1)")
    print(f"  Total trades:    4")
    print(f"  Backup:          {backup_path}")

    print()
    print("=" * 65)
    print("  ✅ State repaired successfully.")
    print("  Verify with: python3 paper_trading/signal_runner.py --force")
    print("=" * 65)
    print()


if __name__ == "__main__":
    main()
