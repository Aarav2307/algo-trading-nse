"""
Regression tests for the tz-stripping bug in data/kite_fetcher.get_ohlcv().

Root cause (confirmed live on the Lightsail server, pandas 2.3.3):
DatetimeIndex.map(lambda dt: dt.replace(tzinfo=None)) silently preserves the
original tz-aware dtype even though each mapped Timestamp is individually
naive. hasattr(df.index, "tz") and df.index.tz is not None both correctly
evaluate True/truthy and the strip branch DOES run -- it just doesn't work.
No exception is raised; the returned DataFrame index is silently tz-aware.

These tests mock kite.historical_data() to return synthetic tz-aware
dateutil.tzoffset records (matching Kite's actual response shape) so the
assertion holds regardless of which pandas version runs the suite -- the old
.map() bug only reproduces on pandas 2.3.x, not the 3.0.x used in local dev,
so a live-only test would pass locally while production stayed broken.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

from dateutil.tz import tzoffset

from data.kite_fetcher import get_ohlcv

_IST = tzoffset(None, 19800)  # +05:30, matches Kite's actual response tzinfo


def _make_records(n_days: int) -> list[dict]:
    return [
        {
            "date": datetime(2026, 7, 1 + i, 0, 0, tzinfo=_IST),
            "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
            "close": 100.5 + i, "volume": 1000 + i,
        }
        for i in range(n_days)
    ]


def _run_get_ohlcv_with_mocked_records(records):
    """Patch auth/token/instrument lookups and kite.historical_data() return value."""
    mock_kite = MagicMock()
    mock_kite.historical_data.return_value = records
    with patch("data.kite_fetcher._load_kite", return_value=mock_kite), \
         patch("data.kite_fetcher._get_instrument_token", return_value=12345):
        return get_ohlcv("TESTSTOCK.NS", "2026-07-01", "2026-07-31")


def test_get_ohlcv_strips_tz_multi_row():
    df = _run_get_ohlcv_with_mocked_records(_make_records(8))
    assert df.index.tz is None, f"Index must be tz-naive, got tz={df.index.tz}"
    assert len(df) == 8


def test_get_ohlcv_strips_tz_single_row():
    df = _run_get_ohlcv_with_mocked_records(_make_records(1))
    assert df.index.tz is None, f"Index must be tz-naive, got tz={df.index.tz}"
    assert len(df) == 1


def test_get_ohlcv_index_values_correct_after_strip():
    """The stripped index must retain the correct wall-clock date, not just be naive."""
    df = _run_get_ohlcv_with_mocked_records(_make_records(3))
    assert df.index[0] == datetime(2026, 7, 1), f"Wrong date after strip: {df.index[0]!r}"
    assert df.index[0].tzinfo is None


def test_get_ohlcv_raises_on_empty_records():
    """Empty records list must raise ValueError before reaching the tz-strip block."""
    try:
        _run_get_ohlcv_with_mocked_records([])
        assert False, "expected ValueError for empty records"
    except ValueError as exc:
        assert "No data returned" in str(exc)
