"""
paper_trading/test_etf_gate_agreement.py — Finding #2.

_etf_rebalance_delta_shares()'s docstring claims it is "the single source of
the delta math -- both the real unwind (rebalance_etf) and the cash-gate
estimate (projected_tier_unwind_cash) call it, so the check and the action can
never disagree."

They share the DELTA math but not the DECISION: rebalance_etf() additionally
returns early when `new_tier == old_tier` (it never corrects intra-tier drift),
and projected_tier_unwind_cash() has no equivalent guard. Where the tier is
unchanged but the ETF has drifted, the estimator credits drift-derived cash the
action will never free.

Tested across EVERY tier boundary, not only the 0.8/0.8 pair, so the fix is
shown to be general rather than a special case for that coincidence.

Temp dirs only. No network.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.paper_portfolio import PaperPortfolio, ETF_TIERS

_NB = 245.0          # NIFTYBEES price
_STOCK = 1_000.0     # per-share price of each open position


def _portfolio(n_positions: int, etf_fraction: float, cash: float, etf_tier: float,
               etf_shares: int = None):
    """
    A portfolio with `n_positions` open and the ETF holding `etf_fraction` of a
    nominal 100k — used to induce intra-tier drift on purpose.
    """
    pos = {
        f"S{i}.NS": {
            "shares": 10, "entry_price": _STOCK, "entry_cost": 0.0,
            "entry_date": "2026-08-01", "highest_high_since_entry": 0.0,
            "bars_held": 1, "chandelier_stop": None, "pending_buy": False,
            "pending_rm_exit": False, "rm_exit_reason": None,
            "rm_sell_requeue_count": 0,
        }
        for i in range(n_positions)
    }
    state = {
        "cash": cash, "initial_capital": 100_000.0,
        "etf_shares": (etf_shares if etf_shares is not None
                       else int(100_000.0 * etf_fraction / _NB)),
        "etf_avg_price": _NB, "etf_tier": etf_tier,
        "total_trades": 0, "trade_log": [], "positions": pos,
        "cooldown_state": {k: {"remaining_bars": 0, "last_exit_reason": None} for k in pos},
    }
    d = Path(tempfile.mkdtemp())
    f = d / "state.json"
    f.write_text(json.dumps(state))
    p = PaperPortfolio(list(pos), str(f), 100_000.0)
    p.load()
    return p, {f"S{i}.NS": _STOCK for i in range(n_positions)}


def _gate_vs_action(n_positions: int, etf_fraction: float = 0.94, cash: float = 1_000.0):
    """Return (credited_by_gate, freed_by_action) for one extra position."""
    tier = ETF_TIERS[min(n_positions, 4)]
    p, prices = _portfolio(n_positions, etf_fraction, cash, tier)
    credited = p.projected_tier_unwind_cash(1, _NB, prices)
    before = p.state["cash"]
    p.rebalance_etf(_NB, current_prices=prices,
                    projected_open_positions=p.committed_open_count() + 1)
    return credited, p.state["cash"] - before


# =============================================================================
# The contract, at EVERY tier boundary
# =============================================================================

@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_gate_estimate_equals_what_the_unwind_actually_frees(n):
    """
    The docstring's claim, asserted directly. n is the committed position count;
    the gate always projects n+1, so this covers boundaries 0->1, 1->2, 2->3
    and 3->4.
    """
    credited, freed = _gate_vs_action(n)
    assert credited == pytest.approx(freed), (
        f"at {n} committed position(s): gate credited Rs{credited:,.2f} but "
        f"rebalance_etf() freed Rs{freed:,.2f} — the check and the action "
        f"disagree by Rs{credited - freed:,.2f}"
    )


def test_the_disagreement_is_specifically_the_equal_tier_boundary():
    """
    Pins WHY: ETF_TIERS[1] == ETF_TIERS[2], so projecting 1 -> 2 leaves the tier
    unchanged and rebalance_etf() early-returns. Every other boundary changes
    tier and so agrees even before the fix. If the tier table is ever rebalanced
    so no two adjacent tiers are equal, this test documents that the coincidence
    was the trigger, not the root cause.
    """
    assert ETF_TIERS[1] == ETF_TIERS[2], "premise: tiers 1 and 2 are numerically equal"
    assert ETF_TIERS[0] != ETF_TIERS[1]
    assert ETF_TIERS[2] != ETF_TIERS[3]
    assert ETF_TIERS[3] != ETF_TIERS[4]


def test_no_credit_is_promised_when_the_tier_does_not_change():
    """
    The specific defect: at 1 committed position the projected tier is identical,
    rebalance_etf() frees nothing, so the estimate must be zero — not the
    drift-derived amount.
    """
    credited, freed = _gate_vs_action(1)
    assert freed == 0.0, "premise: the action correctly no-ops on an unchanged tier"
    assert credited == 0.0, (
        f"gate promised Rs{credited:,.2f} of cash that rebalance_etf() will "
        f"never free"
    )


def test_real_unwinds_are_still_credited_in_full():
    """
    Guard against over-correcting: suppressing the phantom credit must not
    suppress genuine ones. At 0, 2 and 3 committed positions the tier really
    does change and the gate must still credit the full amount.
    """
    for n in (0, 2, 3):
        credited, freed = _gate_vs_action(n)
        assert freed > 0, f"premise: a real unwind happens at n={n}"
        assert credited == pytest.approx(freed), f"real unwind under-credited at n={n}"


def test_no_drift_means_no_disagreement_either_way():
    """
    With the ETF already AT its tier target there is no drift to mis-credit, so
    both sides read zero. Confirms the defect is drift-driven, not unconditional.

    The at-target share count has to be solved for, not guessed: the tier is a
    fraction of the LIVE portfolio (cash + stock + etf), not of the nominal
    100k. etf = t*(cash + stock + etf)  =>  etf = t*(cash + stock)/(1 - t).
    An earlier version of this test passed etf_fraction=0.80 and failed at
    Rs7,105 — the fixture was wrong, not the code.
    """
    cash, stock_value, t = 1_000.0, 10_000.0, ETF_TIERS[1]
    at_target_value  = t * (cash + stock_value) / (1 - t)
    at_target_shares = round(at_target_value / _NB)

    # Built at-target from the start: assigning etf_shares after load() would
    # trip validate_state_integrity()'s 50%-of-initial-capital floor.
    p, prices = _portfolio(1, etf_fraction=0.0, cash=cash, etf_tier=t,
                           etf_shares=at_target_shares)

    credited = p.projected_tier_unwind_cash(1, _NB, prices)
    before   = p.state["cash"]
    p.rebalance_etf(_NB, current_prices=prices,
                    projected_open_positions=p.committed_open_count() + 1)
    freed = p.state["cash"] - before

    assert credited == pytest.approx(freed, abs=_NB), (
        f"at-target portfolio still disagrees: credited Rs{credited:,.2f} "
        f"vs freed Rs{freed:,.2f}"
    )


def test_estimator_never_returns_negative():
    """A tier that would BUY ETF must credit 0.0, never a negative number."""
    p, prices = _portfolio(0, etf_fraction=0.20, cash=50_000.0, etf_tier=1.0)
    assert p.projected_tier_unwind_cash(1, _NB, prices) >= 0.0
