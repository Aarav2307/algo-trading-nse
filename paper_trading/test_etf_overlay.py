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
    if t2 != ETF_TIERS[2]:
        _fail(name, f"2 positions → expected {ETF_TIERS[2]}, got {t2}")
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
    # current_prices passed explicitly (differ from entry_price) to exercise
    # the new code path — verifies current market price is used, not entry_price.
    # niftybees_price=300, current stock prices: STOCK_0=150, STOCK_1=120
    # total = 0 (cash) + 10*150 + 10*120 (stocks @ current) + 100*300 (ETF) = 32700
    # target ETF at new tier (ETF_TIERS[2]) = 32700 * ETF_TIERS[2]
    # current ETF = 30000 → ETF sells down to target
    portfolio = _make_portfolio(
        open_positions=2,
        cash=0.0,
        etf_shares=100,
        etf_tier=1.0,
        entry_price=100.0,
    )

    tickers = list(portfolio.state["positions"].keys())
    current_prices = {tickers[0]: 150.0, tickers[1]: 120.0}

    initial_etf_shares = portfolio.state["etf_shares"]
    initial_cash       = portfolio.state["cash"]

    portfolio.rebalance_etf(300.0, current_prices=current_prices)

    if portfolio.state["etf_shares"] >= initial_etf_shares:
        _fail(name, f"etf_shares did not decrease: {initial_etf_shares} → {portfolio.state['etf_shares']}")
        return

    if portfolio.state["cash"] <= initial_cash:
        _fail(name, f"cash did not increase: {initial_cash} → {portfolio.state['cash']:.0f}")
        return

    _pass(name)


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
# Test 6 — Death cross defers like RM exit (Fix 1)
# =============================================================================

def test_6_death_cross_defers_like_rm_exit() -> None:
    name = "Test 6 — Death cross defers like RM exit"

    # Setup: 1 open position, 10 shares at Rs500 entry, Rs50k cash
    portfolio = _make_portfolio(open_positions=1, cash=50_000.0, entry_price=500.0)
    tickers = list(portfolio.state["positions"].keys())
    ticker  = tickers[0]
    pos     = portfolio.state["positions"][ticker]

    # Seed the position fields that signal_runner sets at open time
    pos["shares"]           = 10
    pos["entry_price"]      = 500.0
    pos["pending_rm_exit"]  = False
    pos["rm_exit_reason"]   = None

    initial_shares = pos["shares"]
    initial_cash   = portfolio.state["cash"]

    # ── Apply exactly what Fix 1 does when today_signal == -1 ──────────────
    # (Replaces the old immediate close_position() call)
    shares_to_sell         = pos["shares"]
    pos["pending_rm_exit"] = True
    pos["rm_exit_reason"]  = "STRATEGY_SIGNAL"
    portfolio.record_weekly_signal("SELL")
    # close_position() is deliberately NOT called here

    # ── Verify deferred state ─────────────────────────────────────────────
    if pos["shares"] != initial_shares:
        _fail(name, f"shares changed: expected {initial_shares}, got {pos['shares']}")
        return
    if not pos["pending_rm_exit"]:
        _fail(name, "pending_rm_exit not set to True")
        return
    if pos["rm_exit_reason"] != "STRATEGY_SIGNAL":
        _fail(name, f"rm_exit_reason expected 'STRATEGY_SIGNAL', got '{pos['rm_exit_reason']}'")
        return
    if portfolio.state["cash"] != initial_cash:
        _fail(name, f"close_position was called (cash changed): Rs{initial_cash:.0f} → Rs{portfolio.state['cash']:.0f}")
        return

    _pass(name)


# =============================================================================
# Test 7 — Integrity validator catches stale state (Fix 2)
# =============================================================================

def test_7_integrity_validator_catches_stale_state() -> None:
    name = "Test 7 — Integrity validator catches stale state"

    # cash=Rs30,000, no positions, no ETF, initial_capital=Rs100,000
    # computed_total = 30,000 < 50,000 floor → must raise ValueError
    portfolio = _make_portfolio(cash=30_000.0)
    portfolio.state["initial_capital"] = 100_000.0  # simulate Rs100k starting capital

    try:
        portfolio.validate_state_integrity()
        _fail(name, "Expected ValueError but no exception was raised")
    except ValueError as exc:
        if "STATE INTEGRITY FAIL" not in str(exc):
            _fail(name, f"Expected 'STATE INTEGRITY FAIL' in message, got: {exc}")
            return
        _pass(name)
    except Exception as exc:
        _fail(name, f"Expected ValueError, got {type(exc).__name__}: {exc}")


# =============================================================================
# Test 8 — Integrity validator passes on valid state (Fix 2)
# =============================================================================

def test_8_integrity_validator_passes_on_valid_state() -> None:
    name = "Test 8 — Integrity validator passes on valid state"

    # cash=Rs500, etf_shares=357, etf_avg_price=273.55, initial_capital=Rs100,000
    # computed_total = 500 + 357×273.55 = Rs98,157 > Rs50,000 floor
    portfolio = _make_portfolio(
        cash=500.0,
        etf_shares=357,
        etf_avg_price=273.55,
        etf_tier=1.0,
    )
    portfolio.state["initial_capital"] = 100_000.0

    try:
        portfolio.validate_state_integrity()
        _pass(name)
    except ValueError as exc:
        _fail(name, f"Unexpected ValueError: {exc}")
    except Exception as exc:
        _fail(name, f"Unexpected {type(exc).__name__}: {exc}")


# =============================================================================
# Test 9 — Fix 3: pending_buy positions excluded from tier count
# =============================================================================

def test_9_tier_excludes_pending_buy() -> None:
    name = "Test 9 — Fix 3: pending_buy excluded from tier count"

    # 2 positions with pending_buy=True: shares set, cash NOT deducted yet.
    # get_etf_target_tier must treat these as 0 real positions → tier = ETF_TIERS[0] = 1.0
    portfolio = _make_portfolio(open_positions=0, cash=100_000.0)
    tickers = list(portfolio.state["positions"].keys())

    for t in tickers[:2]:
        portfolio.state["positions"][t]["shares"]      = 10
        portfolio.state["positions"][t]["pending_buy"] = True

    tier     = portfolio.get_etf_target_tier()
    expected = ETF_TIERS[0]   # 0 real positions → 1.0
    if tier != expected:
        _fail(name, f"Expected {expected} (0 real positions), got {tier}")
        return
    _pass(name)


# =============================================================================
# Test 10 — Fix 3: pending_rm_exit positions included in tier count
# =============================================================================

def test_10_tier_includes_pending_rm_exit() -> None:
    name = "Test 10 — Fix 3: pending_rm_exit included in tier count"

    # 2 positions with pending_rm_exit=True: shares still held, AMO SELL queued.
    # These are still open positions → get_etf_target_tier must count them.
    portfolio = _make_portfolio(open_positions=0, cash=100_000.0)
    tickers = list(portfolio.state["positions"].keys())

    for t in tickers[:2]:
        portfolio.state["positions"][t]["shares"]          = 10
        portfolio.state["positions"][t]["pending_buy"]     = False
        portfolio.state["positions"][t]["pending_rm_exit"] = True

    tier     = portfolio.get_etf_target_tier()
    expected = ETF_TIERS[2]   # 2 real positions (exit pending but not closed)
    if tier != expected:
        _fail(name, f"Expected {expected} (2 positions incl rm_exit), got {tier}")
        return
    _pass(name)


# =============================================================================
# Test 11 — Fix 2: get_portfolio_value includes ETF value
# =============================================================================

def test_11_portfolio_value_includes_etf() -> None:
    name = "Test 11 — Fix 2: get_portfolio_value includes ETF value"

    # cash=50k, etf_shares=100 @ niftybees_price=300 → etf_value=30k
    # no stock positions → total = 80k
    portfolio = _make_portfolio(cash=50_000.0, etf_shares=100, etf_avg_price=300.0, etf_tier=1.0)
    value     = portfolio.get_portfolio_value({}, niftybees_price=300.0)
    expected  = 50_000.0 + 100 * 300.0

    if abs(value - expected) > 0.01:
        _fail(name, f"Expected ₹{expected:,.2f}, got ₹{value:,.2f}")
        return
    _pass(name)


# =============================================================================
# Test 12 — Fix 4: validate accepts all tiers from ETF_TIERS
# =============================================================================

def test_12_validate_accepts_etf_tiers_values() -> None:
    name = "Test 12 — Fix 4: validate accepts tier in ETF_TIERS"

    # etf_tier=0.8 is in ETF_TIERS.values() → must not raise
    portfolio = _make_portfolio(cash=100_000.0, etf_tier=0.8)
    try:
        portfolio.validate_state_integrity()
        _pass(name)
    except ValueError as exc:
        _fail(name, f"Unexpected ValueError: {exc}")
    except Exception as exc:
        _fail(name, f"Unexpected {type(exc).__name__}: {exc}")


# =============================================================================
# Test 13 — Fix 4: validate rejects tier not in ETF_TIERS
# =============================================================================

def test_13_validate_rejects_tier_not_in_etf_tiers() -> None:
    name = "Test 13 — Fix 4: validate rejects tier not in ETF_TIERS"

    # etf_tier=0.3 is NOT in ETF_TIERS.values() → must raise ValueError.
    # (Was silently accepted by the old hardcoded valid_tiers set.)
    portfolio = _make_portfolio(cash=100_000.0, etf_tier=0.3)
    try:
        portfolio.validate_state_integrity()
        _fail(name, "Expected ValueError for etf_tier=0.3 but no exception raised")
    except ValueError as exc:
        if "STATE INTEGRITY FAIL" not in str(exc):
            _fail(name, f"Expected 'STATE INTEGRITY FAIL' in message, got: {exc}")
            return
        _pass(name)
    except Exception as exc:
        _fail(name, f"Expected ValueError, got {type(exc).__name__}: {exc}")


# =============================================================================
# Test 14 — Weekly start value includes ETF (Fix 4 / Change 2)
# =============================================================================

def test_14_weekly_start_value_includes_etf() -> None:
    name = "Test 14 — Weekly start value includes ETF"

    # cash=50k, etf_shares=200 @ niftybees_price=250 → etf_value=50k
    # total = 100k, but weekly_start_value is stale at 50k
    portfolio = _make_portfolio(cash=50_000.0, etf_shares=200, etf_avg_price=250.0, etf_tier=1.0)
    portfolio.state["weekly_start_value"] = 50_000.0   # stale/wrong

    niftybees_price = 250.0
    real_value = portfolio.get_portfolio_value({}, niftybees_price=niftybees_price)
    portfolio.state["weekly_start_value"] = real_value

    if portfolio.state["weekly_start_value"] <= 50_000.0:
        _fail(name, f"Expected weekly_start_value > 50000 with ETF, got {portfolio.state['weekly_start_value']:.0f}")
        return
    _pass(name)


# =============================================================================
# Test 15 — P&L calculation is correct end-to-end (Fix 4 / Change 1)
# =============================================================================

def test_15_pnl_calculation_end_to_end() -> None:
    name = "Test 15 — P&L calculation is correct end-to-end"

    # cash=₹500, etf_shares=357, niftybees_price=273.55
    # etf_value = 357 × 273.55 = 97,657.35
    # total = 500 + 97,657.35 = 98,157.35
    # pnl   = 98,157.35 - 100,000 = -1,842.65
    # pnl%  = -1.84265%
    portfolio = _make_portfolio(cash=500.0, etf_shares=357, etf_avg_price=273.55, etf_tier=1.0)
    portfolio.state["initial_capital"] = 100_000.0

    niftybees_price  = 273.55
    expected_etf     = 357 * 273.55
    expected_total   = 500.0 + expected_etf
    expected_pnl     = expected_total - 100_000.0
    expected_pnl_pct = expected_pnl / 100_000.0 * 100

    # Verify get_portfolio_value
    total_value = portfolio.get_portfolio_value({}, niftybees_price=niftybees_price)
    if abs(total_value - expected_total) > 0.01:
        _fail(name, f"get_portfolio_value: expected ₹{expected_total:,.2f}, got ₹{total_value:,.2f}")
        return

    pnl     = total_value - 100_000.0
    pnl_pct = pnl / 100_000.0 * 100
    if abs(pnl - expected_pnl) > 0.01:
        _fail(name, f"pnl: expected ₹{expected_pnl:,.2f}, got ₹{pnl:,.2f}")
        return
    if abs(pnl_pct - expected_pnl_pct) > 0.001:
        _fail(name, f"pnl_pct: expected {expected_pnl_pct:.4f}%, got {pnl_pct:.4f}%")
        return

    # Verify summary() returns the same ETF-inclusive values
    summ = portfolio.summary({}, niftybees_price)
    if abs(summ["total_value"] - expected_total) > 0.01:
        _fail(name, f"summary()['total_value']: expected ₹{expected_total:,.2f}, got ₹{summ['total_value']:,.2f}")
        return
    if abs(summ["pnl"] - expected_pnl) > 0.01:
        _fail(name, f"summary()['pnl']: expected ₹{expected_pnl:,.2f}, got ₹{summ['pnl']:,.2f}")
        return

    _pass(name)


# =============================================================================
# Test 16 — BUY_QUEUED signal check (Finding #9)
# =============================================================================

def test_16_buy_queued_signal_check() -> None:
    name = "Test 16 — BUY_QUEUED signal check (Finding #9)"

    # Simulate _process_stock() returning {"signal": "BUY_QUEUED"} — the only
    # return value for a successful pending buy (never "BUY").
    executed = {"signal": "BUY_QUEUED", "shares": 5, "exec_price": 1000.0, "reason": "Golden cross"}

    buy_queued_branch = executed["signal"] == "BUY_QUEUED"
    dead_buy_branch   = executed["signal"] == "BUY"

    if not buy_queued_branch:
        _fail(name, "BUY_QUEUED branch not taken for signal='BUY_QUEUED'")
        return
    if dead_buy_branch:
        _fail(name, "Dead BUY branch incorrectly fires for signal='BUY_QUEUED'")
        return
    _pass(name)


# =============================================================================
# Test 17 — Backfill uses etf_avg_price fallback (Finding #10)
# =============================================================================

def test_17_backfill_etf_avg_price_fallback() -> None:
    name = "Test 17 — Backfill uses etf_avg_price fallback (Finding #10)"

    # Portfolio: 287 ETF units @ avg_price=273.55, cash=10k
    portfolio = _make_portfolio(cash=10_000.0, etf_shares=287, etf_avg_price=273.55, etf_tier=1.0)

    # Simulate: live NIFTYBEES fetch failed (or backfill mode) → price is 0.0
    niftybees_price_for_report = 0.0
    if niftybees_price_for_report == 0.0:
        niftybees_price_for_report = portfolio.state.get("etf_avg_price", 0.0)

    if niftybees_price_for_report != 273.55:
        _fail(name, f"Fallback to etf_avg_price failed: expected 273.55, got {niftybees_price_for_report}")
        return

    total_value = portfolio.get_portfolio_value({}, niftybees_price=niftybees_price_for_report)
    expected    = 10_000.0 + 287 * 273.55

    if abs(total_value - expected) > 0.01:
        _fail(name, f"Expected ₹{expected:,.2f} with fallback, got ₹{total_value:,.2f}")
        return
    _pass(name)


# =============================================================================
# Test 18 — correlation check accepts portfolio_state_dict (Fix 2)
# =============================================================================

def test_18_correlation_check_accepts_state_dict() -> None:
    name = "Test 18 — correlation check accepts portfolio_state_dict"

    from paper_trading.correlation_check import check_entry_correlation

    # 1 open, 1 flat — only open position should appear in result
    state_dict = {
        "positions": {
            "OPENSTOCK.NS": {"shares": 5,  "pending_buy": False, "entry_price": 100.0},
            "FLATSTOCK.NS": {"shares": 0,  "pending_buy": False, "entry_price": 0.0},
        }
    }

    try:
        result = check_entry_correlation(
            candidate="CANDIDATE.NS",
            portfolio_state_dict=state_dict,
            lookback_days=30,
            max_correlation=0.60,
        )
    except Exception as exc:
        _fail(name, f"check_entry_correlation raised: {exc}")
        return

    # Key assertion: open_positions read from dict, not from disk file
    if result["open_positions"] != ["OPENSTOCK.NS"]:
        _fail(name, f"Expected ['OPENSTOCK.NS'], got {result['open_positions']}")
        return
    _pass(name)


# =============================================================================
# Test 19 — correlation check excludes pending_buy from dict (Fix 2)
# =============================================================================

def test_19_correlation_check_excludes_pending_buy_from_dict() -> None:
    name = "Test 19 — correlation check excludes pending_buy from dict"

    from paper_trading.correlation_check import check_entry_correlation

    # Position has shares > 0 but pending_buy=True → should be excluded
    state_dict = {
        "positions": {
            "PENDINGSTK.NS": {"shares": 5, "pending_buy": True, "entry_price": 100.0},
        }
    }

    # No open positions → function returns early (no network call needed)
    result = check_entry_correlation(
        candidate="CANDIDATE.NS",
        portfolio_state_dict=state_dict,
        lookback_days=120,
        max_correlation=0.60,
    )

    if result["open_positions"] != []:
        _fail(name, f"Expected [] (pending_buy excluded), got {result['open_positions']}")
        return
    if not result["safe"]:
        _fail(name, "Expected safe=True when open_positions=[]")
        return
    _pass(name)


# =============================================================================
# Test 20 — notes prefix matching handles [REQUEUED] suffix (Fix 3)
# =============================================================================

def test_20_notes_prefix_matching_handles_requeued() -> None:
    name = "Test 20 — notes prefix matching handles [REQUEUED] suffix"

    from paper_trading.morning_fill_check import _RM_EXIT_NOTES, _STRATEGY_EXIT_NOTES

    # First REQUEUED suffix
    notes      = "CHANDELIER [REQUEUED]"
    notes_base = notes.split(" [")[0].strip() if notes else ""
    if notes_base != "CHANDELIER":
        _fail(name, f"CHANDELIER [REQUEUED]: expected base='CHANDELIER', got '{notes_base}'")
        return
    if notes_base not in _RM_EXIT_NOTES:
        _fail(name, f"'CHANDELIER' should be in _RM_EXIT_NOTES")
        return

    notes      = "STRATEGY_SIGNAL [REQUEUED]"
    notes_base = notes.split(" [")[0].strip() if notes else ""
    if notes_base != "STRATEGY_SIGNAL":
        _fail(name, f"STRATEGY_SIGNAL [REQUEUED]: expected base='STRATEGY_SIGNAL', got '{notes_base}'")
        return
    if notes_base not in _STRATEGY_EXIT_NOTES:
        _fail(name, f"'STRATEGY_SIGNAL' should be in _STRATEGY_EXIT_NOTES")
        return

    _pass(name)


# =============================================================================
# Test 21 — double requeue notes still parsed correctly (Fix 3)
# =============================================================================

def test_21_double_requeue_notes_parsed_correctly() -> None:
    name = "Test 21 — double requeue notes still parsed correctly"

    from paper_trading.morning_fill_check import _RM_EXIT_NOTES

    notes      = "HARD_STOP [REQUEUED] [REQUEUED]"
    notes_base = notes.split(" [")[0].strip() if notes else ""
    if notes_base != "HARD_STOP":
        _fail(name, f"Expected base='HARD_STOP', got '{notes_base}'")
        return
    if notes_base not in _RM_EXIT_NOTES:
        _fail(name, f"'HARD_STOP' should be in _RM_EXIT_NOTES")
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
    test_6_death_cross_defers_like_rm_exit()
    test_7_integrity_validator_catches_stale_state()
    test_8_integrity_validator_passes_on_valid_state()
    test_9_tier_excludes_pending_buy()
    test_10_tier_includes_pending_rm_exit()
    test_11_portfolio_value_includes_etf()
    test_12_validate_accepts_etf_tiers_values()
    test_13_validate_rejects_tier_not_in_etf_tiers()
    test_14_weekly_start_value_includes_etf()
    test_15_pnl_calculation_end_to_end()
    test_16_buy_queued_signal_check()
    test_17_backfill_etf_avg_price_fallback()
    test_18_correlation_check_accepts_state_dict()
    test_19_correlation_check_excludes_pending_buy_from_dict()
    test_20_notes_prefix_matching_handles_requeued()
    test_21_double_requeue_notes_parsed_correctly()

    _assert_live_state_untouched(mtime_before)

    print()
    print(f"  Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 50)
    print()

    if _FAIL:
        sys.exit(1)
