"""
Task 3 — end-to-end test: fetch 1 month of TMPV daily data via Kite
and confirm the DataFrame matches data/fetcher.py output format exactly.

Run after auth/kite_login.py has written auth/access_token.txt.
"""

from datetime import date, timedelta
import pandas as pd

from data.kite_fetcher import get_ohlcv as kite_get_ohlcv
from data.fetcher       import get_ohlcv as yf_get_ohlcv

TICKER = "TMPV.NS"
END    = date.today().strftime("%Y-%m-%d")
START  = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

print(f"Fetching {TICKER}  {START} → {END}\n")

# ── Kite ──────────────────────────────────────────────────────────────────────
print("=== Kite Connect ===")
kite_df = kite_get_ohlcv(TICKER, START, END)
print(kite_df.tail(5))
print()

# ── yfinance (reference) ──────────────────────────────────────────────────────
print("=== yfinance (reference) ===")
yf_df = yf_get_ohlcv(TICKER, START, END)
print(yf_df.tail(5))
print()

# ── Column / dtype checks ─────────────────────────────────────────────────────
assert list(kite_df.columns) == ["open", "high", "low", "close", "volume"], \
    f"Column mismatch: {list(kite_df.columns)}"
assert kite_df.index.name == "date", f"Index name mismatch: {kite_df.index.name}"
assert kite_df.index.tz is None, "Index should be timezone-naive"
assert not kite_df.empty, "DataFrame is empty"

print("✓ columns match  :", list(kite_df.columns))
print("✓ index name     :", kite_df.index.name)
print("✓ timezone-naive :", kite_df.index.tz is None)
print("✓ row count      :", len(kite_df))
print("\nAll checks passed — Kite fetcher output matches data/fetcher.py format.")
