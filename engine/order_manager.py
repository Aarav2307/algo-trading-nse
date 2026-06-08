"""
engine/order_manager.py — AMO (After Market Order) manager.

In dry-run mode (the default and only safe mode), every order is logged to a
CSV file and returned as a dict. No real orders are ever placed.

The live-order path (dry_run=False) is intentionally left as a NotImplementedError
stub. To enable live trading in the future, remove the assertion below, implement
the kite.place_order() call, and conduct thorough testing on a paper account first.

SAFETY CONTRACT:
  config["dry_run"] must be True. The assertion on line ~60 enforces this at
  construction time. Removing it is the deliberate act required to go live —
  there is no accidental path to real order placement.

CSV log (paper_trading/amo_orders.csv):
  date, ticker, order_type, signal_price, limit_price, shares,
  status, fill_price, fill_date, notes
"""

import csv
import os
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional


# ── Column header for the CSV log ─────────────────────────────────────────────
_CSV_HEADER = [
    "date", "ticker", "order_type", "signal_price", "limit_price",
    "shares", "status", "fill_price", "fill_date", "notes",
]


class AMOOrderManager:
    """
    Logs simulated AMO orders to CSV and provides a morning-fill-check interface.

    Design:
      - One instance per daily run in signal_runner.py.
      - place_buy_amo / place_sell_amo append a row to the CSV and return the
        order dict immediately.
      - morning_fill_check.py reads yesterday's rows (status="DRY_RUN") and
        updates them with the actual open price to simulate fill confirmation.
      - When dry_run=False (future live mode), this class would call
        kite.place_order() — but that path is not implemented yet.
    """

    def __init__(self, config: dict):
        """
        Args:
            config dict keys:
              enabled          (bool)  — if False, place_*_amo() are no-ops
              dry_run          (bool)  — MUST be True; guards against accidental live orders
              limit_buffer_pct (float) — e.g. 0.005 adds a 0.5% buffer to the signal price
                                         BUY  limit = signal_price × (1 + buffer)
                                         SELL limit = signal_price × (1 − buffer)
              order_log_file   (str)   — path to the AMO order CSV log
        """
        self.enabled          = config.get("enabled", True)
        self.dry_run          = config.get("dry_run", True)
        self.limit_buffer_pct = config.get("limit_buffer_pct", 0.005)
        self.order_log_file   = Path(config.get("order_log_file",
                                                 "paper_trading/amo_orders.csv"))

        # ── SAFETY: refuse to construct if dry_run is disabled ────────────────
        # This assertion is the deliberate gate between paper and live trading.
        # To go live: implement the kite.place_order() path, test thoroughly,
        # then remove this assertion with full awareness of what you're doing.
        assert self.dry_run, (
            "LIVE TRADING NOT ENABLED. "
            "AMOOrderManager.dry_run must be True. "
            "Real order placement is not implemented."
        )

        self._todays_orders: List[dict] = []   # in-memory log for the current run
        self._ensure_csv_header()

    # ── Public API ─────────────────────────────────────────────────────────────

    def place_buy_amo(
        self,
        ticker: str,
        shares: int,
        signal_price: float,
        order_date: date,
        notes: str = "",
    ) -> dict:
        """
        Log a BUY AMO order (dry-run only).

        Limit price = signal_price × (1 + limit_buffer_pct).
        A BUY limit is set ABOVE signal price so a small gap-up still fills.
        The morning fill check considers the order filled if:
            open_price <= limit_price

        Returns the order dict (same row that is written to CSV).
        """
        if not self.enabled:
            return {}

        limit_price = round(signal_price * (1 + self.limit_buffer_pct), 2)
        order = {
            "date":         order_date.isoformat(),
            "ticker":       ticker,
            "order_type":   "BUY",
            "signal_price": round(signal_price, 4),
            "limit_price":  limit_price,
            "shares":       shares,
            "status":       "DRY_RUN",
            "fill_price":   "",      # populated by morning_fill_check.py
            "fill_date":    "",      # populated by morning_fill_check.py
            "notes":        notes,
        }
        self.log_order(order)
        return order

    def place_sell_amo(
        self,
        ticker: str,
        shares: int,
        signal_price: float,
        order_date: date,
        notes: str = "",
    ) -> dict:
        """
        Log a SELL AMO order (dry-run only).

        Limit price = signal_price × (1 − limit_buffer_pct).
        A SELL limit is set BELOW signal price so a small gap-down still fills.
        The morning fill check considers the order filled if:
            open_price >= limit_price

        Returns the order dict.
        """
        if not self.enabled:
            return {}

        limit_price = round(signal_price * (1 - self.limit_buffer_pct), 2)
        order = {
            "date":         order_date.isoformat(),
            "ticker":       ticker,
            "order_type":   "SELL",
            "signal_price": round(signal_price, 4),
            "limit_price":  limit_price,
            "shares":       shares,
            "status":       "DRY_RUN",
            "fill_price":   "",
            "fill_date":    "",
            "notes":        notes,
        }
        self.log_order(order)
        return order

    def get_todays_orders(self) -> List[dict]:
        """Return all orders placed during the current run (in-memory only)."""
        return list(self._todays_orders)

    def log_order(self, order: dict) -> None:
        """Append one order row to the CSV log and to the in-memory list."""
        self._todays_orders.append(order)
        with open(self.order_log_file, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CSV_HEADER)
            writer.writerow(order)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _ensure_csv_header(self) -> None:
        """Create the CSV with a header row if it doesn't exist yet."""
        if not self.order_log_file.exists():
            self.order_log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.order_log_file, "w", newline="") as fh:
                csv.DictWriter(fh, fieldnames=_CSV_HEADER).writeheader()
