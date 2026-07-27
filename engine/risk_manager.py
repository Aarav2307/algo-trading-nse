"""
engine/risk_manager.py — Layered Dynamic Risk Management

Four independent exit layers that each run EVERY BAR a position is open,
BEFORE the strategy signal is evaluated. If any layer triggers, the position
is sold immediately at the current close and the strategy signal is skipped.

Layer ordering (applied in this order so a cheaper check gates a costlier one):
  Layer 1 — Hard Catastrophic Stop   : fixed % loss from entry, never overridden
  Layer 2 — ATR Chandelier Exit      : trailing stop based on ATR, ratchets upward
  Layer 3 — Time Stop                : exit after N bars regardless of P&L
  Layer 4 — Round Number Offset      : adjusts the Layer 2 stop placement (not a
                                       separate trigger; modifies the Chandelier level)

All four layers are individually enable/disable-able via the config dict.

Default config:
    {
        "hard_stop_pct":           -0.20,   # -20% from entry
        "atr_period":               22,     # Wilder's ATR lookback
        "atr_multiplier":            3.0,   # Chandelier = highest_high - 3×ATR
        "max_bars_held":            60,     # ~3 months before time-stop
        "round_number_offset_pct":   0.01,  # push stop 1% below Indian round levels
        "enable_layer_1":           True,
        "enable_layer_2":           True,
        "enable_layer_3":           True,
        "enable_layer_4":           True,
    }

API (designed for future Phase 2 live-trading integration):
    on_position_open(entry_price, entry_date, ohlcv_history) → None
    check_exit(current_bar, ohlcv_history)                   → dict
    on_position_close()                                      → None
    get_current_stop_levels()                                → dict
    to_state() / resume_from_state()                         → resume mid-trade
                                                                 across a process restart
"""

import math
import numpy as np
import pandas as pd
from typing import Optional


# ── Default configuration ─────────────────────────────────────────────────────

_DEFAULTS = {
    "hard_stop_pct":           -0.20,
    "atr_period":               22,
    "atr_multiplier":            3.0,
    "max_bars_held":            60,
    "round_number_offset_pct":   0.01,
    "enable_layer_1":           True,
    "enable_layer_2":           True,
    "enable_layer_3":           True,
    "enable_layer_4":           True,
}


class RiskManager:
    """
    Stateful risk manager. One instance per backtest run. State is reset
    via on_position_open() / on_position_close() each trade.
    """

    def __init__(self, config: dict):
        """
        Merge caller config over defaults so partial dicts work fine.

        config may be a subset of _DEFAULTS — missing keys use defaults.
        The 'enabled' key is handled by the caller (backtester/run_backtest),
        not here; by the time __init__ runs, we're committed to using RM.
        """
        self._cfg = {**_DEFAULTS, **config}

        # ── Position state — reset by on_position_open / on_position_close ──
        self._entry_price:             Optional[float] = None
        self._entry_date                               = None
        self._bars_since_entry:        int             = 0
        self._highest_high_since_entry: Optional[float] = None

        # Chandelier stop level.
        # Initialised to -∞ so the first real calculation always wins
        # in the max(..., prev) ratchet.
        self._chandelier_stop: float = -math.inf

    # ── Position lifecycle ────────────────────────────────────────────────────

    def on_position_open(
        self,
        entry_price: float,
        entry_date,
        ohlcv_history: pd.DataFrame,
    ) -> None:
        """
        Call this immediately after a buy order is filled.

        Sets the baseline state for all four layers:
          - entry_price drives Layer 1's hard stop level
          - the entry bar's high seeds Layer 2's highest-high tracker
          - bars_since_entry resets to 0 for Layer 3's time stop
        """
        self._entry_price              = entry_price
        self._entry_date               = entry_date
        self._bars_since_entry         = 0
        self._chandelier_stop          = -math.inf

        # Seed with entry bar's high so the very first check_exit call has a
        # valid highest-high even before the second bar.
        self._highest_high_since_entry = float(ohlcv_history["high"].iloc[-1])

    def on_position_close(self) -> None:
        """
        Call this immediately after any sell (strategy or RM-triggered).
        Clears all position state so stale values don't leak into the next trade.
        """
        self._entry_price              = None
        self._entry_date               = None
        self._bars_since_entry         = 0
        self._highest_high_since_entry = None
        self._chandelier_stop          = -math.inf

    # ── Resume across a process restart ───────────────────────────────────────
    # A live paper-trading run is a fresh process each day — on_position_open()
    # can't be used to resume a position because it resets bars_since_entry to 0.
    # This pair lets a caller persist and restore the exact mid-trade state
    # instead of reaching into private attributes.

    @classmethod
    def resume_from_state(
        cls,
        config: dict,
        entry_price: float,
        entry_date,
        bars_since_entry: int,
        highest_high_since_entry: float,
        chandelier_stop: Optional[float],
    ) -> "RiskManager":
        """
        Reconstruct a RiskManager mid-trade from previously persisted state,
        continuing from exactly where check_exit() left off. Pair with
        to_state() to round-trip across a process restart.

        Args:
            chandelier_stop: None means "not yet computed" (ATR warm-up) —
                              mirrors the -inf sentinel used internally.
        """
        rm = cls(config)
        rm._entry_price              = float(entry_price)
        rm._entry_date               = entry_date
        rm._bars_since_entry         = int(bars_since_entry)
        rm._highest_high_since_entry = float(highest_high_since_entry)
        rm._chandelier_stop          = (
            -math.inf if chandelier_stop is None else float(chandelier_stop)
        )
        return rm

    def to_state(self) -> dict:
        """
        Serialize the state check_exit() mutates every bar, for persisting
        across a process restart. Pair with resume_from_state().

        Returns:
            {
                "entry_price":              float,
                "entry_date":               same type passed to on_position_open(),
                "bars_since_entry":         int,
                "highest_high_since_entry": float,
                "chandelier_stop":          float | None,  # None = ATR warm-up
            }
        """
        return {
            "entry_price":              self._entry_price,
            "entry_date":                self._entry_date,
            "bars_since_entry":          self._bars_since_entry,
            "highest_high_since_entry":  self._highest_high_since_entry,
            "chandelier_stop": (
                None if math.isinf(self._chandelier_stop) else self._chandelier_stop
            ),
        }

    # ── Per-bar exit check ────────────────────────────────────────────────────

    def check_exit(
        self,
        current_bar: pd.Series,
        ohlcv_history: pd.DataFrame,
    ) -> dict:
        """
        Evaluate all enabled layers against the current bar.

        Must be called EVERY bar a position is open, BEFORE the strategy
        signal is evaluated. Layers are checked in order; the first that
        triggers causes an immediate return — cheaper checks gate costlier ones.

        Args:
            current_bar:    pd.Series row from the OHLCV DataFrame (the bar
                            being processed today). Must include 'high', 'low',
                            'close' keys.
            ohlcv_history:  Full OHLCV DataFrame sliced up to AND INCLUDING
                            current_bar (i.e., df.iloc[:i+1]). Used for ATR
                            computation. No look-ahead: the last row IS
                            current_bar.

        Returns:
            {
                "should_exit":     bool,
                "exit_reason":     str | None,   # "HARD_STOP" | "CHANDELIER" | "TIME_STOP"
                "exit_price":      float | None,  # execution price if exiting
                "layer_triggered": int | None,    # 1, 2, or 3
            }
        """
        # ── Increment counter FIRST so bar-0 of a position is bar 1 here.
        # (on_position_open is called after the buy, so the entry bar itself
        #  is never passed to check_exit — the first call is the next bar.)
        self._bars_since_entry += 1

        current_close = float(current_bar["close"])
        current_high  = float(current_bar["high"])

        # Update highest-high ratchet every bar (strictly increasing)
        self._highest_high_since_entry = max(
            self._highest_high_since_entry, current_high
        )

        # ── Layer 1: Hard Catastrophic Stop ──────────────────────────────────
        # Fixed percentage loss from slippage-adjusted entry price.
        # This is the "last line of defence" — runs even during ATR warm-up.
        if self._cfg["enable_layer_1"]:
            loss_pct = (current_close - self._entry_price) / self._entry_price
            if loss_pct <= self._cfg["hard_stop_pct"]:
                # Current price has fallen to or below the hard stop level.
                # Exit at current close — no further layers evaluated.
                return {
                    "should_exit":     True,
                    "exit_reason":     "HARD_STOP",
                    "exit_price":      current_close,
                    "layer_triggered": 1,
                }

        # ── Layer 2: ATR Chandelier Exit ─────────────────────────────────────
        if self._cfg["enable_layer_2"]:
            atr = self._compute_wilder_atr(ohlcv_history, self._cfg["atr_period"])

            if not math.isnan(atr):
                # ── Chandelier formula ──────────────────────────────────────
                # Chandelier Long Stop = highest_high_since_entry − multiplier × ATR
                #
                # Intuition: the highest high is "where we topped out". ATR
                # measures average daily price swing. Multiplier × ATR is
                # how much breathing room we give the price before calling
                # the trend broken. 3.0 × ATR is the standard value; it
                # allows for normal daily fluctuation while catching genuine
                # reversals.
                raw_chandelier = (
                    self._highest_high_since_entry
                    - self._cfg["atr_multiplier"] * atr
                )

                # ── Layer 4: Round Number Offset ────────────────────────────
                # (Adjusts Chandelier placement only — not a separate trigger)
                # Applied BEFORE the ratchet so it can only lower the initial
                # placement, and the ratchet then enforces it only goes up.
                adjusted_chandelier = self._apply_round_number_offset(raw_chandelier)

                # ── Ratchet: Chandelier stop only ever moves UP ─────────────
                # If the adjusted level is lower than a previously computed stop
                # (because ATR spiked), we keep the higher previous stop.
                # This "locks in" profit protection as the trade moves in our favour.
                self._chandelier_stop = max(
                    self._chandelier_stop, adjusted_chandelier
                )

                # ── Trigger check ────────────────────────────────────────────
                if current_close <= self._chandelier_stop:
                    return {
                        "should_exit":     True,
                        "exit_reason":     "CHANDELIER",
                        "exit_price":      current_close,
                        "layer_triggered": 2,
                    }
            # else: ATR warm-up period — not enough data; Layer 1 already checked

        # ── Layer 3: Time Stop ────────────────────────────────────────────────
        # Exit after N bars regardless of P&L.
        # Purpose: free capital from dead/stalled trades.
        if self._cfg["enable_layer_3"]:
            if self._bars_since_entry >= self._cfg["max_bars_held"]:
                return {
                    "should_exit":     True,
                    "exit_reason":     "TIME_STOP",
                    "exit_price":      current_close,
                    "layer_triggered": 3,
                }

        # No layer triggered — hold the position
        return {
            "should_exit":     False,
            "exit_reason":     None,
            "exit_price":      None,
            "layer_triggered": None,
        }

    # ── Inspection ────────────────────────────────────────────────────────────

    def compute_chandelier(
        self,
        ohlcv_history: pd.DataFrame,
        highest_high: float,
    ) -> float | None:
        """
        Return the Chandelier stop level for the given highest_high and history
        WITHOUT modifying any internal state.

        Used by the backtester's position_sizer path immediately before
        on_position_open() so the sizer has a valid Chandelier at entry.
        Keeping this separate from on_position_open() ensures the all-in path
        (position sizing disabled) is bit-identical to the pre-sizing baseline —
        on_position_open() is called at the same point in both paths.

        Returns None if Layer 2 is disabled or ATR data is insufficient.
        """
        if not self._cfg["enable_layer_2"]:
            return None
        atr = self._compute_wilder_atr(ohlcv_history, self._cfg["atr_period"])
        if math.isnan(atr):
            return None
        raw = highest_high - self._cfg["atr_multiplier"] * atr
        return self._apply_round_number_offset(raw)

    def get_current_stop_levels(self) -> dict:
        """
        Return all active stop levels for the current bar — useful for
        logging, debugging, and the Phase 2 live-trading UI.
        """
        if self._entry_price is None:
            return {"status": "no_open_position"}

        hard_stop_abs = self._entry_price * (1.0 + self._cfg["hard_stop_pct"])
        chandelier    = self._chandelier_stop if not math.isinf(self._chandelier_stop) else None
        time_stop_bar = self._cfg["max_bars_held"]

        return {
            "status":                   "position_open",
            "entry_price":              self._entry_price,
            "bars_held":                self._bars_since_entry,
            "highest_high_since_entry": self._highest_high_since_entry,
            "layer_1_hard_stop":        round(hard_stop_abs, 2),
            "layer_2_chandelier":       round(chandelier, 2) if chandelier is not None else None,
            "layer_3_time_stop_at_bar": time_stop_bar,
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_wilder_atr(ohlcv: pd.DataFrame, period: int) -> float:
        """
        Compute Wilder's Average True Range for the final bar of `ohlcv`.

        ── What is ATR? ──────────────────────────────────────────────────────
        ATR measures the AVERAGE DAILY PRICE RANGE — how much the price
        typically moves in a single session, including overnight gaps.

        ── True Range (TR) ──────────────────────────────────────────────────
        For each bar we take the LARGEST of three measures:
          TR = max(
                   High  − Low,              ← pure intraday range
                   |High − prev_Close|,      ← gap-up: range if open was prev close
                   |Low  − prev_Close|       ← gap-down: range if open was prev close
               )
        This captures the "true" price swing including any overnight gap.

        ── Wilder's Smoothing ────────────────────────────────────────────────
        Wilder used a DIFFERENT initialisation from standard EMA:
          First ATR = arithmetic mean of the first `period` TR values
                      (i.e. a simple average as seed)
          Subsequent ATR[t] = (ATR[t-1] × (period − 1) + TR[t]) / period

        This is mathematically equivalent to EWM with alpha=1/period but with
        a seed from the mean of the first N bars rather than the first bar.
        The difference matters during the warm-up: standard EWM gives too much
        weight to the very first bar, whereas Wilder's seed is smoother.

        Args:
            ohlcv:  DataFrame with 'high', 'low', 'close' columns, indexed by
                    date. Must include at least (period + 1) rows.
            period: Lookback period (default 22 for Chandelier Exit).

        Returns:
            ATR as a float. Returns NaN if there is insufficient data.
        """
        high  = ohlcv["high"].values
        low   = ohlcv["low"].values
        close = ohlcv["close"].values
        n     = len(close)

        # We need prev_close for TR, so minimum 2 bars.
        # We need at least `period` TR values for the initial simple mean seed.
        if n < period + 1:
            return float("nan")

        # ── Compute True Range for every bar from index 1 onward ─────────
        # tr[k] corresponds to OHLCV row k+1 (uses row k as previous close).
        tr = np.empty(n - 1)
        for k in range(1, n):
            hl  = high[k]  - low[k]
            hpc = abs(high[k]  - close[k - 1])
            lpc = abs(low[k]   - close[k - 1])
            tr[k - 1] = max(hl, hpc, lpc)

        if len(tr) < period:
            return float("nan")

        # ── Wilder's ATR via recursive smoothing ─────────────────────────
        # Step 1: seed = simple mean of the first `period` TR values
        atr = float(tr[:period].mean())

        # Step 2: apply the recursive formula forward
        # Each step: ATR = (ATR × (period-1) + TR) / period
        # This progressively down-weights old values with a fixed decay of
        # (period-1)/period per bar.
        for val in tr[period:]:
            atr = (atr * (period - 1) + float(val)) / period

        return atr

    def _apply_round_number_offset(self, chandelier: float) -> float:
        """
        Layer 4 — adjust Chandelier stop if it sits within 1% of an Indian
        psychological round-number level.

        ── Why round numbers matter in Indian markets ────────────────────────
        High-frequency and algo traders in NSE commonly probe round numbers
        (₹500, ₹1000, ₹5000, etc.) during intraday sessions looking for
        clusters of stop-loss orders. Retail traders naturally place stops
        "just below ₹500" or "just below ₹1000" — exactly where the Chandelier
        formula tends to land. By pushing our stop 1% below the round level
        we avoid the most common probe zones.

        ── Round levels used ─────────────────────────────────────────────────
        Price < ₹1000   → nearest multiple of   ₹50  (e.g. 500, 550, 600 …)
        ₹1000–₹5000     → nearest multiple of  ₹100  (e.g. 1000, 1100, 1200 …)
        Price > ₹5000   → nearest multiple of  ₹500  (e.g. 5000, 5500, 6000 …)

        ── Adjustment rule ──────────────────────────────────────────────────
        If |chandelier − nearest_round| / nearest_round ≤ offset (1%):
            adjusted = nearest_round × (1 − offset)   ← 1% BELOW the round level
        Else:
            adjusted = chandelier unchanged

        The adjusted value is always ≤ chandelier (we only push the stop
        further away, never closer to current price). If the ratchet has
        already locked in a higher stop, that higher level takes precedence.

        Args:
            chandelier: Raw chandelier stop level (₹).

        Returns:
            Adjusted chandelier stop level (₹).
        """
        if not self._cfg["enable_layer_4"] or chandelier <= 0:
            return chandelier

        # ── Find the nearest Indian psychological price level ─────────────
        if chandelier < 1_000:
            step = 50
        elif chandelier < 5_000:
            step = 100
        else:
            step = 500

        # Two candidate round numbers: the one below and the one above
        lower_round = (chandelier // step) * step
        upper_round = lower_round + step

        if abs(chandelier - lower_round) <= abs(chandelier - upper_round):
            nearest_round = float(lower_round)
        else:
            nearest_round = float(upper_round)

        if nearest_round == 0:
            return chandelier   # guard against ₹0 edge case

        # ── Within-1% check ───────────────────────────────────────────────
        proximity = abs(chandelier - nearest_round) / nearest_round
        offset    = self._cfg["round_number_offset_pct"]

        if proximity <= offset:
            # Push stop 1% below the round level so it sits in the "safe zone"
            # below the HFT probe window.
            adjusted = nearest_round * (1.0 - offset)
            # Safety: only lower the stop, never raise it via Layer 4
            return min(adjusted, chandelier)

        return chandelier
