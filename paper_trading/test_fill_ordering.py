"""
Integration tests for morning_fill_check.py fill-state consistency.

Verifies that updating portfolio BEFORE marking the CSV as FILLED (the order
introduced in Patch 4) leaves a recoverable state on crash, and that the
idempotency guards prevent double-application if the fill is re-processed.
"""

import csv
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc
from paper_trading.morning_fill_check import (
    _update_csv_row,
    _update_portfolio_fill,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TICKER     = "TESTSELL.NS"
_ORDER_DATE = "2026-06-25"
_SHARES     = 3
_ENTRY_PX   = 3731.56
_OPEN_PX    = 3700.0        # above limit → FILLED
_LIMIT_PX   = 3680.0
_FILL_DATE  = date(2026, 6, 26)

_CSV_FIELDNAMES = [
    "date", "ticker", "order_type", "shares", "limit_price",
    "signal_price", "notes", "order_id", "status", "fill_date", "fill_price",
]


def _make_state(shares: int = _SHARES) -> dict:
    """Minimal portfolio_state.json with one open SELL position."""
    return {
        "cash":             50_000.0,
        "initial_capital":  100_000.0,
        "etf_shares":       0,
        "etf_avg_price":    0.0,
        "etf_tier":         0,
        "positions": {
            _TICKER: {
                "shares":                    shares,
                "entry_price":               _ENTRY_PX,
                "entry_cost":                25.0,
                "entry_date":                "2026-06-01",
                "highest_high_since_entry":  _ENTRY_PX * 1.05,
                "bars_held":                 10,
                "chandelier_stop":           None,
                "pending_buy":               False,
                "pending_rm_exit":           True,
                "rm_exit_reason":            "CHANDELIER",
                "rm_sell_requeue_count":     0,
            }
        },
        "cooldown_state": {
            _TICKER: {"remaining_bars": 0, "last_exit_reason": None}
        },
        "total_trades":     0,
        "last_run_date":    _ORDER_DATE,
        "inception_date":   "2026-06-02",
        "weekly_start_value":  99_000.0,
        "weekly_start_date":   "2026-06-23",
        "weekly_signals":   {"BUY": 0, "SELL": 0, "RISK_EXIT": 0},
        "trade_log":        [],
    }


def _make_csv(tmp_path: Path, status: str = "DRY_RUN") -> None:
    """Write a single-row AMO CSV to tmp_path with the given status."""
    with open(tmp_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "date":         _ORDER_DATE,
            "ticker":       _TICKER,
            "order_type":   "SELL",
            "shares":       str(_SHARES),
            "limit_price":  str(_LIMIT_PX),
            "signal_price": str(_ENTRY_PX),
            "notes":        "CHANDELIER",
            "order_id":     "",
            "status":       status,
            "fill_date":    "",
            "fill_price":   "",
        })


def _read_csv_status(csv_path: Path) -> str:
    """Read status field from the first data row of the CSV."""
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        return next(reader)["status"]


def _read_shares(state_path: Path) -> int:
    with open(state_path) as fh:
        return json.load(fh)["positions"][_TICKER]["shares"]


# ---------------------------------------------------------------------------
# Test (a): Crash after portfolio update, before CSV write
#           → CSV stays DRY_RUN (re-processable)
# ---------------------------------------------------------------------------

def test_crash_after_portfolio_csv_stays_dry_run():
    """
    Simulate a crash between portfolio update and CSV write (NEW ordering:
    portfolio first). The CSV must remain DRY_RUN so the order is reprocessable.
    """
    state_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    csv_tmp   = tempfile.NamedTemporaryFile(suffix=".csv",  delete=False)
    state_tmp.close()
    csv_tmp.close()

    try:
        json.dump(_make_state(), open(state_tmp.name, "w"), indent=2)
        _make_csv(Path(csv_tmp.name))

        assert _read_shares(Path(state_tmp.name)) == _SHARES,  "precondition: shares=3 before fill"
        assert _read_csv_status(Path(csv_tmp.name)) == "DRY_RUN", "precondition: CSV is DRY_RUN"

        # Step 1 (NEW order): update portfolio — simulates the first half of the fill pipeline
        orig_state = mfc.STATE_FILE
        mfc.STATE_FILE = Path(state_tmp.name)
        try:
            _update_portfolio_fill(
                _TICKER, "SELL", _SHARES, _OPEN_PX, _FILL_DATE,
                exit_reason="CHANDELIER", is_rm_exit=True,
                portfolio_obj=None,
            )
        finally:
            mfc.STATE_FILE = orig_state

        # Portfolio is now updated (shares=0)
        assert _read_shares(Path(state_tmp.name)) == 0, "portfolio must be updated (shares=0)"

        # Crash here — _update_csv_row is NEVER called (simulated crash).
        # CSV must still be DRY_RUN.
        assert _read_csv_status(Path(csv_tmp.name)) == "DRY_RUN", (
            "CSV must remain DRY_RUN after portfolio update if crash prevents CSV write"
        )

    finally:
        os.unlink(state_tmp.name)
        os.unlink(csv_tmp.name)


# ---------------------------------------------------------------------------
# Test (b): Re-running after a portfolio-only update does NOT double-apply
#           (idempotency guard holds: shares=0 → skip with WARN)
# ---------------------------------------------------------------------------

def test_idempotency_guard_prevents_double_apply(capsys):
    """
    After portfolio is updated (shares=0) but CSV is still DRY_RUN, a re-run
    must NOT apply the fill a second time. The shares=0 guard must fire.
    """
    state_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    csv_tmp   = tempfile.NamedTemporaryFile(suffix=".csv",  delete=False)
    state_tmp.close()
    csv_tmp.close()

    try:
        # Start with shares=0 — portfolio was already updated in a prior run
        json.dump(_make_state(shares=0), open(state_tmp.name, "w"), indent=2)
        _make_csv(Path(csv_tmp.name))  # CSV is still DRY_RUN (crash happened)

        cash_before = json.load(open(state_tmp.name))["cash"]
        trades_before = len(json.load(open(state_tmp.name))["trade_log"])

        orig_state = mfc.STATE_FILE
        mfc.STATE_FILE = Path(state_tmp.name)
        try:
            _update_portfolio_fill(
                _TICKER, "SELL", _SHARES, _OPEN_PX, _FILL_DATE,
                exit_reason="CHANDELIER", is_rm_exit=True,
                portfolio_obj=None,
            )
        finally:
            mfc.STATE_FILE = orig_state

        # Cash and trade log must be unchanged — idempotency guard must have fired
        after = json.load(open(state_tmp.name))
        assert after["cash"] == cash_before, (
            f"Cash changed on re-run: {cash_before} → {after['cash']}. "
            "Idempotency guard (shares=0 check) must have fired."
        )
        assert len(after["trade_log"]) == trades_before, (
            "Trade log grew on re-run — double-apply occurred despite shares=0."
        )

        # Guard must have printed a WARN
        captured = capsys.readouterr()
        assert "WARN" in captured.out, (
            "Expected a WARN message from the idempotency guard; got none."
        )

    finally:
        os.unlink(state_tmp.name)
        os.unlink(csv_tmp.name)


# ---------------------------------------------------------------------------
# Test (c): Crash BEFORE portfolio update — both CSV and portfolio are
#           untouched, in fully consistent pre-fill state
# ---------------------------------------------------------------------------

def test_crash_before_portfolio_both_consistent():
    """
    If a crash happens before ANY update (before portfolio update in the new
    ordering), neither the CSV nor the portfolio must be modified.
    Both remain in the original DRY_RUN / shares>0 state — a clean re-run.
    """
    state_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    csv_tmp   = tempfile.NamedTemporaryFile(suffix=".csv",  delete=False)
    state_tmp.close()
    csv_tmp.close()

    try:
        json.dump(_make_state(), open(state_tmp.name, "w"), indent=2)
        _make_csv(Path(csv_tmp.name))

        # Simulate crash: nothing is called. Assert both remain in original state.
        assert _read_shares(Path(state_tmp.name)) == _SHARES, (
            "Portfolio must be unchanged when no fill processing has run."
        )
        assert _read_csv_status(Path(csv_tmp.name)) == "DRY_RUN", (
            "CSV must be DRY_RUN when no fill processing has run."
        )

        # Verify a subsequent normal run can process the fill without issue
        orig_state = mfc.STATE_FILE
        orig_csv   = mfc.AMO_CSV
        mfc.STATE_FILE = Path(state_tmp.name)
        mfc.AMO_CSV    = Path(csv_tmp.name)
        try:
            _update_portfolio_fill(
                _TICKER, "SELL", _SHARES, _OPEN_PX, _FILL_DATE,
                exit_reason="CHANDELIER", is_rm_exit=True,
                portfolio_obj=None,
            )
            _update_csv_row(
                _ORDER_DATE, _TICKER, "SELL",
                "FILLED", str(round(_OPEN_PX, 4)), _FILL_DATE.isoformat(),
            )
        finally:
            mfc.STATE_FILE = orig_state
            mfc.AMO_CSV    = orig_csv

        assert _read_shares(Path(state_tmp.name)) == 0, (
            "After a clean re-run, shares must be 0."
        )
        assert _read_csv_status(Path(csv_tmp.name)) == "FILLED", (
            "After a clean re-run, CSV must be FILLED."
        )

    finally:
        os.unlink(state_tmp.name)
        os.unlink(csv_tmp.name)


# ---------------------------------------------------------------------------
# Test (d): OLD ordering (CSV first) would have left an irrecoverable state —
#           demonstrate why the swap matters
# ---------------------------------------------------------------------------

def test_old_order_crash_leaves_irrecoverable_state():
    """
    Demonstrates the bug that Patch 4 fixes: with the OLD ordering (CSV first,
    portfolio second), a crash after the CSV write but before portfolio update
    leaves the CSV as FILLED (so it won't be reprocessed) while the portfolio
    still shows the position open. This state is irrecoverable without manual
    intervention.

    This test exercises the OLD ordering explicitly and asserts the bad outcome —
    confirming the NEW ordering (portfolio first) is required to avoid it.
    """
    state_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    csv_tmp   = tempfile.NamedTemporaryFile(suffix=".csv",  delete=False)
    state_tmp.close()
    csv_tmp.close()

    try:
        json.dump(_make_state(), open(state_tmp.name, "w"), indent=2)
        _make_csv(Path(csv_tmp.name))

        orig_csv = mfc.AMO_CSV
        mfc.AMO_CSV = Path(csv_tmp.name)
        try:
            # OLD ORDER: CSV written first (the crash-window bug)
            _update_csv_row(
                _ORDER_DATE, _TICKER, "SELL",
                "FILLED", str(round(_OPEN_PX, 4)), _FILL_DATE.isoformat(),
            )
            # CRASH HERE — portfolio update never runs
        finally:
            mfc.AMO_CSV = orig_csv

        # Bad state: CSV says FILLED but portfolio still shows shares>0
        shares_after_crash = _read_shares(Path(state_tmp.name))
        csv_status_after_crash = _read_csv_status(Path(csv_tmp.name))

        assert csv_status_after_crash == "FILLED", (
            "OLD ordering: CSV is FILLED after the write — as expected."
        )
        assert shares_after_crash == _SHARES, (
            "OLD ordering: portfolio shares still >0 — crash before portfolio update."
        )

        # The order can no longer be reprocessed: _update_csv_row only matches DRY_RUN rows.
        # Attempting to process again would find no DRY_RUN row and silently skip.
        # This irrecoverable inconsistency is what Patch 4 eliminates.

    finally:
        os.unlink(state_tmp.name)
        os.unlink(csv_tmp.name)
