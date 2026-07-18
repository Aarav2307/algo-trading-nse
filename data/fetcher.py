"""
Data fetcher — Phase 1: yfinance (historical data).

The single public function `get_ohlcv()` is the only data interface
the rest of the system depends on. To switch to Kite Connect in Phase 2:
  1. Create data/kite_fetcher.py with the same `get_ohlcv()` signature.
  2. Change the import in run_backtest.py. Strategy and engine code stays untouched.

Returned DataFrame columns (all lowercase):
    open, high, low, close, volume
Index: DatetimeIndex (timezone-naive, IST dates)
"""

import yfinance as yf
import pandas as pd


def get_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    Download daily OHLCV data for an NSE stock.

    Args:
        ticker: yfinance ticker, e.g. "RELIANCE.NS"
        start:  start date string "YYYY-MM-DD" (inclusive)
        end:    end date string  "YYYY-MM-DD" (exclusive, like pandas)

    Returns:
        DataFrame with columns [open, high, low, close, volume],
        indexed by date. Raises ValueError if no data is returned.
    """
    raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)

    if raw.empty:
        raise ValueError(
            f"No data returned for '{ticker}' between {start} and {end}. "
            "Check the ticker symbol (NSE stocks end in .NS) and date range."
        )

    # yfinance returns MultiIndex columns when downloading a single ticker
    # with auto_adjust=True on newer versions — flatten them.
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]

    # Strip timezone info so date math stays simple
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)
    df.index.name = "date"

    # Drop rows with missing close prices (NSE occasionally has data gaps)
    df = df.dropna(subset=["close"])

    print(f"[fetcher] {ticker}: {len(df)} trading days loaded ({df.index[0].date()} → {df.index[-1].date()})")
    return df
