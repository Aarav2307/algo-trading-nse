"""
engine/position_sizer.py — Fixed-fractional position sizing.

The fixed-fractional rule sizes each trade so the maximum potential loss
equals a fixed percentage of current portfolio value. Position size shrinks
after losses and grows after wins — built-in anti-martingale risk management.

Formula
-------
    risk_amount   = portfolio_value × risk_per_trade_pct
    stop_distance = entry_price − chandelier_stop   (or fallback % if ATR unavailable)
    shares        = floor(risk_amount / stop_distance)

Then capped at:
    min(shares_from_risk,
        max_position_pct × portfolio_value / entry_price,
        (cash − brokerage) / entry_price)

When enabled=False: the backtester uses the original all-in path and this
module is not called. The two paths are completely separate so Run A is
bit-identical to a pre-sizing baseline.
"""

import math

from utils.costs import BROKERAGE_PER_ORDER


class PositionSizer:
    """
    Fixed-fractional position sizer. One instance per backtest run.

    calculate_shares() is pure (no effect on the sizing calculation itself).
    skipped_count is the only mutable state — audit-only, does not feed back
    into any calculation.
    """

    def __init__(self, config: dict):
        self.enabled            = config.get("enabled", True)
        self.method             = config.get("method", "fixed_fractional")
        self.risk_per_trade_pct = config.get("risk_per_trade_pct", 0.015)
        self.max_position_pct   = config.get("max_position_pct", 0.20)
        self.fallback_stop_pct  = config.get("fallback_stop_pct", 0.20)
        self.skipped_count: int = 0   # trades skipped because sizing returned 0 shares

    def calculate_shares(
        self,
        entry_price: float,
        portfolio_value: float,
        chandelier_stop: float | None,
        cash_available: float,
    ) -> tuple[int, dict]:
        """
        Return (shares_to_buy, sizing_log).

        shares == 0 → backtester skips the trade, logs "SKIPPED_SIZING".
        sizing_log  → dict printed inline at buy time and available for audit.

        Args:
            entry_price:     Slippage-adjusted buy price (same value passed to
                             risk_manager.on_position_open).
            portfolio_value: Total value (cash + open position MTM) before this trade.
            chandelier_stop: Entry-bar Chandelier from risk_manager.compute_chandelier(),
                             or None if ATR data is unavailable (first ~22 bars).
            cash_available:  Current cash balance before this trade.
        """
        # ── Stop distance: how far price can fall before we exit ──────────────
        # Prefer the Chandelier stop (dynamic, volatility-aware, already L4-adjusted).
        # Fall back to a fixed % of entry price when ATR data isn't available yet.
        if (chandelier_stop is not None
                and chandelier_stop > 0
                and chandelier_stop < entry_price):
            stop_distance = entry_price - chandelier_stop
            stop_source   = "chandelier"
        else:
            # Cold start (< 23 bars of data) or no risk manager — use fallback
            stop_distance = entry_price * self.fallback_stop_pct
            stop_source   = "fallback_hard_stop"

        if stop_distance <= 0:
            return 0, {
                "stop_source": stop_source, "stop_distance": 0,
                "risk_amount": 0, "shares_from_risk": 0, "binding": "zero_stop_distance",
            }

        # ── Fixed-fractional sizing ───────────────────────────────────────────
        # Risk exactly risk_per_trade_pct of current portfolio on this trade.
        # floor() ensures we never EXCEED the risk budget (we may risk slightly less).
        risk_amount      = portfolio_value * self.risk_per_trade_pct
        shares_from_risk = math.floor(risk_amount / stop_distance)

        # ── Position cap: single trade cannot exceed max_position_pct ─────────
        max_from_pos_cap = math.floor(
            portfolio_value * self.max_position_pct / entry_price
        )

        # ── Cash cap: subtract flat brokerage to prevent settlement shortfall ─
        # Brokerage is charged per order regardless of size, so we must reserve it.
        effective_cash = max(0.0, cash_available - BROKERAGE_PER_ORDER)
        max_from_cash  = math.floor(effective_cash / entry_price)

        shares = min(shares_from_risk, max_from_pos_cap, max_from_cash)

        # Identify the binding constraint for the audit log
        if shares <= 0:
            binding = "all_constraints_zero"
        elif shares == shares_from_risk:
            binding = "risk_budget"
        elif shares == max_from_pos_cap:
            binding = "position_cap"
        else:
            binding = "cash"

        return shares, {
            "stop_source":      stop_source,
            "chandelier_stop":  chandelier_stop,
            "stop_distance":    round(stop_distance, 2),
            "risk_pct":         self.risk_per_trade_pct,
            "risk_amount":      round(risk_amount, 2),
            "shares_from_risk": shares_from_risk,
            "max_from_pos_cap": max_from_pos_cap,
            "max_from_cash":    max_from_cash,
            "shares_final":     shares,
            "binding":          binding,
        }
