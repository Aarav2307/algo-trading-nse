"""
Data fetcher — Zerodha Kite Connect (live NSE data).

get_ohlcv() shares its signature and DataFrame contract with data/fetcher.py
by design, but the two are NOT interchangeable via a config flag — this
module is for live/recent NSE data (paper trading, screening, WF gate).
data/fetcher.py (yfinance) is used instead where Kite can't serve the data
(pre-2023 history) or a Kite login isn't available (some CLI paths).

Requires:
    auth/access_token.txt  — auto-written by auth/kite_login.py after login
    KITE_API_KEY in .env

Returned DataFrame columns (all lowercase):
    open, high, low, close, volume
Index: DatetimeIndex (timezone-naive, IST dates), named "date"
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

load_dotenv()

_AUTH_DIR  = Path(__file__).parent.parent / "auth"
_TOKEN_FILE = _AUTH_DIR / "access_token.txt"
_CACHE_FILE = _AUTH_DIR / "nse_instruments.json"

# Network timeout (seconds) for all Kite API calls made through this client.
# Without this, a hung Zerodha API (maintenance, network stall) blocks the
# 3:45 PM signal run indefinitely — no AMO orders get queued for tomorrow's
# open. Confirmed via KiteConnect.__init__ signature inspection that
# `timeout` is a direct constructor kwarg in the installed pykiteconnect
# version (see docstring/commit context — do not remove this comment).
KITE_REQUEST_TIMEOUT_SECONDS = 15


def _auto_refresh_token(max_retries: int = 1) -> bool:
    """
    Automatically refresh the Kite Connect token by running auto_login.py.
    Called when a TokenException is caught during any Kite API call.

    Returns True if refresh succeeded, False if it failed.
    Never raises exceptions — logs errors and returns False.

    Works on both server (automated TOTP) and local machine (same TOTP secret in .env).
    """
    import subprocess
    import sys

    print("[kite_fetcher] Token expired — attempting automatic refresh via auto_login.py...")

    try:
        result = subprocess.run(
            [sys.executable, "auth/auto_login.py"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=Path(__file__).parent.parent,
        )

        if result.returncode == 0:
            print("[kite_fetcher] ✅ Token refreshed successfully")
            return True
        else:
            print(f"[kite_fetcher] ❌ Token refresh failed (exit code {result.returncode})")
            if result.stderr:
                print(f"[kite_fetcher] Error: {result.stderr.strip()[:200]}")
            return False

    except subprocess.TimeoutExpired:
        print("[kite_fetcher] ❌ Token refresh timed out after 30 seconds")
        return False
    except Exception as e:
        print(f"[kite_fetcher] ❌ Token refresh error: {e}")
        return False


def _load_kite() -> KiteConnect:
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise EnvironmentError("KITE_API_KEY not found in .env")

    if not _TOKEN_FILE.exists():
        raise FileNotFoundError(
            "auth/access_token.txt not found. Run auth/kite_login.py first."
        )

    lines = _TOKEN_FILE.read_text().splitlines()
    access_token = lines[0].strip() if lines else ""
    if not access_token:
        raise ValueError(
            "auth/access_token.txt is empty. Run auth/kite_login.py first."
        )

    kite = KiteConnect(api_key=api_key, timeout=KITE_REQUEST_TIMEOUT_SECONDS)
    kite.set_access_token(access_token)
    return kite


def _get_instrument_token(kite: KiteConnect, symbol: str) -> int:
    """
    Return NSE instrument_token for a trading symbol.

    Fetches and caches the full NSE instrument list once per calendar day.
    Cache lives at auth/nse_instruments.json.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text())
            if cached.get("date") == today:
                token_map = cached["tokens"]
                if symbol in token_map:
                    return token_map[symbol]
                raise ValueError(
                    f"Symbol '{symbol}' not found in NSE instrument list "
                    f"(cached {today}). Check the trading symbol (no .NS suffix). "
                    f"Examples: RELIANCE, INFY, TMPV"
                )
        except (json.JSONDecodeError, KeyError):
            pass  # corrupt cache — fall through to fresh fetch

    print("[kite_fetcher] Fetching NSE instrument list from Kite...")
    try:
        instruments = kite.instruments("NSE")
    except TokenException:
        if _auto_refresh_token():
            kite = _load_kite()
            try:
                instruments = kite.instruments("NSE")
            except Exception as retry_exc:
                raise ConnectionError(
                    f"Kite token refreshed but instrument list fetch failed: {retry_exc}"
                ) from retry_exc
        else:
            raise ConnectionError(
                "Kite session expired and auto-refresh failed. "
                "Run auth/auto_login.py manually to get a fresh token."
            )

    token_map = {
        inst["tradingsymbol"]: inst["instrument_token"]
        for inst in instruments
    }
    _CACHE_FILE.write_text(json.dumps({"date": today, "tokens": token_map}, indent=2))
    print(f"[kite_fetcher] Cached {len(token_map)} NSE instruments.")

    if symbol not in token_map:
        raise ValueError(
            f"Symbol '{symbol}' not found in NSE instrument list. "
            f"Check the trading symbol (no .NS suffix). Examples: RELIANCE, INFY, TMPV"
        )
    return token_map[symbol]


def get_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    Fetch daily OHLCV data for an NSE stock via Kite Connect.

    Drop-in replacement for data/fetcher.get_ohlcv() — identical signature.

    Args:
        ticker: NSE ticker in yfinance format, e.g. "TMPV.NS" or "RELIANCE.NS"
                The ".NS" suffix is stripped internally to get the trading symbol.
        start:  start date string "YYYY-MM-DD" (inclusive)
        end:    end date string   "YYYY-MM-DD" (exclusive, same convention as yfinance)

    Returns:
        DataFrame with columns [open, high, low, close, volume],
        DatetimeIndex (timezone-naive, IST dates), named "date".
        Raises ValueError if no data is returned.
    """
    symbol = ticker.upper().removesuffix(".NS").removesuffix(".BSE")

    try:
        kite = _load_kite()
        instrument_token = _get_instrument_token(kite, symbol)
    except (FileNotFoundError, ValueError, EnvironmentError, ConnectionError):
        raise
    except TokenException as exc:
        if _auto_refresh_token():
            try:
                kite = _load_kite()
                instrument_token = _get_instrument_token(kite, symbol)
            except Exception as retry_exc:
                raise ConnectionError(
                    f"Kite session refresh succeeded but instrument lookup failed for '{symbol}': {retry_exc}"
                ) from retry_exc
        else:
            raise ConnectionError(
                "Kite session expired and auto-refresh failed. "
                "Run auth/auto_login.py manually to get a fresh token."
            ) from exc
    except Exception as exc:
        if "Invalid session" in str(exc) or "token" in str(exc).lower():
            raise ConnectionError(
                "Kite session expired or invalid. Run auth/kite_login.py first."
            ) from exc
        raise

    # Kite to_date is inclusive; yfinance end is exclusive — subtract 1 day.
    from_dt = datetime.strptime(start, "%Y-%m-%d")
    to_dt   = datetime.strptime(end,   "%Y-%m-%d") - timedelta(days=1)

    try:
        records = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_dt,
            to_date=to_dt,
            interval="day",
        )
    except TokenException as exc:
        if _auto_refresh_token():
            try:
                kite = _load_kite()
                records = kite.historical_data(
                    instrument_token=instrument_token,
                    from_date=from_dt,
                    to_date=to_dt,
                    interval="day",
                )
            except Exception as retry_exc:
                raise ConnectionError(
                    f"Kite session refresh succeeded but retry failed for '{symbol}': {retry_exc}"
                ) from retry_exc
        else:
            raise ConnectionError(
                "Kite session expired and auto-refresh failed. "
                "Run auth/auto_login.py manually to get a fresh token."
            ) from exc
    except Exception as exc:
        if "Invalid session" in str(exc) or "token" in str(exc).lower():
            raise ConnectionError(
                "Kite session expired. Run auth/kite_login.py to get a fresh token."
            ) from exc
        raise

    if not records:
        raise ValueError(
            f"No data returned for '{ticker}' ({symbol}) between {start} and {end}. "
            "Check the symbol and date range."
        )

    df = pd.DataFrame(records)[["date", "open", "high", "low", "close", "volume"]].copy()

    # Kite returns tz-aware IST datetimes (dateutil.tzoffset, +05:30). Strip tz
    # on the COLUMN via .dt.tz_localize(None) BEFORE set_index(), not via
    # DatetimeIndex.map() on the index. Verified live on the Lightsail server
    # (pandas 2.3.3): DatetimeIndex.map(lambda dt: dt.replace(tzinfo=None))
    # silently preserves the original tz-aware dtype even though each mapped
    # Timestamp is individually naive -- the old .map()-based strip was a
    # silent no-op there, leaving every DataFrame this function returned in
    # production tz-aware. pd.to_datetime() first guarantees .dt is available
    # regardless of how pandas inferred the raw records' dtype.
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    df = df.set_index("date")
    df.index.name = "date"

    # Kite occasionally returns garbage placeholder rows (zero OHLC, nonzero
    # volume) for dates before a stock's actual listing/IPO -- confirmed live
    # for MAZDOCK.NS: 7 rows scattered Jan-Jul 2018, real trading data starts
    # 2020-10-12. A stock's price is never actually zero while trading; a
    # zero close always means "no data," never "the market said zero." Null
    # out the whole OHLC row so the dropna below removes it the same way it
    # already removes a genuinely missing close -- NaN is the only correct
    # sentinel for missing price data, precisely because it propagates and
    # fails loud, whereas 0.0 silently participates in downstream arithmetic
    # (e.g. becomes a benchmark entry price, divides-by-zero, or looks like
    # a valid signal bar).
    zero_price_mask = df["close"] <= 0
    if zero_price_mask.any():
        print(
            f"[kite_fetcher] {ticker}: dropping {int(zero_price_mask.sum())} "
            f"garbage zero-price row(s) from Kite (likely pre-listing dates)"
        )
        df.loc[zero_price_mask, ["open", "high", "low", "close"]] = float("nan")

    df = df.dropna(subset=["close"])

    if df.empty:
        raise ValueError(
            f"No real data for '{ticker}' ({symbol}) between {start} and {end} — "
            "every row Kite returned was a garbage zero-price placeholder "
            "(stock likely wasn't listed yet in this window)."
        )

    print(
        f"[kite_fetcher] {ticker}: {len(df)} trading days loaded "
        f"({df.index[0].date()} → {df.index[-1].date()})"
    )
    return df
