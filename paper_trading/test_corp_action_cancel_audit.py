"""
paper_trading/test_corp_action_cancel_audit.py — Finding #12.

A CANCELLED_CA order (ex-date landed inside the fill window) marks the AMO
ledger row terminal but never resets the position it belongs to. Two distinct
failure modes, tested separately because they strand differently:

  BUY  — pending_buy stays True forever. _process_stock() returns early on
         pending_buy, so the ticker is frozen: no signals, no risk management,
         no AMO. Same bricking as audit Finding #3, different route.
  SELL — pending_rm_exit stays True with shares > 0. signal_runner's
         pending_rm_exit branch keeps running check_exit() (chandelier
         ratchets, bars_held climbs) but never sets needs_amo_order, so Step 13
         never emits another SELL AMO. The position has NO code path that can
         close it.

Temp dirs only; the live state files are never opened for writing.
No network: every fetch and the NSE corporate-actions call are monkeypatched.
"""
import csv
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc

_T = "CORPACT.NS"
_ORDER_DAY = "2026-08-24"
_CHECK_DAY = date(2026, 8, 25)

_CSV_HEADER = ["date", "ticker", "order_type", "signal_price", "limit_price",
               "shares", "status", "fill_price", "fill_date", "notes", "order_id"]


def _row(order_type, notes=""):
    return {"date": _ORDER_DAY, "ticker": _T, "order_type": order_type,
            "signal_price": "1000.00", "limit_price": "1005.00" if order_type == "BUY" else "995.00",
            "shares": "10", "status": "DRY_RUN", "fill_price": "", "fill_date": "",
            "notes": notes, "order_id": ""}


def _state(pending_buy: bool):
    return {
        "cash": 100_000.0, "initial_capital": 100_000.0,
        "etf_shares": 0, "etf_avg_price": 0.0, "etf_tier": 0,
        "total_trades": 0, "trade_log": [],
        "positions": {_T: {
            "shares": 10, "entry_price": 1_000.5, "entry_cost": 12.0,
            "entry_date": _ORDER_DAY, "highest_high_since_entry": 1_050.0,
            "bars_held": 3, "chandelier_stop": 960.0,
            "pending_buy": pending_buy,
            "pending_rm_exit": not pending_buy,
            "rm_exit_reason": None if pending_buy else "CHANDELIER",
            "rm_sell_requeue_count": 0,
        }},
        "cooldown_state": {_T: {"remaining_bars": 0, "last_exit_reason": None}},
    }


def _run_with_ex_date_today(tmp_path, order_type, notes, pending_buy):
    """Run the morning check with --apply and an ex-date falling TODAY."""
    csv_path, state_path = tmp_path / "amo.csv", tmp_path / "state.json"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_HEADER); w.writeheader()
        w.writerow(_row(order_type, notes))
    state_path.write_text(json.dumps(_state(pending_buy)))

    ca = {"skip": True, "reason": "1:2 split ex-date", "ex_date": _CHECK_DAY,
          "action_type": "SPLIT"}
    with patch.object(mfc, "AMO_CSV", csv_path), \
         patch.object(mfc, "_AMO_ORDER_LOG", csv_path), \
         patch.object(mfc, "STATE_FILE", state_path), \
         patch.object(mfc, "_check_auth", lambda: None), \
         patch.object(mfc, "is_trading_day", lambda d: True), \
         patch.object(mfc, "_fetch_open_price", lambda t, d: 500.0), \
         patch.object(mfc, "_fetch_prev_close", lambda t, d: 1000.0), \
         patch.object(mfc, "_fetch_close_price", lambda t, d: 498.0), \
         patch("utils.corporate_actions.get_corporate_action_warning",
               lambda t, check_date=None: ca):
        mfc.run_morning_check(check_date=_CHECK_DAY, apply_fills=True)

    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows, json.loads(state_path.read_text())


# =============================================================================
# BUY side — ticker frozen
# =============================================================================

def test_cancelled_ca_buy_resets_position_to_flat(tmp_path):
    """
    A BUY AMO cancelled for a corporate action must reset the position to flat,
    exactly as the MISSED and REJECTED BUY paths already do via
    cancel_pending_buy(). Otherwise pending_buy stays True forever and
    _process_stock() returns early on it every day — the ticker never trades
    again and never gets risk management.
    """
    rows, state = _run_with_ex_date_today(tmp_path, "BUY", "", pending_buy=True)
    pos = state["positions"][_T]

    assert pos["pending_buy"] is False, (
        "pending_buy still True after CANCELLED_CA — ticker is frozen: "
        "_process_stock() returns PENDING_BUY early every run, so no signal, "
        "no risk management, and no AMO will ever be emitted for it again"
    )
    assert pos["shares"] == 0, f"position not reset to flat: shares={pos['shares']}"


def test_cancelled_ca_buy_ledger_row_is_terminal_so_nothing_can_reprocess(tmp_path):
    """
    Supporting evidence for why the strand is permanent: the ledger row is
    flipped out of DRY_RUN, so _load_pending_orders() will never return it
    again and no later run can reconcile the position.
    """
    rows, _ = _run_with_ex_date_today(tmp_path, "BUY", "", pending_buy=True)
    assert rows[0]["status"] == "CANCELLED_CA"
    assert not any(r["status"] == "DRY_RUN" for r in rows), (
        "no DRY_RUN row remains — the order is unreachable to every future run"
    )


# =============================================================================
# SELL side — position can never exit
# =============================================================================

def test_cancelled_ca_sell_requeues_so_the_position_can_still_exit(tmp_path):
    """
    An RM SELL AMO cancelled for a corporate action must be re-queued, exactly
    as the MISSED SELL path already does via requeue_rm_sell(). Otherwise
    pending_rm_exit stays True with shares > 0 and NO code path can ever close
    the position:
      - morning_fill_check sees no DRY_RUN row (it was marked CANCELLED_CA)
      - signal_runner's pending_rm_exit branch runs check_exit() but never sets
        needs_amo_order, so Step 13 emits nothing
    The risk manager keeps ratcheting a chandelier stop that can never fire an
    order. This is Finding #2 (Jun 19, "Orphaned RM SELL position") reappearing
    through the corporate-action path the Jun 19 requeue fix never covered.
    """
    rows, state = _run_with_ex_date_today(tmp_path, "SELL", "CHANDELIER", pending_buy=False)
    pos = state["positions"][_T]

    assert pos["pending_rm_exit"] is True, "precondition: still pending exit"
    assert pos["shares"] == 10, "precondition: position still held"

    requeued = [r for r in rows if r["order_type"] == "SELL"
                and r["status"] == "DRY_RUN" and "REQUEUED" in r.get("notes", "")]
    assert requeued, (
        "no re-queued SELL AMO was written — the position is stranded with "
        "pending_rm_exit=True, shares=10, and no order that can ever close it"
    )
    assert pos["rm_sell_requeue_count"] >= 1, (
        "requeue counter not incremented — the 3-attempt CRITICAL cap that "
        "protects the MISSED path is not being applied here"
    )


def test_cancelled_ca_sell_preserves_the_three_attempt_cap(tmp_path):
    """
    Reuse must preserve requeue_rm_sell()'s existing cap rather than invent a
    new threshold: a position already at 3 requeues must cross into the
    CRITICAL alert on the next one, not silently continue.
    """
    csv_path, state_path = tmp_path / "amo.csv", tmp_path / "state.json"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_HEADER); w.writeheader()
        w.writerow(_row("SELL", "CHANDELIER"))
    st = _state(pending_buy=False)
    st["positions"][_T]["rm_sell_requeue_count"] = 3     # already at the cap
    state_path.write_text(json.dumps(st))

    ca = {"skip": True, "reason": "split", "ex_date": _CHECK_DAY, "action_type": "SPLIT"}
    import io, contextlib
    buf = io.StringIO()
    with patch.object(mfc, "AMO_CSV", csv_path), \
         patch.object(mfc, "_AMO_ORDER_LOG", csv_path), \
         patch.object(mfc, "STATE_FILE", state_path), \
         patch.object(mfc, "_check_auth", lambda: None), \
         patch.object(mfc, "is_trading_day", lambda d: True), \
         patch.object(mfc, "_fetch_open_price", lambda t, d: 500.0), \
         patch.object(mfc, "_fetch_prev_close", lambda t, d: 1000.0), \
         patch.object(mfc, "_fetch_close_price", lambda t, d: 498.0), \
         patch("utils.corporate_actions.get_corporate_action_warning",
               lambda t, check_date=None: ca):
        with contextlib.redirect_stdout(buf):
            mfc.run_morning_check(check_date=_CHECK_DAY, apply_fills=True)

    out = buf.getvalue()
    state = json.loads(state_path.read_text())
    assert state["positions"][_T]["rm_sell_requeue_count"] == 4
    assert "CRITICAL" in out and "MANUAL ACTION REQUIRED" in out, (
        "crossing the 3-requeue cap must raise the existing CRITICAL alert"
    )


# =============================================================================
# Every CANCELLED_CA write site goes through reset logic
# =============================================================================

def test_every_cancel_ca_decision_is_handled_by_execute_decision():
    """
    Guard against a second CANCELLED_CA site being added without a reset. Walks
    the AST: every action that can carry csv_status == "CANCELLED_CA" must be
    named in execute_decision()'s dispatch chain.
    """
    import ast
    src = (_ROOT / "paper_trading" / "morning_fill_check.py").read_text()
    tree = ast.parse(src)

    plan = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "plan_fill")
    ca_actions = set()
    for r in ast.walk(plan):
        if isinstance(r, ast.Return) and isinstance(r.value, ast.Call):
            kw = {k.arg: k.value for k in r.value.keywords}
            cs, ac = kw.get("csv_status"), kw.get("action")
            if (isinstance(cs, ast.Constant) and cs.value == "CANCELLED_CA"
                    and isinstance(ac, ast.Constant)):
                ca_actions.add(ac.value)
    assert ca_actions, "no CANCELLED_CA-producing return found"

    ex = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "execute_decision")
    handled = {c.value for n in ast.walk(ex) if isinstance(n, ast.Compare)
               for c in ast.walk(n) if isinstance(c, ast.Constant) and isinstance(c.value, str)}

    unhandled = ca_actions - handled
    assert not unhandled, (
        f"CANCELLED_CA action(s) {sorted(unhandled)} produce a terminal ledger "
        f"write but are not dispatched in execute_decision() — the position "
        f"they belong to is never reset"
    )
