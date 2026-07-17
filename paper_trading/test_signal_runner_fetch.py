"""Tests for _fetch_stock_data rate-limiting behaviour in signal_runner."""
import pytest
from unittest.mock import patch
import pandas as pd
from datetime import date
from paper_trading.signal_runner import _fetch_stock_data, STOCKS, SMA_SLOW


def _fake_df(n=None):
    n = n or (SMA_SLOW + 20)
    idx = pd.date_range(end=pd.Timestamp(date.today()), periods=n, freq="B")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        index=idx,
    )


def test_fetch_stock_data_sleeps_once_per_successful_fetch():
    """Every successful get_ohlcv call must be followed by exactly one sleep(1.1)."""
    with patch("paper_trading.signal_runner.get_ohlcv", return_value=_fake_df()) as mock_fetch, \
         patch("paper_trading.signal_runner.time.sleep") as mock_sleep:
        _fetch_stock_data(date.today())
        assert mock_fetch.call_count == len(STOCKS)
        assert mock_sleep.call_count == len(STOCKS)
        mock_sleep.assert_called_with(1.1)


def test_fetch_stock_data_sleeps_even_when_bar_count_guard_skips_ticker():
    """A ticker rejected by the bar-count guard (too few bars) must still
    have consumed a sleep, since the API call itself already happened."""
    short_df = _fake_df(n=SMA_SLOW - 5)  # too few bars, will be skipped by guard
    with patch("paper_trading.signal_runner.get_ohlcv", return_value=short_df), \
         patch("paper_trading.signal_runner.time.sleep") as mock_sleep:
        result = _fetch_stock_data(date.today())
        assert mock_sleep.call_count == len(STOCKS)
        assert len(result) == 0  # all tickers skipped by the guard


def test_fetch_stock_data_no_extra_sleep_on_fatal_auth_error():
    """A ConnectionError from get_ohlcv must not add a sleep call for that
    ticker — the sleep only follows a SUCCESSFUL fetch.

    The except block calls sys.exit(1) after printing the fatal message,
    so we expect SystemExit to be raised.
    """
    with patch("paper_trading.signal_runner.get_ohlcv", side_effect=ConnectionError("token expired")), \
         patch("paper_trading.signal_runner.time.sleep") as mock_sleep:
        with pytest.raises(SystemExit):
            _fetch_stock_data(date.today())
        assert mock_sleep.call_count == 0


def test_news_flags_file_is_absolute_and_cwd_independent():
    """
    Regression test for the Jul 8/13 'No flags file found' bug: the
    reader's file path must be absolute so it resolves correctly
    regardless of the process's working directory at check time.
    """
    import os
    from paper_trading.signal_runner import NEWS_FLAGS_FILE

    assert NEWS_FLAGS_FILE.is_absolute(), (
        f"NEWS_FLAGS_FILE is not absolute: {NEWS_FLAGS_FILE!r}"
    )

    original_cwd = os.getcwd()
    exists_from_root = NEWS_FLAGS_FILE.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = NEWS_FLAGS_FILE.exists()
    finally:
        os.chdir(original_cwd)

    assert exists_from_root == exists_from_tmp, (
        f"NEWS_FLAGS_FILE.exists() is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )


def test_manual_blocks_file_is_absolute_and_cwd_independent():
    """
    Regression test: same relative-path bug class as NEWS_FLAGS_FILE
    (fixed in commit 57f112d) also existed for MANUAL_BLOCKS_FILE.
    """
    import os
    from paper_trading.signal_runner import MANUAL_BLOCKS_FILE

    assert MANUAL_BLOCKS_FILE.is_absolute(), (
        f"MANUAL_BLOCKS_FILE is not absolute: {MANUAL_BLOCKS_FILE!r}"
    )

    original_cwd = os.getcwd()
    exists_from_root = MANUAL_BLOCKS_FILE.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = MANUAL_BLOCKS_FILE.exists()
    finally:
        os.chdir(original_cwd)

    assert exists_from_root == exists_from_tmp, (
        f"MANUAL_BLOCKS_FILE.exists() is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )
