"""
engine/cooldown.py — Cooldown gate between the risk manager and the strategy.

WHY THIS LIVES HERE AND NOT IN risk_manager.py
───────────────────────────────────────────────
RiskManager answers one question per bar: "should I exit THIS position NOW
based on price action?" It is stateful about the current trade.

CooldownTracker answers a different question: "should I allow a NEW entry NOW
based on HOW the last trade ended?" It is stateful about the exit history.

These are different temporal concerns:
  RiskManager  → looks at today's price vs entry price vs ATR
  CooldownTracker → looks at yesterday's exit reason vs today's date

Keeping them separate makes each class independently testable and means the
risk manager stays focused on price analysis rather than becoming an entry gate.

OFF-BY-ONE NOTES — read before modifying
─────────────────────────────────────────
The backtester calls `advance_bar()` unconditionally at the end of EVERY bar,
including the bar on which the cooldown is triggered (the exit bar).

To deliver exactly `cooldown_bars` days of suppression starting from the bar
AFTER the exit, `trigger_cooldown` initialises `cooldown_remaining` to
`cooldown_bars + 1`. The +1 is immediately consumed by the end-of-exit-bar
`advance_bar()` call, leaving `cooldown_bars` on the first bar where a buy
signal could appear.

Verification for cooldown_bars = 7, triggered on day X:
  End of day X:   trigger sets remaining = 8 → advance_bar → 7
  Day X+1:        is_in_cooldown() = (7 > 0) = True → buy suppressed → 6
  Day X+2:        True → suppressed → 5
  …
  Day X+7:        True → suppressed → 0
  Day X+8:        is_in_cooldown() = (0 > 0) = False → buy ALLOWED  ✓
  (7 suppressed days, first allowed buy on X+8)
"""

from typing import Optional


class CooldownTracker:
    """
    Tracks the cooldown window that follows a risk-manager-driven exit.

    The backtester is responsible for calling:
      trigger_cooldown(exit_reason, date)   — after every RM exit
      advance_bar()                          — unconditionally once per bar
      is_in_cooldown()                       — before every buy decision
    """

    def __init__(self, config: dict):
        """
        Args:
            config dict keys:
              enabled                (bool)  — if False, is_in_cooldown() always returns False
              cooldown_bars          (int)   — number of bars to suppress buys after an RM exit
              cooldown_after_reasons (list)  — which exit reasons trigger cooldown
              reset_on_strategy_exit (bool)  — reserved for future use; strategy exits
                                               currently never reset the cooldown
        """
        self.enabled = config.get("enabled", True)

        # Treat 0 or negative cooldown_bars as "disabled"
        raw_bars = config.get("cooldown_bars", 7)
        self.cooldown_bars = max(0, int(raw_bars))
        if self.cooldown_bars == 0:
            self.enabled = False   # no-op config → disable cleanly

        self.cooldown_after_reasons: list[str] = config.get(
            "cooldown_after_reasons", ["HARD_STOP", "CHANDELIER", "TIME_STOP"]
        )

        # reset_on_strategy_exit: accepted for forward-compatibility but currently
        # unused — strategy exits never affect cooldown state. The described
        # behaviour (cooldown continues regardless) is always applied.
        self._reset_on_strategy_exit: bool = config.get("reset_on_strategy_exit", True)

        # ── Mutable state ─────────────────────────────────────────────────────
        # See the module-level comment for why +1 is used in trigger_cooldown.
        self.cooldown_remaining: int  = 0
        self.last_exit_reason: Optional[str] = None
        self.last_trigger_date          = None

        # Audit trails — inspected after the backtest
        self.cooldown_history:    list[dict] = []   # every trigger event
        self.suppressed_signals:  list[dict] = []   # every suppressed buy

    # ── Called by the backtester ──────────────────────────────────────────────

    def trigger_cooldown(self, exit_reason: str, bar_date) -> None:
        """
        Activate the cooldown gate after a risk-manager exit.

        Only activates if `exit_reason` is in `cooldown_after_reasons`.
        May be called for every exit; internally filters by reason so the
        caller doesn't need to pre-filter.

        The initialisation sets `cooldown_remaining = cooldown_bars + 1` to
        account for the `advance_bar()` call that fires at the end of this
        same bar (see module docstring).
        """
        if not self.enabled:
            return
        if exit_reason not in self.cooldown_after_reasons:
            # e.g. "STRATEGY_SIGNAL" or "END_OF_DATA" — never triggers cooldown
            return

        self.cooldown_remaining = self.cooldown_bars + 1   # +1: absorbs end-of-exit-bar advance
        self.last_exit_reason   = exit_reason
        self.last_trigger_date  = bar_date

        self.cooldown_history.append({
            "trigger_date":  bar_date,
            "exit_reason":   exit_reason,
            "cooldown_bars": self.cooldown_bars,
        })

    def advance_bar(self) -> None:
        """
        Decrement the cooldown counter by one bar.

        Called unconditionally at the end of every bar in the backtester loop,
        regardless of whether an exit happened or a signal was suppressed.
        Never decrements below zero.
        """
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

    def is_in_cooldown(self) -> bool:
        """
        True if buy signals should be suppressed on the current bar.
        False if disabled, if cooldown_bars=0, or if the window has elapsed.
        """
        if not self.enabled:
            return False
        return self.cooldown_remaining > 0

    def record_suppressed_signal(self, date, close_price: float) -> None:
        """
        Log a buy signal that was blocked by the cooldown.
        Called by the backtester immediately before suppressing the buy.
        """
        self.suppressed_signals.append({
            "date":               date,
            "close_price":        close_price,
            "cooldown_remaining": self.cooldown_remaining,
            "last_exit_reason":   self.last_exit_reason,
        })

    def reset(self) -> None:
        """Manually clear the cooldown (rarely needed; available for testing)."""
        self.cooldown_remaining = 0
        self.last_exit_reason   = None
        self.last_trigger_date  = None

    def get_status(self) -> dict:
        """Snapshot of current cooldown state — for debugging and logging."""
        return {
            "enabled":            self.enabled,
            "in_cooldown":        self.is_in_cooldown(),
            "cooldown_remaining": self.cooldown_remaining,
            "last_exit_reason":   self.last_exit_reason,
            "last_trigger_date":  self.last_trigger_date,
            "total_triggers":     len(self.cooldown_history),
            "total_suppressed":   len(self.suppressed_signals),
        }
