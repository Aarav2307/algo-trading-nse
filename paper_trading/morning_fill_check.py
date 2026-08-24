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
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.kite_fetcher import get_ohlcv
from utils.market_calendar import is_trading_day
from strategy_config import COOLDOWN, AMO_LIMIT_BUFFER_PCT   # single source of truth — see strategy_config.py
from engine.fill_resolution import (   # single source of truth — see engine/fill_resolution.py
    check_circuit_breaker as _check_circuit_breaker,
    is_fill_hit,
    classify_missed_sell,
)


# ── Paths ─────────────────────────────────────────────────────────────────────
AMO_CSV         = _ROOT / "paper_trading" / "amo_orders.csv"
STATE_FILE      = _ROOT / "paper_trading" / "portfolio_state.json"
TOKEN_FILE      = _ROOT / "auth" / "access_token.txt"

# RM exit reasons — these come through in the AMO order's "notes" field.
# When a SELL fills with one of these notes, we must also trigger cooldown
# and clear the pending_rm_exit flag on the position.
_RM_EXIT_NOTES       = frozenset({"HARD_STOP", "CHANDELIER", "TIME_STOP"})
_STRATEGY_EXIT_NOTES = frozenset({"STRATEGY_SIGNAL"})

_AMO_LIMIT_BUFFER = AMO_LIMIT_BUFFER_PCT   # 0.5% buffer below close for SELL AMO requeue
_AMO_ORDER_LOG    = _ROOT / "paper_trading" / "amo_orders.csv"

# Gap-down circuit breaker threshold for SELL AMOs.
# If a stock opens more than this % below the SELL limit price,
# we exit immediately at open rather than requeuing.
# Rationale: a 3%+ gap-down is a genuine adverse event (not noise).
# Requeuing after a 3%+ gap means holding a position in a confirmed
# downtrend absorbing further losses. Exit now at market open.
# The 0.5% AMO buffer is already in the limit price, so the effective
# gap from yesterday's close that triggers this is ~3.5%.
GAP_BREAKER_THRESHOLD = 0.03   # 3% gap triggers immediate exit at open
# The +1 offset (absorbed by the same evening's signal_runner.advance_cooldown())
# is applied inside PaperPortfolio.trigger_cooldown() itself, not here.
_COOLDOWN_BARS  = COOLDOWN["cooldown_bars"]

# ETF overlay (NIFTYBEES) is managed by signal_runner.py — no AMO orders

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

    Deduplication: if multiple DRY_RUN rows exist for the same
    (date, ticker, order_type) combination — which can happen if signal_runner
    is run twice for the same day — only the LAST row is kept (most recent
    takes precedence). A warning is printed for each duplicate detected.

    Returns list of unique pending order dicts, one per (date, ticker, order_type).
    """
    if not AMO_CSV.exists():
        return []

    # Load all pending rows
    all_pending = []
    with open(AMO_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["status"] == "DRY_RUN" and row.get("fill_date", "") == "":
                all_pending.append(row)

    if not all_pending:
        return []

    # Deduplicate: keep last row per (date, ticker, order_type)
    # "last" = last appearance in CSV = most recently written
    # Use ordered dict to preserve insertion order while deduplicating
    seen: dict = {}
    duplicates_found = False

    for row in all_pending:
        # Date column is named 'date'; handle 'signal_date' as a fallback
        row_date = row.get("date", row.get("signal_date", "unknown"))
        key = (row_date, row["ticker"], row["order_type"])

        if key in seen:
            duplicates_found = True
            print(
                f"  WARN [dedup]: Duplicate DRY_RUN order found for "
                f"{row['ticker']} {row['order_type']} on {row_date} — "
                f"keeping most recent, discarding earlier entry"
            )

        seen[key] = row  # overwrite with most recent

    if duplicates_found:
        print(
            f"  WARN [dedup]: Duplicate AMO orders detected in {AMO_CSV}. "
            f"This can happen if signal_runner.py was run twice for the same date. "
            f"Only the most recent order per (date, ticker, order_type) will be processed."
        )

    return list(seen.values())


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
        # Rate limit: paces every Kite API call at ~55 req/min (safe under the
        # 60 req/min cap), mirroring signal_runner.py's and
        # screener/auto_screener.py's identical pattern. Applied immediately
        # after the call and BEFORE any result guards, since the API call itself
        # already consumed quota regardless of what we do with the response.
        # An exception skips it — no quota was consumed.
        time.sleep(1.1)
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
        # Rate limit: paces every Kite API call at ~55 req/min (safe under the
        # 60 req/min cap), mirroring signal_runner.py's and
        # screener/auto_screener.py's identical pattern. Applied immediately
        # after the call and BEFORE any result guards, since the API call itself
        # already consumed quota regardless of what we do with the response.
        # An exception skips it — no quota was consumed.
        time.sleep(1.1)
        if df is None or df.empty:
            return 0.0
        return float(df["close"].iloc[-1])
    except Exception:
        return 0.0


def _fetch_close_price(ticker: str, today: date) -> Optional[float]:
    """
    Fetch today's closing price for ticker from Kite.
    Used when requeueing a missed RM SELL AMO — the new limit is based on today's close.
    Returns None if data is unavailable.
    """
    try:
        start = today.isoformat()
        end   = (today + timedelta(days=1)).isoformat()
        df    = get_ohlcv(ticker, start, end)
        # Rate limit: paces every Kite API call at ~55 req/min (safe under the
        # 60 req/min cap), mirroring signal_runner.py's and
        # screener/auto_screener.py's identical pattern. Applied immediately
        # after the call and BEFORE any result guards, since the API call itself
        # already consumed quota regardless of what we do with the response.
        # An exception skips it — no quota was consumed.
        time.sleep(1.1)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])
    except Exception as exc:
        print(f"  WARN: Could not fetch {ticker} close — {exc}")
        return None


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
        # Same pacing as the get_ohlcv sites above. Reachable only under
        # LIVE_TRADING_MODE=True (dead code in paper mode). It hits the orderbook
        # endpoint rather than historical-data, which Kite rate-limits more
        # generously — 1.1s is deliberately conservative rather than tuned, so
        # all four Kite call sites in this module share one rule.
        time.sleep(1.1)
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
    filled = is_fill_hit(order_type, limit_price, today_open)

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

    Both BUY and SELL fills go through PaperPortfolio's own seam
    (confirm_buy_fill() / close_position()) — this function never touches
    self.state directly. Both methods save atomically on success.

    For a BUY: calls portfolio_obj.confirm_buy_fill() which deducts cash exactly
               once at the actual open fill price (fixing the double-deduction bug
               where signal_runner previously deducted at close and here at open).
    For a SELL: calls portfolio_obj.close_position(), which credits cash, records
                the trade with the correct exit_reason, and — when is_rm_exit=True —
                triggers cooldown, clearing the pending_rm_exit flag set by
                yesterday's signal_runner.
    """
    from utils.costs import apply_slippage

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

        print(f"  [portfolio] {STATE_FILE} updated (BUY fill confirmed for {ticker}).")
        return

    # ── SELL path: go through PaperPortfolio's own seam ──────────────────────
    if portfolio_obj is None:
        print(f"  ERROR [{ticker}]: SELL fill requires portfolio_obj — cannot process.")
        return

    # Reload fresh state from JSON before modifying (other fills earlier in this
    # same run may have already saved through this same portfolio_obj).
    portfolio_obj.load()

    pos_check = portfolio_obj.state["positions"].get(ticker)
    if pos_check is None:
        print(f"  WARN: {ticker} not in portfolio state — skipping update.")
        return

    exec_price = apply_slippage(fill_price, "sell")

    try:
        portfolio_obj.close_position(
            ticker=ticker,
            exec_price=exec_price,
            date_str=fill_date.isoformat(),
            reason=exit_reason,
            is_rm_exit=is_rm_exit,
            cooldown_bars=_COOLDOWN_BARS,
        )
    except ValueError as e:
        # Idempotency guard inside close_position() — position already closed.
        print(f"  WARN [{ticker}]: {e}")
        return

    if is_rm_exit:
        print(f"  [cooldown] {ticker} — {_COOLDOWN_BARS}-bar cooldown triggered ({exit_reason})")
    print(f"  [portfolio] {STATE_FILE} updated (SELL fill confirmed for {ticker}).")


# =============================================================================
# Fill decisions — plan / render / execute
# =============================================================================
# One frozen FillDecision per pending AMO order.
#
#   plan_fill()         reads market data. Writes NOTHING.
#   execute_decision()  is the ONLY writer in this module. It is called from
#                       exactly one place, behind exactly one `if apply_fills:`.
#
# Why this shape instead of an `if apply_fills:` guard on each write:
# the dry-run contract in this module's docstring ("portfolio state is NOT
# modified") used to be enforced by guarding N call sites. Those guards
# covered the portfolio write but not the amo_orders.csv write, so a dry run
# flipped orders out of DRY_RUN status without ever confirming the fill.
# _load_pending_orders() selects on status == "DRY_RUN", so the order became
# invisible to every later run and the fill was lost silently — cash never
# deducted, position never opened, no risk management ever applied to it.
# Guarding the fifth call site the same way only relocates the problem to the
# sixth. Here the report path has no writer reachable from it, so a read-only
# mode cannot mutate state no matter what branches are added later.
#
# Second defect this closes structurally: _update_csv_row() only matches rows
# still at status "DRY_RUN". The old gap-exit path wrote "MISSED" first and
# then tried to write "GAP_EXIT", which silently no-opped because the row was
# no longer DRY_RUN — so amo_orders.csv recorded every gap-exit as a missed
# order with no fill price, while the portfolio correctly showed the position
# closed. A decision now carries exactly one csv_status, written exactly once.


@dataclass(frozen=True)
class FillDecision:
    """
    Immutable description of what should happen to one pending AMO order.

    Produced by plan_fill() (read-only) and consumed by execute_decision()
    (the single write seam). `detail` is the pre-rendered report line, so the
    reporting path needs no logic of its own.
    """
    order:       dict
    action:      str                      # FILL | GAP_EXIT | MISS_CANCEL_BUY
                                          # | REQUEUE_SELL | UNMANAGED_MISS
                                          # | CANCEL_CA | REJECT | NO_DATA
    open_px:     Optional[float] = None
    close_px:    Optional[float] = None   # REQUEUE_SELL only — new limit basis
    csv_status:  Optional[str]   = None   # None → leave the row at DRY_RUN
    is_rm_exit:  bool            = False
    exit_reason: str             = "STRATEGY_SIGNAL"
    circuit_msg: str             = ""
    notes:       str             = ""
    status:      str             = ""     # mirrors _process_order()'s status
    reason:      str             = ""     # short factual phrase, no ticker/side
                                          # prefix — matches _process_order()'s
                                          # own `reason` style so the two are
                                          # interchangeable to consumers
    detail:      str             = ""     # rendered report line

    @property
    def is_miss(self) -> bool:
        """True for the three outcomes that count as a genuine missed fill."""
        return self.action in ("MISS_CANCEL_BUY", "REQUEUE_SELL", "UNMANAGED_MISS")


def plan_fill(order: dict, today: date, ex_today: dict) -> FillDecision:
    """
    Decide what should happen to one pending order. READ-ONLY: fetches market
    data and inspects the order, but writes to no file and mutates no state.

    Deliberate behaviour change vs the previous inline loop: the gap-exit /
    requeue classification now runs in BOTH modes. It used to be reachable
    only when apply_fills was True, so a dry run reported a 3%+ gap-down SELL
    as a plain MISS and never showed the GAP_EXIT it would actually take. A
    dry run that does not preview the real decision is not a useful dry run.
    Cost: one extra read-only close-price fetch per requeued SELL in dry mode.
    """
    ticker     = order["ticker"]
    order_type = order["order_type"]
    shares     = int(order["shares"])
    limit_px   = float(order["limit_price"])

    notes      = order.get("notes", "")
    notes_base = notes.split(" [")[0].strip() if notes else ""
    is_rm_exit = notes_base in _RM_EXIT_NOTES
    rm_tag     = f" [RM:{notes}]" if is_rm_exit else ""

    # Ex-date today: the open already carries the corporate-action adjustment,
    # so the limit price (set against the pre-adjustment price) is meaningless.
    #
    # Finding #12: cancelling the FILL is correct, but the POSITION still has to
    # be resolved or it is stranded. This branch used to return a bare
    # CANCELLED_CA that execute_decision() had no case for, so nothing reset the
    # position: a cancelled BUY froze the ticker on pending_buy forever, and a
    # cancelled SELL left pending_rm_exit set with NO order that could ever close
    # it (signal_runner's pending_rm_exit branch never sets needs_amo_order, so
    # Step 13 emits nothing). The resolutions below reuse the two seams the
    # MISSED paths already use — cancel_pending_buy() and requeue_rm_sell() — so
    # no new machinery and no new retry threshold is introduced.
    if ex_today.get(ticker, False):
        ca_reason = "Corporate action ex-date today — fill cancelled, open price is adjusted"
        ca_detail = (f"  ✗ CANCELLED  {ticker:<14} {order_type:<4} {shares:>3} shr "
                     f"| ex-date today — fill skipped")

        # A managed SELL needs a replacement order or the position can never
        # exit. Today's close is already post-adjustment, so it is the correct
        # basis for tomorrow's limit — the same basis the MISSED-SELL requeue
        # uses. Costs one extra paced Kite call, only on an ex-date SELL.
        ca_close = None
        if order_type == "SELL" and (is_rm_exit or notes_base in _STRATEGY_EXIT_NOTES):
            ca_close = _fetch_close_price(ticker, today)
            if ca_close is None:
                ca_reason += (" — could not fetch today's close for requeue; "
                              "MANUAL ACTION REQUIRED")
                ca_detail += (f"\n  ERROR [{ticker}]: SELL AMO cancelled for ex-date but "
                              f"today's close is unavailable, so it could not be re-queued. "
                              f"MANUAL ACTION REQUIRED: position is still open with no "
                              f"exit order.")
            else:
                _ca_limit = round(ca_close * (1 - _AMO_LIMIT_BUFFER), 2)
                ca_reason += f" — SELL AMO requeued at ₹{_ca_limit:.2f} for tomorrow's open"
                ca_detail += (f"\n  ⚠️  REQUEUED: {ticker} SELL → new AMO SELL limit "
                              f"₹{_ca_limit:.2f} for tomorrow's open (ex-date cancel)")

        return FillDecision(
            order=order, action="CANCEL_CA", csv_status="CANCELLED_CA",
            close_px=ca_close,
            is_rm_exit=is_rm_exit, notes=notes, status="CANCELLED_CA",
            reason=ca_reason,
            detail=ca_detail,
        )

    open_px = _fetch_open_price(ticker, today)
    if open_px is None:
        return FillDecision(
            order=order, action="NO_DATA", csv_status=None,
            is_rm_exit=is_rm_exit, notes=notes, status="UNKNOWN",
            reason="Open price unavailable — order left pending for the next run",
            detail=(f"  ? UNKNOWN  {ticker:<14} {order_type:<4} {shares:>3} shr "
                    f"| limit ₹{limit_px:,.2f} | open price unavailable"),
        )

    prev_close = _fetch_prev_close(ticker, today)
    result     = _process_order(order, open_px, prev_close, kite=None)
    circuit    = result["circuit_msg"] if result["circuit_flag"] else ""
    gap_pct    = (open_px - limit_px) / limit_px * 100

    # ── Filled ───────────────────────────────────────────────────────────────
    if result["filled"]:
        return FillDecision(
            order=order, action="FILL", open_px=open_px, csv_status="FILLED",
            is_rm_exit=is_rm_exit,
            exit_reason=notes if notes else "STRATEGY_SIGNAL",
            circuit_msg=circuit, notes=notes, status="FILLED",
            reason=result["reason"],
            detail=(f"  ✓ FILLED   {ticker:<14} {order_type:<4} {shares:>3} shr "
                    f"| limit ₹{limit_px:,.2f} | opened ₹{open_px:,.2f} "
                    f"| FILLED at ₹{result['fill_price']:,.2f}{rm_tag}"),
        )

    # ── Rejected / cancelled by the broker (live mode) ───────────────────────
    if result["status"] in ("REJECTED", "CANCELLED"):
        return FillDecision(
            order=order, action="REJECT", open_px=open_px,
            csv_status=result["status"], is_rm_exit=is_rm_exit,
            circuit_msg=circuit, notes=notes, status=result["status"],
            reason=result["reason"],
            detail=(f"  ✗ {result['status']:<10} {ticker:<14} {order_type:<4} "
                    f"{shares:>3} shr | {result['reason']}{rm_tag}"),
        )

    # ── Missed ───────────────────────────────────────────────────────────────
    miss_detail = (f"  ✗ MISSED   {ticker:<14} {order_type:<4} {shares:>3} shr "
                   f"| limit ₹{limit_px:,.2f} | opened ₹{open_px:,.2f} "
                   f"| gap {'+' if gap_pct > 0 else ''}{gap_pct:.1f}%{rm_tag}")

    if order_type == "BUY":
        return FillDecision(
            order=order, action="MISS_CANCEL_BUY", open_px=open_px,
            csv_status="MISSED", is_rm_exit=is_rm_exit, circuit_msg=circuit,
            notes=notes, status="MISSED", reason=result["reason"],
            detail=miss_detail,
        )

    # SELL miss: gap-down circuit breaker vs requeue vs unmanaged.
    is_managed  = is_rm_exit or notes_base in _STRATEGY_EXIT_NOTES
    miss_class  = classify_missed_sell(limit_px, open_px, GAP_BREAKER_THRESHOLD, is_managed)
    gap_mag     = (limit_px - open_px) / limit_px

    if miss_class == "GAP_EXIT":
        return FillDecision(
            order=order, action="GAP_EXIT", open_px=open_px,
            csv_status="GAP_EXIT", is_rm_exit=is_rm_exit,
            exit_reason="GAP_EXIT", circuit_msg=circuit, notes=notes,
            status="GAP_EXIT",
            reason=(f"{result['reason']} — gap {gap_mag*100:.1f}% exceeds "
                    f"{GAP_BREAKER_THRESHOLD*100:.0f}% breaker, exiting at open"),
            detail=(f"  ⚡ GAP_EXIT  {ticker:<14} {order_type:<4} {shares:>3} shr "
                    f"| limit ₹{limit_px:,.2f} | opened ₹{open_px:,.2f} "
                    f"| gap {gap_mag*100:.1f}% > {GAP_BREAKER_THRESHOLD*100:.0f}% threshold "
                    f"— exiting at open{rm_tag}"),
        )

    if miss_class == "REQUEUE":
        today_close = _fetch_close_price(ticker, today)
        if today_close is None:
            return FillDecision(
                order=order, action="REQUEUE_SELL", open_px=open_px,
                close_px=None, csv_status="MISSED", is_rm_exit=is_rm_exit,
                circuit_msg=circuit, notes=notes, status="MISSED",
                reason=(f"{result['reason']} — could not fetch today's close "
                        f"for requeue; MANUAL ACTION REQUIRED"),
                detail=(miss_detail + f"\n  ERROR [{ticker}]: RM SELL MISSED but could not "
                        f"fetch today's close for requeue. MANUAL ACTION REQUIRED: "
                        f"check position in Zerodha dashboard."),
            )
        new_limit = round(today_close * (1 - _AMO_LIMIT_BUFFER), 2)
        return FillDecision(
            order=order, action="REQUEUE_SELL", open_px=open_px,
            close_px=today_close, csv_status="MISSED", is_rm_exit=is_rm_exit,
            circuit_msg=circuit, notes=notes, status="MISSED",
            reason=(f"{result['reason']} — SELL AMO requeued at "
                    f"₹{new_limit:.2f} for tomorrow's open"),
            detail=(miss_detail +
                    f"\n  ⚠️  REQUEUED: {ticker} RM SELL → new AMO SELL limit "
                    f"₹{new_limit:.2f} for tomorrow's open"
                    f"\n  ⚠️  Original MISSED: open ₹{open_px:.2f} vs limit ₹{limit_px:.2f}"),
        )

    return FillDecision(
        order=order, action="UNMANAGED_MISS", open_px=open_px,
        csv_status="MISSED", is_rm_exit=is_rm_exit, circuit_msg=circuit,
        notes=notes, status="MISSED",
        reason=f"{result['reason']} — not a managed exit, no automatic follow-up",
        detail=miss_detail,
    )


def _requeue_sell_amo(d: FillDecision, portfolio, today: date) -> None:
    """Log a fresh SELL AMO at an updated limit and bump the requeue counter."""
    ticker = d.order["ticker"]
    shares = int(d.order["shares"])

    if d.close_px is None:
        # plan_fill already rendered the MANUAL ACTION REQUIRED line.
        return

    new_limit = round(d.close_px * (1 - _AMO_LIMIT_BUFFER), 2)

    from engine.order_manager import AMOOrderManager
    amo = AMOOrderManager({
        "enabled":          True,
        "dry_run":          True,
        "limit_buffer_pct": _AMO_LIMIT_BUFFER,
        "order_log_file":   str(_AMO_ORDER_LOG),
    })
    amo.place_sell_amo(
        ticker=ticker, shares=shares, signal_price=d.close_px,
        order_date=today, notes=d.notes + " [REQUEUED]",
    )

    portfolio.load()
    portfolio.requeue_rm_sell(ticker, new_limit, today.isoformat())


def execute_decision(d: FillDecision, portfolio, today: date) -> None:
    """
    The single write seam of this module. Never call this outside an
    `if apply_fills:` block — doing so reintroduces the dry-run bug.

    Ordering contract (Jul 18 hardening, preserved): the portfolio is updated
    BEFORE the AMO ledger row is marked terminal. A crash between the two
    leaves the row at DRY_RUN and therefore reprocessable, rather than
    FILLED-with-no-portfolio-update, which was irrecoverable.
    """
    o      = d.order
    ticker = o["ticker"]
    shares = int(o["shares"])

    if d.action == "FILL":
        _update_portfolio_fill(
            ticker, o["order_type"], shares, d.open_px, today,
            exit_reason=d.exit_reason, is_rm_exit=d.is_rm_exit,
            portfolio_obj=portfolio,
        )
    elif d.action == "GAP_EXIT":
        _update_portfolio_fill(
            ticker, "SELL", shares, d.open_px, today,
            exit_reason="GAP_EXIT", is_rm_exit=d.is_rm_exit,
            portfolio_obj=portfolio,
        )
    elif d.action in ("MISS_CANCEL_BUY", "REJECT"):
        # Missed/rejected BUY AMO: reset pending_buy so the position returns to
        # flat and the stock is eligible for signals again. Without this,
        # signal_runner would see pending_buy=True indefinitely.
        if o["order_type"] == "BUY":
            portfolio.load()
            portfolio.cancel_pending_buy(ticker)
            portfolio.save()
            print(f"  [{ticker}]: BUY AMO {d.status} — pending_buy cancelled, "
                  f"position reset to flat")
    elif d.action == "REQUEUE_SELL":
        _requeue_sell_amo(d, portfolio, today)
    elif d.action == "CANCEL_CA":
        # Finding #12. The fill is cancelled; the position must still be resolved.
        if o["order_type"] == "BUY":
            portfolio.load()
            portfolio.cancel_pending_buy(ticker)
            portfolio.save()
            print(f"  [{ticker}]: BUY AMO cancelled for ex-date — pending_buy "
                  f"cancelled, position reset to flat")
        else:
            # SELL. _requeue_sell_amo() self-guards on close_px is None, which
            # covers both the unmanaged-SELL edge case and a failed close fetch
            # (plan_fill has already rendered MANUAL ACTION REQUIRED for that).
            _requeue_sell_amo(d, portfolio, today)

    # Ledger LAST, and exactly once — see the ordering contract above and the
    # GAP_EXIT overwrite defect described at the top of this section.
    if d.csv_status:
        fill_px = str(round(d.open_px, 4)) if d.action in ("FILL", "GAP_EXIT") else ""
        fill_dt = today.isoformat() if d.action in ("FILL", "GAP_EXIT") else ""
        _update_csv_row(o["date"], ticker, o["order_type"],
                        d.csv_status, fill_px, fill_dt)


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

    # ── Trading day guard ──────────────────────────────────────────────────────
    # AMO orders only fill on trading days. If today is a weekend or NSE holiday,
    # there is no open price to check against — exit cleanly.
    # Guard runs BEFORE portfolio load so validate_state_integrity() is never
    # triggered on weekends (avoids false STATE INTEGRITY FAIL on low-cash states).
    if not is_trading_day(today):
        print(f"Market closed today — {today}. No fill check needed.")
        return

    # ── Load PaperPortfolio for BUY fill confirmation ──────────────────────────
    # Required for confirm_buy_fill() and cancel_pending_buy(). Instantiate with
    # empty tickers list — load() reads tickers from the existing state file.
    from paper_trading.paper_portfolio import PaperPortfolio
    _portfolio = PaperPortfolio([], str(STATE_FILE), 100_000.0)
    if STATE_FILE.exists():
        _portfolio.load()

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
    # ── Phase A: PLAN — read-only ────────────────────────────────────────────
    # Every decision is computed from ONE consistent market snapshot before
    # anything is written, so a crash partway through execution can't leave
    # some orders decided against 9:20 prices and others against 9:24 prices.
    decisions = [plan_fill(order, today, ex_today) for order in pending]

    # ── Phase B: REPORT — read-only ──────────────────────────────────────────
    for d in decisions:
        print(d.detail)
        if d.circuit_msg:
            print(f"  ⚠️  CIRCUIT BREAKER WARNING: {d.circuit_msg}")
            print(f"  ⚠️  Verify position manually in Zerodha dashboard")

    # ── Phase C: EXECUTE — the only writer, the only apply_fills guard ───────
    if apply_fills:
        for d in decisions:
            execute_decision(d, _portfolio, today)

    # Counter buckets are MUTUALLY EXCLUSIVE, so a GAP_EXIT can no longer also be
    # counted as a MISS (Audit2 Finding #4) by construction, rather than by
    # hand-maintained increments in each branch. They are NOT total: NO_DATA and
    # REJECT fall into no bucket, so filled+missed+gap_exit+cancelled can be less
    # than len(decisions). Pre-existing at baseline; tracked separately.
    results         = [{"ticker": d.order["ticker"], "status": d.status,
                        "reason": d.reason} for d in decisions]
    filled_count    = sum(1 for d in decisions if d.action == "FILL")
    gap_exit_count  = sum(1 for d in decisions if d.action == "GAP_EXIT")
    missed_count    = sum(1 for d in decisions if d.is_miss)
    cancelled_count = sum(1 for d in decisions if d.action == "CANCEL_CA")

    print()
    parts = [f"{filled_count} FILLED", f"{missed_count} MISSED", f"{gap_exit_count} GAP_EXIT"]
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
    try:
        run_morning_check(check_date=check_date, apply_fills=args.apply)
    except Exception as exc:
        from utils.alerts import send_crash_alert
        send_crash_alert("morning_fill_check.py", exc)
        raise
