"""
paper_trading/test_concurrency_audit.py — Audit stress tests for same-day
sequencing, re-run idempotency, correlation at portfolio extremes, and
fail-open behaviour of the pre-trade flag loader.

NEW FILE — does not modify or overwrite any existing test.
Temp dirs only; no live state touched. No network calls.
"""

import csv
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc
import paper_trading.signal_runner as sr
from paper_trading.correlation_check import check_entry_correlation
from paper_trading.paper_portfolio import PaperPortfolio

_CSV_HEADER = [
    "date", "ticker", "order_type", "signal_price", "limit_price",
    "shares", "status", "fill_price", "fill_date", "notes", "order_id",
]


def _pos(shares=0, entry=0.0, **o):
    p = {"shares": shares, "entry_price": entry, "entry_cost": 0.0, "entry_date": None,
         "highest_high_since_entry": 0.0, "bars_held": 0, "chandelier_stop": None,
         "pending_buy": False, "pending_rm_exit": False, "rm_exit_reason": None,
         "rm_sell_requeue_count": 0}
    p.update(o); return p


# =============================================================================
# Correlation check at 0 / 1 / MAX open positions
# =============================================================================

def _price_data(tickers, n=120, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    return {t: pd.DataFrame({"close": 100 + np.cumsum(rng.normal(0, 1, n))}, index=idx)
            for t in tickers}


def test_correlation_zero_open_positions_is_safe_and_makes_no_calls():
    """0 open positions → nothing to correlate against; must allow entry."""
    r = check_entry_correlation("CAND.NS", portfolio_state_dict={"positions": {}})
    assert r["safe"] is True
    assert r["open_positions"] == []
    assert r["max_correlation"] is None


def test_correlation_one_open_position():
    """1 open position → exactly one pair measured."""
    state = {"positions": {"A.NS": _pos(shares=5, entry=100.0)}}
    r = check_entry_correlation(
        "CAND.NS", portfolio_state_dict=state,
        price_data=_price_data(["CAND.NS", "A.NS"]),
    )
    assert r["open_positions"] == ["A.NS"]
    assert len(r["correlations"]) == 1
    assert r["max_correlation"] is not None


def test_correlation_at_max_concurrent_positions():
    """
    MAX_CONCURRENT_POSITIONS (4) open → all 4 pairs measured, none dropped.
    """
    tickers = [f"P{i}.NS" for i in range(sr.MAX_CONCURRENT_POSITIONS)]
    state = {"positions": {t: _pos(shares=5, entry=100.0) for t in tickers}}
    r = check_entry_correlation(
        "CAND.NS", portfolio_state_dict=state,
        price_data=_price_data(["CAND.NS"] + tickers),
    )
    assert len(r["open_positions"]) == sr.MAX_CONCURRENT_POSITIONS
    assert len(r["correlations"]) == sr.MAX_CONCURRENT_POSITIONS


def test_correlation_pending_buy_excluded_from_file_path():
    """
    check_entry_correlation excludes pending_buy from open_positions.
    signal_runner compensates by rewriting pending_buy=False before calling —
    this pins the underlying behaviour that compensation depends on.
    """
    state = {"positions": {"A.NS": _pos(shares=5, entry=100.0, pending_buy=True)}}
    r = check_entry_correlation("CAND.NS", portfolio_state_dict=state)
    assert r["open_positions"] == [], "pending_buy must be excluded on the raw path"


@pytest.mark.skipif(
    not (Path(__file__).parent / "portfolio_state.json").exists(),
    reason="needs the real portfolio_state.json, which is gitignored and absent "
           "from any fresh clone or worktree. The cwd-independence PROPERTY is "
           "covered without this coupling by "
           "test_correlation_path_audit.py::test_correlation_state_path_default_"
           "is_absolute_and_cwd_independent; what this test uniquely adds is the "
           "CLI end-to-end path, which genuinely cannot be evaluated without a "
           "real state file. Skipping where it cannot be meaningful is honest; "
           "failing there is a false signal.",
)
def test_correlation_cli_default_path_is_cwd_dependent():
    """
    FIXED Aug 26 2026 by fix/correlation-cli-path — retained as the CLI
    end-to-end regression for that finding.

    portfolio_state_path defaults to the RELATIVE string
    "paper_trading/portfolio_state.json". Resolved against the process cwd,
    so running the documented CLI from anywhere but the repo root silently
    takes the "No portfolio state file" branch and returns safe=True with
    zero open positions checked — a fail-open wrong answer, not an error.

    Same bug class as the Jul 17 news_flags.json relative-path incident; the
    health check's regex scan does not cover default parameter values.
    """
    import os
    cwd = os.getcwd()
    try:
        os.chdir(tempfile.gettempdir())
        r = check_entry_correlation("CAND.NS")   # no explicit path — uses default
        assert "No portfolio state file" not in r["reason"], (
            "correlation check silently skipped: the relative default path did "
            f"not resolve from cwd={os.getcwd()!r}. reason={r['reason']!r}"
        )
    finally:
        os.chdir(cwd)


# =============================================================================
# Same-day sequencing — position limit with multiple simultaneous BUYs
# =============================================================================

def test_pending_buy_counts_toward_max_concurrent_positions(tmp_path):
    """
    Phase 2 gates on len(get_open_positions()), which counts shares > 0 —
    including same-day pending_buy rows. So candidate #5 in one run is
    correctly blocked by the position limit.
    """
    tickers = [f"P{i}.NS" for i in range(sr.MAX_CONCURRENT_POSITIONS)]
    state = {
        "cash": 60_000.0, "initial_capital": 100_000.0,
        "etf_shares": 0, "etf_avg_price": 0.0, "etf_tier": 0,
        "total_trades": 0, "trade_log": [],
        "positions": {t: _pos(shares=3, entry=1_000.0, pending_buy=True) for t in tickers},
        "cooldown_state": {t: {"remaining_bars": 0, "last_exit_reason": None} for t in tickers},
    }
    f = tmp_path / "s.json"; f.write_text(json.dumps(state))
    p = PaperPortfolio(tickers, str(f), 100_000.0); p.load()

    assert len(p.get_open_positions()) == sr.MAX_CONCURRENT_POSITIONS
    ok, reason = sr.can_open_position(p, proposed_shares=1, proposed_price=100.0)
    assert ok is False and "Position limit" in reason


def test_queue_pending_sell_twice_same_day_raises(tmp_path):
    """
    BUY+SELL / double-exit on one ticker in one day: a second queue_pending_sell
    must raise rather than silently overwrite the first exit reason.
    """
    t = "X.NS"
    state = {
        "cash": 60_000.0, "initial_capital": 100_000.0,
        "etf_shares": 0, "etf_avg_price": 0.0, "etf_tier": 0,
        "total_trades": 0, "trade_log": [],
        "positions": {t: _pos(shares=5, entry=1_000.0)},
        "cooldown_state": {t: {"remaining_bars": 0, "last_exit_reason": None}},
    }
    f = tmp_path / "s.json"; f.write_text(json.dumps(state))
    p = PaperPortfolio([t], str(f), 100_000.0); p.load()

    p.queue_pending_sell(t, "CHANDELIER")
    with pytest.raises(ValueError, match="already True"):
        p.queue_pending_sell(t, "STRATEGY_SIGNAL")


def test_queue_pending_buy_on_open_position_raises(tmp_path):
    """A BUY must never be queued on top of a real open position."""
    t = "X.NS"
    state = {
        "cash": 60_000.0, "initial_capital": 100_000.0,
        "etf_shares": 0, "etf_avg_price": 0.0, "etf_tier": 0,
        "total_trades": 0, "trade_log": [],
        "positions": {t: _pos(shares=5, entry=1_000.0)},
        "cooldown_state": {t: {"remaining_bars": 0, "last_exit_reason": None}},
    }
    f = tmp_path / "s.json"; f.write_text(json.dumps(state))
    p = PaperPortfolio([t], str(f), 100_000.0); p.load()
    with pytest.raises(ValueError, match="already open"):
        p.queue_pending_buy(t, 3, 1_000.0, "2026-08-21")


# =============================================================================
# morning_fill_check run twice with --apply
# =============================================================================

def test_morning_fill_check_twice_with_apply_does_not_double_deduct(tmp_path):
    """
    Cron double-fire / operator re-run: the second --apply pass must not
    deduct cash twice. Guarded by the CSV status flip plus the pending_buy
    check inside _update_portfolio_fill.
    """
    t = "X.NS"
    csv_path   = tmp_path / "amo.csv"
    state_path = tmp_path / "s.json"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_HEADER); w.writeheader()
        w.writerow({"date": "2026-08-20", "ticker": t, "order_type": "BUY",
                    "signal_price": "1000.00", "limit_price": "1005.00", "shares": "10",
                    "status": "DRY_RUN", "fill_price": "", "fill_date": "",
                    "notes": "", "order_id": ""})
    state_path.write_text(json.dumps({
        "cash": 100_000.0, "initial_capital": 100_000.0,
        "etf_shares": 0, "etf_avg_price": 0.0, "etf_tier": 0,
        "total_trades": 0, "trade_log": [],
        "positions": {t: _pos(shares=10, entry=1_000.5, pending_buy=True,
                              entry_date="2026-08-20")},
        "cooldown_state": {t: {"remaining_bars": 0, "last_exit_reason": None}},
    }))

    def _run():
        with patch.object(mfc, "AMO_CSV", csv_path), \
             patch.object(mfc, "STATE_FILE", state_path), \
             patch.object(mfc, "_check_auth", lambda: None), \
             patch.object(mfc, "is_trading_day", lambda d: True), \
             patch.object(mfc, "_fetch_open_price", lambda a, b: 1002.0), \
             patch.object(mfc, "_fetch_prev_close", lambda a, b: 1000.0), \
             patch("utils.corporate_actions.get_corporate_action_warning",
                   lambda x, check_date=None: {"skip": False, "reason": None, "ex_date": None}):
            mfc.run_morning_check(check_date=date(2026, 8, 21), apply_fills=True)

    _run()
    cash_after_first = json.loads(state_path.read_text())["cash"]
    _run()
    cash_after_second = json.loads(state_path.read_text())["cash"]

    assert cash_after_first < 100_000.0, "first run must deduct"
    assert cash_after_second == cash_after_first, "second run must be a no-op"


# =============================================================================
# news_flags — malformed / missing must fail open
# =============================================================================

@pytest.mark.parametrize("content", ["", "{ broken", '{"no_generated_at": 1}', "[]"])
def test_load_news_flags_fails_open_on_malformed(tmp_path, content):
    """Any unreadable flags file must return {} and never raise."""
    f = tmp_path / "news_flags.json"; f.write_text(content)
    with patch.object(sr, "NEWS_FLAGS_FILE", f):
        assert sr.load_news_flags() == {}


def test_load_news_flags_missing_file_fails_open(tmp_path):
    with patch.object(sr, "NEWS_FLAGS_FILE", tmp_path / "absent.json"):
        assert sr.load_news_flags() == {}


def test_news_flags_path_is_cwd_independent():
    """Regression guard for the Jul 17 relative-path incident."""
    import os
    cwd = os.getcwd()
    try:
        os.chdir(tempfile.gettempdir())
        assert sr.NEWS_FLAGS_FILE.is_absolute()
    finally:
        os.chdir(cwd)
