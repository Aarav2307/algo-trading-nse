"""
Portfolio — tracks cash, open position, and completed trades.

Responsibilities:
  - Execute buy/sell orders (applies slippage + costs from utils/costs.py)
  - Track current cash and share holdings
  - Record every completed round-trip trade to the trade log
  - Compute current portfolio value (cash + mark-to-market position)

Design note: Portfolio is stateful but has no idea what date it is or what
signals are coming — the Backtester drives it bar by bar.
"""

import pandas as pd
from utils.costs import apply_slippage, transaction_costs


class Portfolio:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash = initial_capital

        # Current open position (only one stock at a time in this version)
        self.shares_held = 0
        self.entry_price = None       # slippage-adjusted price we actually paid
        self.entry_date = None
        self.entry_raw_price = None   # close price before slippage (for reporting)

        # Completed trades — each entry is a dict
        self.trade_log: list[dict] = []

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def buy(self, date, close_price: float, shares: int | None = None):
        """
        Open a long position.

        Args:
            shares: If None (default), invests all available cash (original
                    all-in behaviour). If provided, buys exactly that many
                    shares — used when the position sizer pre-computes quantity.
        """
        if self.shares_held > 0:
            return  # already in a position, ignore duplicate signals

        exec_price = apply_slippage(close_price, "buy")

        if shares is None:
            # All-in: original behaviour — buy as many whole shares as cash allows.
            from utils.costs import BROKERAGE_PER_ORDER
            affordable_cash = self.cash - BROKERAGE_PER_ORDER
            if affordable_cash <= 0:
                print(f"  [portfolio] {date.date()}: not enough cash to buy (₹{self.cash:.2f})")
                return
            shares = int(affordable_cash / exec_price)

        if shares == 0:
            return

        cost        = transaction_costs(exec_price, shares, "buy", "delivery")
        total_spent = shares * exec_price + cost

        # Safety guard: position sizer subtracts brokerage, so this should
        # only fire in a float precision edge case.
        if total_spent > self.cash:
            shares = max(0, int((self.cash - cost) / exec_price))
            if shares == 0:
                return
            cost        = transaction_costs(exec_price, shares, "buy", "delivery")
            total_spent = shares * exec_price + cost

        self.cash        -= total_spent
        self.shares_held  = shares
        self.entry_price  = exec_price
        self.entry_date   = date
        self.entry_raw_price = close_price

        print(
            f"  BUY  {date.date()} | {shares} shares @ ₹{exec_price:.2f} "
            f"(close ₹{close_price:.2f}) | cost ₹{cost:.2f} | cash left ₹{self.cash:.2f}"
        )

    def sell(self, date, close_price: float, reason: str = "STRATEGY_SIGNAL"):
        """
        Close the open long position.

        Args:
            reason: Why the position was closed. One of:
                    "STRATEGY_SIGNAL" — normal exit from the strategy
                    "HARD_STOP"       — Layer 1 risk manager triggered
                    "CHANDELIER"      — Layer 2 ATR Chandelier triggered
                    "TIME_STOP"       — Layer 3 time stop triggered
        """
        if self.shares_held == 0:
            return  # nothing to sell

        exec_price = apply_slippage(close_price, "sell")
        cost = transaction_costs(exec_price, self.shares_held, "sell", "delivery")
        proceeds = self.shares_held * exec_price - cost

        self.cash += proceeds

        # Record the trade — exit_reason lets us audit which exits were
        # risk-manager-driven vs strategy-driven in the trade log.
        gross_pnl = (exec_price - self.entry_price) * self.shares_held
        net_pnl = gross_pnl - cost  # entry costs were already deducted at buy time
        trade = {
            "entry_date":  self.entry_date,
            "exit_date":   date,
            "entry_price": self.entry_price,
            "exit_price":  exec_price,
            "shares":      self.shares_held,
            "gross_pnl":   gross_pnl,
            "net_pnl":     net_pnl,
            "return_pct":  (exec_price / self.entry_price - 1) * 100,
            "exit_reason": reason,
        }
        self.trade_log.append(trade)

        reason_tag = f" [{reason}]" if reason != "STRATEGY_SIGNAL" else ""
        print(
            f"  SELL {date.date()} | {self.shares_held} shares @ ₹{exec_price:.2f} "
            f"(close ₹{close_price:.2f}) | P&L ₹{net_pnl:+.2f} ({trade['return_pct']:+.2f}%)"
            f"{reason_tag}"
        )

        self.shares_held = 0
        self.entry_price = None
        self.entry_date = None
        self.entry_raw_price = None

    # ------------------------------------------------------------------
    # Valuation
    # ------------------------------------------------------------------

    def current_value(self, current_price: float) -> float:
        """Portfolio value = cash + mark-to-market value of open position."""
        return self.cash + self.shares_held * current_price

    def get_trade_log(self) -> pd.DataFrame:
        if not self.trade_log:
            return pd.DataFrame()
        return pd.DataFrame(self.trade_log)
