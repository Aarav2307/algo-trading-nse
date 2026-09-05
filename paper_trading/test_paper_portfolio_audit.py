"""
paper_trading/test_paper_portfolio_audit.py — Audit stress tests for
PaperPortfolio state integrity, ETF tier boundaries, and universe mutation.

NEW FILE — does not modify or overwrite any existing test.
Every test writes only into a pytest tmp_path. The live
paper_trading/portfolio_state.json is never opened for writing.
No network calls.
"""

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.paper_portfolio import PaperPortfolio, ETF_TIERS

_T = "AUDIT.NS"


def _state(**over) -> dict:
    base = {
        "cash":            100_000.0,
        "initial_capital": 100_000.0,
        "etf_shares":      0,
        "etf_avg_price":   0.0,
        "etf_tier":        0,
        "total_trades":    0,
        "trade_log":       [],
        "positions":       {},
        "cooldown_state":  {},
    }
    base.update(over)
    return base


def _pos(shares=0, entry=0.0, **over) -> dict:
    p = {
        "shares": shares, "entry_price": entry, "entry_cost": 0.0,
        "entry_date": None, "highest_high_since_entry": 0.0, "bars_held": 0,
        "chandelier_stop": None, "pending_buy": False, "pending_rm_exit": False,
        "rm_exit_reason": None, "rm_sell_requeue_count": 0,
    }
    p.update(over)
    return p


def _write(tmp_path: Path, state: dict) -> Path:
    f = tmp_path / "portfolio_state.json"
    f.write_text(json.dumps(state))
    return f


# =============================================================================
# State file integrity — cold start, empty, malformed
# =============================================================================

def test_missing_state_file_cold_starts_cleanly(tmp_path):
    """No file at all (first ever run) → fresh initial state, full capital."""
    p = PaperPortfolio([_T], str(tmp_path / "nope.json"), 100_000.0)
    p.load()
    assert p.state["cash"] == 100_000.0
    assert p.state["positions"][_T]["shares"] == 0
    assert p.state["total_trades"] == 0


def test_empty_state_file_raises_not_silently_cold_starts(tmp_path):
    """
    A zero-byte portfolio_state.json (partial write / disk-full mid-crash)
    must NOT be mistaken for a cold start — that would silently reset the
    portfolio to ₹100,000 and erase all history.
    """
    f = tmp_path / "portfolio_state.json"
    f.write_text("")
    p = PaperPortfolio([_T], str(f), 100_000.0)
    with pytest.raises(Exception) as exc:
        p.load()
    # json.JSONDecodeError is correct-and-loud. What must NOT happen is a
    # silent reset to initial capital.
    assert "Expecting value" in str(exc.value) or isinstance(exc.value, ValueError)


def test_truncated_json_raises_loudly(tmp_path):
    """Half-written JSON (crash mid-write) must raise, never partially load."""
    f = tmp_path / "portfolio_state.json"
    f.write_text('{"cash": 50000.0, "positions": {"AUDIT.NS": {"shar')
    p = PaperPortfolio([_T], str(f), 100_000.0)
    with pytest.raises(Exception):
        p.load()


def test_total_trades_mismatch_is_caught(tmp_path):
    """Check 2 of validate_state_integrity: total_trades vs trade_log length."""
    f = _write(tmp_path, _state(total_trades=5, trade_log=[]))
    p = PaperPortfolio([_T], str(f), 100_000.0)
    with pytest.raises(ValueError, match="total_trades"):
        p.load()


def test_below_50pct_floor_is_caught(tmp_path):
    """Check 1: a stale/low-value state file must abort the run."""
    f = _write(tmp_path, _state(cash=1_000.0))
    p = PaperPortfolio([_T], str(f), 100_000.0)
    with pytest.raises(ValueError, match="50% floor"):
        p.load()


def test_integrity_floor_counts_pending_buy_shares_that_have_no_cash_backing(tmp_path):
    """
    validate_state_integrity() sums shares × entry_price over ALL positions,
    including pending_buy ones whose cash has NOT been deducted yet.
    get_portfolio_value() deliberately EXCLUDES pending_buy for exactly that
    double-count reason. The validator disagreeing with the valuation function
    means the 50% floor is computed on an inflated total.
    """
    st = _state(
        cash=30_000.0,
        positions={_T: _pos(shares=100, entry=400.0, pending_buy=True)},
        cooldown_state={_T: {"remaining_bars": 0, "last_exit_reason": None}},
    )
    f = _write(tmp_path, st)
    p = PaperPortfolio([_T], str(f), 100_000.0)
    p.load()   # passes: 30k cash + 40k phantom stock = 70k > 50k floor

    real = p.get_portfolio_value({_T: 400.0})
    assert real == 30_000.0, "get_portfolio_value correctly excludes pending_buy"
    # The validator saw 70,000 while the true value is 30,000 — below the floor.
    assert real < p.state["initial_capital"] * 0.50, (
        "true value is below the 50% floor but validate_state_integrity passed, "
        "because it counted un-funded pending_buy shares as real assets"
    )


# =============================================================================
# Universe mutation — stock removed from universe.py with a position open
# =============================================================================

def test_removed_ticker_with_open_position_is_still_in_state_but_unprocessed(tmp_path):
    """
    A stock removed from universe.py while a position is open stays in
    portfolio_state.json (load() only backfills, never prunes) — correct.

    But signal_runner's Phase 1 loop iterates `for ticker in STOCKS`, so the
    orphan is never passed to _process_stock(): no RM check_exit, no chandelier
    ratchet, no bars_held increment, no exit signal. It also never appears in
    _fetch_stock_data()'s dfs, so current_prices has no entry for it and
    get_portfolio_value() marks it at the frozen entry_price forever.

    This test documents the exposure at the ledger layer.
    """
    st = _state(
        cash=50_000.0,
        positions={"REMOVED.NS": _pos(shares=10, entry=2_000.0, entry_date="2026-08-01")},
        cooldown_state={"REMOVED.NS": {"remaining_bars": 0, "last_exit_reason": None}},
    )
    f = _write(tmp_path, st)

    # New universe no longer contains REMOVED.NS
    p = PaperPortfolio(["KEPT.NS"], str(f), 100_000.0)
    p.load()

    assert "REMOVED.NS" in p.state["positions"], "position survives load()"
    assert p.get_open_positions()["REMOVED.NS"]["shares"] == 10

    # It counts toward the ETF tier (capital reserved)...
    assert p.committed_open_count() == 1
    # ...and is marked at its stale entry price, since no price feed covers it.
    assert p.get_portfolio_value({"KEPT.NS": 100.0}) == 50_000.0 + 10 * 2_000.0


# =============================================================================
# ETF tier boundary transitions — exactly 0/1/2/3/4 positions
# =============================================================================

@pytest.mark.parametrize("n,expected", [(0, 1.0), (1, 0.8), (2, 0.8), (3, 0.5), (4, 0.0), (5, 0.0), (9, 0.0)])
def test_etf_tier_at_each_boundary(tmp_path, n, expected):
    """Tier lookup must be exact at every boundary, and clamp above 4."""
    positions = {f"S{i}.NS": _pos(shares=1, entry=100.0) for i in range(n)}
    cds = {f"S{i}.NS": {"remaining_bars": 0, "last_exit_reason": None} for i in range(n)}
    f = _write(tmp_path, _state(cash=60_000.0, positions=positions, cooldown_state=cds))
    p = PaperPortfolio(list(positions), str(f), 100_000.0)
    p.load()
    assert p.committed_open_count() == n
    assert p.get_etf_target_tier() == expected


def test_gate_estimate_and_real_unwind_must_agree(tmp_path):
    """
    CONFIRMED DEFECT.

    _etf_rebalance_delta_shares()'s docstring states it is "the single source
    of the delta math -- both the real unwind (rebalance_etf) and the cash-gate
    estimate (projected_tier_unwind_cash) call it, so the check and the action
    can never disagree." signal_runner.py:1398 repeats the claim.

    They do disagree: rebalance_etf() applies an extra `new_tier == old_tier`
    early-return (intra-tier drift is deliberately never corrected) that
    projected_tier_unwind_cash() does not mirror. Because ETF_TIERS[1] ==
    ETF_TIERS[2] == 0.8, EVERY second same-day candidate lands on this path.

    Effect: Phase 2's cash gate passes on phantom cash, the unwind no-ops, the
    sizer then returns 0 shares against real cash, and the top-ranked golden
    cross is logged as HOLD "sizing skipped" -- and is never added to
    skipped_signals, so it reaches signal_log.csv as neither SKIPPED nor BUY.
    """
    f = _write(tmp_path, _state(
        cash=1_000.0, etf_shares=400, etf_avg_price=245.0, etf_tier=0.8,
        positions={_T: _pos(shares=5, entry=1_000.0, entry_date="2026-08-01")},
        cooldown_state={_T: {"remaining_bars": 0, "last_exit_reason": None}},
    ))
    p = PaperPortfolio([_T], str(f), 100_000.0)
    p.load()

    prices, nb = {_T: 1_000.0}, 245.0
    assert p.committed_open_count() == 1
    assert ETF_TIERS[1] == ETF_TIERS[2], "premise: tiers 1 and 2 are equal"

    credited = p.projected_tier_unwind_cash(1, nb, prices)

    cash_before = p.state["cash"]
    p.rebalance_etf(nb, current_prices=prices,
                    projected_open_positions=p.committed_open_count() + 1)
    actually_freed = p.state["cash"] - cash_before

    assert credited == actually_freed, (
        f"cash gate credited Rs{credited:,.0f} but rebalance_etf() freed "
        f"Rs{actually_freed:,.0f} -- the check and the action disagree by "
        f"Rs{credited - actually_freed:,.0f}"
    )


def test_projected_unwind_never_exceeds_shares_held(tmp_path):
    """Unwind estimate is capped at ETF shares actually held."""
    f = _write(tmp_path, _state(
        cash=60_000.0, etf_shares=5, etf_avg_price=245.0, etf_tier=1.0,
    ))
    p = PaperPortfolio([], str(f), 100_000.0)
    p.load()
    freed = p.projected_tier_unwind_cash(4, 245.0, {})
    assert freed <= 5 * 245.0


# =============================================================================
# close_position — division and rounding edges
# =============================================================================

def test_close_position_on_flat_position_raises(tmp_path):
    """Idempotency guard: duplicate fill confirmation must not double-close."""
    f = _write(tmp_path, _state(
        positions={_T: _pos(shares=0)},
        cooldown_state={_T: {"remaining_bars": 0, "last_exit_reason": None}},
    ))
    p = PaperPortfolio([_T], str(f), 100_000.0)
    p.load()
    with pytest.raises(ValueError, match="already closed"):
        p.close_position(_T, 100.0, "2026-08-21", "STRATEGY_SIGNAL")


def test_close_position_with_zero_entry_price_divides_by_zero(tmp_path):
    """
    close_position computes return_pct = (exec_price / entry_px - 1) * 100
    with no guard on entry_px == 0. A position whose entry_price is 0.0
    (schema-migrated legacy row, or a fill confirmed at a bad price) makes
    close_position raise ZeroDivisionError *after* cash has already been
    credited and the trade appended — leaving state half-mutated and unsaved.
    """
    f = _write(tmp_path, _state(
        cash=60_000.0,
        positions={_T: _pos(shares=10, entry=0.0, entry_date="2026-08-01")},
        cooldown_state={_T: {"remaining_bars": 0, "last_exit_reason": None}},
    ))
    p = PaperPortfolio([_T], str(f), 100_000.0)
    p.load()
    with pytest.raises(ZeroDivisionError):
        p.close_position(_T, 500.0, "2026-08-21", "STRATEGY_SIGNAL")

    # Damage assessment: cash was already credited before the raise.
    assert p.state["cash"] > 60_000.0, "cash mutated before the exception"
    assert len(p.state["trade_log"]) == 0, "trade_log not appended (raise came first)"
