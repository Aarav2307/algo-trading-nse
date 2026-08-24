"""
Tests for Kite API rate-limiting in morning_fill_check.

Mirrors test_signal_runner_fetch.py's sleep tests: every SUCCESSFUL Kite call
is followed by exactly one sleep(1.1); an exception path adds none, because a
failed call consumed no quota.

morning_fill_check.py has four Kite call sites, not the three obvious ones —
_fetch_live_order_status() calls kite.orders() and is covered here too, even
though it is unreachable while LIVE_TRADING_MODE is False.

No network calls: get_ohlcv and time.sleep are both patched.
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc

_TICKER = "PACE.NS"
_DAY = date(2026, 8, 24)


def _fake_df(n=3):
    idx = pd.date_range(end=pd.Timestamp(_DAY), periods=n, freq="B")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        index=idx,
    )


# ── One sleep per successful fetch, for each of the three OHLCV helpers ──────

@pytest.mark.parametrize("fn_name", ["_fetch_open_price", "_fetch_prev_close", "_fetch_close_price"])
def test_each_ohlcv_helper_sleeps_once_per_successful_fetch(fn_name):
    fn = getattr(mfc, fn_name)
    with patch.object(mfc, "get_ohlcv", return_value=_fake_df()) as mock_fetch, \
         patch.object(mfc.time, "sleep") as mock_sleep:
        fn(_TICKER, _DAY)
        assert mock_fetch.call_count == 1
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(1.1)


# ── The sleep fires BEFORE the result guards, not after ─────────────────────

def test_open_price_sleeps_even_when_empty_df_guard_returns_none():
    """
    An empty DataFrame makes _fetch_open_price return None, but the API call
    already consumed quota — the sleep must still have happened.
    """
    with patch.object(mfc, "get_ohlcv", return_value=pd.DataFrame()), \
         patch.object(mfc.time, "sleep") as mock_sleep:
        assert mfc._fetch_open_price(_TICKER, _DAY) is None
        assert mock_sleep.call_count == 1


def test_prev_close_sleeps_even_when_empty_df_guard_returns_zero():
    with patch.object(mfc, "get_ohlcv", return_value=pd.DataFrame()), \
         patch.object(mfc.time, "sleep") as mock_sleep:
        assert mfc._fetch_prev_close(_TICKER, _DAY) == 0.0
        assert mock_sleep.call_count == 1


def test_close_price_sleeps_even_when_empty_df_guard_returns_none():
    with patch.object(mfc, "get_ohlcv", return_value=pd.DataFrame()), \
         patch.object(mfc.time, "sleep") as mock_sleep:
        assert mfc._fetch_close_price(_TICKER, _DAY) is None
        assert mock_sleep.call_count == 1


# ── No sleep on the exception path — a failed call consumed no quota ─────────

@pytest.mark.parametrize("fn_name", ["_fetch_open_price", "_fetch_prev_close", "_fetch_close_price"])
def test_no_sleep_when_fetch_raises(fn_name):
    fn = getattr(mfc, fn_name)
    with patch.object(mfc, "get_ohlcv", side_effect=ConnectionError("token expired")), \
         patch.object(mfc.time, "sleep") as mock_sleep:
        fn(_TICKER, _DAY)          # all three swallow and return a default
        assert mock_sleep.call_count == 0


# ── Fourth call site: kite.orders() ─────────────────────────────────────────

def test_live_order_status_sleeps_after_kite_orders():
    """
    _fetch_live_order_status() is the 4th Kite call site in this module and is
    easy to miss — it calls kite.orders(), not get_ohlcv. Unreachable while
    LIVE_TRADING_MODE is False, but paced for when that flips.
    """
    kite = MagicMock()
    kite.orders.return_value = [
        {"order_id": "abc", "status": "COMPLETE", "average_price": 100.0,
         "filled_quantity": 10},
    ]
    with patch.object(mfc.time, "sleep") as mock_sleep:
        out = mfc._fetch_live_order_status("abc", kite)
        assert out["status"] == "COMPLETE"
        assert kite.orders.call_count == 1
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(1.1)


def test_live_order_status_no_sleep_without_order_id():
    """Early return before any API call must not sleep."""
    with patch.object(mfc.time, "sleep") as mock_sleep:
        out = mfc._fetch_live_order_status("", MagicMock())
        assert out["status"] == "UNKNOWN"
        assert mock_sleep.call_count == 0


def test_live_order_status_no_sleep_when_kite_raises():
    kite = MagicMock()
    kite.orders.side_effect = RuntimeError("network down")
    with patch.object(mfc.time, "sleep") as mock_sleep:
        out = mfc._fetch_live_order_status("abc", kite)
        assert out["status"] == "ERROR"
        assert mock_sleep.call_count == 0


# ── Every Kite call site in the module is paced ──────────────────────────────

def test_no_unpaced_kite_call_site_remains():
    """
    Guard against a future call site being added without pacing. Counts Kite
    call sites and sleep calls in the source and requires them to match.
    """
    src = (_ROOT / "paper_trading" / "morning_fill_check.py").read_text()
    call_sites = src.count("get_ohlcv(ticker, start, end)") + src.count("kite.orders()")
    sleeps = src.count("time.sleep(1.1)")
    assert call_sites == sleeps, (
        f"{call_sites} Kite call sites but {sleeps} sleep(1.1) calls — "
        f"a call site is unpaced"
    )
