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


def test_state_file_is_absolute_and_cwd_independent():
    """
    Regression test: STATE_FILE must be absolute — highest-stakes of this bug
    class. A cwd-dependent path could cause the system to silently read/write
    portfolio state to the wrong location.
    Also asserts .exists() from the project root: confirms the path resolves to
    the real portfolio state file, not just that it happens to be absolute.
    """
    import os
    from paper_trading.signal_runner import STATE_FILE

    assert STATE_FILE.is_absolute(), (
        f"STATE_FILE is not absolute: {STATE_FILE!r}"
    )

    original_cwd = os.getcwd()
    exists_from_root = STATE_FILE.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = STATE_FILE.exists()
    finally:
        os.chdir(original_cwd)

    assert exists_from_root == exists_from_tmp, (
        f"STATE_FILE.exists() is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )
    assert exists_from_root, (
        f"STATE_FILE not found from project root — "
        f"portfolio state missing at {STATE_FILE}"
    )


def test_log_csv_is_absolute_and_cwd_independent():
    """Regression test: LOG_CSV must be absolute (same bug class as NEWS_FLAGS_FILE)."""
    import os
    from paper_trading.signal_runner import LOG_CSV

    assert LOG_CSV.is_absolute(), f"LOG_CSV is not absolute: {LOG_CSV!r}"

    original_cwd = os.getcwd()
    exists_from_root = LOG_CSV.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = LOG_CSV.exists()
    finally:
        os.chdir(original_cwd)

    assert exists_from_root == exists_from_tmp, (
        f"LOG_CSV.exists() is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )


def test_logs_dir_is_absolute_and_cwd_independent():
    """Regression test: LOGS_DIR must be absolute (same bug class as NEWS_FLAGS_FILE)."""
    import os
    from paper_trading.signal_runner import LOGS_DIR

    assert LOGS_DIR.is_absolute(), f"LOGS_DIR is not absolute: {LOGS_DIR!r}"

    original_cwd = os.getcwd()
    exists_from_root = LOGS_DIR.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = LOGS_DIR.exists()
    finally:
        os.chdir(original_cwd)

    assert exists_from_root == exists_from_tmp, (
        f"LOGS_DIR.exists() is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )


def test_token_file_is_absolute_and_cwd_independent():
    """Regression test: TOKEN_FILE must be absolute (same bug class as NEWS_FLAGS_FILE)."""
    import os
    from paper_trading.signal_runner import TOKEN_FILE

    assert TOKEN_FILE.is_absolute(), f"TOKEN_FILE is not absolute: {TOKEN_FILE!r}"

    original_cwd = os.getcwd()
    exists_from_root = TOKEN_FILE.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = TOKEN_FILE.exists()
    finally:
        os.chdir(original_cwd)

    assert exists_from_root == exists_from_tmp, (
        f"TOKEN_FILE.exists() is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )


def test_amo_config_order_log_file_is_absolute_and_cwd_independent():
    """AMO_CONFIG['order_log_file'] must be an absolute path — same bug class as the 6 Path() fixes."""
    import os
    from pathlib import Path
    from paper_trading.signal_runner import AMO_CONFIG

    p = Path(AMO_CONFIG["order_log_file"])
    assert p.is_absolute(), f"AMO_CONFIG['order_log_file'] is not absolute: {p!r}"

    original_cwd = os.getcwd()
    exists_from_root = p.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = p.exists()
    finally:
        os.chdir(original_cwd)

    assert exists_from_root == exists_from_tmp, (
        f"AMO_CONFIG['order_log_file'] is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )
