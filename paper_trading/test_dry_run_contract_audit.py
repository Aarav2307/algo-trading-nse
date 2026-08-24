"""
paper_trading/test_dry_run_contract_audit.py — the dry-run contract as a
PROPERTY rather than a per-branch assertion.

NEW FILE — does not modify or overwrite any existing test.

morning_fill_check.py's docstring promises "Dry-run mode (the default):
portfolio state is NOT modified". The old implementation enforced that with a
separate `if apply_fills:` guard at each write site, and four sites guarded
the portfolio write but not the amo_orders.csv write.

These tests do not check individual branches. They assert that after a dry
run, every file the module can write is BYTE-IDENTICAL — parametrized across
every order outcome (fill, miss, gap-exit, corporate-action cancel). A branch
added later is covered automatically, without the new branch having to
remember anything.

Content hashes, not mtimes: mtime resolution is coarse enough that a fast
test can rewrite a file within the same tick and still look untouched. This
generalises the _assert_live_state_untouched() idiom already used in
test_etf_overlay.py / test_gap_breaker.py / test_correlation_check.py.

Temp dirs only. No live state touched. No network calls.
"""

import csv
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc

_TICKER    = "DRYRUN.NS"
_ORDER_DAY = "2026-08-20"
_CHECK_DAY = date(2026, 8, 21)
_LIMIT_BUY  = 1005.00
_LIMIT_SELL = 995.00

_CSV_HEADER = [
    "date", "ticker", "order_type", "signal_price", "limit_price",
    "shares", "status", "fill_price", "fill_date", "notes", "order_id",
]


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _order_row(order_type: str, limit: float, notes: str = "") -> dict:
    return {
        "date": _ORDER_DAY, "ticker": _TICKER, "order_type": order_type,
        "signal_price": "1000.00", "limit_price": f"{limit:.2f}", "shares": "10",
        "status": "DRY_RUN", "fill_price": "", "fill_date": "",
        "notes": notes, "order_id": "",
    }


def _state(pending_buy: bool, shares: int = 10) -> dict:
    return {
        "cash": 100_000.0, "initial_capital": 100_000.0,
        "etf_shares": 0, "etf_avg_price": 0.0, "etf_tier": 0,
        "total_trades": 0, "trade_log": [],
        "positions": {_TICKER: {
            "shares": shares, "entry_price": 1_000.5, "entry_cost": 12.0,
            "entry_date": _ORDER_DAY, "highest_high_since_entry": 1_050.0,
            "bars_held": 3, "chandelier_stop": 960.0,
            "pending_buy": pending_buy,
            "pending_rm_exit": not pending_buy,
            "rm_exit_reason": None if pending_buy else "CHANDELIER",
            "rm_sell_requeue_count": 0,
        }},
        "cooldown_state": {_TICKER: {"remaining_bars": 0, "last_exit_reason": None}},
    }


def _setup(tmp_path: Path, row: dict, state: dict):
    csv_path   = tmp_path / "amo_orders.csv"
    state_path = tmp_path / "portfolio_state.json"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_HEADER)
        w.writeheader()
        w.writerow(row)
    state_path.write_text(json.dumps(state, indent=2))
    return csv_path, state_path


def _run(csv_path, state_path, open_px, close_px, apply_fills, ex_date_today=False):
    ca = {"skip": bool(ex_date_today), "reason": "split ex-date",
          "ex_date": _CHECK_DAY if ex_date_today else None}
    with patch.object(mfc, "AMO_CSV", csv_path), \
         patch.object(mfc, "_AMO_ORDER_LOG", csv_path), \
         patch.object(mfc, "STATE_FILE", state_path), \
         patch.object(mfc, "_check_auth", lambda: None), \
         patch.object(mfc, "is_trading_day", lambda d: True), \
         patch.object(mfc, "_fetch_open_price", lambda t, d: open_px), \
         patch.object(mfc, "_fetch_prev_close", lambda t, d: 1_000.0), \
         patch.object(mfc, "_fetch_close_price", lambda t, d: close_px), \
         patch("utils.corporate_actions.get_corporate_action_warning",
               lambda t, check_date=None: ca):
        mfc.run_morning_check(check_date=_CHECK_DAY, apply_fills=apply_fills)


# Every distinct outcome the fill loop can reach.
_SCENARIOS = [
    # id,            order_type, limit,        open_px,  pending_buy, ex_date
    ("buy_filled",   "BUY",  _LIMIT_BUY,  1_002.0, True,  False),
    ("buy_missed",   "BUY",  _LIMIT_BUY,  1_050.0, True,  False),
    ("sell_filled",  "SELL", _LIMIT_SELL,   998.0, False, False),
    ("sell_requeue", "SELL", _LIMIT_SELL,   985.0, False, False),   # ~1% gap → REQUEUE
    ("sell_gapexit", "SELL", _LIMIT_SELL,   940.0, False, False),   # ~5.5% gap → GAP_EXIT
    ("corp_action",  "BUY",  _LIMIT_BUY,  1_002.0, True,  True),
]


@pytest.mark.parametrize(
    "scenario,order_type,limit,open_px,pending_buy,ex_date",
    _SCENARIOS, ids=[s[0] for s in _SCENARIOS],
)
def test_dry_run_writes_to_no_file_whatsoever(
    tmp_path, scenario, order_type, limit, open_px, pending_buy, ex_date
):
    """
    THE contract: a dry run leaves every writable file byte-identical.

    Holds for every outcome and, being a property over files rather than a
    per-branch assertion, for any branch added in future.
    """
    notes = "CHANDELIER" if order_type == "SELL" else ""
    csv_path, state_path = _setup(
        tmp_path, _order_row(order_type, limit, notes), _state(pending_buy)
    )
    before = {p: _digest(p) for p in (csv_path, state_path)}

    _run(csv_path, state_path, open_px, 990.0, apply_fills=False,
         ex_date_today=ex_date)

    for p, want in before.items():
        assert _digest(p) == want, (
            f"[{scenario}] dry run modified {p.name} — the module's docstring "
            f"promises 'portfolio state is NOT modified'"
        )


@pytest.mark.parametrize(
    "scenario,order_type,limit,open_px,pending_buy,ex_date",
    _SCENARIOS, ids=[s[0] for s in _SCENARIOS],
)
def test_dry_run_leaves_order_reprocessable_by_a_later_apply(
    tmp_path, scenario, order_type, limit, open_px, pending_buy, ex_date
):
    """
    The consequence that made this CRITICAL: _load_pending_orders() selects on
    status == "DRY_RUN". If a dry run advances that field, the order is
    invisible to every later run and the fill is lost.
    """
    notes = "CHANDELIER" if order_type == "SELL" else ""
    csv_path, state_path = _setup(
        tmp_path, _order_row(order_type, limit, notes), _state(pending_buy)
    )

    _run(csv_path, state_path, open_px, 990.0, apply_fills=False,
         ex_date_today=ex_date)

    row = list(csv.DictReader(open(csv_path, newline="")))[0]
    assert row["status"] == "DRY_RUN", (
        f"[{scenario}] status advanced to {row['status']!r} during a dry run — "
        f"the order can never be processed again"
    )


def test_dry_run_then_apply_still_confirms_a_buy_fill(tmp_path):
    """End to end: inspect first (as documented), then the real cron run."""
    csv_path, state_path = _setup(
        tmp_path, _order_row("BUY", _LIMIT_BUY), _state(pending_buy=True)
    )

    _run(csv_path, state_path, 1_002.0, 990.0, apply_fills=False)
    _run(csv_path, state_path, 1_002.0, 990.0, apply_fills=True)

    final = json.loads(state_path.read_text())
    assert final["positions"][_TICKER]["pending_buy"] is False, \
        "BUY fill lost: the preceding dry run consumed the order"
    assert final["cash"] < 100_000.0, "BUY fill lost: cash never deducted"


def test_gap_exit_is_recorded_as_gap_exit_not_missed(tmp_path):
    """
    Second defect closed by this restructure. _update_csv_row() only matches
    rows still at "DRY_RUN". The old path wrote "MISSED" and then tried to
    write "GAP_EXIT", which silently no-opped — so amo_orders.csv recorded
    every gap-exit as a missed order with no fill price, contradicting the
    trade_log, which correctly showed the position closed.
    """
    csv_path, state_path = _setup(
        tmp_path, _order_row("SELL", _LIMIT_SELL, "CHANDELIER"),
        _state(pending_buy=False),
    )

    _run(csv_path, state_path, 940.0, 938.0, apply_fills=True)

    row = list(csv.DictReader(open(csv_path, newline="")))[0]
    assert row["status"] == "GAP_EXIT", (
        f"ledger says {row['status']!r} for an order that closed the position "
        f"at the open — amo_orders.csv now contradicts trade_log"
    )
    assert row["fill_price"], "GAP_EXIT row must carry the actual fill price"
    assert row["fill_date"] == _CHECK_DAY.isoformat()

    state = json.loads(state_path.read_text())
    assert state["positions"][_TICKER]["shares"] == 0, "position must be closed"
    assert state["trade_log"][-1]["exit_reason"] == "GAP_EXIT"


def test_apply_still_performs_every_write(tmp_path):
    """
    Guard against over-correcting: --apply must still mutate both files.
    A dry-run fix that silently disabled the real path would pass every test
    above and be far worse than the bug.
    """
    csv_path, state_path = _setup(
        tmp_path, _order_row("BUY", _LIMIT_BUY), _state(pending_buy=True)
    )
    before = {p: _digest(p) for p in (csv_path, state_path)}

    _run(csv_path, state_path, 1_002.0, 990.0, apply_fills=True)

    for p, was in before.items():
        assert _digest(p) != was, f"--apply did not write {p.name}"

    row = list(csv.DictReader(open(csv_path, newline="")))[0]
    assert row["status"] == "FILLED"
    assert json.loads(state_path.read_text())["positions"][_TICKER]["pending_buy"] is False
