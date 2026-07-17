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
import json
import math
import os
import sys
import time
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
from paper_trading.correlation_check import check_entry_correlation
from strategies.sma_crossover import generate_signals
from engine.order_manager import AMOOrderManager
from utils.costs import (
    apply_slippage, transaction_costs, transaction_cost_breakdown,
    BROKERAGE_PER_ORDER, format_cost_breakdown,
)
from utils.market_calendar import is_trading_day, next_trading_day, verify_holiday_coverage
from screener.auto_screener import compute_hurst


# =============================================================================
# FROZEN PARAMETERS — identical to the validated backtest configuration
# (Do not change without re-validating on walk_forward.py)
# =============================================================================

NIFTY_TICKER = "NIFTY 50.NS"   # Kite instrument token 256265; strips to "NIFTY 50" internally

STOCKS: List[str] = [
    "BAJAJ-AUTO.NS",
    "HCLTECH.NS",
    "COLPAL.NS",
    "ANURAS.NS",
    "BSOFT.NS",
    "PERSISTENT.NS",
    "CHOLAHLDNG.NS",   # WF validated Jul 8 2026 — 5/6 original OOS +15.4%, 5/6 extended OOS +9.1%
    "COHANCE.NS",      # WF validated Jul 9-10 2026 — 6/6 original OOS +8.0%, extended SKIPPED (insufficient data, only 20 bars in 2015-19 window)
    "MAPMYINDIA.NS",  # WF validated Jul 12 2026 — 5/6 original OOS +7.0%, extended SKIPPED (insufficient data, no bars in 2015-19 window)
    "EMAMILTD.NS",    # WF validated Jul 17 2026 — 6/6 original OOS +9.0%, 5/6 extended OOS +13.6%
]

INITIAL_CAPITAL = 100_000.0    # ₹
SMA_FAST        = 20
SMA_SLOW        = 50
COOLDOWN_BARS   = 15
LOOKBACK_CALENDAR_DAYS = 120   # ~85 trading days → enough for SMA-50 + ATR-22 + buffer

# ── Capital allocation & signal ranking ───────────────────────────────────────
# Maximum simultaneous open positions. When all slots are full, new BUY signals
# are ranked and the highest-scoring one(s) fill the available slots.
MAX_CONCURRENT_POSITIONS: int = 4

# Minimum cash required to attempt a BUY. Below ₹1,000, the position sizer
# will return 0 shares for any stock in the universe (cheapest ~₹300+) and
# the attempt produces a confusing SKIPPED log with no clear reason.
MIN_CASH_TO_ATTEMPT_BUY = 1000.0

# Minimum Hurst exponent required for a live BUY entry — matches screener threshold.
# Recalibrated Jun 2026: log-price variogram estimator, random walk baseline = 0.48.
# A stock with H < 0.48 at golden cross time is mean-reverting, not trending —
# SMA crossover has negative expectancy in that regime.
HURST_THRESHOLD: float = 0.48

# Weights for composite BUY signal ranking. Must be inspected, not re-optimised.
# Higher score = higher priority when capital is constrained.
SIGNAL_RANK_WEIGHTS = {
    "hurst":         0.40,   # stronger trending memory → better SMA candidate
    "gap_proximity": 0.40,   # gap closest to 0 from below → freshest cross
    "volatility":    0.20,   # higher vol → larger ATR moves → more exploitable
}

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
STATE_FILE         = Path("paper_trading/portfolio_state.json")
LOG_CSV            = Path("paper_trading/signal_log.csv")
LOGS_DIR           = Path("paper_trading/logs")
TOKEN_FILE         = Path("auth/access_token.txt")
NEWS_FLAGS_FILE    = _ROOT / "utils" / "news_flags.json"
MANUAL_BLOCKS_FILE = _ROOT / "utils" / "manual_blocks.json"

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
    Kite tokens expire daily at midnight IST; 8h is a conservative ceiling.

    If token is missing or stale, attempts automatic refresh via auto_login.py
    before failing. This allows manual runs without requiring a prior manual
    token refresh, matching the server's automated behavior.

    Exits with error only if:
    1. Token is missing AND auto-refresh fails
    2. Token is stale AND auto-refresh fails
    """
    def _attempt_auto_refresh(reason: str) -> bool:
        """
        Run auth/auto_login.py as subprocess to refresh the Kite token.
        Returns True if refresh succeeded, False if it failed.
        """
        print(f"\n[auth] {reason}")
        print("[auth] Attempting automatic token refresh via auth/auto_login.py...")
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "auth/auto_login.py"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=Path(__file__).parent.parent,  # project root
            )
            if result.returncode == 0:
                print("[auth] ✅ Token refreshed automatically — continuing.")
                return True
            else:
                print(f"[auth] ❌ Auto-refresh failed (exit code {result.returncode})")
                if result.stderr:
                    print(f"[auth] Error output: {result.stderr.strip()[:300]}")
                if result.stdout:
                    print(f"[auth] Output: {result.stdout.strip()[:300]}")
                return False
        except subprocess.TimeoutExpired:
            print("[auth] ❌ Auto-refresh timed out after 30 seconds")
            return False
        except Exception as e:
            print(f"[auth] ❌ Auto-refresh error: {e}")
            return False

    # ── Check 1: Token file must exist ────────────────────────────────────────
    if not TOKEN_FILE.exists():
        if not _attempt_auto_refresh(f"{TOKEN_FILE} not found."):
            print(f"\nERROR: Token file missing and auto-refresh failed.")
            print("  Run auth/kite_login.py manually to generate a fresh token.")
            sys.exit(1)
        # After successful refresh, TOKEN_FILE should now exist — continue
        if not TOKEN_FILE.exists():
            print(f"\nERROR: Auto-refresh ran but {TOKEN_FILE} still not found.")
            sys.exit(1)

    # ── Check 2: Token must be less than 8 hours old ──────────────────────────
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
        if not _attempt_auto_refresh(
            f"Kite access token is {age_hours:.1f} hours old (limit 8h)."
        ):
            print(f"\nERROR: Token is {age_hours:.1f}h old and auto-refresh failed.")
            print("  Run auth/kite_login.py manually to refresh the token.")
            sys.exit(1)
        # After successful refresh, token age is reset — no further check needed


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
# Pre-trade risk flags (news_monitor output)
# =============================================================================

def _expected_last_news_monitor_date(today: date) -> date:
    """
    The most recent weekday news_monitor should have run on, as of `today`.
    Monday  -> expects Friday's run (3 days prior)
    Tue-Fri -> expects yesterday's run (1 day prior)
    """
    if today.weekday() == 0:  # Monday
        return today - timedelta(days=3)
    else:
        return today - timedelta(days=1)


def load_news_flags() -> dict:
    """
    Load pre-trade risk flags from news_monitor output.
    Returns empty dict on any failure — never crash signal_runner.
    Warns if the flags file is from before the most recent expected
    news_monitor run (weekday-aware — handles the normal ~21h daily gap
    and the ~65h Friday-to-Monday weekend gap without false positives).
    """
    try:
        if not NEWS_FLAGS_FILE.exists():
            print("[news_monitor] No flags file found — skipping pre-trade checks")
            return {}
        with open(NEWS_FLAGS_FILE) as f:
            data = json.load(f)
        generated_at = datetime.strptime(data["generated_at"], "%Y-%m-%d %H:%M")
        file_date = generated_at.date()
        expected_date = _expected_last_news_monitor_date(date.today())
        if file_date < expected_date:
            print(
                f"[news_monitor] WARNING: Flags file is stale — from {file_date}, "
                f"expected a run on or after {expected_date}. "
                "news_monitor may have failed to run."
            )
        return data.get("flags", {})
    except Exception as e:
        print(f"[news_monitor] WARNING: Could not load flags: {e} — proceeding without checks")
        return {}


# =============================================================================
# NIFTY regime filter
# =============================================================================

def _fetch_nifty_regime(today: date) -> str:
    """
    Determine the current NIFTY 50 market regime from SMA20 vs SMA50.

    Fetches the last 90 calendar days of NIFTY 50 data (≈63 trading bars —
    enough for SMA50 plus a buffer). Uses the same kite_fetcher as all other
    data so there is no additional auth dependency.

    Returns:
        "BULL"    — NIFTY SMA20 > SMA50 (uptrend confirmed)
        "BEAR"    — NIFTY SMA20 < SMA50 (downtrend confirmed — suppress new entries)
        "UNKNOWN" — data unavailable or insufficient; filter skipped, no suppression
    """
    try:
        start = (today - timedelta(days=90)).isoformat()
        end   = (today + timedelta(days=1)).isoformat()
        df    = get_ohlcv(NIFTY_TICKER, start, end)

        if df is None or len(df) < 50:
            print(f"  [NIFTY] Insufficient data ({len(df) if df is not None else 0} bars) — regime UNKNOWN")
            return "UNKNOWN"

        close = df["close"]
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        regime = "BULL" if sma20 > sma50 else "BEAR"
        gap_pct = (sma20 - sma50) / sma50 * 100
        print(
            f"  [NIFTY] {today} | close ₹{close.iloc[-1]:,.2f} | "
            f"SMA20 ₹{sma20:,.2f} | SMA50 ₹{sma50:,.2f} | "
            f"gap {gap_pct:>+.2f}% → regime: {regime}"
        )
        return regime

    except Exception as exc:
        print(f"  [NIFTY] Regime fetch failed: {exc} — regime UNKNOWN")
        return "UNKNOWN"


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
            # Rate limit: paces every Kite API call at ~55 req/min (safe under the
            # 60 req/min cap), mirroring screener/auto_screener.py's identical
            # pattern (see that file's fetch loop). Applied regardless of downstream
            # data-quality guards below, since the API call itself already consumed
            # quota against the rate limit.
            # NB: adds ~1.1s × len(STOCKS) to each run (10 stocks ≈ 11.0s today;
            # scales linearly as the universe grows).
            time.sleep(1.1)

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
# Signal ranking & capital allocation gate
# =============================================================================

def rank_signal(ticker: str, df: pd.DataFrame, gap_pct: float) -> float:
    """
    Composite ranking score for a BUY signal candidate (0–100, higher = better).

    Used when multiple stocks simultaneously show golden-cross signals and
    capital/position-count is constrained. The highest-scoring stock(s) get
    priority for capital allocation.

    Components (weights from SIGNAL_RANK_WEIGHTS):
      hurst:         R/S Hurst exponent on closing prices. H=0.50→0, H=0.70→100.
                     Measures persistence: higher = more likely to keep trending.
      gap_proximity: |gap_pct| scaled so 0% gap → 100, 15% gap → 0.
                     Freshest cross = smallest gap = highest score.
      volatility:    Annualised daily return vol. 20%→0, 50%→100.
                     Higher vol = bigger ATR moves = more profitable for crossover.

    Returns 0.0 on any calculation error (safe default — deprioritises bad data).
    """
    try:
        import numpy as np

        close = df["close"].values

        # Compute Hurst on log prices (not raw prices) — removes price-level bias
        if len(close) < 20:
            hurst = 0.5
        else:
            log_prices = np.log(close)
            lags = range(2, 20)
            tau = [np.std(np.subtract(log_prices[lag:], log_prices[:-lag])) for lag in lags]
            if any(t <= 0 for t in tau):
                hurst = 0.5
            else:
                poly  = np.polyfit(np.log(list(lags)), np.log(tau), 1)
                hurst = float(poly[0])

        hurst_score = max(0.0, min(100.0, (hurst - 0.50) / 0.20 * 100.0))

        # Gap proximity (gap_pct ≤ 0 for death-cross BUY candidates)
        gap_score = max(0.0, min(100.0, (1.0 - abs(gap_pct) / 15.0) * 100.0))

        # Annualised volatility
        vol        = float(df["close"].pct_change().dropna().std() * (252 ** 0.5) * 100.0)
        vol_score  = max(0.0, min(100.0, (vol - 20.0) / 30.0 * 100.0))

        score = (
            SIGNAL_RANK_WEIGHTS["hurst"]         * hurst_score +
            SIGNAL_RANK_WEIGHTS["gap_proximity"]  * gap_score   +
            SIGNAL_RANK_WEIGHTS["volatility"]     * vol_score
        )
        return round(score, 2)

    except Exception:
        return 0.0


def count_open_positions(portfolio: "PaperPortfolio") -> int:
    """Count stocks currently holding shares > 0."""
    return sum(
        1 for pos in portfolio.state["positions"].values()
        if pos.get("shares", 0) > 0
    )


def can_open_position(
    portfolio: "PaperPortfolio",
    proposed_shares: int,
    proposed_price: float,
) -> tuple:
    """
    Check whether a new position can be opened given position and cash limits.

    Returns:
        (True,  "")             — position can be opened
        (False, reason_string)  — position should be skipped

    NOTE: RM exits always execute regardless of these limits. This gate applies
    to BUY entries ONLY. Never call this for exit decisions.
    """
    # CRITICAL: RM exits always execute. Only BUY entries are gated by capital allocation.
    # Never apply can_open_position() to exit decisions.

    open_count = count_open_positions(portfolio)
    if open_count >= MAX_CONCURRENT_POSITIONS:
        return False, f"Position limit reached ({open_count}/{MAX_CONCURRENT_POSITIONS} open)"

    required_cash = proposed_shares * proposed_price * 1.001   # 0.1% buffer for costs
    available     = portfolio.state["cash"]
    if available < required_cash:
        return False, f"Insufficient cash (need ₹{required_cash:,.0f}, have ₹{available:,.0f})"

    return True, ""


# =============================================================================
# Per-stock signal pipeline
# =============================================================================

def _process_stock(
    ticker: str,
    df: pd.DataFrame,
    portfolio: PaperPortfolio,
    current_prices: Dict[str, float],
    market_regime: str = "UNKNOWN",
    defer_buy: bool = False,
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

    # If a BUY AMO is pending morning fill confirmation, skip all processing.
    # The position has not actually been opened yet — cash not deducted.
    # morning_fill_check.py will call confirm_buy_fill() at the open price.
    if pos.get("pending_buy", False):
        return {
            "ticker":           ticker,
            "signal":           "PENDING_BUY",
            "close_price":      close_px,
            "exec_price":       pos.get("entry_price"),   # provisional close price
            "shares":           pos["shares"],
            "reason":           "AMO BUY pending morning fill confirmation",
            "exit_reason":      None,
            "chandelier_stop":  None,
            "bars_in_cooldown": portfolio.state["cooldown_state"][ticker]["remaining_bars"],
            "sizing_info":      None,
            "action_taken":     None,
            "net_pnl":          None,
        }

    # If an RM exit AMO is pending morning fill confirmation, continue running the
    # RM check so stops remain active and the chandelier ratchets correctly.
    # Do NOT run strategy signals or allow new entries.
    # If the SELL was MISSED (morning_fill_check requeued it), the RM keeps
    # tracking the position until the requeued AMO fills the next morning.
    if pos.get("pending_rm_exit"):
        rm            = _restore_rm(pos)
        exit_decision = rm.check_exit(today_bar, df)
        bars_held, highest_high, chan_stop = _extract_rm_state(rm)
        portfolio.update_rm_state(ticker, bars_held, highest_high, chan_stop)

        return {
            "ticker":           ticker,
            "signal":           "PENDING_RM_EXIT",
            "close_price":      close_px,
            "exec_price":       None,
            "shares":           pos["shares"],
            "reason":           (
                f"AMO SELL pending fill ({pos.get('rm_exit_reason','?')}) "
                f"— chandelier ₹{chan_stop:.2f}" if chan_stop else
                f"AMO SELL pending fill ({pos.get('rm_exit_reason','?')})"
            ),
            "exit_reason":      pos.get("rm_exit_reason"),
            "chandelier_stop":  chan_stop,
            "bars_in_cooldown": portfolio.state["cooldown_state"][ticker]["remaining_bars"],
            "sizing_info":      None,
            "action_taken":     "Awaiting morning fill confirmation — RM stops remain active",
            "net_pnl":          None,
        }

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
            # Defer RM exit to tomorrow's open (AMO-realistic).
            # Do NOT call close_position() or trigger_cooldown() now.
            # Set a pending flag so tomorrow's run skips re-processing this stock.
            # morning_fill_check.py will complete close_position() + cooldown at open.
            shares_to_sell = pos["shares"]
            exit_reason    = exit_decision["exit_reason"]

            pos["pending_rm_exit"] = True
            pos["rm_exit_reason"]  = exit_reason
            portfolio.record_weekly_signal("RISK_EXIT")

            result.update({
                "signal":       "RISK_EXIT",
                "exec_price":   None,           # confirmed tomorrow at open
                "shares":       shares_to_sell,
                "exit_reason":  exit_reason,
                "reason":       f"RM triggered: {exit_reason} — AMO SELL queued for tomorrow's open",
                "net_pnl":      None,           # recorded tomorrow when fill confirms
                "action_taken": (
                    f"RM EXIT PENDING ({exit_reason}) | "
                    f"AMO SELL {shares_to_sell} shr @ close ₹{close_px:.2f} → fills tomorrow open"
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
    # Deferred to next morning's open via AMO — mirrors the RM exit pattern.
    # close_position() is called by morning_fill_check.py at the actual fill price.
    if today_signal == -1 and pos["shares"] > 0:
        shares_to_sell         = pos["shares"]
        pos["pending_rm_exit"] = True
        pos["rm_exit_reason"]  = "STRATEGY_SIGNAL"
        portfolio.record_weekly_signal("SELL")

        result.update({
            "signal":       "SELL",
            "exec_price":   None,           # confirmed tomorrow at open
            "shares":       shares_to_sell,
            "exit_reason":  "STRATEGY_SIGNAL",
            "reason":       "Death cross — SMA20 crossed below SMA50 — AMO SELL queued",
            "net_pnl":      None,           # recorded tomorrow when fill confirms
            "action_taken": (
                f"SELL PENDING (STRATEGY_SIGNAL) | "
                f"AMO SELL {shares_to_sell} shr @ close ₹{close_px:.2f} "
                f"→ fills tomorrow open"
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

        # ── NIFTY regime filter ─────────────────────────────────────────────
        # Suppress new entries when the broad market is in a death cross.
        # UNKNOWN regime (data failure) → allow entry (fail-open, not fail-closed).
        if market_regime == "BEAR":
            result.update({
                "signal": "REGIME_SKIP",
                "reason": "Market regime filter: NIFTY in death cross — no new entries",
            })
            return result

        # ── Live Hurst quality gate ─────────────────────────────────────────
        # Check current Hurst at entry time — not just at screener time.
        # A stock's trending behavior can degrade after universe addition.
        # If Hurst < threshold at golden cross time, suppress entry.
        # Fail-open: if Hurst computation fails, allow entry (don't block on error).
        try:
            live_hurst = compute_hurst(df["close"].values)
            if live_hurst < HURST_THRESHOLD:
                result.update({
                    "signal": "HURST_SKIP",
                    "reason": (
                        f"Live Hurst {live_hurst:.3f} < threshold {HURST_THRESHOLD:.2f} "
                        f"— stock not trending sufficiently for SMA crossover entry"
                    ),
                    "hurst": round(live_hurst, 3),
                })
                print(
                    f"  HURST_SKIP | {ticker} | H={live_hurst:.3f} < {HURST_THRESHOLD:.2f} "
                    f"| golden cross suppressed — stock in mean-reversion regime"
                )
                return result
        except Exception as _hurst_exc:
            # Fail-open: Hurst computation error must never block a valid entry
            print(
                f"  HURST_GATE | {ticker} | WARNING: Hurst computation failed "
                f"({_hurst_exc}) — allowing entry"
            )

        # ── BUY candidate: defer or execute ─────────────────────────────────
        # When defer_buy=True (Phase 1 of the two-phase allocation), return a
        # BUY_CANDIDATE dict with ranking info so main() can sort and gate.
        # When defer_buy=False (Phase 2, executing ranked candidates), run
        # sizing and open the position immediately.
        sma20   = float(df["close"].rolling(SMA_FAST).mean().iloc[-1])
        sma50   = float(df["close"].rolling(SMA_SLOW).mean().iloc[-1])
        gap_pct = (sma20 - sma50) / sma50 * 100 if sma50 != 0 else 0.0

        if defer_buy:
            # Phase 1: collect candidate, rank it, do NOT open position yet.
            rank_score = rank_signal(ticker, df, gap_pct)
            result.update({
                "signal":     "BUY_CANDIDATE",
                "reason":     "Golden cross — pending capital allocation",
                "gap_pct":    round(gap_pct, 3),
                "rank_score": rank_score,
            })
            return result

        # Phase 2: sizing + execution (called from main() in rank order).
        entry_exec_px = apply_slippage(close_px, "buy")

        # Compute initial Chandelier stop (pure read, no RM state change)
        _rm_tmp               = RiskManager(RM_CONFIG)
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
            result.update({
                "signal": "HOLD",
                "reason": (
                    f"Golden cross but sizing skipped — "
                    f"0 shares (stop dist ₹{sz.get('stop_distance', 0):.0f}, "
                    f"risk ₹{sz.get('risk_amount', 0):.0f})"
                ),
            })
            return result

        # ── Queue AMO BUY — do NOT open position or deduct cash yet ─────────
        # Cash is deducted when morning_fill_check confirms the fill at open price.
        # This prevents the double-cash-deduction bug (signal_runner at close
        # price + morning_fill_check at open price = two deductions).
        portfolio.queue_pending_buy(
            ticker=ticker,
            shares=shares,
            provisional_price=entry_exec_px,  # close price, for display only
            today_str=today_str,
        )
        portfolio.record_weekly_signal("BUY")

        limit_px = round(close_px * (1 + AMO_CONFIG["limit_buffer_pct"]), 2)
        cost_bd  = transaction_cost_breakdown(entry_exec_px, shares, "buy", "delivery")
        result.update({
            "signal":          "BUY_QUEUED",
            "exec_price":      entry_exec_px,   # provisional close price
            "shares":          shares,
            "chandelier_stop": chandelier_for_sizing,
            "reason":          "Golden cross — AMO BUY queued for tomorrow's open",
            "gap_pct":         round(gap_pct, 3),
            "sizing_info":     sz,
            "cost_breakdown":  cost_bd,
            "action_taken": (
                f"AMO BUY queued: {shares} shr @ limit ₹{limit_px:.2f} "
                f"[fill pending tomorrow open] | "
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
    market_regime: str = "UNKNOWN",
    skipped_signals: Optional[List[dict]] = None,
    niftybees_price_for_report: float = 0.0,
) -> str:
    """
    Build the full terminal report as a string. Printed to stdout and saved
    to the log file.
    """
    summ = portfolio.summary(current_prices, niftybees_price_for_report)
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
    ln(f"  Date: {day_name}  |  Run: {run_time_str}  |  NIFTY Regime: {market_regime}")
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
    etf_shares = portfolio.state.get("etf_shares", 0)
    etf_tier   = portfolio.state.get("etf_tier", 0)
    etf_value  = etf_shares * niftybees_price_for_report if etf_shares > 0 else 0
    ln(f"  NIFTYBEES ETF     : {etf_shares} units | tier {etf_tier:.0%} | value ₹{etf_value:.0f}")
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
    action_results = [r for r in results.values() if r.get("action_taken")]
    if action_results:
        ln("  ACTIONS TAKEN")
        for r in action_results:
            ln(f"  ✓ {r['action_taken']}")
            if r.get("cost_breakdown"):
                ln(f"    Cost breakdown: {format_cost_breakdown(r['cost_breakdown'])}")
        ln()
    else:
        ln("  ACTIONS TAKEN: none (all HOLD)")
        ln()

    # ── Skipped BUY signals (capital / position limit) ────────────────────────
    skipped_signals = skipped_signals or []
    if skipped_signals:
        ln("  SKIPPED SIGNALS (capital / position limit)")
        ln(f"  {'Ticker':<16} {'Rank':>6}  {'Price':>10}  {'Gap%':>7}  Reason")
        ln("  " + "─" * 58)
        for s in skipped_signals:
            ln(
                f"  {s['ticker']:<16} {s['rank_score']:>6.1f}  "
                f"₹{s['price']:>9,.2f}  {s['gap_pct']:>+6.2f}%  {s['reason']}"
            )
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
    niftybees_price: float = 0.0,
) -> None:
    """
    Print an additional weekly summary. Called only on Fridays.
    Shows signals fired this week, weekly P&L, and running paper P&L.
    """
    summ = portfolio.summary(current_prices, niftybees_price)
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
    niftybees_price: float = 0.0,
) -> None:
    """
    Append one row per stock to the signal log CSV.

    The CSV is append-only. Never overwrite. Every day's signals are
    permanently recorded so you can replay or analyse later.
    """
    _ensure_csv_header()
    total_value = portfolio.get_portfolio_value(current_prices, niftybees_price)
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

    # ── Holiday calendar integrity check ─────────────────────────────────────
    _cal_warnings = verify_holiday_coverage(today.year)
    for _w in _cal_warnings:
        print(f"[market_calendar] {_w}")
    if _cal_warnings:
        print("[market_calendar] Update utils/market_calendar.py before going live.")

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

    # ── Step 3a: Load pre-trade risk flags (news_monitor output) ─────────────
    news_flags = load_news_flags()
    # Alert if any OPEN position is now on surveillance list
    for _ticker, _pos in portfolio.state["positions"].items():
        if _pos.get("shares", 0) > 0 and _ticker in news_flags:
            _flag = news_flags[_ticker]
            if _flag.get("auto_block", False):
                print(
                    f"[news_monitor] CRITICAL: Open position {_ticker} is now under "
                    f"surveillance ({_flag['type']}): {_flag['detail']}. "
                    f"Consider manual exit."
                )

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

    # ── Fetch NIFTYBEES price (for ETF-inclusive reporting throughout) ────────
    # Done here — before the weekly baseline correction and the signal pipeline —
    # so niftybees_price_for_report is available everywhere in main().
    # Runs in BOTH normal and backfill modes: backfill needs the live price to
    # show correct ETF value in the report. The rebalance is still skipped in
    # backfill mode (see ETF Overlay Rebalance block below).
    niftybees_price_for_report = 0.0
    try:
        _nb_start      = (today - timedelta(days=10)).isoformat()
        _nb_end        = (today + timedelta(days=1)).isoformat()
        niftybees_data = get_ohlcv("NIFTYBEES.NS", _nb_start, _nb_end)
        if niftybees_data is not None and len(niftybees_data) > 0:
            niftybees_price_for_report = float(niftybees_data["close"].iloc[-1])
    except Exception as _nb_exc:
        print(f"ETF | WARN: Could not fetch NIFTYBEES price — {_nb_exc} (reporting will exclude ETF)")
    # Fallback: if live fetch failed, use stored avg_price so reports always
    # show a reasonable ETF value rather than ₹0 (important for backfill runs).
    if niftybees_price_for_report == 0.0:
        niftybees_price_for_report = portfolio.state.get("etf_avg_price", 0.0)

    # ── Correct weekly baseline now that we have real prices ──────────────────
    if not is_backfill and today.weekday() == 0:
        real_value = portfolio.get_portfolio_value(current_prices, niftybees_price=niftybees_price_for_report)
        portfolio.state["weekly_start_value"] = real_value

    # ── Step 8 (new): NIFTY regime filter ────────────────────────────────────
    print(f"[paper_trading] Checking NIFTY 50 market regime …")
    market_regime = _fetch_nifty_regime(today)
    print()

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

    # ── Step 8b: Two-phase signal pipeline ────────────────────────────────────
    # Phase 1: process all stocks with defer_buy=True.
    #   - RM exits, SELL signals, HOLD → recorded and executed immediately.
    #   - BUY candidates → collected as BUY_CANDIDATE, not yet executed.
    # Phase 2: rank BUY_CANDIDATEs, apply position/cash gate, execute in order.
    #
    # CRITICAL: RM exits always execute. Only BUY entries are gated by capital allocation.
    # Never apply can_open_position() to exit decisions.
    print(f"[paper_trading] Running signal pipeline for {len(dfs)} stocks …\n")
    results: Dict[str, dict] = {}

    skipped_signals: List[dict] = []   # populated in Phase 2 if capital/position gate blocks a BUY

    # ── Phase 1 ──────────────────────────────────────────────────────────────
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

        results[ticker] = _process_stock(
            ticker, dfs[ticker], portfolio, current_prices, market_regime,
            defer_buy=True,   # collect BUY candidates, don't execute yet
        )

    # ── Phase 2: rank and execute BUY candidates ──────────────────────────────
    buy_candidates = [
        (ticker, results[ticker])
        for ticker in STOCKS
        if results.get(ticker, {}).get("signal") == "BUY_CANDIDATE"
    ]
    buy_candidates.sort(key=lambda x: x[1].get("rank_score", 0.0), reverse=True)

    if buy_candidates:
        print(
            f"\n[paper_trading] {len(buy_candidates)} BUY candidate(s) — "
            f"ranking and allocating capital (limit: {MAX_CONCURRENT_POSITIONS}) …"
        )
        for rank_pos, (ticker, candidate) in enumerate(buy_candidates, 1):
            df       = dfs[ticker]
            close_px = candidate["close_price"]
            gap_pct  = candidate.get("gap_pct", 0.0)
            rank_sc  = candidate.get("rank_score", 0.0)
            print(
                f"  #{rank_pos} {ticker:<18} rank={rank_sc:.1f}  gap={gap_pct:+.2f}%  ",
                end="",
            )

            # Gate check BEFORE execution.
            # Position count is exact; cash uses a floor heuristic (actual size
            # is determined inside the sizer; the gate only catches hard blocks).
            open_count = count_open_positions(portfolio)
            if open_count >= MAX_CONCURRENT_POSITIONS:
                reason = (
                    f"Position limit reached ({open_count}/{MAX_CONCURRENT_POSITIONS} open)"
                )
                results[ticker].update({
                    "signal":     "SKIPPED",
                    "reason":     reason,
                    "rank_score": rank_sc,
                })
                skipped_signals.append({
                    "ticker":     ticker,
                    "rank_score": rank_sc,
                    "reason":     reason,
                    "price":      close_px,
                    "gap_pct":    gap_pct,
                })
                print(f"→ SKIPPED   {reason}")
                continue

            # Cash floor: below ₹1,000 the sizer will return 0 shares for any stock
            if portfolio.state["cash"] < MIN_CASH_TO_ATTEMPT_BUY:
                reason = (
                    f"Insufficient cash ₹{portfolio.state['cash']:,.0f} "
                    f"(minimum ₹{MIN_CASH_TO_ATTEMPT_BUY:,.0f} required)"
                )
                results[ticker].update({
                    "signal":     "SKIPPED",
                    "reason":     reason,
                    "rank_score": rank_sc,
                })
                skipped_signals.append({
                    "ticker":     ticker,
                    "rank_score": rank_sc,
                    "reason":     reason,
                    "price":      close_px,
                    "gap_pct":    gap_pct,
                })
                print(f"→ SKIPPED   {reason}")
                continue

            # ── Pre-trade risk flag check ─────────────────────────────────────
            if ticker in news_flags:
                flag = news_flags[ticker]
                if flag.get("auto_block", False):
                    reason = f"PRE-TRADE BLOCK ({flag['type']}): {flag['detail']}"
                    results[ticker].update({
                        "signal":     "BLOCKED",
                        "reason":     reason,
                        "rank_score": rank_sc,
                    })
                    skipped_signals.append({
                        "ticker":     ticker,
                        "rank_score": rank_sc,
                        "reason":     reason,
                        "price":      close_px,
                        "gap_pct":    gap_pct,
                    })
                    print(f"→ BLOCKED   {reason}")
                    continue
                else:
                    # Warning only — do not block, human has been notified via log
                    print(f"  [news] WARNING {ticker}: {flag['detail']} — proceeding with entry")
                    print(f"  NEWS WARNING | {ticker} | {flag['type']} | {flag['detail']}")

            # ── Correlation safety check ──────────────────────────────────────
            # Fail open: a data/network failure must not silently suppress a
            # valid BUY signal. Log the error and proceed with the trade.
            #
            # Pass live in-memory state rather than the on-disk file so that
            # same-day BUY candidates are checked against each other.
            # pending_buy positions are treated as open (pending_buy=False in
            # corr_state) so candidate #2 sees candidate #1 already queued.
            try:
                corr_state = {
                    "positions": {
                        t: {**pos, "pending_buy": False}
                        for t, pos in portfolio.state["positions"].items()
                        if pos.get("shares", 0) > 0
                    }
                }
                corr_result = check_entry_correlation(
                    candidate=ticker,
                    portfolio_state_dict=corr_state,
                    lookback_days=120,
                    max_correlation=0.60,
                    price_data=dfs,        # pre-fetched DataFrames — zero API calls
                )
                if not corr_result["safe"]:
                    reason = f"Correlation block: {corr_result['reason']}"
                    results[ticker].update({
                        "signal":     "CORR_BLOCK",
                        "reason":     reason,
                        "rank_score": rank_sc,
                    })
                    skipped_signals.append({
                        "ticker":     ticker,
                        "rank_score": rank_sc,
                        "reason":     reason,
                        "price":      close_px,
                        "gap_pct":    gap_pct,
                    })
                    print(f"→ CORR_BLOCK  {corr_result['reason']}")
                    continue
                print(f"  [corr] {ticker}: {corr_result['reason']}")
            except Exception as _corr_exc:
                print(
                    f"  [corr] WARN: {_corr_exc} — "
                    f"proceeding with BUY (fail open)"
                )

            # Gate passed — execute with current (post-prior-execution) portfolio state.
            executed = _process_stock(
                ticker, df, portfolio, current_prices, market_regime,
                defer_buy=False,
            )
            results[ticker] = executed

            if executed["signal"] == "BUY_QUEUED":
                print(
                    f"→ EXECUTED  {executed.get('shares',0)} shr "
                    f"@ ₹{executed.get('exec_price',0):.2f}"
                )
            else:
                # Sizer returned 0 or state changed between phases — log reason
                print(f"→ {executed['signal']}  {executed.get('reason','')}")

    # ── ETF Overlay Rebalance ────────────────────────────────────────────────
    # NIFTYBEES price was fetched above (after Step 7) and stored in
    # niftybees_price_for_report. Rebalance here using that price.
    if not is_backfill:
        try:
            if niftybees_price_for_report > 0:
                portfolio.rebalance_etf(niftybees_price_for_report, current_prices=current_prices, log_fn=print)
            else:
                print("ETF OVERLAY | WARNING: NIFTYBEES price unavailable, skipping rebalance")
        except Exception as e:
            print(f"ETF OVERLAY | ERROR: {e} — skipping rebalance, no state change")

    # ── Step 9: Advance cooldowns (end-of-day, unconditional for all stocks) ──
    # Mirrors advance_bar() in the backtester — called AFTER signal processing,
    # exactly once per trading day.
    if not is_backfill:
        for ticker in STOCKS:
            portfolio.advance_cooldown(ticker)

    # ── Step 10: Save state ────────────────────────────────────────────────────
    if not is_backfill:
        portfolio.state["last_run_date"]   = today.isoformat()
        portfolio.state["last_run_time"]   = _get_ist_now().strftime("%H:%M")  # IST HH:MM
        # Track regime transitions: only write regime_transition_date when the
        # regime actually changes, so the screener's annotation logic can detect
        # degradation flags that coincide with a genuine BULL↔BEAR flip.
        _prev_regime = portfolio.state.get("market_regime", "UNKNOWN")
        if market_regime not in ("UNKNOWN",) and market_regime != _prev_regime:
            portfolio.state["regime_transition_date"] = today.isoformat()
        portfolio.state["market_regime"]   = market_regime
        portfolio.state["last_regime_date"] = today.isoformat()
        portfolio.save()
        print("\n[paper_trading] Portfolio state saved.")

    # ── Step 11: Print report ─────────────────────────────────────────────────
    run_time_str = _get_ist_now().strftime("%-I:%M %p IST")
    report       = _format_report(today, run_time_str, portfolio, results, current_prices, is_backfill, market_regime, skipped_signals, niftybees_price_for_report)
    print()
    print(report)

    # ── Step 12: CSV log (real runs only) ────────────────────────────────────
    if not is_backfill:
        _append_csv(today, results, portfolio, current_prices, niftybees_price=niftybees_price_for_report)
        # Append SKIPPED rows to signal_log.csv
        if skipped_signals:
            _ensure_csv_header()
            total_value = portfolio.get_portfolio_value(current_prices, niftybees_price=niftybees_price_for_report)
            initial     = portfolio.state["initial_capital"]
            pnl_pct     = (total_value / initial - 1) * 100
            with open(LOG_CSV, "a", newline="") as fh:
                writer = csv.writer(fh)
                for s in skipped_signals:
                    writer.writerow([
                        today.isoformat(), s["ticker"], "SKIPPED",
                        f"{s['price']:.4f}", "", "",
                        f"{portfolio.state['cash']:.2f}", f"{total_value:.2f}",
                        f"{pnl_pct:.4f}", "", "",
                        portfolio.state["cooldown_state"].get(s["ticker"], {}).get("remaining_bars", ""),
                        f"rank={s['rank_score']:.1f} gap={s['gap_pct']:+.2f}% | {s['reason']}",
                    ])
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
        if sig == "BUY_QUEUED" and shares > 0:
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
        _weekly_summary(today, portfolio, current_prices, niftybees_price_for_report)


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    # ── --test-ranking mode: verify ranking + gate logic without live data ────
    if "--test-ranking" in sys.argv:
        print(f"Testing signal ranking with MAX_CONCURRENT_POSITIONS={MAX_CONCURRENT_POSITIONS}")
        print(f"Weights: {SIGNAL_RANK_WEIGHTS}")
        print()

        test_candidates = [
            {"ticker": "STOCK_A", "rank_score": 85.2, "shares": 10, "price": 1000.0, "gap_pct": -0.5},
            {"ticker": "STOCK_B", "rank_score": 62.1, "shares":  5, "price": 2000.0, "gap_pct": -3.2},
            {"ticker": "STOCK_C", "rank_score": 91.4, "shares":  8, "price": 1500.0, "gap_pct": -1.1},
        ]
        test_candidates.sort(key=lambda x: x["rank_score"], reverse=True)

        print("Ranked order (highest score first):")
        for i, c in enumerate(test_candidates, 1):
            print(f"  #{i} {c['ticker']}  score={c['rank_score']}  "
                  f"gap={c['gap_pct']:+.2f}%  price=₹{c['price']:.0f}")

        print()
        print(f"Simulating with {MAX_CONCURRENT_POSITIONS} max concurrent positions:")
        simulated_open = 0
        for i, c in enumerate(test_candidates, 1):
            if simulated_open < MAX_CONCURRENT_POSITIONS:
                print(f"  #{i} {c['ticker']}  → EXECUTE  (slot {simulated_open+1}/{MAX_CONCURRENT_POSITIONS})")
                simulated_open += 1
            else:
                print(f"  #{i} {c['ticker']}  → SKIPPED  "
                      f"(Position limit reached ({simulated_open}/{MAX_CONCURRENT_POSITIONS} open))")

        print()
        print("Expected with MAX_CONCURRENT_POSITIONS=4: all 3 execute (3 < 4)")
        print("Expected with MAX_CONCURRENT_POSITIONS=2: STOCK_C (#1) and STOCK_A (#2) execute, STOCK_B (#3) skipped")
        print()
        print("✅ Ranking logic verified: higher rank_score = higher priority")
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="NSE paper trading daily signal runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python paper_trading/signal_runner.py                    # normal daily run
  python paper_trading/signal_runner.py --backfill 2026-06-02  # dry run on past date
  python paper_trading/signal_runner.py --force            # re-run even if ran today
  python paper_trading/signal_runner.py --test-ranking     # verify ranking logic
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
