"""
paper_trading/paper_portfolio.py — Portfolio state manager for paper trading.

Responsibilities:
  - Load/save portfolio state to a JSON file (atomically — crash-safe)
  - Track cash, open positions, completed trade log
  - Manage per-stock cooldown windows (mirrors engine/cooldown.py semantics exactly)
  - Expose methods the signal_runner uses to open/close positions

Design decisions:
  - Atomic writes: write to .tmp file first, then os.replace() — if the process
    crashes mid-write, the old state file survives intact.
  - Cooldown mirror: trigger_cooldown() sets remaining = bars + 1, just like
    CooldownTracker in engine/cooldown.py. The +1 is consumed by advance_cooldown()
    at end of the same trading day, giving exactly `bars` suppressed days.
  - Cash is deducted/credited here to keep all financial state in one place.
  - No orders are ever placed — this class is purely a bookkeeping ledger.
"""

import json
import math
import os
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from utils.costs import transaction_costs

ETF_TIERS: dict = {0: 1.0, 1: 0.8, 2: 0.8, 3: 0.5, 4: 0.0}


class PaperPortfolio:
    """
    Stateful paper trading ledger. One instance per run. Load state at start,
    save state at end. Never modify state after save (signal_runner enforces this).

    Example usage:
        portfolio = PaperPortfolio(STOCKS, "paper_trading/portfolio_state.json", 100_000)
        portfolio.load()
        # ... process signals, call open_position / close_position ...
        portfolio.save()
    """

    def __init__(
        self,
        tickers: List[str],
        state_file: str = "paper_trading/portfolio_state.json",
        initial_capital: float = 100_000.0,
    ):
        self.tickers       = tickers
        self.state_file    = Path(state_file)
        self.initial_capital = initial_capital
        self.state: Optional[dict] = None   # loaded by load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Load portfolio state from JSON. If the file doesn't exist (first run),
        create a fresh initial state in memory (save() will persist it).

        Also ensures every ticker in self.tickers has an entry in positions and
        cooldown_state — safe to add new stocks after initial run.
        """
        if not self.state_file.exists():
            self.state = self._make_initial_state()
            return

        with open(self.state_file, "r") as fh:
            self.state = json.load(fh)

        # Backfill any tickers added after initial creation
        for ticker in self.tickers:
            if ticker not in self.state["positions"]:
                self.state["positions"][ticker] = self._blank_position()
            if ticker not in self.state["cooldown_state"]:
                self.state["cooldown_state"][ticker] = {
                    "remaining_bars": 0,
                    "last_exit_reason": None,
                }

        # Migrate existing positions to add new fields if missing
        for pos in self.state["positions"].values():
            if "pending_buy" not in pos:
                pos["pending_buy"] = False
            if "pending_rm_exit" not in pos:
                pos["pending_rm_exit"] = False
            if "rm_exit_reason" not in pos:
                pos["rm_exit_reason"] = None
            if "rm_sell_requeue_count" not in pos:
                pos["rm_sell_requeue_count"] = 0
            if "entry_cost" not in pos:
                pos["entry_cost"] = 0.0   # existing open positions: conservative (buy cost unknown)

        # Migrate top-level ETF state (backward-compatible)
        self.state.setdefault("etf_shares",    0)
        self.state.setdefault("etf_avg_price", 0.0)
        self.state.setdefault("etf_tier",      0)

        self.validate_state_integrity()

    def validate_state_integrity(self) -> None:
        """
        Validates portfolio state on every load. Raises ValueError if state
        appears corrupted or stale. Better to abort than trade on bad data.

        Catches the most common corruption scenario: a stale local
        portfolio_state.json accidentally SCP'd over the live server file,
        which would show a much lower total value than expected.
        """
        initial_capital = self.state.get("initial_capital", 100_000.0)
        cash            = self.state.get("cash", 0.0)
        etf_shares      = self.state.get("etf_shares", 0)
        etf_avg_price   = self.state.get("etf_avg_price", 0.0)

        # Stock value using entry_price as proxy (current price unavailable at load time)
        stock_value = sum(
            pos.get("shares", 0) * pos.get("entry_price", 0.0)
            for pos in self.state.get("positions", {}).values()
        )

        etf_value      = etf_shares * etf_avg_price
        computed_total = cash + stock_value + etf_value

        # Check 1: total value must be >= 50% of initial capital.
        # Catches stale file overwrites; allows legitimate large drawdowns.
        floor = initial_capital * 0.50
        if computed_total < floor:
            raise ValueError(
                f"STATE INTEGRITY FAIL: computed total value ₹{computed_total:,.0f} "
                f"is below 50% floor ₹{floor:,.0f}. "
                f"Possible stale portfolio_state.json loaded. "
                f"Cash=₹{cash:,.0f} | Stock=₹{stock_value:,.0f} | ETF=₹{etf_value:,.0f}. "
                f"Aborting to prevent trading on corrupted state."
            )

        # Check 2: total_trades must match trade_log length
        total_trades  = self.state.get("total_trades", 0)
        trade_log_len = len(self.state.get("trade_log", []))
        if total_trades != trade_log_len:
            raise ValueError(
                f"STATE INTEGRITY FAIL: total_trades={total_trades} but "
                f"trade_log has {trade_log_len} entries. State is inconsistent."
            )

        # Check 3: etf_tier must be a valid D_aggressive tier value
        valid_tiers = set(ETF_TIERS.values()) | {0, 0.0}
        etf_tier = self.state.get("etf_tier", 0)
        if etf_tier not in valid_tiers:
            raise ValueError(
                f"STATE INTEGRITY FAIL: etf_tier={etf_tier} is not a valid "
                f"D_aggressive tier value. Valid values: {valid_tiers}"
            )

    def save(self) -> None:
        """
        Atomically save portfolio state to JSON.

        Write to a .tmp file first, then os.replace() which is an atomic rename
        on POSIX systems. If the process crashes between write and rename, the
        original state file is untouched. The .tmp file can be safely deleted.
        """
        if self.state is None:
            raise RuntimeError("Cannot save — state not loaded. Call load() first.")

        tmp_path = str(self.state_file) + ".tmp"
        with open(tmp_path, "w") as fh:
            # default=str handles date objects that might slip through
            json.dump(self.state, fh, indent=2, default=str)

        # Atomic rename — survives crashes
        os.replace(tmp_path, str(self.state_file))

    def _make_initial_state(self) -> dict:
        """Return a fresh portfolio state dict with no open positions."""
        today_str = date.today().isoformat()
        return {
            "cash":             self.initial_capital,
            "initial_capital":  self.initial_capital,
            "positions":        {t: self._blank_position() for t in self.tickers},
            "cooldown_state":   {
                t: {"remaining_bars": 0, "last_exit_reason": None}
                for t in self.tickers
            },
            "total_trades":     0,
            "last_run_date":    None,
            "inception_date":   today_str,
            # Weekly tracking — reset every Monday
            "weekly_start_value": self.initial_capital,
            "weekly_start_date":  today_str,
            "weekly_signals":     {"BUY": 0, "SELL": 0, "RISK_EXIT": 0},
            # Complete trade history for analysis
            "trade_log":        [],
            # ETF overlay (NIFTYBEES paper tracking)
            "etf_shares":       0,
            "etf_avg_price":    0.0,
            "etf_tier":         0,
        }

    @staticmethod
    def _blank_position() -> dict:
        """Return a zero-state position dict (no open position)."""
        return {
            "shares":                   0,
            "entry_price":              0.0,   # slippage-adjusted exec price at buy
            "entry_cost":               0.0,   # buy-side transaction costs (brokerage + SEBI + exchange + GST + stamp duty)
            "entry_date":               None,
            "highest_high_since_entry": 0.0,   # highest high bar since position opened
            "bars_held":                0,      # trading bars held (incremented by RM check_exit)
            "chandelier_stop":          None,   # None = -inf (not yet computed by ATR)
            "pending_buy":              False,  # True when AMO BUY queued but not yet filled
            "pending_rm_exit":          False,  # True when RM exit AMO queued but not yet filled
            "rm_exit_reason":           None,   # "HARD_STOP" | "CHANDELIER" | "TIME_STOP"
            "rm_sell_requeue_count":    0,      # times RM SELL AMO was requeued after MISS
        }

    # ── Position management ────────────────────────────────────────────────────

    def open_position(
        self,
        ticker: str,
        shares: int,
        exec_price: float,          # slippage-adjusted buy execution price
        date_str: str,
        chandelier_stop: Optional[float],
        entry_high: float,           # today's high — seeds highest_high tracker
    ) -> None:
        """
        Record a new long position and deduct cost from cash.

        Args:
            ticker:          NSE ticker, e.g. "TMPV.NS"
            shares:          number of shares (pre-computed by PositionSizer)
            exec_price:      slippage-adjusted fill price (same value passed to RM)
            date_str:        ISO date string "YYYY-MM-DD"
            chandelier_stop: initial Chandelier stop level (None if ATR data insufficient)
            entry_high:      today bar's high — seeds the highest_high tracker
        """
        cost        = transaction_costs(exec_price, shares, "buy", "delivery")
        total_spent = shares * exec_price + cost

        if total_spent > self.state["cash"]:
            # Should not happen — PositionSizer caps at available cash — but guard anyway
            raise ValueError(
                f"Insufficient cash: need ₹{total_spent:,.2f} "
                f"but only ₹{self.state['cash']:,.2f} available."
            )

        self.state["cash"] -= total_spent

        pos = self.state["positions"][ticker]
        pos["shares"]                   = shares
        pos["entry_price"]              = exec_price
        pos["entry_cost"]               = cost   # stored for accurate net_pnl at close time
        pos["entry_date"]               = date_str
        pos["highest_high_since_entry"] = entry_high
        pos["bars_held"]                = 0
        pos["chandelier_stop"]          = chandelier_stop   # None serialises to JSON null

    def queue_pending_buy(
        self,
        ticker: str,
        shares: int,
        provisional_price: float,
        today_str: str,
    ) -> None:
        """
        Mark a position as having a pending BUY AMO order queued.
        Does NOT deduct cash or open the position.
        Cash will be deducted when morning_fill_check confirms the fill.

        Args:
            ticker:            stock ticker
            shares:            number of shares ordered
            provisional_price: slippage-adjusted close price (for display only)
            today_str:         signal date in YYYY-MM-DD format
        """
        pos = self.state["positions"][ticker]

        if pos["shares"] > 0 and not pos.get("pending_buy", False):
            raise ValueError(
                f"queue_pending_buy called for {ticker} but position already open "
                f"({pos['shares']} shares). Cannot queue a BUY on an existing position."
            )

        pos["pending_buy"]              = True
        pos["shares"]                   = shares           # store intended shares for display
        pos["entry_price"]              = provisional_price # provisional — overwritten at fill
        pos["entry_date"]               = today_str
        pos["highest_high_since_entry"] = 0.0
        pos["bars_held"]                = 0
        pos["chandelier_stop"]          = None
        # Do NOT deduct cash here — cash is deducted at fill time in morning_fill_check

    def cancel_pending_buy(self, ticker: str) -> None:
        """
        Cancel a pending BUY (e.g. AMO order missed or cancelled).
        Resets position back to flat without touching cash.
        """
        pos = self.state["positions"][ticker]
        if not pos.get("pending_buy", False):
            return  # nothing to cancel
        self.state["positions"][ticker] = self._blank_position()

    def requeue_rm_sell(self, ticker: str, new_limit_price: float, today_str: str) -> None:
        """
        Re-queue an RM exit SELL AMO after a previous attempt was MISSED.
        Keeps pending_rm_exit=True and increments the requeue counter.
        Called by morning_fill_check when a SELL AMO is MISSED.

        Does NOT clear pending_rm_exit — position remains flagged until closed.
        Does NOT touch cash — cash only changes when position actually closes.

        Args:
            ticker:          stock ticker
            new_limit_price: updated limit price based on today's close
            today_str:       today's date in YYYY-MM-DD format
        """
        MAX_RM_SELL_REQUEUES = 3

        pos = self.state["positions"][ticker]

        if not pos.get("pending_rm_exit", False):
            raise ValueError(
                f"requeue_rm_sell called for {ticker} but pending_rm_exit=False. "
                f"Only call this after a confirmed MISSED RM SELL."
            )

        if pos["shares"] <= 0:
            raise ValueError(
                f"requeue_rm_sell called for {ticker} but shares={pos['shares']}. "
                f"Cannot requeue sell on flat position."
            )

        pos["rm_sell_requeue_count"] = pos.get("rm_sell_requeue_count", 0) + 1

        if pos["rm_sell_requeue_count"] > MAX_RM_SELL_REQUEUES:
            print(
                f"  🚨 CRITICAL: {ticker} RM SELL has been requeued "
                f"{pos['rm_sell_requeue_count']} times without filling."
            )
            print(f"  🚨 MANUAL ACTION REQUIRED: Check position in Zerodha dashboard immediately.")
            print(f"  🚨 Possible circuit breaker or liquidity issue.")

        # pending_rm_exit stays True — position not closed yet
        # The new AMO order is logged by the caller via AMOOrderManager
        print(
            f"  [portfolio] {ticker}: RM SELL requeued (attempt #{pos['rm_sell_requeue_count']}) "
            f"— new limit ₹{new_limit_price:.2f} for {today_str}"
        )
        self.save()

    def confirm_buy_fill(
        self,
        ticker: str,
        actual_exec_price: float,
        actual_shares: int,
        fill_date: str,
        ohlcv_history,   # reserved for future RM seeding; pass None
    ) -> None:
        """
        Confirm a pending BUY AMO order was filled at actual_exec_price.
        Deducts cash exactly once at the actual open-price fill.
        Updates entry_price to the actual fill price.

        Args:
            ticker:             stock ticker
            actual_exec_price:  slippage-adjusted open fill price
            actual_shares:      shares actually filled (should match pending)
            fill_date:          fill date in YYYY-MM-DD format
            ohlcv_history:      reserved; pass None
        """
        pos = self.state["positions"][ticker]

        if not pos.get("pending_buy", False):
            raise ValueError(
                f"confirm_buy_fill called for {ticker} but no pending_buy flag set."
            )

        cost        = transaction_costs(actual_exec_price, actual_shares, "buy", "delivery")
        total_spent = actual_shares * actual_exec_price + cost

        if total_spent > self.state["cash"]:
            raise ValueError(
                f"Insufficient cash to confirm BUY fill for {ticker}: "
                f"need ₹{total_spent:,.2f} but have ₹{self.state['cash']:,.2f}"
            )

        self.state["cash"] -= total_spent

        pos["pending_buy"]              = False
        pos["shares"]                   = actual_shares
        pos["entry_price"]              = actual_exec_price
        pos["entry_cost"]               = cost   # stored for accurate net_pnl at close time
        pos["entry_date"]               = fill_date
        pos["highest_high_since_entry"] = 0.0  # seeded on first check_exit call
        pos["bars_held"]                = 0
        pos["chandelier_stop"]          = None

    def close_position(
        self,
        ticker: str,
        exec_price: float,   # slippage-adjusted sell execution price
        date_str: str,
        reason: str,
    ) -> float:
        """
        Close an open position, credit proceeds to cash, record trade.

        Args:
            exec_price: slippage-adjusted fill price
            date_str:   ISO date string
            reason:     exit reason ("STRATEGY_SIGNAL" | "HARD_STOP" | "CHANDELIER" | "TIME_STOP")

        Returns:
            net_pnl — profit or loss net of sell-side costs (₹, signed).
            Note: entry cost was already deducted at open_position() time.
        """
        pos        = self.state["positions"][ticker]
        shares     = pos["shares"]
        entry_px   = pos["entry_price"]

        cost       = transaction_costs(exec_price, shares, "sell", "delivery")
        proceeds   = shares * exec_price - cost
        self.state["cash"] += proceeds

        entry_cost = pos.get("entry_cost", 0.0)   # buy-side costs stored at open_position()
        gross_pnl  = (exec_price - entry_px) * shares
        net_pnl    = gross_pnl - cost - entry_cost  # sell costs + buy costs = true round-trip P&L

        # Persist completed trade for later analysis
        self.state["trade_log"].append({
            "ticker":       ticker,
            "entry_date":   pos["entry_date"],
            "exit_date":    date_str,
            "entry_price":  round(entry_px, 4),
            "exit_price":   round(exec_price, 4),
            "shares":       shares,
            "gross_pnl":    round(gross_pnl, 2),
            "entry_cost":   round(entry_cost, 2),
            "net_pnl":      round(net_pnl, 2),
            "return_pct":   round((exec_price / entry_px - 1) * 100, 4),
            "exit_reason":  reason,
        })
        self.state["total_trades"] += 1

        # Reset position to blank
        self.state["positions"][ticker] = self._blank_position()
        return net_pnl

    def update_rm_state(
        self,
        ticker: str,
        bars_held: int,
        highest_high: float,
        chandelier_stop: Optional[float],
    ) -> None:
        """
        Persist updated RiskManager internals after check_exit() runs.

        Called every day a position is held, even when the RM doesn't exit.
        The RiskManager updates highest_high and chandelier_stop on every bar,
        so we must save those back to keep state consistent across days.

        Args:
            bars_held:       rm._bars_since_entry after check_exit()
            highest_high:    rm._highest_high_since_entry after check_exit()
            chandelier_stop: rm._chandelier_stop after check_exit()
                             None if still at -inf (insufficient ATR history)
        """
        pos = self.state["positions"][ticker]
        pos["bars_held"]                = bars_held
        pos["highest_high_since_entry"] = highest_high
        pos["chandelier_stop"]          = chandelier_stop   # None = -inf

    # ── Cooldown (mirrors engine/cooldown.py semantics) ────────────────────────

    def is_in_cooldown(self, ticker: str) -> bool:
        """
        True if buy signals for this ticker should be suppressed.
        Returns False when remaining_bars == 0 (cooldown elapsed).
        """
        return self.state["cooldown_state"][ticker]["remaining_bars"] > 0

    def trigger_cooldown(self, ticker: str, reason: str, bars: int = 15) -> None:
        """
        Activate the cooldown gate for ticker after a risk-manager exit.

        Sets remaining_bars = bars + 1. The +1 is consumed by advance_cooldown()
        called at the END of the same trading day — exactly mirroring the
        backtester's trigger_cooldown() + advance_bar() pair.

        After advance_cooldown() at end of today, remaining_bars = bars.
        Starting tomorrow, bars days of buy suppression follow. This gives
        exactly `bars` suppressed trading days, matching the backtest.

        Args:
            bars: number of trading days to suppress (default 15 to match CONFIG)
        """
        self.state["cooldown_state"][ticker] = {
            "remaining_bars": bars + 1,   # +1 absorbed by end-of-day advance
            "last_exit_reason": reason,
        }

    def advance_cooldown(self, ticker: str) -> None:
        """
        Decrement the cooldown counter by exactly one trading bar.

        Called unconditionally at the END of each daily run for every ticker.
        Never decrements below zero. The unconditional call mirrors advance_bar()
        in the backtester which runs at the end of EVERY bar regardless of signals.
        """
        cd = self.state["cooldown_state"][ticker]
        if cd["remaining_bars"] > 0:
            cd["remaining_bars"] -= 1

    # ── Weekly tracking ────────────────────────────────────────────────────────

    def reset_weekly_stats_if_monday(self, today: date, current_value: float) -> None:
        """
        If today is Monday (or the first run), reset weekly tracking baseline.
        Call this BEFORE processing signals so the baseline reflects start-of-week value.
        """
        if today.weekday() == 0:   # 0 = Monday
            self.state["weekly_start_value"] = current_value
            self.state["weekly_start_date"]  = today.isoformat()
            self.state["weekly_signals"]     = {"BUY": 0, "SELL": 0, "RISK_EXIT": 0}

    def record_weekly_signal(self, signal_type: str) -> None:
        """Track weekly signal count. signal_type: 'BUY' | 'SELL' | 'RISK_EXIT'."""
        ws = self.state.get("weekly_signals", {"BUY": 0, "SELL": 0, "RISK_EXIT": 0})
        if signal_type in ws:
            ws[signal_type] += 1
        self.state["weekly_signals"] = ws

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_portfolio_value(self, current_prices: Dict[str, float], niftybees_price: float = 0.0) -> float:
        """
        Total portfolio value = cash + mark-to-market open positions + ETF value.

        Args:
            current_prices:  {ticker: current_close_price} for all tickers.
                             Missing tickers default to their entry price (fallback).
            niftybees_price: current NIFTYBEES price; 0.0 omits ETF value (backward-compat).
        """
        mtm = 0.0
        for ticker, pos in self.state["positions"].items():
            # Exclude pending_buy positions: cash not yet deducted, so adding
            # their MTM value would double-count the capital.
            if pos["shares"] > 0 and not pos.get("pending_buy", False):
                price = current_prices.get(ticker, pos["entry_price"])
                mtm  += pos["shares"] * price
        etf_value = self.state.get("etf_shares", 0) * niftybees_price
        return self.state["cash"] + mtm + etf_value

    def get_open_positions(self) -> dict:
        """Return {ticker: position_dict} for all tickers currently holding shares."""
        return {
            ticker: pos
            for ticker, pos in self.state["positions"].items()
            if pos["shares"] > 0
        }

    def summary(self, current_prices: Dict[str, float], niftybees_price: float = 0.0) -> dict:
        """
        Compute a snapshot of all key portfolio metrics for the daily report.

        Returns a dict with clean, pre-formatted metrics so signal_runner.py
        doesn't need to do arithmetic.

        Args:
            niftybees_price: current NIFTYBEES price; 0.0 omits ETF value (backward-compat).
        """
        total_value   = self.get_portfolio_value(current_prices, niftybees_price)
        open_pos      = self.get_open_positions()
        initial       = self.state["initial_capital"]

        invested_value = sum(
            pos["shares"] * current_prices.get(ticker, pos["entry_price"])
            for ticker, pos in open_pos.items()
        )

        pnl       = total_value - initial
        pnl_pct   = pnl / initial * 100
        cash      = self.state["cash"]

        weekly_pnl     = total_value - self.state.get("weekly_start_value", initial)
        weekly_pnl_pct = weekly_pnl / self.state.get("weekly_start_value", initial) * 100

        return {
            "cash":             cash,
            "invested_value":   invested_value,
            "total_value":      total_value,
            "pnl":              pnl,
            "pnl_pct":          pnl_pct,
            "open_count":       len(open_pos),
            "total_trades":     self.state["total_trades"],
            "initial_capital":  initial,
            "weekly_pnl":       weekly_pnl,
            "weekly_pnl_pct":   weekly_pnl_pct,
            "weekly_start_date": self.state.get("weekly_start_date"),
            "weekly_signals":   self.state.get("weekly_signals", {}),
            "inception_date":   self.state.get("inception_date"),
        }

    # ── ETF overlay (NIFTYBEES paper tracking) ────────────────────────────────

    def get_etf_target_tier(self) -> float:
        """Return target ETF allocation fraction based on open stock positions."""
        open_positions = sum(
            1 for pos in self.state["positions"].values()
            if pos.get("shares", 0) > 0 and not pos.get("pending_buy", False)
        )
        return ETF_TIERS[min(open_positions, 4)]

    def rebalance_etf(self, niftybees_price: float, current_prices: dict = None, log_fn=None) -> None:
        """
        Paper-rebalance the NIFTYBEES ETF position to match the target tier.
        Called by signal_runner after all stock signals are processed.
        No-ops if the tier is unchanged. Cash-guards buys against available cash.

        Args:
            niftybees_price:  today's NIFTYBEES close price
            current_prices:   {ticker: today's close price} for open stock positions.
                              Falls back to entry_price if a ticker is missing or
                              current_prices is None — ensures backward compatibility.
            log_fn:           optional callable for logging the rebalance action
        """
        # Mirror get_etf_target_tier() exactly so the two functions can never disagree.
        # pending_buy=True positions: shares are set but cash not yet deducted → exclude.
        # pending_rm_exit=True with shares>0: position still open, sell pending → include.
        open_positions = 0
        for pos in self.state["positions"].values():
            shares      = pos.get("shares", 0)
            pending_buy  = pos.get("pending_buy", False)
            pending_sell = pos.get("pending_rm_exit", False)

            if shares > 0 and not pending_buy:
                open_positions += 1
            elif pending_sell and shares == 0:
                open_positions += 1

        open_positions = min(open_positions, 4)
        new_tier = ETF_TIERS[open_positions]
        old_tier = self.state["etf_tier"]

        if new_tier == old_tier:
            return

        stock_value = 0.0
        for ticker, pos in self.state["positions"].items():
            if pos.get("shares", 0) > 0:
                if current_prices and ticker in current_prices:
                    price = current_prices[ticker]
                else:
                    price = pos["entry_price"]  # fallback if price not provided
                stock_value += pos["shares"] * price

        total_portfolio_value = (
            self.state["cash"] + stock_value + self.state["etf_shares"] * niftybees_price
        )

        target_etf_value  = total_portfolio_value * new_tier
        current_etf_value = self.state["etf_shares"] * niftybees_price
        delta_value       = target_etf_value - current_etf_value
        delta_shares      = int(delta_value / niftybees_price)

        if delta_shares == 0:
            self.state["etf_tier"] = new_tier
            return

        if delta_shares > 0:
            cost = delta_shares * niftybees_price
            if cost > self.state["cash"]:
                delta_shares = int(self.state["cash"] / niftybees_price)
            if delta_shares == 0:
                return
            old_shares = self.state["etf_shares"]
            old_avg    = self.state["etf_avg_price"]
            new_shares = old_shares + delta_shares
            # VWAP: weighted average of existing position and new purchase.
            # First purchase: avg = purchase price.
            # Subsequent: avg = (old_shares × old_avg + new_shares × price) / total
            if old_shares == 0 or old_avg == 0.0:
                new_avg = niftybees_price
            else:
                new_avg = (
                    (old_shares * old_avg + delta_shares * niftybees_price)
                    / new_shares
                )
            self.state["cash"]          -= delta_shares * niftybees_price
            self.state["etf_shares"]     = new_shares
            self.state["etf_avg_price"]  = round(new_avg, 4)
        else:
            sell_shares = min(abs(delta_shares), self.state["etf_shares"])
            self.state["cash"]        += sell_shares * niftybees_price
            self.state["etf_shares"]  -= sell_shares
            if self.state["etf_shares"] == 0:
                self.state["etf_avg_price"] = 0.0

        self.state["etf_tier"] = new_tier
        self.save()

        if log_fn:
            log_fn(
                f"ETF REBALANCE | tier {old_tier:.0%}→{new_tier:.0%} | "
                f"NIFTYBEES {delta_shares:+d} shares @ ₹{niftybees_price:.2f} | "
                f"ETF value ₹{self.state['etf_shares'] * niftybees_price:.0f} | "
                f"cash ₹{self.state['cash']:.0f}"
            )
