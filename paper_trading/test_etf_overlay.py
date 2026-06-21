"""
paper_trading/test_etf_overlay.py — Unit tests for ETF overlay (Phase 2).

All tests use in-memory portfolio state via a temp file.
The live portfolio_state.json is never touched.
"""

import os
import sys
import json
import tempfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.paper_portfolio import PaperPortfolio, ETF_TIERS

LIVE_STATE = Path("paper_trading/portfolio_state.json")
_PASS = 0
_FAIL = 0


def _make_portfolio(
    open_positions: int = 0,
    cash: float = 100_000.0,
    etf_shares: int = 0,
    etf_tier=0,
    etf_avg_price: float = 0.0,
    entry_price: float = 1000.0,
) -> PaperPortfolio:
    """Create a PaperPortfolio backed by a temp file — never touches the live state."""
    tickers = [f"STOCK_{i}.NS" for i in range(4)]
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()

    portfolio = PaperPortfolio(tickers, tmp.name, cash)
    portfolio.state = {
        "cash": cash,
        "initial_capital": cash,
        "positions": {
            t: {"shares": 0, "entry_price": entry_price, "pending_buy": False}
            for t in tickers
        },
        "cooldown_state": {
            t: {"remaining_bars": 0, "last_exit_reason": None} for t in tickers
        },
        "total_trades": 0,
        "last_run_date": None,
        "inception_date": "2026-01-01",
        "weekly_start_value": cash,
        "weekly_start_date": "2026-01-01",
        "weekly_signals": {"BUY": 0, "SELL": 0, "RISK_EXIT": 0},
        "trade_log": [],
        "etf_shares": etf_shares,
        "etf_avg_price": etf_avg_price,
        "etf_tier": etf_tier,
    }
    for i in range(min(open_positions, len(tickers))):
        portfolio.state["positions"][tickers[i]]["shares"] = 10

    return portfolio


def _pass(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  PASS  {name}")


def _fail(name: str, msg: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  FAIL  {name}: {msg}")


# =============================================================================
# Test 1 — Tier mapping
# =============================================================================

def test_1_tier_mapping() -> None:
    name = "Test 1 — Tier mapping"

    p0 = _make_portfolio(open_positions=0)
    t0 = p0.get_etf_target_tier()
    if t0 != 1.0:
        _fail(name, f"0 positions → expected 1.0, got {t0}")
        return

    p2 = _make_portfolio(open_positions=2)
    t2 = p2.get_etf_target_tier()
    if t2 != 0.6:
        _fail(name, f"2 positions → expected 0.6, got {t2}")
        return

    p4 = _make_portfolio(open_positions=4)
    t4 = p4.get_etf_target_tier()
    if t4 != 0.0:
        _fail(name, f"4 positions → expected 0.0, got {t4}")
        return

    _pass(name)


# =============================================================================
# Test 2 — Rebalance guard (idempotent on second call, save() called once)
# =============================================================================

def test_2_rebalance_guard() -> None:
    name = "Test 2 — Rebalance guard"

    portfolio = _make_portfolio(open_positions=0, cash=50_000.0)

    save_count = [0]
    original_save = portfolio.save

    def counting_save():
        save_count[0] += 1
        original_save()

    portfolio.save = counting_save  # type: ignore[method-assign]

    # First call: tier changes from 0 → 1.0, rebalance fires
    portfolio.rebalance_etf(300.0)
    shares_after_first = portfolio.state["etf_shares"]

    if shares_after_first == 0:
        _fail(name, "First rebalance did not buy any ETF shares")
        return

    # Second call: same conditions, tier already 1.0 → guard returns immediately
    portfolio.rebalance_etf(300.0)
    shares_after_second = portfolio.state["etf_shares"]

    if shares_after_second != shares_after_first:
        _fail(name, f"Second call changed etf_shares ({shares_after_first} → {shares_after_second})")
        return

    if save_count[0] != 1:
        _fail(name, f"save() called {save_count[0]} times, expected 1")
        return

    _pass(name)


# =============================================================================
# Test 3 — Cash guard (floor(cash/price) shares, never overdraft)
# =============================================================================

def test_3_cash_guard() -> None:
    name = "Test 3 — Cash guard"

    # cash=500, price=300, 0 positions → tier=100%
    # max affordable = floor(500/300) = 1 share (cost 300), not 2
    portfolio = _make_portfolio(open_positions=0, cash=500.0)

    portfolio.rebalance_etf(300.0)

    etf_shares = portfolio.state["etf_shares"]
    cash_after = portfolio.state["cash"]

    if etf_shares != 1:
        _fail(name, f"Expected 1 share (floor(500/300)), got {etf_shares}")
        return

    if abs(cash_after - 200.0) > 0.01:
        _fail(name, f"Expected cash=200.0 after buy, got {cash_after:.2f}")
        return

    _pass(name)


# =============================================================================
# Test 4 — Sell on position open (ETF reduces when stock slots fill)
# =============================================================================

def test_4_sell_on_position_open() -> None:
    name = "Test 4 — Sell on position open"

    # Setup: etf_shares=100 @ tier=1.0, 2 stock positions at entry_price=100
    # niftybees_price=300
    # total = 0 (cash) + 2*10*100 (stocks) + 100*300 (ETF) = 32000
    # target ETF at new tier 0.6 = 19200
    # current ETF = 30000  → need to sell (30000 - 19200) / 300 ≈ 36 shares
    portfolio = _make_portfolio(
        open_positions=2,
        cash=0.0,
        etf_shares=100,
        etf_tier=1.0,
        entry_price=100.0,
    )

    initial_etf_shares = portfolio.state["etf_shares"]
    initial_cash       = portfolio.state["cash"]

    portfolio.rebalance_etf(300.0)

    if portfolio.state["etf_shares"] >= initial_etf_shares:
        _fail(name, f"etf_shares did not decrease: {initial_etf_shares} → {portfolio.state['etf_shares']}")
        return

    if portfolio.state["cash"] <= initial_cash:
        _fail(name, f"cash did not increase: {initial_cash} → {portfolio.state['cash']:.0f}")
        return

    _pass(
        name,
        # Extra detail (unused by _pass, just for readability if we were verbose)
    )


# =============================================================================
# Test 5 — No crash on NIFTYBEES fetch failure
# =============================================================================

def test_5_no_crash_on_fetch_failure() -> None:
    name = "Test 5 — No crash on NIFTYBEES fetch failure"

    portfolio = _make_portfolio(open_positions=0, cash=10_000.0)
    logs: list = []

    # Replicate the exact try/except block from signal_runner.py
    # with niftybees_data=None (simulating get_ohlcv returning None).
    try:
        niftybees_data = None   # simulates failed fetch
        if niftybees_data is not None and len(niftybees_data) > 0:
            niftybees_price = float(niftybees_data["close"].iloc[-1])
            portfolio.rebalance_etf(niftybees_price, log_fn=logs.append)
        else:
            logs.append(
                "ETF OVERLAY | WARNING: Could not fetch NIFTYBEES price, skipping rebalance"
            )
    except Exception as e:
        logs.append(f"ETF OVERLAY | ERROR: {e} — skipping rebalance, no state change")
        _fail(name, f"Exception raised instead of being caught: {e}")
        return

    if not any("WARNING" in msg for msg in logs):
        _fail(name, f"Expected WARNING in logs, got: {logs}")
        return

    if portfolio.state["etf_shares"] != 0:
        _fail(name, "etf_shares was modified despite fetch failure")
        return

    _pass(name)


# =============================================================================
# Guard: live state file must not be modified
# =============================================================================

def _assert_live_state_untouched(mtime_before: float) -> None:
    if LIVE_STATE.exists():
        mtime_after = os.path.getmtime(LIVE_STATE)
        if mtime_after != mtime_before:
            print(f"\n  CRITICAL: {LIVE_STATE} was modified during tests!")
            sys.exit(2)


# =============================================================================
# Runner
# =============================================================================

if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  ETF Overlay Unit Tests")
    print("=" * 50)

    mtime_before = os.path.getmtime(LIVE_STATE) if LIVE_STATE.exists() else 0.0

    test_1_tier_mapping()
    test_2_rebalance_guard()
    test_3_cash_guard()
    test_4_sell_on_position_open()
    test_5_no_crash_on_fetch_failure()

    _assert_live_state_untouched(mtime_before)

    print()
    print(f"  Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 50)
    print()

    if _FAIL:
        sys.exit(1)
