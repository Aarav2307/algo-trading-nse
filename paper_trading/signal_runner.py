"""
paper_trading/signal_runner.py — Daily paper trading signal runner.

Runs once per day after NSE market close (3:45 PM IST). Fetches end-of-day
data for the 4 validated stocks, runs the full strategy + risk pipeline, and
outputs a clean signal report. All portfolio state is persisted to a JSON file
and all signals are recorded to a CSV log.

Usage:
    # Normal daily run (after 3:30 PM IST):
    python paper_trading/signal_runner.py

    # Dry-run backfill (does NOT modify portfolio state):
    python paper_trading/signal_runner.py --backfill 2026-06-02

    # Force re-run if already ran today:
    python paper_trading/signal_runner.py --force

Safety note:
    PAPER_TRADING_MODE = True is enforced at module level. The assertion below
    prevents any accidental live-order path from executing.
"""

# ══════════════════════════════════════════════════════════════════════════════
# SAFETY: PAPER TRADING ONLY — DO NOT REMOVE
# ══════════════════════════════════════════════════════════════════════════════
PAPER_TRADING_MODE = True
assert PAPER_TRADING_MODE, (
    "LIVE TRADING NOT ENABLED. "
    "This system is a paper trading simulator. "
    "Real order placement is not implemented."
)
# ══════════════════════════════════════════════════════════════════════════════

import argparse
import csv
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Make project root importable when running as a script
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import pandas as pd

from data.kite_fetcher import get_ohlcv
from engine.position_sizer import PositionSizer
from engine.risk_manager import RiskManager
from paper_trading.paper_portfolio import PaperPortfolio
from strategies.sma_crossover import generate_signals
from engine.order_manager import AMOOrderManager
from utils.costs import apply_slippage, transaction_costs, BROKERAGE_PER_ORDER
from utils.market_calendar import is_trading_day, next_trading_day


# =============================================================================
# FROZEN PARAMETERS — identical to the validated backtest configuration
# (Do not change without re-validating on walk_forward.py)
# =============================================================================

STOCKS: List[str] = [
    "TMPV.NS",
    "WHIRLPOOL.NS",
    "SIEMENS.NS",
    "BAJAJ-AUTO.NS",
    "CUMMINSIND.NS",
    "HCLTECH.NS",
]

INITIAL_CAPITAL = 100_000.0    # ₹
SMA_FAST        = 20
SMA_SLOW        = 50
COOLDOWN_BARS   = 15
LOOKBACK_CALENDAR_DAYS = 120   # ~85 trading days → enough for SMA-50 + ATR-22 + buffer

AMO_CONFIG = {
    "enabled":          True,
    "dry_run":          True,          # MUST stay True — real orders not implemented
    "limit_buffer_pct": 0.005,         # 0.5% buffer: BUY limit above signal, SELL below
    "order_log_file":   "paper_trading/amo_orders.csv",
}

RM_CONFIG = {
    "enabled":                True,
    "hard_stop_pct":         -0.20,
    "atr_period":             22,
    "atr_multiplier":          3.0,
    "max_bars_held":          60,
    "round_number_offset_pct": 0.01,
    "enable_layer_1":         True,
    "enable_layer_2":         True,
    "enable_layer_3":         True,
    "enable_layer_4":         True,
}

PS_CONFIG = {
    "enabled":            True,
    "method":             "fixed_fractional",
    "risk_per_trade_pct": 0.015,
    "max_position_pct":   0.20,
    "fallback_stop_pct":  0.20,
}

# File paths (relative to project root)
STATE_FILE  = Path("paper_trading/portfolio_state.json")
LOG_CSV     = Path("paper_trading/signal_log.csv")
LOGS_DIR    = Path("paper_trading/logs")
TOKEN_FILE  = Path("auth/access_token.txt")

# IST = UTC+5:30
_IST_OFFSET = timedelta(hours=5, minutes=30)

# CSV column header — written once when the file is first created
_CSV_HEADER = [
    "date", "ticker", "signal", "close_price", "exec_price",
    "shares_affected", "cash_after", "portfolio_value",
    "pnl_pct", "exit_reason", "chandelier_stop",
    "bars_in_cooldown", "note",
]


# =============================================================================
# Authentication helpers
# =============================================================================

def _get_ist_now() -> datetime:
    """Return current datetime in IST (UTC+5:30), timezone-naive for display."""
    utc_now = datetime.now(timezone.utc)
    return (utc_now + _IST_OFFSET).replace(tzinfo=None)


def _check_auth() -> None:
    """
    Verify the Kite access token exists and is less than 8 hours old.
    Kite tokens expire daily at midnight IST; 8h is a conservative ceiling
    that prevents stale tokens from the previous session.

    Exits with error if token is missing or too old.
    """
    if not TOKEN_FILE.exists():
        print(f"\nERROR: {TOKEN_FILE} not found.")
        print("  Run auth/kite_login.py first to generate a fresh access token.")
        sys.exit(1)

    # Parse the '# saved YYYY-MM-DD HH:MM:SS' comment written by kite_login.py
    saved_dt = None
    try:
        for line in TOKEN_FILE.read_text().splitlines():
            if line.strip().startswith("# saved"):
                saved_str = line.replace("# saved", "").strip()
                saved_dt  = datetime.strptime(saved_str, "%Y-%m-%d %H:%M:%S")
                break
    except Exception:
        pass

    if saved_dt is None:
        # Fallback: use file modification time
        mtime    = os.path.getmtime(TOKEN_FILE)
        saved_dt = datetime.fromtimestamp(mtime)

    age_hours = (datetime.now() - saved_dt).total_seconds() / 3600
    if age_hours > 8:
        print(f"\nERROR: Kite access token is {age_hours:.1f} hours old (limit 8h).")
        print("  Run auth/kite_login.py to refresh the token before running.")
        sys.exit(1)


def _warn_if_market_open() -> None:
    """
    Print a warning if the script is run before 3:30 PM IST.
    Signals based on incomplete data may differ from end-of-day signals.
    The script still continues — it does not exit.
    """
    now_ist = _get_ist_now()
    market_close_ist = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    if now_ist < market_close_ist:
        print(
            f"\n⚠  WARNING: Market may still be open "
            f"(current IST time {now_ist.strftime('%H:%M')} < 15:30). "
            f"For accurate end-of-day signals, run after 3:30 PM IST."
        )


# =============================================================================
# Data fetching
# =============================================================================

def _fetch_stock_data(today: date) -> Dict[str, pd.DataFrame]:
    """
    Fetch OHLCV history for all STOCKS ending on `today`.

    Uses LOOKBACK_CALENDAR_DAYS of history (≈85 trading days), which is enough
    for SMA-50 + ATR-22 + 13-bar buffer. Any fetch failure is logged and that
    stock is skipped for today — the other stocks still run.

    Args:
        today: the "current" date (real today or backfill date)

    Returns:
        {ticker: DataFrame} for every stock that loaded successfully.
    """
    # kite_fetcher's `end` is exclusive (yfinance convention)
    # Adding 1 day makes today's bar inclusive.
    start = (today - timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
    end   = (today + timedelta(days=1)).isoformat()

    dfs: Dict[str, pd.DataFrame] = {}
    for ticker in STOCKS:
        try:
            df = get_ohlcv(ticker, start, end)

            # Guard: need at least SMA_SLOW bars to generate signals
            if len(df) < SMA_SLOW:
                print(
                    f"  WARN  {ticker}: only {len(df)} bars returned "
                    f"(need ≥ {SMA_SLOW}), skipping today."
                )
                continue

            # Guard: today's bar must be included (last bar date == today)
            last_date = df.index[-1].date()
            if last_date != today:
                print(
                    f"  WARN  {ticker}: last bar is {last_date}, "
                    f"expected {today}. Market may be closed or data delayed."
                )
                # Still use the data — it's valid as of the last available close

            dfs[ticker] = df

        except (ConnectionError, FileNotFoundError) as exc:
            # Auth errors are fatal — no point continuing
            print(f"\nFATAL: {exc}")
            sys.exit(1)

        except Exception as exc:
            print(f"  ERROR {ticker}: data fetch failed — {exc}. Skipping today.")

    return dfs


# =============================================================================
# Risk manager state helpers
# =============================================================================

def _restore_rm(pos_state: dict) -> RiskManager:
    """
    Reconstruct a RiskManager mid-trade from persisted portfolio state.

    The RiskManager tracks 5 internal values. We seed them directly
    from the portfolio JSON so check_exit() continues from where it
    left off on the previous trading day.

    Why direct attribute access to private vars: we cannot call
    on_position_open() here because that would reset bars_held to 0.
    No engine files are modified — this is the only access point.

    The RM's check_exit() will:
      - increment _bars_since_entry by 1 (one more bar has passed)
      - update _highest_high_since_entry with today's high
      - recompute and ratchet up _chandelier_stop
    All three updated values are read back via _save_rm_state().
    """
    rm = RiskManager(RM_CONFIG)
    rm._entry_price              = float(pos_state["entry_price"])
    rm._entry_date               = pos_state["entry_date"]      # string; RM stores but doesn't calc with it
    rm._bars_since_entry         = int(pos_state["bars_held"])  # check_exit increments this to bars_held+1
    rm._highest_high_since_entry = float(pos_state["highest_high_since_entry"])

    # chandelier_stop: JSON null == not yet computed == -inf in RiskManager
    stored = pos_state.get("chandelier_stop")
    rm._chandelier_stop = -math.inf if stored is None else float(stored)

    return rm


def _extract_rm_state(rm: RiskManager) -> Tuple[int, float, Optional[float]]:
    """
    Read the three values that change after every check_exit() call.

    Returns:
        (bars_held, highest_high, chandelier_stop)
        chandelier_stop is None when still at -inf (ATR warm-up not complete).
    """
    chan = rm._chandelier_stop
    return (
        rm._bars_since_entry,
        rm._highest_high_since_entry,
        None if math.isinf(chan) else float(chan),
    )


# =============================================================================
# Per-stock signal pipeline
# =============================================================================

def _process_stock(
    ticker: str,
    df: pd.DataFrame,
    portfolio: PaperPortfolio,
    current_prices: Dict[str, float],
) -> dict:
    """
    Run the full signal pipeline for one stock and return a signal dict.

    Execution order (mirrors engine/backtester.py exactly):
      ① RM check_exit   — if position open; exits if any stop fires
      ② Strategy signal  — SMA crossover on today's bar
      ③ Cooldown gate    — suppress BUY if within cooldown window
      ④ Execute BUY/SELL — update portfolio, record action

    The result dict is consumed by the report formatter and CSV logger.

    Returns:
        dict with keys: ticker, signal, close_price, exec_price, shares,
                        reason, exit_reason, chandelier_stop, bars_in_cooldown,
                        sizing_info, action_taken (human-readable)
    """
    pos        = portfolio.state["positions"][ticker]
    today_bar  = df.iloc[-1]
    today_str  = df.index[-1].date().isoformat()
    close_px   = float(today_bar["close"])
    today_high = float(today_bar["high"])

    # Template result — filled in as we go
    result = {
        "ticker":           ticker,
        "signal":           "HOLD",
        "close_price":      close_px,
        "exec_price":       None,
        "shares":           0,
        "reason":           "No signal",
        "exit_reason":      None,
        "chandelier_stop":  None,
        "bars_in_cooldown": portfolio.state["cooldown_state"][ticker]["remaining_bars"],
        "sizing_info":      None,
        "action_taken":     None,
        "net_pnl":          None,
    }

    # ── ① Risk manager exit check ────────────────────────────────────────────
    # Called every bar a position is open — even when no exit fires — because
    # check_exit() increments bars_held and ratchets up chandelier_stop.
    if pos["shares"] > 0:
        rm            = _restore_rm(pos)
        exit_decision = rm.check_exit(today_bar, df)

        # Save updated RM internals back to portfolio state
        bars_held, highest_high, chan_stop = _extract_rm_state(rm)
        portfolio.update_rm_state(ticker, bars_held, highest_high, chan_stop)

        result["chandelier_stop"] = chan_stop

        if exit_decision["should_exit"]:
            # RM fires — sell at today's close (with slippage), trigger cooldown
            exec_px  = apply_slippage(exit_decision["exit_price"], "sell")
            net_pnl  = portfolio.close_position(ticker, exec_px, today_str, exit_decision["exit_reason"])
            portfolio.trigger_cooldown(ticker, exit_decision["exit_reason"], COOLDOWN_BARS)
            portfolio.record_weekly_signal("RISK_EXIT")

            result.update({
                "signal":       "RISK_EXIT",
                "exec_price":   exec_px,
                "shares":       pos["shares"],  # read before close_position cleared it
                "exit_reason":  exit_decision["exit_reason"],
                "reason":       f"RM triggered: {exit_decision['exit_reason']}",
                "net_pnl":      net_pnl,
                "action_taken": (
                    f"SELL {pos['shares']} shares @ ₹{exec_px:.2f} "
                    f"[{exit_decision['exit_reason']}]  "
                    f"net P&L ₹{net_pnl:>+,.0f}"
                ),
            })
            return result

        # RM didn't fire — update the chandelier display for the report
        result["chandelier_stop"] = chan_stop

    # ── ② Strategy signal (SMA crossover) ────────────────────────────────────
    # generate_signals() prints a status line — acceptable noise in paper trading
    try:
        signals       = generate_signals(df, SMA_FAST, SMA_SLOW)
        today_signal  = int(signals.iloc[-1])
    except ValueError as exc:
        # Insufficient data for the strategy (shouldn't happen with 120-day lookback)
        result.update({
            "signal": "HOLD",
            "reason": f"Strategy error — {exc}",
        })
        return result

    # ── ③ + ④ Cooldown gate + execution ──────────────────────────────────────

    # SELL: strategy exit (death cross while in position, no cooldown triggered)
    if today_signal == -1 and pos["shares"] > 0:
        exec_px = apply_slippage(close_px, "sell")
        shares  = pos["shares"]
        net_pnl = portfolio.close_position(ticker, exec_px, today_str, "STRATEGY_SIGNAL")
        # Strategy exits do NOT trigger cooldown (mirrors backtester behaviour)
        portfolio.record_weekly_signal("SELL")

        result.update({
            "signal":       "SELL",
            "exec_price":   exec_px,
            "shares":       shares,
            "exit_reason":  "STRATEGY_SIGNAL",
            "reason":       "Death cross — SMA20 crossed below SMA50",
            "net_pnl":      net_pnl,
            "action_taken": (
                f"SELL {shares} shares @ ₹{exec_px:.2f} "
                f"[STRATEGY_SIGNAL]  net P&L ₹{net_pnl:>+,.0f}"
            ),
        })
        return result

    # BUY: golden cross while flat, not in cooldown
    if today_signal == 1 and pos["shares"] == 0:
        cd_remaining = portfolio.state["cooldown_state"][ticker]["remaining_bars"]

        if portfolio.is_in_cooldown(ticker):
            # Buy signal exists but cooldown is active — suppress
            result.update({
                "signal":           "COOLDOWN",
                "reason":           f"Cooldown active — {cd_remaining} bars remaining",
                "bars_in_cooldown": cd_remaining,
            })
            return result

        # ── Position sizing ─────────────────────────────────────────────────
        entry_exec_px = apply_slippage(close_px, "buy")

        # Compute initial Chandelier stop (pure read, no RM state change)
        _rm_tmp        = RiskManager(RM_CONFIG)
        chandelier_for_sizing = _rm_tmp.compute_chandelier(df, today_high)

        sizer      = PositionSizer(PS_CONFIG)
        port_value = portfolio.get_portfolio_value(current_prices)
        shares, sz = sizer.calculate_shares(
            entry_exec_px,
            port_value,
            chandelier_for_sizing,
            portfolio.state["cash"],
        )

        if shares == 0:
            # Sizing returned 0: stop too wide or capital too small — skip
            result.update({
                "signal": "HOLD",
                "reason": (
                    f"Golden cross but sizing skipped — "
                    f"0 shares (stop dist ₹{sz.get('stop_distance', 0):.0f}, "
                    f"risk ₹{sz.get('risk_amount', 0):.0f})"
                ),
            })
            return result

        # ── Execute buy ──────────────────────────────────────────────────────
        portfolio.open_position(
            ticker, shares, entry_exec_px, today_str,
            chandelier_for_sizing, today_high,
        )
        portfolio.record_weekly_signal("BUY")

        cost = transaction_costs(entry_exec_px, shares, "buy")
        result.update({
            "signal":          "BUY",
            "exec_price":      entry_exec_px,
            "shares":          shares,
            "chandelier_stop": chandelier_for_sizing,
            "reason":          "Golden cross — SMA20 crossed above SMA50",
            "sizing_info":     sz,
            "action_taken": (
                f"BUY  {shares} shares @ ₹{entry_exec_px:.2f} | "
                f"cost ₹{cost:.0f} | "
                f"risk ₹{sz.get('risk_amount', 0):,.0f} "
                f"({sz.get('stop_source', '?')}) [{sz.get('binding', '?')}]"
            ),
        })
        return result

    # ── HOLD ─────────────────────────────────────────────────────────────────
    if pos["shares"] > 0:
        result["reason"] = "In position — no exit signal"
    else:
        result["reason"] = "No signal"

    return result


# =============================================================================
# Report formatting
# =============================================================================

def _format_report(
    today: date,
    run_time_str: str,
    portfolio: PaperPortfolio,
    results: Dict[str, dict],
    current_prices: Dict[str, float],
    is_backfill: bool = False,
) -> str:
    """
    Build the full terminal report as a string. Printed to stdout and saved
    to the log file.
    """
    summ = portfolio.summary(current_prices)
    lines = []
    W = 62

    def hr(char="="):
        lines.append(char * W)

    def ln(text=""):
        lines.append(text)

    # ── Header ─────────────────────────────────────────────────────────────────
    hr()
    if is_backfill:
        ln(f"  ── DRY RUN / BACKFILL  {today}  (state NOT saved) ──")
        hr()
    ln("  NSE PAPER TRADING SIGNAL REPORT")
    day_name = today.strftime("%A, %d %B %Y")
    ln(f"  Date: {day_name}  |  Run: {run_time_str}")
    hr()

    # ── Portfolio summary ──────────────────────────────────────────────────────
    ln()
    ln("  PORTFOLIO SUMMARY")
    ln(f"  Cash available    : ₹{summ['cash']:>12,.2f}")
    ln(f"  Invested value    : ₹{summ['invested_value']:>12,.2f}")
    ln(f"  Total value       : ₹{summ['total_value']:>12,.2f}")
    pnl_sign = "+" if summ["pnl"] >= 0 else ""
    ln(f"  P&L vs start      : {pnl_sign}₹{summ['pnl']:>+,.2f}  ({summ['pnl_pct']:>+.2f}%)")
    ln(f"  Open positions    : {summ['open_count']} / {len(STOCKS)} stocks")
    ln(f"  Completed trades  : {summ['total_trades']}")
    ln()

    # ── Signal table ──────────────────────────────────────────────────────────
    ln("  TODAY'S SIGNALS")
    COL = [14, 10, 11, 9, 29]
    hbar = "─"
    border_top  = "  ┌" + "┬".join(hbar * (c + 2) for c in COL) + "┐"
    border_hdr  = "  ├" + "┼".join(hbar * (c + 2) for c in COL) + "┤"
    border_bot  = "  └" + "┴".join(hbar * (c + 2) for c in COL) + "┘"

    def trow(cells, char="│"):
        row = f"  {char}"
        for val, w in zip(cells, COL):
            row += f" {str(val):<{w}} {char}"
        lines.append(row)

    lines.append(border_top)
    trow(["Stock", "Signal", "Price", "Shares", "Reason"])
    lines.append(border_hdr)

    for ticker in STOCKS:
        if ticker not in results:
            trow([ticker, "ERROR", "—", "—", "Data unavailable"])
            continue
        r = results[ticker]
        price_str  = f"₹{r['close_price']:,.2f}"
        shares_str = f"{r['shares']} shr" if r["shares"] > 0 else "—"
        reason     = r["reason"][:28]   # truncate to fit column
        trow([ticker, r["signal"], price_str, shares_str, reason])

    lines.append(border_bot)
    ln()

    # ── Open position risk levels ──────────────────────────────────────────────
    open_pos = portfolio.get_open_positions()
    if open_pos:
        ln("  OPEN POSITION RISK LEVELS")
        for ticker, pos in open_pos.items():
            price = current_prices.get(ticker, pos["entry_price"])
            ep    = pos["entry_price"]
            pnl_pct = (price / ep - 1) * 100
            chan  = pos.get("chandelier_stop")
            chan_str = f"₹{chan:,.2f}" if chan is not None else "computing…"
            ln(
                f"  {ticker:<15}: entry ₹{ep:,.2f} | current ₹{price:,.2f} "
                f"| chandelier {chan_str} | P&L {pnl_pct:>+.1f}%"
            )
        ln()

    # ── Actions taken ─────────────────────────────────────────────────────────
    actions = [r["action_taken"] for r in results.values() if r.get("action_taken")]
    if actions:
        ln("  ACTIONS TAKEN")
        for act in actions:
            ln(f"  ✓ {act}")
        ln()
    else:
        ln("  ACTIONS TAKEN: none (all HOLD)")
        ln()

    # ── Footer ─────────────────────────────────────────────────────────────────
    if not is_backfill:
        next_run = next_trading_day(today)
        ln(f"  Next run: {next_run.strftime('%A %d %b')} 3:45 PM IST")
        ln(f"  Log saved: {LOG_CSV}")
    hr()

    return "\n".join(lines)


def _weekly_summary(
    today: date,
    portfolio: PaperPortfolio,
    current_prices: Dict[str, float],
) -> None:
    """
    Print an additional weekly summary. Called only on Fridays.
    Shows signals fired this week, weekly P&L, and running paper P&L.
    """
    summ = portfolio.summary(current_prices)
    W    = 62

    print("=" * W)
    print(f"  WEEKLY SUMMARY  (week ending {today.strftime('%d %b %Y')})")
    print("=" * W)

    ws = summ.get("weekly_signals", {})
    print(
        f"  Signals this week: "
        f"{ws.get('BUY', 0)} BUY, "
        f"{ws.get('SELL', 0)} SELL, "
        f"{ws.get('RISK_EXIT', 0)} RISK_EXIT"
    )

    w_pnl = summ["weekly_pnl"]
    w_pct = summ["weekly_pnl_pct"]
    w_start = summ.get("weekly_start_date", "?")
    print(f"  Weekly P&L        : {'+'if w_pnl>=0 else ''}₹{w_pnl:,.0f}  ({w_pct:>+.2f}%)")
    print(
        f"  Running paper P&L : {'+'if summ['pnl']>=0 else ''}₹{summ['pnl']:,.0f}"
        f"  ({summ['pnl_pct']:>+.2f}%)"
        f"  since {summ.get('inception_date', '?')}"
    )
    print("=" * W)
    print()


# =============================================================================
# CSV logging
# =============================================================================

def _ensure_csv_header() -> None:
    """Create signal_log.csv with header row if it doesn't exist."""
    if LOG_CSV.exists():
        return
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_CSV, "w", newline="") as fh:
        csv.writer(fh).writerow(_CSV_HEADER)


def _append_csv(
    today: date,
    results: Dict[str, dict],
    portfolio: PaperPortfolio,
    current_prices: Dict[str, float],
) -> None:
    """
    Append one row per stock to the signal log CSV.

    The CSV is append-only. Never overwrite. Every day's signals are
    permanently recorded so you can replay or analyse later.
    """
    _ensure_csv_header()
    total_value = portfolio.get_portfolio_value(current_prices)
    initial     = portfolio.state["initial_capital"]
    pnl_pct     = (total_value / initial - 1) * 100

    with open(LOG_CSV, "a", newline="") as fh:
        writer = csv.writer(fh)
        for ticker in STOCKS:
            if ticker not in results:
                writer.writerow([
                    today.isoformat(), ticker, "ERROR", "", "", "",
                    f"{portfolio.state['cash']:.2f}", f"{total_value:.2f}",
                    f"{pnl_pct:.4f}", "", "", "", "data fetch failed",
                ])
                continue

            r      = results[ticker]
            pos    = portfolio.state["positions"][ticker]
            chan   = r.get("chandelier_stop")
            cd_rem = portfolio.state["cooldown_state"][ticker]["remaining_bars"]

            note = ""
            if r.get("sizing_info"):
                sz   = r["sizing_info"]
                note = (
                    f"stop={sz.get('stop_source','?')} "
                    f"dist=₹{sz.get('stop_distance',0):.0f} "
                    f"binding={sz.get('binding','?')}"
                )

            writer.writerow([
                today.isoformat(),
                ticker,
                r["signal"],
                f"{r['close_price']:.4f}",
                f"{r['exec_price']:.4f}" if r.get("exec_price") else "",
                r["shares"] if r["shares"] > 0 else "",
                f"{portfolio.state['cash']:.2f}",
                f"{total_value:.2f}",
                f"{pnl_pct:.4f}",
                r.get("exit_reason") or "",
                f"{chan:.4f}" if chan is not None else "",
                cd_rem if cd_rem > 0 else "",
                note,
            ])


# =============================================================================
# Main entry point
# =============================================================================

def main(backfill_date: Optional[str] = None, force: bool = False) -> None:
    """
    Execute the daily paper trading run.

    Args:
        backfill_date: ISO date string "YYYY-MM-DD". If set, simulate running
                       as of that date. Portfolio state is NOT modified (dry run).
        force:         If True, override the idempotency guard and re-run even
                       if last_run_date == today.
    """
    is_backfill = backfill_date is not None

    # ── Resolve "today" ───────────────────────────────────────────────────────
    if is_backfill:
        try:
            today = date.fromisoformat(backfill_date)
        except ValueError:
            print(f"ERROR: Invalid date '{backfill_date}'. Use YYYY-MM-DD format.")
            sys.exit(1)
        print(f"\n[paper_trading] BACKFILL DRY RUN for {today} — state will NOT be saved.")
    else:
        today = date.today()

    # ── Step 1: Market day check ──────────────────────────────────────────────
    if not is_backfill:
        if not is_trading_day(today):
            day_label = "Weekend" if today.weekday() >= 5 else "NSE Holiday"
            print(f"Market closed today — {today} ({day_label}). No signals generated.")
            sys.exit(0)

    # ── Step 2: Auth check ────────────────────────────────────────────────────
    _check_auth()

    # ── Market-still-open warning (real runs only) ────────────────────────────
    if not is_backfill:
        _warn_if_market_open()

    # ── Step 3: Load portfolio state ──────────────────────────────────────────
    portfolio = PaperPortfolio(STOCKS, str(STATE_FILE), INITIAL_CAPITAL)
    portfolio.load()

    # ── Step 4: Idempotency guard ─────────────────────────────────────────────
    # Blocks re-runs only when the previous run already captured end-of-day data
    # (i.e. it ran after 3:30 PM IST). A run that happened before market close
    # may have used incomplete data and should be allowed to re-run post-close
    # without needing --force.
    if not is_backfill and not force:
        last_run_date = portfolio.state.get("last_run_date")
        last_run_time = portfolio.state.get("last_run_time", "00:00")  # "HH:MM" IST
        if last_run_date == today.isoformat():
            # Parse the hour and minute of the previous run (stored in IST)
            try:
                last_hh, last_mm = (int(x) for x in last_run_time.split(":"))
            except (ValueError, AttributeError):
                last_hh, last_mm = 0, 0
            ran_after_close = (last_hh, last_mm) >= (15, 30)
            if ran_after_close:
                print(
                    f"Already ran today after market close "
                    f"({last_run_time} IST) — {today}. "
                    f"Use --force flag to override and re-run."
                )
                sys.exit(0)
            else:
                print(
                    f"[paper_trading] Previous run today was at {last_run_time} IST "
                    f"(before market close). Re-running with fresh end-of-day data."
                )

    # ── Step 5: Weekly baseline reset (Mondays) ───────────────────────────────
    # Compute approximate current prices for the reset (needed for get_portfolio_value)
    # At this point we don't have prices yet, so we use entry prices as proxy —
    # the actual baseline will be overwritten once real prices are loaded below.
    if not is_backfill:
        pre_prices = {
            t: portfolio.state["positions"][t]["entry_price"] or 0
            for t in STOCKS
        }
        portfolio.reset_weekly_stats_if_monday(today, portfolio.get_portfolio_value(pre_prices))

    # ── Step 6: Fetch data ────────────────────────────────────────────────────
    print(f"\n[paper_trading] Fetching data for {today} …")
    dfs = _fetch_stock_data(today)

    if not dfs:
        print("ERROR: No data fetched for any stock. Check network and token.")
        sys.exit(1)

    # ── Step 7: Current prices (from latest bar) ──────────────────────────────
    current_prices = {
        ticker: float(df.iloc[-1]["close"])
        for ticker, df in dfs.items()
    }

    # ── Correct weekly baseline now that we have real prices ──────────────────
    if not is_backfill and today.weekday() == 0:
        real_value = portfolio.get_portfolio_value(current_prices)
        portfolio.state["weekly_start_value"] = real_value

    # ── Step 8a: Corporate actions safety check ───────────────────────────────
    # Run BEFORE any strategy signal is generated. If a stock has a material
    # corporate action ex-date within the next 2 trading days (split, bonus,
    # rights, large dividend), skip it entirely and log a clear warning.
    # API failures default to skip=False so an outage never blocks trading.
    print(f"[paper_trading] Checking corporate actions …")
    from utils.corporate_actions import check_all_stocks as _ca_check
    ca_warnings = _ca_check(
        [t for t in STOCKS if t in dfs],
        check_date=today,
        current_prices=current_prices,
    )
    print()

    # ── Step 8b: Process each stock ───────────────────────────────────────────
    print(f"[paper_trading] Running signal pipeline for {len(dfs)} stocks …\n")
    results: Dict[str, dict] = {}
    for ticker in STOCKS:
        if ticker not in dfs:
            results[ticker] = {
                "ticker":           ticker,
                "signal":           "ERROR",
                "close_price":      0.0,
                "exec_price":       None,
                "shares":           0,
                "reason":           "Data fetch failed",
                "exit_reason":      None,
                "chandelier_stop":  None,
                "bars_in_cooldown": 0,
                "sizing_info":      None,
                "action_taken":     None,
                "net_pnl":          None,
            }
            continue

        # Corporate action guard: skip signal generation for this stock today
        ca = ca_warnings.get(ticker, {"skip": False, "reason": None})
        if ca["skip"]:
            close_px = float(dfs[ticker].iloc[-1]["close"])
            results[ticker] = {
                "ticker":           ticker,
                "signal":           "SKIP_CA",
                "close_price":      close_px,
                "exec_price":       None,
                "shares":           0,
                "reason":           ca["reason"],
                "exit_reason":      None,
                "chandelier_stop":  None,
                "bars_in_cooldown": portfolio.state["cooldown_state"][ticker]["remaining_bars"],
                "sizing_info":      None,
                "action_taken":     None,
                "net_pnl":          None,
            }
            print(f"  [CORP_ACTION] {ticker} SKIPPED — {ca['reason']}")
            continue

        results[ticker] = _process_stock(ticker, dfs[ticker], portfolio, current_prices)

    # ── Step 9: Advance cooldowns (end-of-day, unconditional for all stocks) ──
    # Mirrors advance_bar() in the backtester — called AFTER signal processing,
    # exactly once per trading day.
    if not is_backfill:
        for ticker in STOCKS:
            portfolio.advance_cooldown(ticker)

    # ── Step 10: Save state ────────────────────────────────────────────────────
    if not is_backfill:
        portfolio.state["last_run_date"] = today.isoformat()
        portfolio.state["last_run_time"] = _get_ist_now().strftime("%H:%M")  # IST HH:MM
        portfolio.save()
        print("\n[paper_trading] Portfolio state saved.")

    # ── Step 11: Print report ─────────────────────────────────────────────────
    run_time_str = _get_ist_now().strftime("%-I:%M %p IST")
    report       = _format_report(today, run_time_str, portfolio, results, current_prices, is_backfill)
    print()
    print(report)

    # ── Step 12: CSV log (real runs only) ────────────────────────────────────
    if not is_backfill:
        _append_csv(today, results, portfolio, current_prices)
        print(f"[paper_trading] Signal log appended → {LOG_CSV}")

    # ── Step 13: Queue AMO orders for tomorrow's open ─────────────────────────
    # For every BUY or SELL signal, log an AMO order at a limit price with a
    # 0.5% buffer so a modest overnight gap still fills.
    # In dry_run mode (the only mode currently), this just writes to the CSV.
    # morning_fill_check.py processes these rows at 9:30 AM the next day.
    amo = AMOOrderManager(AMO_CONFIG)
    amo_orders = []
    for ticker, r in results.items():
        sig    = r.get("signal")
        shares = r.get("shares", 0)
        price  = r.get("close_price", 0.0)
        if sig == "BUY" and shares > 0:
            order = amo.place_buy_amo(ticker, shares, price, today,
                                      notes=f"chan={r.get('chandelier_stop','?')}")
            amo_orders.append(order)
        elif sig in ("SELL", "RISK_EXIT") and shares > 0:
            order = amo.place_sell_amo(ticker, shares, price, today,
                                       notes=r.get("exit_reason", "STRATEGY_SIGNAL"))
            amo_orders.append(order)

    if amo_orders:
        print()
        print(f"  {'─'*60}")
        print(f"  AMO ORDERS QUEUED FOR TOMORROW")
        for o in amo_orders:
            dry_tag = " [DRY RUN]" if o["status"] == "DRY_RUN" else ""
            print(
                f"  {o['ticker']:<15} {o['order_type']:<4}  "
                f"{o['shares']:>3} shr | "
                f"signal ₹{o['signal_price']:,.2f} | "
                f"limit ₹{o['limit_price']:,.2f}"
                f"{dry_tag}"
            )
        print(f"  {'─'*60}")

    # ── Step 14: Weekly summary (Fridays only, real runs) ────────────────────
    if not is_backfill and today.weekday() == 4:   # 4 = Friday
        _weekly_summary(today, portfolio, current_prices)


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NSE paper trading daily signal runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python paper_trading/signal_runner.py                    # normal daily run
  python paper_trading/signal_runner.py --backfill 2026-06-02  # dry run on past date
  python paper_trading/signal_runner.py --force            # re-run even if ran today
        """,
    )
    parser.add_argument(
        "--backfill",
        metavar="DATE",
        help="Simulate running on DATE (YYYY-MM-DD). Does NOT modify portfolio state.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override idempotency guard and re-run even if already ran today.",
    )
    args = parser.parse_args()
    main(backfill_date=args.backfill, force=args.force)
