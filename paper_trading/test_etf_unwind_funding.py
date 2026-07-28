"""
paper_trading/test_etf_unwind_funding.py — Regression tests for the ETF
capital-funding fix (proactive same-day unwind).

Root problem: the ETF overlay's rebalance ran AFTER Phase 2's cash gate, so a
flat portfolio (100% ETF, 0 open positions) could never fund its next stock
signal — a hard deadlock, confirmed frozen live from Jun 25 to Jul 27 2026
(₹243 cash, 100% tier, 0 positions). The fix restores the same-day rebalancing
the validated backtest (D_aggressive schedule, validation/etf_tier_grid_result.json)
already assumes: a committed BUY shrinks the ETF to its tier BEFORE sizing, and
pending_buy now counts as committed so the end-of-day rebalance can't re-absorb
the freed cash.

These tests drive PaperPortfolio's ETF mechanics directly — the same sequence
signal_runner's Phase 2 performs (unwind → queue → end-of-day rebalance) —
without mocking the full pipeline, since that sequence is exactly where the
money logic and the frozen-live bug live.

Two honest notes encoded below:
  • The ₹243 residual is NOT a round-vs-truncate artifact: at the 100% tier the
    last sub-share of cash can't be invested regardless. round() improves
    tier-targeting accuracy on the sell-side unwinds this fix introduces, which
    is what test_unwind_0_to_1 checks — it does not (and cannot) dissolve the
    100%-tier residual.
  • etf_overlay_backtest.py hardcodes the NON-selected A_current tiers
    (0.6/0.6/0.3). The live/validated schedule is D_aggressive (0.8/0.8/0.5).
    Parity is tested against etf_tier_grid_result.json (the authoritative
    artifact), not against that stale harness default.
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.paper_portfolio import PaperPortfolio, ETF_TIERS
from strategy_config import POSITION_SIZING

_NB          = 243.0      # NIFTYBEES price throughout (matches the frozen-state residual)
_COLPAL_PX   = 2135.20    # COLPAL.NS real price — the one signal this deadlock actually blocked


def _frozen_portfolio(tickers=("AAA.NS", "BBB.NS"), cash=243.49, etf_shares=405):
    """
    PaperPortfolio in the exact confirmed-live frozen state: 0 stock positions,
    100% ETF tier, ~one-share cash residual. Temp-file backed — never touches
    live state.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    p = PaperPortfolio(list(tickers), tmp.name, 100_000.0)
    p.state = {
        "cash": cash,
        "initial_capital": 100_000.0,
        "positions": {
            t: {
                "shares": 0, "entry_price": 0.0, "entry_cost": 0.0, "entry_date": None,
                "highest_high_since_entry": 0.0, "bars_held": 0, "chandelier_stop": None,
                "pending_buy": False, "pending_rm_exit": False, "rm_exit_reason": None,
                "rm_sell_requeue_count": 0,
            }
            for t in tickers
        },
        "cooldown_state": {t: {"remaining_bars": 0, "last_exit_reason": None} for t in tickers},
        "total_trades": 0, "last_run_date": "2026-06-25", "inception_date": "2026-06-02",
        "weekly_start_value": 100_000.0, "weekly_start_date": "2026-06-22",
        "weekly_signals": {"BUY": 0, "SELL": 0, "RISK_EXIT": 0}, "trade_log": [],
        "etf_shares": etf_shares, "etf_avg_price": _NB, "etf_tier": 1.0,
    }
    return p


def _total_value(p):
    """Portfolio value excluding pending_buy provisional stock value (matches
    get_portfolio_value() and _etf_rebalance_delta_shares())."""
    stock = sum(
        pos["shares"] * pos["entry_price"]
        for pos in p.state["positions"].values()
        if pos["shares"] > 0 and not pos.get("pending_buy", False)
    )
    return p.state["cash"] + stock + p.state["etf_shares"] * _NB


# =============================================================================
# 0 → 1 transition: frees the right cash, targets the tier accurately (rounding)
# =============================================================================

def test_unwind_0_to_1_frees_twenty_percent_and_hits_tier():
    p = _frozen_portfolio()
    total = _total_value(p)
    cash0 = p.state["cash"]

    freed_est = p.projected_tier_unwind_cash(1, _NB)      # non-mutating estimate
    p.rebalance_etf(_NB, projected_open_positions=1)       # the real unwind

    # Tier stepped to the 1-position tier (D_aggressive: 80%).
    assert p.state["etf_tier"] == ETF_TIERS[1] == 0.8
    # The gate estimate matched the real freed cash exactly (shared plan).
    assert abs((p.state["cash"] - cash0) - freed_est) < 0.01
    # After the unwind the portfolio sits at ~80% ETF, so cash is ~20% of total
    # (within one ETF share — rounding plus the pre-existing sub-share residual).
    assert abs(p.state["cash"] - 0.20 * total) <= _NB
    # Rounding accuracy: post-unwind ETF lands within half a share of the 80%
    # target. Truncation could leave up to a full share off on the sell side.
    assert abs(p.state["etf_shares"] * _NB - 0.80 * total) <= _NB / 2 + 0.01
    # Paper ETF rebalance charges no transaction cost → total value conserved.
    assert abs(_total_value(p) - total) < 0.01


# =============================================================================
# Rounding-shortfall edge: freed cash covers a max-size position (within a share)
# =============================================================================

def test_unwind_frees_enough_for_a_max_size_position():
    # max_position_pct = 0.20. A 100%→80% unwind frees ~20% — i.e. ~= the max
    # position cap, NOT comfortably above it. The pre-existing cash residual is
    # the cushion; in a no-residual case the freed cash can land a hair UNDER an
    # exact max-size position, and the position sizer's cash-cap (sizes to
    # available) absorbs that marginal rounding shortfall. So the honest
    # guarantee is "within one share of a max-size position and far above the
    # ₹1,000 floor", not "strictly ≥ max-size".
    p = _frozen_portfolio()
    total = _total_value(p)
    max_pos_cash = POSITION_SIZING["max_position_pct"] * total

    p.rebalance_etf(_NB, projected_open_positions=1)

    assert p.state["cash"] >= 1000.0                       # clears the floor by a wide margin
    assert p.state["cash"] >= max_pos_cash - _NB           # within one ETF share of max-size


# =============================================================================
# The exact confirmed-live deadlock scenario resolves
# =============================================================================

def test_confirmed_live_deadlock_resolves():
    p = _frozen_portfolio()
    ticker = "AAA.NS"

    # Cash gate: raw ₹243 is below the ₹1,000 floor (the old skip), but the
    # unwind credit makes the candidate fundable.
    assert p.state["cash"] < 1000.0
    credit = p.projected_tier_unwind_cash(1, _NB)
    assert p.state["cash"] + credit >= 1000.0

    # Proactive unwind (projected committed = 0 + 1).
    p.rebalance_etf(_NB, projected_open_positions=p.committed_open_count() + 1)
    assert p.state["etf_tier"] == 0.8
    freed_cash = p.state["cash"]
    assert freed_cash > 1000.0

    # Size + queue the BUY at COLPAL.NS's real price. queue_pending_buy does NOT
    # deduct cash (deferred to next-morning confirm_buy_fill).
    shares = int(freed_cash / _COLPAL_PX)
    assert shares > 0
    p.queue_pending_buy(ticker, shares, _COLPAL_PX, "2026-07-27")
    cash_after_queue = p.state["cash"]
    assert cash_after_queue == freed_cash                 # queue moved no cash
    assert p.committed_open_count() == 1                  # pending_buy now counts

    # End-of-day rebalance (no projection): committed=1 → tier 0.8 == 0.8 → NO-OP.
    # This is the crux: the OLD pending_buy exclusion made this rebuy the ETF and
    # eat the reserved cash, re-freezing the deadlock. It must now leave it alone.
    p.rebalance_etf(_NB)
    assert p.state["etf_tier"] == 0.8
    assert abs(p.state["cash"] - cash_after_queue) < 0.01
    assert p.state["cash"] >= shares * _COLPAL_PX          # reserved cash still covers the fill


# =============================================================================
# Missed fill self-heals cleanly next cycle — no phantom state, no double-charge
# =============================================================================

def test_missed_fill_self_heals():
    p = _frozen_portfolio()
    ticker = "AAA.NS"

    p.rebalance_etf(_NB, projected_open_positions=1)
    shares = int(p.state["cash"] / _COLPAL_PX)
    p.queue_pending_buy(ticker, shares, _COLPAL_PX, "2026-07-27")
    etf_after_unwind  = p.state["etf_shares"]
    cash_after_unwind = p.state["cash"]

    # Next morning the AMO gaps beyond the limit → cancel_pending_buy resets the
    # position to flat, cash untouched (no fill occurred).
    p.cancel_pending_buy(ticker)
    assert p.state["positions"][ticker]["shares"] == 0
    assert p.state["positions"][ticker]["pending_buy"] is False
    assert abs(p.state["cash"] - cash_after_unwind) < 0.01
    assert p.committed_open_count() == 0

    # Next cycle's end-of-day rebalance: committed 0 → tier 1.0 → rebuy ETF,
    # re-absorbing the freed cash. Self-heals; no phantom trade recorded.
    p.rebalance_etf(_NB)
    assert p.state["etf_tier"] == 1.0
    assert p.state["etf_shares"] > etf_after_unwind        # ETF rebought
    assert p.state["cash"] < cash_after_unwind             # cash re-absorbed
    assert len(p.state["trade_log"]) == 0                  # no phantom trade
    assert p.state["total_trades"] == 0


# =============================================================================
# Multi-candidate: #1's unwind funds itself; #2 cannot over-unwind the ETF
# =============================================================================

def test_multi_candidate_second_cannot_over_unwind():
    p = _frozen_portfolio(tickers=("AAA.NS", "BBB.NS"))

    # Candidate #1 (committed 0 → project 1): unwind 100%→80%, frees ~20%.
    credit1 = p.projected_tier_unwind_cash(1, _NB)
    assert credit1 > 1000.0
    p.rebalance_etf(_NB, projected_open_positions=p.committed_open_count() + 1)
    p.queue_pending_buy("AAA.NS", 5, _COLPAL_PX, "2026-07-27")   # committed now 1
    etf_after_first = p.state["etf_shares"]

    # Candidate #2 (committed 1 → project 2): tier 80%→80%, frees NOTHING —
    # #2 must be funded only from residual cash, matching the backtest's
    # n_active step (ETF_TIERS[1] == ETF_TIERS[2]).
    credit2 = p.projected_tier_unwind_cash(1, _NB)
    assert credit2 == 0.0
    # An actual unwind attempt for #2 must sell no further ETF.
    p.rebalance_etf(_NB, projected_open_positions=p.committed_open_count() + 1)
    assert p.state["etf_shares"] == etf_after_first


# =============================================================================
# Backtest parity — validated tier values + same-day n_active stepping
# =============================================================================

def test_live_tiers_match_validated_d_aggressive():
    # The live schedule must be the grid-search-selected D_aggressive config,
    # NOT the stale A_current default hardcoded in etf_overlay_backtest.py.
    grid  = json.loads((_ROOT / "validation" / "etf_tier_grid_result.json").read_text())
    d_agg = grid["configs"]["D_aggressive"]["tiers"]
    for k in ("0", "1", "2", "3", "4"):
        assert ETF_TIERS[int(k)] == d_agg[k], (
            f"live ETF tier {k}={ETF_TIERS[int(k)]} diverges from validated "
            f"D_aggressive {k}={d_agg[k]}"
        )


def test_committed_count_steps_tier_same_day_like_backtest():
    # etf_overlay_backtest.py steps the tier to ETF_TIERS[n_active] the same day
    # a position becomes active. The live committed-count path must produce the
    # same tier for each n — the timing the deadlock fix restores. Uses a mix of
    # real and pending_buy positions to prove BOTH count as committed.
    names = tuple(f"S{i}.NS" for i in range(5))
    for n in range(0, 6):
        p = _frozen_portfolio(tickers=names)
        for i in range(min(n, 5)):
            pos = p.state["positions"][names[i]]
            pos["shares"]      = 10
            pos["pending_buy"] = (i % 2 == 1)   # alternate real / pending_buy
        assert p.committed_open_count() == min(n, 5)
        assert p.get_etf_target_tier() == ETF_TIERS[min(n, 4)]
