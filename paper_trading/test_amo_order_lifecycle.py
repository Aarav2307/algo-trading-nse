"""
paper_trading/test_amo_order_lifecycle.py — Regression tests for the
SELL/RISK_EXIT side of the AMO order-queueing lifecycle.

Root problem this closes: _process_stock()'s BUY path queues portfolio state
via PaperPortfolio.queue_pending_buy() -- a validated method -- but the
SELL/RISK_EXIT path used to poke pos["pending_rm_exit"]/pos["rm_exit_reason"]
directly, bypassing PaperPortfolio entirely, with no equivalent validation.
Separately, main()'s Step 13 (AMO CSV logging) used to re-derive "does this
ticker need an order" from `signal`/`shares` independently of where the
portfolio-state flag was actually set, a second, informally-matched
condition rather than one fact set once.

Fixes:
  - PaperPortfolio.queue_pending_sell() mirrors queue_pending_buy()'s
    validation discipline for the SELL/RISK_EXIT path.
  - _process_stock() now sets an explicit needs_amo_order field
    ("BUY"/"SELL") at the exact point it queues portfolio state, and Step 13
    reads that field instead of re-deriving the same fact from signal/shares.

No existing test in this repo called _process_stock() through its
RISK_EXIT/strategy-SELL branches end-to-end (test_etf_overlay.py's related
tests replicate the old inline logic rather than calling the real function)
-- these tests close that gap.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.signal_runner import _process_stock
from paper_trading.paper_portfolio import PaperPortfolio


# =============================================================================
# PaperPortfolio.queue_pending_sell()
# =============================================================================

def _make_open_position_portfolio(ticker: str, shares: int = 10, cash: float = 50_000.0) -> PaperPortfolio:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    os.unlink(tmp.name)   # load() must see a missing file to build a fresh initial state
    portfolio = PaperPortfolio([ticker], tmp.name, cash)
    portfolio.load()
    portfolio.state["positions"][ticker].update({
        "shares":                   shares,
        "entry_price":              500.0,
        "entry_date":               "2026-06-01",
        "highest_high_since_entry": 525.0,
        "bars_held":                10,
        "chandelier_stop":          None,
        "pending_buy":              False,
        "pending_rm_exit":          False,
        "rm_exit_reason":           None,
    })
    return portfolio


def test_queue_pending_sell_sets_flag_and_reason():
    ticker = "TEST.NS"
    port = _make_open_position_portfolio(ticker)
    port.queue_pending_sell(ticker, "CHANDELIER")

    pos = port.state["positions"][ticker]
    assert pos["pending_rm_exit"] is True
    assert pos["rm_exit_reason"] == "CHANDELIER"


def test_queue_pending_sell_raises_on_flat_position():
    ticker = "TEST.NS"
    port = _make_open_position_portfolio(ticker, shares=0)
    with pytest.raises(ValueError, match="flat position"):
        port.queue_pending_sell(ticker, "STRATEGY_SIGNAL")


def test_queue_pending_sell_raises_if_already_pending():
    ticker = "TEST.NS"
    port = _make_open_position_portfolio(ticker)
    port.queue_pending_sell(ticker, "CHANDELIER")
    with pytest.raises(ValueError, match="already"):
        port.queue_pending_sell(ticker, "STRATEGY_SIGNAL")


# =============================================================================
# _process_stock() — RISK_EXIT and strategy SELL wired to needs_amo_order
# =============================================================================

def _make_df(n_bars: int = 70) -> pd.DataFrame:
    dates  = pd.date_range("2026-01-01", periods=n_bars, freq="B")
    prices = np.linspace(120.0, 100.0, n_bars)   # declining, plausible for an exit
    return pd.DataFrame({
        "open":   prices * 1.001,
        "high":   prices * 1.002,
        "low":    prices * 0.998,
        "close":  prices,
        "volume": np.ones(n_bars) * 10_000_000,
    }, index=dates)


def _flat_signals(df, fast, slow):
    """No golden/death cross -- isolates the RM-exit path from strategy signals."""
    return pd.Series(0, index=df.index)


def _death_cross_signals(df, fast, slow):
    s = pd.Series(0, index=df.index)
    s.iloc[-1] = -1
    return s


def test_risk_exit_sets_needs_amo_order_sell_and_queues_portfolio_state():
    ticker = "TEST.NS"
    df = _make_df()
    port = _make_open_position_portfolio(ticker, shares=10)

    with (
        patch("paper_trading.signal_runner.generate_signals", side_effect=_flat_signals),
        patch(
            "engine.risk_manager.RiskManager.check_exit",
            return_value={"should_exit": True, "exit_reason": "CHANDELIER", "exit_price": 105.0},
        ),
    ):
        result = _process_stock(ticker, df, port, {ticker: 105.0}, "BULL", defer_buy=True)

    assert result["signal"] == "RISK_EXIT"
    assert result["needs_amo_order"] == "SELL"
    assert result["shares"] == 10

    pos = port.state["positions"][ticker]
    assert pos["pending_rm_exit"] is True
    assert pos["rm_exit_reason"] == "CHANDELIER"


def test_strategy_sell_sets_needs_amo_order_sell_and_queues_portfolio_state():
    ticker = "TEST.NS"
    df = _make_df()
    port = _make_open_position_portfolio(ticker, shares=10)

    with (
        patch("paper_trading.signal_runner.generate_signals", side_effect=_death_cross_signals),
        patch(
            "engine.risk_manager.RiskManager.check_exit",
            return_value={"should_exit": False, "exit_reason": None, "exit_price": None},
        ),
    ):
        result = _process_stock(ticker, df, port, {ticker: 105.0}, "BULL", defer_buy=True)

    assert result["signal"] == "SELL"
    assert result["needs_amo_order"] == "SELL"
    assert result["exit_reason"] == "STRATEGY_SIGNAL"

    pos = port.state["positions"][ticker]
    assert pos["pending_rm_exit"] is True
    assert pos["rm_exit_reason"] == "STRATEGY_SIGNAL"


def test_hold_does_not_set_needs_amo_order():
    """A plain HOLD (no exit, no cross) must not carry a needs_amo_order value --
    Step 13 relies on its absence (via .get()) to skip non-order tickers."""
    ticker = "TEST.NS"
    df = _make_df()
    port = _make_open_position_portfolio(ticker, shares=10)

    with (
        patch("paper_trading.signal_runner.generate_signals", side_effect=_flat_signals),
        patch(
            "engine.risk_manager.RiskManager.check_exit",
            return_value={"should_exit": False, "exit_reason": None, "exit_price": None},
        ),
    ):
        result = _process_stock(ticker, df, port, {ticker: 105.0}, "BULL", defer_buy=True)

    assert result["signal"] == "HOLD"
    assert result.get("needs_amo_order") is None
