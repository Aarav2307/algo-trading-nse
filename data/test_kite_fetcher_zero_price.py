"""
Regression tests for the zero-price garbage-row bug in
data/kite_fetcher.get_ohlcv().

Root cause (confirmed live against the real Kite API): for a stock queried
before its actual listing/IPO date, Kite's historical_data() can return
placeholder rows with open=high=low=close=0 and nonzero volume, instead of
simply omitting those dates. Confirmed for MAZDOCK.NS (real IPO ~Oct 2020):
querying 2018-01-01..2023-01-01 returned 7 such rows scattered Jan-Jul 2018,
followed by a ~2-year gap, then 553 real trading days starting 2020-10-12.

Before the fix, get_ohlcv() only dropped rows with a NaN close
(dropna(subset=["close"])) -- a literal 0.0 close is not NaN, so these rows
passed straight through as if they were real trading days. Downstream, this
silently became a "valid" first bar: engine/backtester.py's benchmark
entry-price calculation picked up close=0.0 at the phantom 2018-01-01 row
and crashed (ValueError: Invalid benchmark entry price). A ticker whose
entire requested window is garbage (e.g. MAZDOCK.NS's 2015-2019 extended
IS window, which predates its Oct-2020 listing entirely) left get_ohlcv()
returning an empty DataFrame after filtering, which used to crash with an
unhandled IndexError on df.index[0] instead of a catchable "insufficient
data" condition.

These tests mock kite.historical_data() to return synthetic zero-price rows
so the assertions don't depend on live Kite data or a specific point in time.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

from dateutil.tz import tzoffset

from data.kite_fetcher import get_ohlcv

_IST = tzoffset(None, 19800)  # +05:30


def _real_row(day: int, price: float = 100.0) -> dict:
    return {
        "date": datetime(2020, 10, day, 0, 0, tzinfo=_IST),
        "open": price, "high": price + 1, "low": price - 1,
        "close": price, "volume": 50_000,
    }


def _garbage_row(month: int) -> dict:
    """Zero-OHLC, nonzero-volume placeholder row, matching Kite's real shape."""
    return {
        "date": datetime(2018, month, 1, 0, 0, tzinfo=_IST),
        "open": 0, "high": 0, "low": 0, "close": 0, "volume": 670_998,
    }


def _run_get_ohlcv_with_mocked_records(records, start="2018-01-01", end="2023-01-01"):
    mock_kite = MagicMock()
    mock_kite.historical_data.return_value = records
    with patch("data.kite_fetcher._load_kite", return_value=mock_kite), \
         patch("data.kite_fetcher._get_instrument_token", return_value=12345):
        return get_ohlcv("MAZDOCK.NS", start, end)


def test_get_ohlcv_drops_zero_price_rows_mixed_with_real_data():
    """MAZDOCK.NS's actual shape: garbage pre-listing rows + real trading data."""
    records = [_garbage_row(m) for m in range(1, 8)] + [_real_row(d) for d in range(12, 20)]
    df = _run_get_ohlcv_with_mocked_records(records)

    assert len(df) == 8, f"expected only the 8 real rows to survive, got {len(df)}"
    assert (df["close"] > 0).all(), "no zero-price row should remain"
    assert df.index[0] == datetime(2020, 10, 12), (
        f"first surviving row must be the real listing date, got {df.index[0]!r}"
    )


def test_get_ohlcv_zero_price_row_not_treated_as_valid_first_bar():
    """
    The specific failure mode: a garbage row at the START of the requested
    range must not become index[0] of the returned DataFrame -- that's
    exactly what corrupted the backtester's benchmark entry-price calc.
    """
    records = [_garbage_row(1)] + [_real_row(d) for d in range(12, 15)]
    df = _run_get_ohlcv_with_mocked_records(records)

    assert df.index[0] != datetime(2018, 1, 1)
    assert df.iloc[0]["close"] > 0


def test_get_ohlcv_raises_when_every_row_is_garbage():
    """
    A ticker's requested window predating its listing entirely (e.g.
    MAZDOCK.NS's 2015-2019 extended IS window) must raise a catchable
    ValueError, not crash with an unhandled IndexError on an empty index.
    """
    records = [_garbage_row(m) for m in range(1, 6)]
    try:
        _run_get_ohlcv_with_mocked_records(records, start="2015-01-01", end="2019-12-31")
        assert False, "expected ValueError when every row is a garbage zero-price row"
    except ValueError as exc:
        assert "No real data" in str(exc)
    except IndexError:
        assert False, "must raise ValueError, not leak an unhandled IndexError"


def test_get_ohlcv_all_real_data_unaffected():
    """A ticker with no garbage rows at all must be completely unaffected."""
    records = [_real_row(d, price=100.0 + d) for d in range(12, 20)]
    df = _run_get_ohlcv_with_mocked_records(records)

    assert len(df) == 8
    assert (df["close"] > 0).all()
