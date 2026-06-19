"""
paper_trading/morning_fill_check.py — Morning AMO fill checker.

Runs at 9:30 AM IST every trading day (via run_morning_check.sh / cron).

What it does:
  1. Loads rows from amo_orders.csv where status = "DRY_RUN" and date = yesterday
  2. For each pending order, fetches today's opening price from Kite
  3. Checks the fill condition:
       BUY  filled if open_price <= limit_price
       SELL filled if open_price >= limit_price
  4. If filled   → updates portfolio_state.json with the actual open fill price
  5. If not filled → logs "MISSED" with the open price that was too far
  6. Prints the morning fill report

Dry-run mode (the default): portfolio state is NOT modified — just shows what
would have happened. Set DRY_RUN_PORTFOLIO = False only after confirming the
logic is correct on a few real sessions.

Usage:
    python paper_trading/morning_fill_check.py
    python paper_trading/morning_fill_check.py --date 2026-06-05   # check a past date
    python paper_trading/morning_fill_check.py --apply             # update portfolio state
"""

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.kite_fetcher import get_ohlcv
from utils.market_calendar import is_trading_day


# ── Paths ─────────────────────────────────────────────────────────────────────
AMO_CSV         = Path("paper_trading/amo_orders.csv")
STATE_FILE      = Path("paper_trading/portfolio_state.json")
TOKEN_FILE      = Path("auth/access_token.txt")

# RM exit reasons — these come through in the AMO order's "notes" field.
# When a SELL fills with one of these notes, we must also trigger cooldown
# and clear the pending_rm_exit flag on the position.
_RM_EXIT_NOTES  = frozenset({"HARD_STOP", "CHANDELIER", "TIME_STOP"})
# Cooldown bars must match signal_runner.py COOLDOWN_BARS.
# +1 offset: advance_cooldown() in the same evening's signal_runner absorbs one bar,
# leaving exactly COOLDOWN_BARS days of suppression starting the next trading day.
_COOLDOWN_BARS  = 15
_COOLDOWN_BARS_WITH_OFFSET = _COOLDOWN_BARS + 1

# Set to True only when PAPER_TRADING_MODE = False in signal_runner.py
# When True: queries actual Zerodha order status instead of simulating fills
LIVE_TRADING_MODE: bool = False  # NEVER set to True manually — controlled by deployment config

# ── IST offset ────────────────────────────────────────────────────────────────
_IST = timedelta(hours=5, minutes=30)


def _get_ist_now() -> datetime:
    from datetime import timezone
    return (datetime.now(timezone.utc) + _IST).replace(tzinfo=None)


def _check_auth() -> None:
    if not TOKEN_FILE.exists():
        print("ERROR: auth/access_token.txt not found. Run auth/kite_login.py first.")
        sys.exit(1)


def _load_pending_orders() -> List[dict]:
    """
    Return ALL rows in amo_orders.csv that are still awaiting a fill check:
      - status == "DRY_RUN"   (not yet resolved)
      - fill_date == ""        (no fill recorded yet)

    Date-agnostic: a Friday order that was never checked on Saturday/Sunday
    will still appear here on Monday, so weekend carry-forward works correctly.
    """
    if not AMO_CSV.exists():
        return []

    pending = []
    with open(AMO_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["status"] == "DRY_RUN" and row.get("fill_date", "") == "":
                pending.append(row)
    return pending


def _fetch_open_price(ticker: str, today: date) -> Optional[float]:
    """
    Fetch today's opening price for ticker from Kite.
    Returns None if data is unavailable.
    """
    try:
        # Fetch today's bar only (start = today, end = tomorrow exclusive)
        start = today.isoformat()
        end   = (today + timedelta(days=1)).isoformat()
        df    = get_ohlcv(ticker, start, end)
        if df.empty:
            return None
        # Use the first bar's open (should be today's only bar for daily data)
        return float(df["open"].iloc[0])
    except Exception as exc:
        print(f"  WARN: Could not fetch {ticker} open — {exc}")
        return None


def _fetch_prev_close(ticker: str, today: date) -> float:
    """
    Fetch the previous trading day's closing price for circuit breaker check.
    Returns 0.0 on failure (circuit check is skipped — safe default).
    """
    try:
        start = (today - timedelta(days=5)).isoformat()
        end   = today.isoformat()   # exclusive — data up to but not including today
        df    = get_ohlcv(ticker, start, end)
        if df is None or df.empty:
            return 0.0
        return float(df["close"].iloc[-1])
    except Exception:
        return 0.0


def _fetch_live_order_status(order_id: str, kite) -> dict:
    """
    Query Zerodha Kite API for actual order status.
    Only called when LIVE_TRADING_MODE = True and order_id is not empty.

    Returns dict with keys:
        status:        "COMPLETE" | "REJECTED" | "CANCELLED" | "OPEN" | "UNKNOWN"
        fill_price:    float | None  (actual average fill price if COMPLETE)
        fill_qty:      int | None    (actual filled quantity)
        reject_reason: str | None    (rejection reason if REJECTED)
        raw:           full order dict from Kite API
    """
    if not order_id:
        return {"status": "UNKNOWN", "fill_price": None, "fill_qty": None,
                "reject_reason": "No order_id — paper trading order", "raw": {}}

    try:
        orders = kite.orders()
        for order in orders:
            if str(order.get("order_id")) == str(order_id):
                status        = order.get("status", "UNKNOWN").upper()
                fill_price    = None
                fill_qty      = None
                reject_reason = None

                if status == "COMPLETE":
                    fill_price = float(order.get("average_price", 0))
                    fill_qty   = int(order.get("filled_quantity", 0))
                elif status == "REJECTED":
                    reject_reason = order.get("status_message", "Unknown rejection reason")
                elif status == "CANCELLED":
                    reject_reason = "Order was cancelled"

                return {
                    "status":        status,
                    "fill_price":    fill_price,
                    "fill_qty":      fill_qty,
                    "reject_reason": reject_reason,
                    "raw":           order,
                }

        # order_id not found in today's orders
        return {"status": "NOT_FOUND", "fill_price": None, "fill_qty": None,
                "reject_reason": f"order_id {order_id} not found in Kite order book", "raw": {}}

    except Exception as e:
        return {"status": "ERROR", "fill_price": None, "fill_qty": None,
                "reject_reason": str(e), "raw": {}}


def _check_circuit_breaker(ticker: str, open_price: float, prev_close: float) -> tuple[bool, str]:
    """
    Check if a stock has hit a circuit breaker at open.
    NSE circuit limits: 2%, 5%, 10%, 20% depending on stock category.

    A circuit breaker is suspected if open price moved > 19% from prev close
    (lower circuit = stock can't be sold, upper circuit = can't be bought).

    Returns:
        (True, reason) if circuit breaker suspected
        (False, "") if normal
    """
    if prev_close <= 0:
        return False, ""

    pct_move = abs(open_price - prev_close) / prev_close * 100

    if pct_move >= 19.0:
        direction = "upper" if open_price > prev_close else "lower"
        return True, (
            f"Possible {direction} circuit breaker: open moved {pct_move:.1f}% "
            f"from prev close ₹{prev_close:.2f}"
        )

    return False, ""


def _process_order(order: dict, today_open: float, prev_close: float, kite=None) -> dict:
    """
    Process a single AMO order and determine fill status.

    Paper mode (LIVE_TRADING_MODE=False):
        Simulates fill based on open price vs limit price.

    Live mode (LIVE_TRADING_MODE=True):
        Queries actual Zerodha order status via Kite API.
        Falls back to simulation if order_id missing or API fails.

    Returns dict with keys:
        filled:       bool
        fill_price:   float | None
        fill_qty:     int | None
        status:       "FILLED" | "MISSED" | "REJECTED" | "CANCELLED" | "ERROR"
        reason:       str explaining the outcome
        circuit_flag: bool  (True if circuit breaker suspected)
        circuit_msg:  str
    """
    ticker      = order["ticker"]
    order_type  = order["order_type"]   # "BUY" or "SELL"
    limit_price = float(order["limit_price"])
    shares      = int(order["shares"])
    order_id    = order.get("order_id", "")

    # Check for circuit breaker regardless of mode
    circuit_flag, circuit_msg = _check_circuit_breaker(ticker, today_open, prev_close)

    if LIVE_TRADING_MODE and order_id and kite is not None:
        # ── LIVE MODE: query actual Zerodha order status ──────────────────────
        live_status = _fetch_live_order_status(order_id, kite)

        if live_status["status"] == "COMPLETE":
            return {
                "filled":       True,
                "fill_price":   live_status["fill_price"],
                "fill_qty":     live_status["fill_qty"],
                "status":       "FILLED",
                "reason":       f"Zerodha confirmed fill @ ₹{live_status['fill_price']:.2f}",
                "circuit_flag": circuit_flag,
                "circuit_msg":  circuit_msg,
            }
        elif live_status["status"] in ("REJECTED", "CANCELLED"):
            return {
                "filled":       False,
                "fill_price":   None,
                "fill_qty":     None,
                "status":       live_status["status"],
                "reason":       live_status["reject_reason"],
                "circuit_flag": circuit_flag,
                "circuit_msg":  circuit_msg,
            }
        else:
            # OPEN, NOT_FOUND, ERROR — fall through to simulation with warning
            print(f"  WARN [{ticker}]: Live order status '{live_status['status']}' — falling back to price simulation")
            print(f"  WARN [{ticker}]: {live_status['reject_reason']}")

    # ── PAPER MODE (or live fallback): simulate fill from open price ──────────
    if order_type == "BUY":
        filled = today_open <= limit_price
    else:  # SELL
        filled = today_open >= limit_price

    if filled:
        return {
            "filled":       True,
            "fill_price":   today_open,
            "fill_qty":     shares,
            "status":       "FILLED",
            "reason":       f"Open ₹{today_open:.2f} {'≤' if order_type == 'BUY' else '≥'} limit ₹{limit_price:.2f}",
            "circuit_flag": circuit_flag,
            "circuit_msg":  circuit_msg,
        }
    else:
        return {
            "filled":       False,
            "fill_price":   None,
            "fill_qty":     None,
            "status":       "MISSED",
            "reason":       f"Open ₹{today_open:.2f} {'>' if order_type == 'BUY' else '<'} limit ₹{limit_price:.2f}",
            "circuit_flag": circuit_flag,
            "circuit_msg":  circuit_msg,
        }


def _update_csv_row(target_date: str, ticker: str, order_type: str,
                    new_status: str, fill_price: str, fill_date: str) -> None:
    """
    Rewrite amo_orders.csv updating the matching row's status/fill fields.
    target_date is the ISO date string from order["date"] (e.g. "2026-06-06").
    Uses a full rewrite of the file (safe for small files up to ~1000 rows).
    """
    if not AMO_CSV.exists():
        return

    rows = []
    with open(AMO_CSV, newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        for row in reader:
            if (row["date"] == target_date
                    and row["ticker"] == ticker
                    and row["order_type"] == order_type
                    and row["status"] == "DRY_RUN"):
                row["status"]     = new_status
                row["fill_price"] = fill_price
                row["fill_date"]  = fill_date
            rows.append(row)

    tmp = str(AMO_CSV) + ".tmp"
    with open(tmp, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, str(AMO_CSV))


def _update_portfolio_fill(
    ticker: str,
    order_type: str,
    shares: int,
    fill_price: float,
    fill_date: date,
    exit_reason: str = "STRATEGY_SIGNAL",
    is_rm_exit: bool = False,
    portfolio_obj=None,   # PaperPortfolio instance — required for BUY fills
) -> None:
    """
    Update portfolio_state.json to reflect the actual next-day fill price.

    For a BUY: calls portfolio_obj.confirm_buy_fill() which deducts cash exactly
               once at the actual open fill price (fixing the double-deduction bug
               where signal_runner previously deducted at close and here at open).
    For a SELL: update cash, clear position, record trade with the correct
                exit_reason. If is_rm_exit=True, also trigger cooldown and
                clear the pending_rm_exit flag (set by yesterday's signal_runner).

    Uses atomic write to prevent state corruption on crash.
    """
    import json
    import math
    from utils.costs import apply_slippage, transaction_costs

    if order_type == "BUY":
        if portfolio_obj is None:
            print(f"  ERROR [{ticker}]: BUY fill requires portfolio_obj — cannot process.")
            return

        exec_price = apply_slippage(fill_price, "buy")

        # Reload fresh state from JSON before modifying (other SELL fills may have
        # already written since portfolio_obj was first loaded).
        portfolio_obj.load()

        # Guard: pending_buy must be True — if not, fill was already processed
        pos_check = portfolio_obj.state["positions"].get(ticker, {})
        if not pos_check.get("pending_buy", False):
            print(
                f"  WARN [{ticker}]: BUY fill received but pending_buy=False "
                f"— already processed? Skipping."
            )
            return

        # Confirm fill: deducts cash exactly once at the actual open-price fill.
        # This is the ONLY cash deduction for this BUY — signal_runner no longer
        # deducts cash when queuing the AMO (queue_pending_buy does not touch cash).
        try:
            portfolio_obj.confirm_buy_fill(
                ticker=ticker,
                actual_exec_price=exec_price,
                actual_shares=shares,
                fill_date=fill_date.isoformat(),
                ohlcv_history=None,
            )
        except ValueError as e:
            print(f"  ERROR [{ticker}]: confirm_buy_fill failed: {e}")
            return

        # confirm_buy_fill() already saved state atomically — nothing more to do.
        print(f"  [portfolio] {STATE_FILE} updated (BUY fill confirmed for {ticker}).")
        return

    # ── SELL path: direct raw-state update ──────────────────────────────────
    if not STATE_FILE.exists():
        print(f"  WARN: {STATE_FILE} not found — cannot update portfolio state.")
        return

    with open(STATE_FILE) as fh:
        state = json.load(fh)

    pos = state["positions"].get(ticker)
    if pos is None:
        print(f"  WARN: {ticker} not in portfolio state — skipping update.")
        return

    if order_type == "SELL":
        if pos["shares"] <= 0:
            print(f"  WARN: {ticker} has no shares to sell — skipping update.")
            return
        exec_price = apply_slippage(fill_price, "sell")
        cost       = transaction_costs(exec_price, shares, "sell")["total"]
        proceeds   = shares * exec_price - cost

        state["cash"] += proceeds

        # Record in trade log with the actual exit reason
        entry_px  = pos["entry_price"]
        gross_pnl = (exec_price - entry_px) * shares
        net_pnl   = gross_pnl - cost
        state["trade_log"].append({
            "ticker":      ticker,
            "entry_date":  pos["entry_date"],
            "exit_date":   fill_date.isoformat(),
            "entry_price": round(entry_px, 4),
            "exit_price":  round(exec_price, 4),
            "shares":      shares,
            "gross_pnl":   round(gross_pnl, 2),
            "net_pnl":     round(net_pnl, 2),
            "return_pct":  round((exec_price / entry_px - 1) * 100, 4),
            "exit_reason": exit_reason,
        })
        state["total_trades"] += 1

        # Reset position — clears pending_rm_exit and pending_buy flags if set
        state["positions"][ticker] = {
            "shares": 0, "entry_price": 0.0, "entry_date": None,
            "highest_high_since_entry": 0.0, "bars_held": 0,
            "chandelier_stop": None, "pending_buy": False,
        }

        # Deferred RM exits: trigger cooldown now that the fill is confirmed.
        # +1 offset: the evening signal_runner's advance_cooldown() absorbs one bar,
        # leaving exactly _COOLDOWN_BARS days of buy suppression from tomorrow.
        if is_rm_exit and "cooldown_state" in state and ticker in state["cooldown_state"]:
            state["cooldown_state"][ticker] = {
                "remaining_bars":   _COOLDOWN_BARS_WITH_OFFSET,
                "last_exit_reason": exit_reason,
            }
            print(f"  [cooldown] {ticker} — {_COOLDOWN_BARS}-bar cooldown triggered ({exit_reason})")

    # Atomic write
    tmp = str(STATE_FILE) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, default=str)
    os.replace(tmp, str(STATE_FILE))
    print(f"  [portfolio] {STATE_FILE} updated.")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_morning_check(check_date: Optional[date] = None, apply_fills: bool = False) -> None:
    """
    Execute the morning fill check.

    Args:
        check_date:   Override today's date (used for testing past dates).
        apply_fills:  If True, update portfolio_state.json with actual fills.
                      If False (default / dry-run), only print the report.
    """
    _check_auth()

    if LIVE_TRADING_MODE:
        print("⚠️  LIVE_TRADING_MODE = True — querying actual Zerodha order status")
        print("⚠️  Any REJECTED or CANCELLED orders require immediate manual attention")
    else:
        print("[morning_fill_check] Paper trading mode — simulating fills from open price")

    today = check_date or date.today()

    # ── Load PaperPortfolio for BUY fill confirmation ──────────────────────────
    # Required for confirm_buy_fill() and cancel_pending_buy(). Instantiate with
    # empty tickers list — load() reads tickers from the existing state file.
    from paper_trading.paper_portfolio import PaperPortfolio
    _portfolio = PaperPortfolio([], str(STATE_FILE), 100_000.0)
    if STATE_FILE.exists():
        _portfolio.load()

    # ── Trading day guard ──────────────────────────────────────────────────────
    # AMO orders only fill on trading days. If today is a weekend or NSE holiday,
    # there is no open price to check against — exit cleanly.
    if not is_trading_day(today):
        print(f"Market closed today — {today}. No fill check needed.")
        return

    W = 62
    print("\n" + "=" * W)
    print(f"  MORNING FILL REPORT — {today.strftime('%A %d %b %Y')}")
    print(f"  Fill date: {today}  |  Checking all unfilled DRY_RUN orders")
    if not apply_fills:
        print("  MODE: DRY RUN — portfolio state will NOT be modified")
    else:
        print("  MODE: APPLY — portfolio state will be updated with fills")
    print("=" * W)
    print()

    pending = _load_pending_orders()

    if not pending:
        print("  No pending DRY_RUN orders found (amo_orders.csv is empty or all filled).")
        print("=" * W)
        return

    print(f"  Pending unfilled orders: {len(pending)}")

    # ── Corporate actions check (ex-date = TODAY) ─────────────────────────────
    # If a stock has its ex-date TODAY, the open price includes the price-gap
    # adjustment (e.g. a split halves the price). Filling the AMO at that
    # adjusted open would buy/sell at a price that doesn't match our limit price
    # (which was set against the pre-adjustment price). Cancel those fills.
    pending_tickers = list({o["ticker"] for o in pending})
    try:
        from utils.corporate_actions import get_corporate_action_warning
        # We check only for ex-date == TODAY (not the 2-day window used at signal time)
        ex_today: dict[str, bool] = {}
        for t in pending_tickers:
            w = get_corporate_action_warning(t, check_date=today)
            # skip=True AND ex_date == today means the action happens TODAY
            ex_today[t] = w["skip"] and w.get("ex_date") == today
            if ex_today[t]:
                print(
                    f"\n  [CORP_ACTION] {t} — ex-date is TODAY ({today}). "
                    f"AMO fill CANCELLED: {w['reason']}"
                )
    except Exception as exc:
        print(f"\n  [CORP_ACTION] WARNING: check failed — {exc}. Proceeding with fills.")
        ex_today = {t: False for t in pending_tickers}

    print()
    results:        list[dict] = []
    filled_count    = 0
    missed_count    = 0
    cancelled_count = 0

    for order in pending:
        ticker      = order["ticker"]
        order_type  = order["order_type"]
        shares      = int(order["shares"])
        signal_px   = float(order["signal_price"])
        limit_px    = float(order["limit_price"])
        order_date  = order["date"]   # the day the signal fired (may be Friday)

        # Skip fill if this stock has a corporate action ex-date today
        if ex_today.get(ticker, False):
            cancelled_count += 1
            print(
                f"  ✗ CANCELLED  {ticker:<14} {order_type:<4} {shares:>3} shr "
                f"| ex-date today — fill skipped"
            )
            _update_csv_row(order_date, ticker, order_type, "CANCELLED_CA", "", "")
            continue

        # Detect deferred RM exits — notes field carries the exit reason
        notes      = order.get("notes", "")
        is_rm_exit = notes in _RM_EXIT_NOTES

        open_px = _fetch_open_price(ticker, today)

        if open_px is None:
            print(f"  ? UNKNOWN  {ticker:<14} {order_type:<4} {shares:>3} shr "
                  f"| limit ₹{limit_px:,.2f} | open price unavailable")
            continue

        prev_close = _fetch_prev_close(ticker, today)
        result     = _process_order(order, open_px, prev_close, kite=None)
        result["ticker"] = ticker
        results.append(result)

        gap_pct = (open_px - limit_px) / limit_px * 100
        rm_tag  = f" [RM:{notes}]" if is_rm_exit else ""

        if result["filled"]:
            filled_count += 1
            detail = (
                f"| limit ₹{limit_px:,.2f} | opened ₹{open_px:,.2f} "
                f"| FILLED at ₹{result['fill_price']:,.2f}{rm_tag}"
            )
            print(f"  ✓ FILLED   {ticker:<14} {order_type:<4} {shares:>3} shr {detail}")
            if result["circuit_flag"]:
                print(f"  ⚠️  CIRCUIT BREAKER WARNING: {result['circuit_msg']}")
                print(f"  ⚠️  Verify position manually in Zerodha dashboard")

            _update_csv_row(order_date, ticker, order_type,
                            "FILLED", str(round(open_px, 4)), today.isoformat())
            if apply_fills:
                _update_portfolio_fill(
                    ticker, order_type, shares, open_px, today,
                    exit_reason=notes if notes else "STRATEGY_SIGNAL",
                    is_rm_exit=is_rm_exit,
                    portfolio_obj=_portfolio,
                )

        elif result["status"] in ("REJECTED", "CANCELLED"):
            detail = f"| {result['reason']}{rm_tag}"
            print(f"  ✗ {result['status']:<10} {ticker:<14} {order_type:<4} {shares:>3} shr {detail}")
            if result["circuit_flag"]:
                print(f"  ⚠️  CIRCUIT BREAKER WARNING: {result['circuit_msg']}")
            _update_csv_row(order_date, ticker, order_type, result["status"], "", "")
            # Cancelled BUY AMO: reset the pending_buy flag so position returns to flat
            if apply_fills and order_type == "BUY":
                _portfolio.load()
                _portfolio.cancel_pending_buy(ticker)
                _portfolio.save()
                print(f"  [{ticker}]: BUY AMO {result['status']} — pending_buy cancelled")

        else:
            missed_count += 1
            detail = (
                f"| limit ₹{limit_px:,.2f} | opened ₹{open_px:,.2f} "
                f"| gap {'+'if gap_pct>0 else ''}{gap_pct:.1f}%{rm_tag}"
            )
            print(f"  ✗ MISSED   {ticker:<14} {order_type:<4} {shares:>3} shr {detail}")
            if result["circuit_flag"]:
                print(f"  ⚠️  CIRCUIT BREAKER WARNING: {result['circuit_msg']}")
                print(f"  ⚠️  Verify position manually in Zerodha dashboard")

            _update_csv_row(order_date, ticker, order_type, "MISSED", "", "")

            # Missed BUY AMO: cancel the pending_buy so position returns to flat.
            # Without this, signal_runner would see pending_buy=True indefinitely
            # and skip the stock forever.
            if apply_fills and order_type == "BUY":
                _portfolio.load()
                _portfolio.cancel_pending_buy(ticker)
                _portfolio.save()
                print(f"  [{ticker}]: BUY AMO MISSED — pending_buy cancelled, position reset to flat")

    print()
    parts = [f"{filled_count} FILLED", f"{missed_count} MISSED"]
    if cancelled_count:
        parts.append(f"{cancelled_count} CANCELLED (corp action ex-date today)")
    print(f"  Summary: {' | '.join(parts)}")
    if apply_fills and filled_count > 0:
        print(f"  Portfolio updated with {filled_count} actual fill price(s).")
    elif not apply_fills and filled_count > 0:
        print(f"  To update portfolio with these fills, re-run with --apply flag.")

    # ── REJECTED / CANCELLED orders requiring manual attention ────────────────
    if any(r["status"] in ("REJECTED", "CANCELLED") for r in results):
        print("\n" + "="*60)
        print("⚠️  ORDERS REQUIRING MANUAL ATTENTION")
        print("="*60)
        for r in results:
            if r["status"] in ("REJECTED", "CANCELLED"):
                print(f"  {r['ticker']:<20} {r['status']}: {r['reason']}")
        print("Manual action required: check Zerodha dashboard and update portfolio_state.json")
        print("="*60)

    print("=" * W)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Morning AMO fill checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python paper_trading/morning_fill_check.py                   # dry run, yesterday's orders
  python paper_trading/morning_fill_check.py --apply           # update portfolio state
  python paper_trading/morning_fill_check.py --date 2026-06-05 # check a specific date
        """,
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Signal date to check (default: last trading day)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply fills to portfolio_state.json (default: dry run only)",
    )
    args = parser.parse_args()

    check_date = date.fromisoformat(args.date) if args.date else None
    run_morning_check(check_date=check_date, apply_fills=args.apply)
