"""
Regression tests for data/fetcher.flatten_yf_columns() — the shared helper
extracted during the Jul 21 architecture review to stop fetcher.py and
screener/sma_screener.py from each hand-rolling the same yfinance
MultiIndex-column-flattening logic. A convention change here (e.g. a new
edge case in how yfinance shapes single-ticker downloads) now only needs
to be fixed in one place.
"""
import pandas as pd

from data.fetcher import flatten_yf_columns


def test_flat_columns_are_left_unchanged():
    df = pd.DataFrame({"Open": [1.0], "Close": [2.0]})
    out = flatten_yf_columns(df)
    assert list(out.columns) == ["Open", "Close"]
    assert not isinstance(out.columns, pd.MultiIndex)


def test_multiindex_columns_are_flattened_to_field_names():
    df = pd.DataFrame({("Open", "RELIANCE.NS"): [1.0], ("Close", "RELIANCE.NS"): [2.0]})
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    out = flatten_yf_columns(df)
    assert list(out.columns) == ["Open", "Close"]
    assert not isinstance(out.columns, pd.MultiIndex)
