"""
paper_trading/test_morning_fill_check_audit.py — Audit stress tests for
morning_fill_check.py.

NEW FILE — does not modify or overwrite any existing test.

All tests operate on temp-directory copies of amo_orders.csv and
portfolio_state.json. The real repo/live state files are never touched.
No live Kite or NSE calls are made — every fetch is monkeypatched.

Focus: the dry-run contract. morning_fill_check.py's own docstring states
"Dry-run mode (the default): portfolio state is NOT modified", and the report
header prints "MODE: DRY RUN — portfolio state will NOT be modified".
These tests check whether that contract actually holds for the AMO order
ledger (amo_orders.csv), which is the file that decides whether a fill is
still pending.
"""

import csv
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc

_TICKER    = "AUDITSTK.NS"
_ORDER_DAY = "2026-08-20"
_CHECK_DAY = date(2026, 8, 21)

_CSV_HEADER = [
    "date", "ticker", "order_type", "signal_price", "limit_price",
    "shares", "status", "fill_price", "fill_date", "notes", "order_id",
]


def _write_amo_csv(path: Path, rows: list) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _buy_row(status="DRY_RUN"):
    return {
        "date": _ORDER_DAY, "ticker": _TICKER, "order_type": "BUY",
        "signal_price": "1000.00", "limit_price": "1005.00", "shares": "10",
        "status": status, "fill_price": "", "fill_date": "", "notes": "chan=950.0",
        "order_id": "",
    }


def _pending_buy_state() -> dict:
    """Portfolio with one queued (pending_buy) BUY — cash not yet deducted."""
    return {
        "cash":            100_000.0,
        "initial_capital": 100_000.0,
        "etf_shares":      0,
        "etf_avg_price":   0.0,
        "etf_tier":        0,
        "total_trades":    0,
        "trade_log":       [],
        "positions": {
            _TICKER: {
                "shares": 10, "entry_price": 1000.5, "entry_cost": 0.0,
                "entry_date": _ORDER_DAY, "highest_high_since_entry": 0.0,
                "bars_held": 0, "chandelier_stop": None,
                "pending_buy": True, "pending_rm_exit": False,
                "rm_exit_reason": None, "rm_sell_requeue_count": 0,
            }
        },
        "cooldown_state": {_TICKER: {"remaining_bars": 0, "last_exit_reason": None}},
    }


def _run_check(tmpdir: Path, open_px: float, apply_fills: bool, state: dict, rows: list):
    """Run morning_fill_check against temp files with all I/O monkeypatched."""
    csv_path   = tmpdir / "amo_orders.csv"
    state_path = tmpdir / "portfolio_state.json"
    _write_amo_csv(csv_path, rows)
    state_path.write_text(json.dumps(state))

    with patch.object(mfc, "AMO_CSV", csv_path), \
         patch.object(mfc, "STATE_FILE", state_path), \
         patch.object(mfc, "_AMO_ORDER_LOG", csv_path), \
         patch.object(mfc, "_check_auth", lambda: None), \
         patch.object(mfc, "is_trading_day", lambda d: True), \
         patch.object(mfc, "_fetch_open_price", lambda t, d: open_px), \
         patch.object(mfc, "_fetch_prev_close", lambda t, d: 1000.0), \
         patch.object(mfc, "_fetch_close_price", lambda t, d: open_px), \
         patch("utils.corporate_actions.get_corporate_action_warning",
               lambda t, check_date=None: {"skip": False, "reason": None, "ex_date": None}):
        mfc.run_morning_check(check_date=_CHECK_DAY, apply_fills=apply_fills)

    with open(csv_path, newline="") as fh:
        out_rows = list(csv.DictReader(fh))
    out_state = json.loads(state_path.read_text())
    return out_rows, out_state


# =============================================================================
# Test 1 — dry run must not consume a pending BUY fill
# =============================================================================

def test_dry_run_does_not_mark_filled_order_as_filled_in_csv():
    """
    A dry run (no --apply) that sees a fillable BUY must leave the AMO row as
    DRY_RUN so the later --apply run can still process it.

    If the row is flipped to FILLED without the portfolio being updated, the
    order is permanently invisible to _load_pending_orders() (which selects
    only status == "DRY_RUN"), and the fill is silently lost.
    """
    with tempfile.TemporaryDirectory() as td:
        rows, state = _run_check(
            Path(td), open_px=1002.0, apply_fills=False,
            state=_pending_buy_state(), rows=[_buy_row()],
        )

    assert rows[0]["status"] == "DRY_RUN", (
        f"dry run mutated amo_orders.csv: status is {rows[0]['status']!r}, "
        f"expected 'DRY_RUN' to remain reprocessable"
    )


def test_dry_run_then_apply_still_confirms_the_buy_fill():
    """
    End-to-end consequence: dry run first (as the module docstring documents),
    then the real --apply run. The BUY must still be confirmed — cash deducted,
    pending_buy cleared.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        csv_path   = tmp / "amo_orders.csv"
        state_path = tmp / "portfolio_state.json"
        _write_amo_csv(csv_path, [_buy_row()])
        state_path.write_text(json.dumps(_pending_buy_state()))

        patches = dict(
            open_px=1002.0, state=None, rows=None,
        )
        # First: dry run
        with patch.object(mfc, "AMO_CSV", csv_path), \
             patch.object(mfc, "STATE_FILE", state_path), \
             patch.object(mfc, "_check_auth", lambda: None), \
             patch.object(mfc, "is_trading_day", lambda d: True), \
             patch.object(mfc, "_fetch_open_price", lambda t, d: 1002.0), \
             patch.object(mfc, "_fetch_prev_close", lambda t, d: 1000.0), \
             patch("utils.corporate_actions.get_corporate_action_warning",
                   lambda t, check_date=None: {"skip": False, "reason": None, "ex_date": None}):
            mfc.run_morning_check(check_date=_CHECK_DAY, apply_fills=False)

        # Then: the real applying run
        with patch.object(mfc, "AMO_CSV", csv_path), \
             patch.object(mfc, "STATE_FILE", state_path), \
             patch.object(mfc, "_check_auth", lambda: None), \
             patch.object(mfc, "is_trading_day", lambda d: True), \
             patch.object(mfc, "_fetch_open_price", lambda t, d: 1002.0), \
             patch.object(mfc, "_fetch_prev_close", lambda t, d: 1000.0), \
             patch("utils.corporate_actions.get_corporate_action_warning",
                   lambda t, check_date=None: {"skip": False, "reason": None, "ex_date": None}):
            mfc.run_morning_check(check_date=_CHECK_DAY, apply_fills=True)

        final = json.loads(state_path.read_text())

    pos = final["positions"][_TICKER]
    assert pos["pending_buy"] is False, (
        "BUY fill was lost: pending_buy still True after the --apply run, "
        "because the preceding dry run already flipped the CSV row out of DRY_RUN"
    )
    assert final["cash"] < 100_000.0, (
        f"BUY fill was lost: cash still ₹{final['cash']:,.2f} — never deducted"
    )


def test_dry_run_does_not_mark_missed_order_as_missed_in_csv():
    """
    Same contract for the MISSED branch: a dry run must not consume the order.
    In dry run the pending_buy flag is NOT cancelled (that is correctly guarded
    by apply_fills), so flipping the CSV row to MISSED strands the position in
    pending_buy with no order left to reconcile it.
    """
    with tempfile.TemporaryDirectory() as td:
        rows, state = _run_check(
            Path(td), open_px=1050.0, apply_fills=False,   # above limit → MISSED
            state=_pending_buy_state(), rows=[_buy_row()],
        )

    assert rows[0]["status"] == "DRY_RUN", (
        f"dry run mutated amo_orders.csv: status is {rows[0]['status']!r}. "
        f"pending_buy is still True in state, so the position is now stranded."
    )
