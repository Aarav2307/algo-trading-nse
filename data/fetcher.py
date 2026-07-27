"""
Data fetcher — yfinance-backed OHLCV source.

get_ohlcv() shares its signature and DataFrame contract with
data/kite_fetcher.get_ohlcv() by design, but the two are NOT
interchangeable via a config flag — each caller picks one deliberately:
  - data.kite_fetcher: live/recent NSE data (paper trading, screening, WF gate)
  - data.fetcher (this module): pre-2023 history Kite can't serve, and
    yfinance-only CLI paths that must work without a Kite login

Returned DataFrame columns (all lowercase):
    open, high, low, close, volume
Index: DatetimeIndex (timezone-naive, IST dates)
"""

import yfinance as yf
import pandas as pd


def flatten_yf_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance returns MultiIndex columns when downloading a single ticker
    with auto_adjust=True on newer versions — flatten them down to plain
    field names ("Open", "Close", ...). No-op if columns are already flat.
    Shared by every single-ticker yf.download() call site in the repo so
    this quirk is handled in exactly one place.
    """
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


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

    raw = flatten_yf_columns(raw)
    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]

    # Strip timezone info so date math stays simple
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)
    df.index.name = "date"

    # Drop rows with missing close prices (NSE occasionally has data gaps)
    df = df.dropna(subset=["close"])

    print(f"[fetcher] {ticker}: {len(df)} trading days loaded ({df.index[0].date()} → {df.index[-1].date()})")
    return df
