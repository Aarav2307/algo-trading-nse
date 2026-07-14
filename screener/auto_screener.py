"""
screener/auto_screener.py — Automated bi-weekly universe expansion screener.

Runs every Wednesday and Sunday at 6 PM IST via cron.
Pipeline:
  1. Fetch NIFTY 500 constituent list from NSE
  2. Filter stocks with >= 744 trading days (2 years) of history
  3. Compute Hurst exponent + ADX for regime classification
  4. Filter TRENDING_STRONG (H > 0.48, ADX > 25)
  5. Compute SMA 20/50 gap — flag stocks closest to golden cross
  6. Run correlation check against current trading universe
  7. Check existing universe stocks for regime degradation
  8. Generate ranked ADD / WATCH / REMOVE recommendations
  9. Send HTML email report via SendGrid

Usage:
    python screener/auto_screener.py
    python screener/auto_screener.py --dry-run   # print report, don't send email
"""

import sys
import os
import math
import argparse
import json
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.kite_fetcher import get_ohlcv

# ── Configuration ─────────────────────────────────────────────────────────────

PORTFOLIO_STATE   = _ROOT / "paper_trading" / "portfolio_state.json"
MIN_BARS          = 744       # 2 years minimum history
HURST_THRESHOLD   = 0.48      # minimum Hurst for TRENDING_STRONG
                              # (log-price variogram values are ~0.07 lower than regime_classifier's
                              # full R/S algorithm — calibrated so all 10 validated stocks pass)
HURST_DEGRADE     = 0.45      # existing stock flagged for removal below this
                              # (similarly recalibrated for log-price variogram estimator)
ADX_THRESHOLD     = 25.0      # minimum ADX for TRENDING_STRONG
ADX_DEGRADE       = 22.0      # existing stock flagged for removal below this
MAX_CORR_AVG      = 0.60      # max avg correlation with existing universe
MAX_CORR_PAIR     = 0.70      # max pairwise correlation
MIN_LIQUIDITY     = 10_000_000  # ₹1 crore avg daily turnover (vol × price, 20-day)
MAX_PRICE         = 15_000    # skip stocks above ₹15,000/share
START_DATE           = "2023-01-01"
END_DATE             = date.today().strftime("%Y-%m-%d")
SMA_FAST             = 20
SMA_SLOW             = 50
DEGRADATION_TRACKER  = _ROOT / "screener" / "degradation_tracker.json"
CANDIDATES_FILE      = _ROOT / "screener" / "latest_candidates.json"
# Calendar-day window around a NIFTY regime transition within which degradation
# flags are annotated as potentially mechanical (ADX dips ~3-4 trading days ≈
# 5 calendar days after an SMA-based BULL↔BEAR flip).  Matches EARNINGS_DAYS_AHEAD
# precedent in news_monitor.py.  Annotation only — never suppresses flags.
#
# Known asymmetry (accepted limitation, not a bug): live annotation only ever
# catches POST-transition flags (days_since_transition >= 0).  A flag that fires
# before the transition cannot be annotated at the time it is recorded because
# signal_runner.py only writes regime_transition_date when the flip actually
# occurs — the future transition date is not knowable in advance.  Pre-transition
# proximity (days_since_transition < 0) can only be added retroactively via
# manual backfill after the transition has been observed, exactly as was done
# for the 2026-07-05 flags in degradation_tracker.json on 2026-07-09.
REGIME_TRANSITION_WINDOW = 5
# Must match paper_trading/signal_runner.py LOOKBACK_CALENDAR_DAYS exactly.
# The signal runner fetches this many calendar days of history when generating
# entry/exit signals. Using the same window here ensures screener and live
# system see the same SMA crossover picture.
SIGNAL_LOOKBACK_DAYS: int = 120

# Cache for last successful NIFTY 500 fetch
# Used as fallback if live NSE fetch fails
NIFTY500_CACHE     = _ROOT / "screener" / "nifty500_cache.json"
NIFTY500_MIN_COUNT = 400  # below this = fetch failure

# ── Degradation tracker ───────────────────────────────────────────────────────

def load_degradation_tracker() -> dict:
    """
    Load degradation tracker from JSON with full integrity checking.

    Integrity checks performed:
    1. File exists and is valid JSON
    2. Each stock entry has all required fields
    3. consecutive_flags is non-negative integer
    4. Dates are valid ISO format strings
    5. _meta section exists with required fields

    If file is missing: returns empty valid tracker (not an error)
    If file is corrupt: logs warning, returns empty valid tracker, saves backup of corrupt file
    If file has old schema (version < 2): migrates to new schema automatically

    Returns always-valid tracker dict — never raises exceptions.
    """
    import shutil
    from datetime import datetime

    def empty_tracker() -> dict:
        return {
            "_meta": {
                "last_screen_date": date.today().isoformat(),
                "screen_count": 0,
                "created": date.today().isoformat(),
                "version": "2",
            }
        }

    def valid_stock_entry(entry: dict) -> bool:
        """Check if a stock entry has all required fields with correct types."""
        required = ["consecutive_flags", "last_flagged", "last_screen_date", "flag_history"]
        if not all(k in entry for k in required):
            return False
        if not isinstance(entry["consecutive_flags"], int) or entry["consecutive_flags"] < 0:
            return False
        if not isinstance(entry["flag_history"], list):
            return False
        for date_field in ["last_flagged", "last_screen_date"]:
            try:
                datetime.strptime(entry[date_field], "%Y-%m-%d")
            except (ValueError, TypeError):
                return False
        return True

    def migrate_v1_entry(ticker: str, old_entry: dict) -> dict:
        """Migrate old schema (consecutive_flags + last_flagged only) to new schema."""
        last_flagged = old_entry.get("last_flagged", date.today().isoformat())
        consecutive  = old_entry.get("consecutive_flags", 0)
        if not last_flagged:
            last_flagged = date.today().isoformat()
        return {
            "consecutive_flags": consecutive,
            "last_flagged":      last_flagged,
            "last_screen_date":  last_flagged,  # best guess for old entries
            "flag_history":      [last_flagged] if consecutive > 0 else [],
        }

    if not DEGRADATION_TRACKER.exists():
        print("[tracker] No tracker file found — starting fresh")
        return empty_tracker()

    # Attempt to load existing file
    try:
        with open(DEGRADATION_TRACKER) as f:
            raw = f.read().strip()

        if not raw:
            print("[tracker] WARNING: tracker file is empty — starting fresh")
            return empty_tracker()

        tracker = json.loads(raw)

        if not isinstance(tracker, dict):
            raise ValueError("Tracker root is not a dict")

    except (json.JSONDecodeError, ValueError) as e:
        # File is corrupt — save backup and start fresh
        backup_path = DEGRADATION_TRACKER.with_suffix(
            f".corrupt_{date.today().isoformat()}.json"
        )
        shutil.copy2(DEGRADATION_TRACKER, backup_path)
        print(f"[tracker] WARNING: tracker file corrupt ({e})")
        print(f"[tracker] Backup saved to {backup_path}")
        print(f"[tracker] Starting fresh tracker")
        return empty_tracker()

    # Ensure _meta section exists
    if "_meta" not in tracker:
        tracker["_meta"] = {
            "last_screen_date": date.today().isoformat(),
            "screen_count":     0,
            "created":          date.today().isoformat(),
            "version":          "1",  # mark as old schema
        }

    # Migrate old schema entries to new schema
    needs_migration = tracker["_meta"].get("version", "1") != "2"
    if needs_migration:
        print(f"[tracker] Migrating tracker from schema v{tracker['_meta'].get('version','1')} to v2")
        for ticker in list(tracker.keys()):
            if ticker == "_meta":
                continue
            entry = tracker[ticker]
            if not valid_stock_entry(entry):
                tracker[ticker] = migrate_v1_entry(ticker, entry)
        tracker["_meta"]["version"] = "2"
        print(f"[tracker] Migration complete — {len(tracker) - 1} stock entries migrated")

    # Validate each stock entry — fix any invalid entries
    invalid_tickers = []
    for ticker, entry in tracker.items():
        if ticker == "_meta":
            continue
        if not valid_stock_entry(entry):
            print(f"[tracker] WARNING: invalid entry for {ticker} — resetting to zero")
            invalid_tickers.append(ticker)

    for ticker in invalid_tickers:
        tracker[ticker] = {
            "consecutive_flags": 0,
            "last_flagged":      date.today().isoformat(),
            "last_screen_date":  date.today().isoformat(),
            "flag_history":      [],
        }

    return tracker


def save_degradation_tracker(tracker: dict) -> None:
    """
    Save degradation tracker to JSON with backup protection.

    Always creates a backup of the previous version before overwriting.
    Backup is saved as degradation_tracker.backup.json and overwritten
    each time (only one backup kept — the previous good version).

    Never raises exceptions — logs errors but continues.
    """
    import shutil

    # Update _meta before saving
    tracker.setdefault("_meta", {})
    tracker["_meta"]["last_screen_date"] = date.today().isoformat()
    tracker["_meta"]["screen_count"]     = tracker["_meta"].get("screen_count", 0) + 1
    tracker["_meta"]["version"]          = "2"

    # Create backup of existing file before overwriting
    backup_path = DEGRADATION_TRACKER.with_name("degradation_tracker.backup.json")
    if DEGRADATION_TRACKER.exists():
        try:
            shutil.copy2(DEGRADATION_TRACKER, backup_path)
        except Exception as e:
            print(f"[tracker] WARNING: could not create backup: {e}")

    # Write new tracker atomically (write to temp file, then rename)
    tmp_path = DEGRADATION_TRACKER.with_suffix(".tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(tracker, f, indent=2, sort_keys=True)
        tmp_path.rename(DEGRADATION_TRACKER)
    except Exception as e:
        print(f"[tracker] ERROR: could not save tracker: {e}")
        if tmp_path.exists():
            tmp_path.unlink()


# ── Metric functions ───────────────────────────────────────────────────────────

def compute_hurst(ts: np.ndarray) -> float:
    """
    R/S Hurst exponent computed on LOG PRICES (not raw prices).

    Raw closing prices are non-stationary — their absolute level biases R/S
    upward (a ₹5,000 stock shows larger absolute differences than a ₹500 stock
    at the same relative volatility). Using log(prices) removes the price-level
    bias: log(price[t+lag]) - log(price[t]) = sum of log returns, whose std
    correctly scales as sqrt(lag) * sigma for a random walk regardless of level.

    The variogram estimator:
        tau[lag] = std(log_prices[t+lag] - log_prices[t])

    For a random walk:       tau[lag] ∝ lag^0.5  →  H ≈ 0.50
    For a trending series:   tau[lag] ∝ lag^H,  H > 0.50
    For mean-reverting:      tau[lag] ∝ lag^H,  H < 0.50

    Args:
        ts: numpy array of raw closing prices (log-transformed internally)

    Returns:
        float H in (0, 1). Returns 0.5 on error (random walk assumption).
    """
    try:
        if len(ts) < 20:
            return 0.5

        log_prices = np.log(ts)   # remove price-level bias

        lags = range(2, 20)
        tau = [np.std(np.subtract(log_prices[lag:], log_prices[:-lag])) for lag in lags]
        if any(t <= 0 for t in tau):
            return 0.5

        poly = np.polyfit(np.log(list(lags)), np.log(tau), 1)
        return float(poly[0])

    except Exception:
        return 0.5


def compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    20-day average ADX using standard 14-period Wilder calculation.

    Returns the mean of the last 20 ADX values rather than the
    full-history median. This reflects CURRENT trend strength —
    what the stock is doing now, not what it did over 2 years.

    Research basis:
    - Wilder designed ADX as a short-term indicator (14-period)
    - Professional screeners use current ADX, not historical median
    - 20-day average smooths single-bar noise while staying current
    - A falling ADX from 40→25 signals fading momentum even if
      price continues — the median would miss this entirely

    Returns 0.0 on error (safe default — stock fails ADX filter).
    """
    try:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr      = tr.ewm(span=period, min_periods=period).mean()
        up       = high.diff()
        down     = -low.diff()
        plus_dm  = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)
        plus_di  = 100 * plus_dm.ewm(span=period, min_periods=period).mean() / atr
        minus_di = 100 * minus_dm.ewm(span=period, min_periods=period).mean() / atr
        denom    = (plus_di + minus_di).replace(0, np.nan)
        dx       = 100 * (plus_di - minus_di).abs() / denom
        adx      = dx.ewm(span=period, min_periods=period).mean()

        if len(adx.dropna()) < 20:
            # Insufficient bars for 20-day average — use what we have
            return float(adx.dropna().mean())

        return float(adx.tail(20).mean())

    except Exception:
        return 0.0


def compute_adx_trend(df: pd.DataFrame, period: int = 14) -> str:
    """
    Returns whether ADX is currently RISING, FALLING, or FLAT.

    Used for early warning in degradation checks:
    - A stock with ADX=28 but FALLING is weakening
    - A stock with ADX=23 but RISING may be strengthening
    - The degradation tracker can use this as a soft flag

    Compares 5-day average ADX vs 20-day average ADX:
      RISING:  5d_avg > 20d_avg + 1.0  (trend strengthening)
      FALLING: 5d_avg < 20d_avg - 1.0  (trend weakening)
      FLAT:    within ±1.0             (stable)

    Returns "UNKNOWN" on error.
    """
    try:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr      = tr.ewm(span=period, min_periods=period).mean()
        up       = high.diff()
        down     = -low.diff()
        plus_dm  = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)
        plus_di  = 100 * plus_dm.ewm(span=period, min_periods=period).mean() / atr
        minus_di = 100 * minus_dm.ewm(span=period, min_periods=period).mean() / atr
        denom    = (plus_di + minus_di).replace(0, np.nan)
        dx       = 100 * (plus_di - minus_di).abs() / denom
        adx      = dx.ewm(span=period, min_periods=period).mean()
        adx_clean = adx.dropna()

        if len(adx_clean) < 20:
            return "UNKNOWN"

        avg_5d  = float(adx_clean.tail(5).mean())
        avg_20d = float(adx_clean.tail(20).mean())

        if avg_5d > avg_20d + 1.0:
            return "RISING"
        elif avg_5d < avg_20d - 1.0:
            return "FALLING"
        else:
            return "FLAT"

    except Exception:
        return "UNKNOWN"


def compute_sma_gap(df: pd.DataFrame) -> Optional[float]:
    """SMA20 vs SMA50 gap as % of SMA50. Positive = golden cross."""
    try:
        close = df["close"]
        sma20 = float(close.rolling(SMA_FAST).mean().iloc[-1])
        sma50 = float(close.rolling(SMA_SLOW).mean().iloc[-1])
        if sma50 == 0:
            return None
        return (sma20 - sma50) / sma50 * 100
    except Exception:
        return None


def compute_sma_gap_short(ticker: str, lookback_days: int = SIGNAL_LOOKBACK_DAYS) -> Optional[float]:
    """
    Compute the SMA20/SMA50 gap using only the last `lookback_days` calendar
    days — the exact same window paper_trading/signal_runner.py uses.

    This is the gap the LIVE TRADING SYSTEM sees when it decides whether to
    enter a position.  It can differ from compute_sma_gap() which uses the
    full 2-year screener window and is used only for regime classification.

    Returns None if the fetched data has fewer than 50 bars (SMA50 needs warmup).
    """
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    try:
        df = get_ohlcv(ticker, start, end)
        if df is None or len(df) < SMA_SLOW:
            return None
        close = df["close"]
        sma20 = float(close.rolling(SMA_FAST).mean().iloc[-1])
        sma50 = float(close.rolling(SMA_SLOW).mean().iloc[-1])
        if sma50 == 0:
            return None
        return (sma20 - sma50) / sma50 * 100
    except Exception:
        return None


def compute_vol(df: pd.DataFrame) -> float:
    """Annualised daily return volatility."""
    try:
        return float(df["close"].pct_change().dropna().std() * math.sqrt(252) * 100)
    except Exception:
        return 0.0

# ── Regime-transition annotation helper ──────────────────────────────────────

def _days_since_regime_transition(screen_date: date) -> Optional[int]:
    """
    Returns (screen_date - regime_transition_date).days from portfolio_state.json's
    regime_transition_date field — the date the NIFTY regime last actually changed.

    Positive = flag occurred N days AFTER the transition.
    Negative = flag occurred N days BEFORE the transition (only possible in
               retroactive backfills; live operation always produces non-negative).

    Returns None if the state file is unavailable, the field is missing, or
    any error occurs.  Fail-open: never crash the screener or block a flag.

    READ-ONLY: never writes to portfolio_state.json.
    """
    try:
        with open(PORTFOLIO_STATE) as f:
            state = json.load(f)
        transition_str = state.get("regime_transition_date")
        if not transition_str:
            return None
        transition_date = date.fromisoformat(transition_str)
        return (screen_date - transition_date).days
    except Exception:
        return None


# ── NIFTY 500 fetch ───────────────────────────────────────────────────────────

def fetch_nifty500() -> tuple[dict[str, str], bool]:
    """
    Returns ({ticker.NS: industry}, used_cache).

    Fetches NIFTY 500 from NSE archives URL.
    Falls back to local cache if fetch fails or returns < 400 tickers.

    used_cache=True means the screen is running on stale data —
    caller should warn in email subject and logs.

    Cache is saved to screener/nifty500_cache.json on every
    successful live fetch.
    """
    url     = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}

    # ── Attempt live fetch ────────────────────────────────────────
    live_result = {}
    live_error  = None
    try:
        r  = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        df = pd.read_csv(pd.io.common.StringIO(r.text))
        live_result = {
            str(row["Symbol"]).strip() + ".NS": str(row["Industry"]).strip()
            for _, row in df.iterrows()
        }
    except Exception as e:
        live_error = str(e)

    # ── Validate live result ──────────────────────────────────────
    if len(live_result) >= NIFTY500_MIN_COUNT:
        # Success — save to cache and return
        try:
            cache_data = {
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "count":      len(live_result),
                "data":       live_result,
            }
            with open(NIFTY500_CACHE, "w") as f:
                json.dump(cache_data, f, indent=2)
            print(f"[auto_screener] NIFTY 500: {len(live_result)} stocks "
                  f"(live, cache updated)")
        except Exception as ce:
            print(f"[auto_screener] WARNING: Could not save cache: {ce}")
        return live_result, False

    # ── Live fetch failed or returned too few tickers ─────────────
    if live_error:
        print(f"[auto_screener] NIFTY 500 live fetch failed: {live_error}")
    else:
        print(f"[auto_screener] NIFTY 500 live fetch returned only "
              f"{len(live_result)} tickers (< {NIFTY500_MIN_COUNT}) "
              f"— treating as failure")

    # ── Try cache fallback ────────────────────────────────────────
    if NIFTY500_CACHE.exists():
        try:
            with open(NIFTY500_CACHE) as f:
                cache_data = json.load(f)
            cached     = cache_data.get("data", {})
            fetched_at = cache_data.get("fetched_at", "unknown date")
            if len(cached) >= NIFTY500_MIN_COUNT:
                print(
                    f"[auto_screener] WARNING: Using cached NIFTY 500 "
                    f"from {fetched_at} ({len(cached)} stocks). "
                    f"Live fetch failed — results may be slightly stale."
                )
                return cached, True
        except Exception as ce:
            print(f"[auto_screener] Cache load failed: {ce}")

    # ── Both live and cache failed ────────────────────────────────
    print(
        "[auto_screener] CRITICAL: NIFTY 500 fetch failed AND no valid "
        "cache found. Screener cannot run. Check NSE connectivity and "
        f"ensure {NIFTY500_CACHE} exists from a prior successful run."
    )
    return {}, True  # empty — caller must handle gracefully

# ── Current universe ──────────────────────────────────────────────────────────

def get_current_universe() -> list[str]:
    """
    Return the current tradeable universe from signal_runner.STOCKS.

    This is NOT the same as "stocks with open positions". A stock can be in
    the universe with zero shares held (flat, waiting for a signal). The old
    implementation read portfolio_state.json's positions dict, which retains
    stale entries for removed stocks (SIEMENS, JKTYRE) and misses new additions
    (CHOLAHLDNG, COHANCE) until a position actually opens in them — causing
    the screener to compute correlation checks against the wrong baseline.

    Deferred import (not top-level) to avoid a circular dependency:
    signal_runner.py already imports compute_hurst from this module.
    The import is safe here because get_current_universe() is only called
    at run time (inside run_screen()), never at module load time.
    """
    from paper_trading.signal_runner import STOCKS as _LIVE_UNIVERSE  # noqa: PLC0415
    return list(_LIVE_UNIVERSE)

# ── Correlation check ─────────────────────────────────────────────────────────

def compute_correlation(
    candidate: str,
    existing: list[str],
    data_cache: dict[str, pd.DataFrame]
) -> tuple[float, float]:
    """Returns (avg_correlation, max_correlation) of candidate vs existing universe."""
    try:
        cand_ret = data_cache[candidate]["close"].pct_change().dropna()
        corrs = []
        for ticker in existing:
            if ticker not in data_cache:
                continue
            ex_ret = data_cache[ticker]["close"].pct_change().dropna()
            common = cand_ret.index.intersection(ex_ret.index)
            if len(common) < 100:
                continue
            c = float(cand_ret.loc[common].corr(ex_ret.loc[common]))
            corrs.append(c)
        if not corrs:
            return 0.0, 0.0
        return float(np.mean(corrs)), float(np.max(corrs))
    except Exception:
        return 0.0, 0.0

# ── Candidates file writer ────────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))


def _write_candidates_file(results: dict) -> None:
    """Write ADD/WATCH tickers atomically to screener/latest_candidates.json.

    Called alongside send_report() after each real (non-dry-run) screen so the
    post-screener WF batch pipeline can read candidates without manual copy-paste.
    Fail-open: a write failure logs a warning but never crashes the screener run.
    """
    try:
        add_tickers       = [s["ticker"] for s in results.get("adds",    [])]
        watchlist_tickers = [s["ticker"] for s in results.get("watches", [])]
        data = {
            "screen_date":       date.today().isoformat(),
            "run_time_ist":      datetime.now(_IST).isoformat(timespec="seconds"),
            "add_tickers":       add_tickers,
            "watchlist_tickers": watchlist_tickers,
        }
        # Atomic write: write to .tmp then rename (POSIX rename is atomic on same fs)
        tmp = CANDIDATES_FILE.parent / (CANDIDATES_FILE.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.rename(CANDIDATES_FILE)
        print(f"[candidates] Written {len(add_tickers)} ADD + {len(watchlist_tickers)} WATCH → {CANDIDATES_FILE.name}")
    except Exception as e:
        print(f"[candidates] WARNING: failed to write candidates file: {e}", file=sys.stderr)


# ── Main screening pipeline ───────────────────────────────────────────────────

def run_screen() -> dict:
    """
    Full screening pipeline. Returns a dict with:
        adds        — list of recommended additions
        watches     — list of stocks to watch (close to golden cross)
        removes     — list of existing stocks with degraded regime
        meta        — run metadata
    """
    print(f"\n[auto_screener] Starting screen — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    nifty500, used_cache = fetch_nifty500()
    current_universe     = get_current_universe()

    if used_cache:
        print(
            "[auto_screener] WARNING: Screen running on CACHED NIFTY 500 data. "
            "Results may not reflect latest index composition."
        )
    if not nifty500:
        print("[auto_screener] CRITICAL: Empty NIFTY 500 — aborting screen.")
        return {
            "adds":     [],
            "monitors": [],
            "watches":  [],
            "removes":  [],
            "meta": {
                "run_date":         datetime.now().strftime("%Y-%m-%d %H:%M IST"),
                "screened":         0,
                "passed_filters":   0,
                "current_universe": current_universe,
                "data_coverage":    0,
                "used_cache":       True,
                "error":            "NIFTY 500 fetch failed — no cache available",
            },
        }

    print(f"[auto_screener] Current universe: {len(current_universe)} stocks")

    # ── Step 1: Fetch data for all stocks ─────────────────────────────────────
    data_cache: dict[str, pd.DataFrame] = {}
    all_tickers = list(set(list(nifty500.keys()) + current_universe))
    attempted   = 0
    skipped     = 0

    print(f"[auto_screener] Fetching data for {len(all_tickers)} stocks "
          f"(rate-limited to 55 req/min)...")

    for i, ticker in enumerate(all_tickers, 1):
        attempted += 1
        try:
            df = get_ohlcv(ticker, START_DATE, END_DATE)
            if df is not None and len(df) >= MIN_BARS:
                data_cache[ticker] = df
            # Sleep after every successful fetch to stay under Kite's
            # ~60 req/min limit. 1.1s gives ~55 req/min — safe buffer.
            time.sleep(1.1)
        except Exception as e:
            skipped += 1
            # Log first 5 skips explicitly so silent failures are visible
            if skipped <= 5:
                print(f"[auto_screener]   SKIP {ticker}: {e}")
            elif skipped == 6:
                print(f"[auto_screener]   (further skips suppressed — "
                      f"check Kite rate limit or token)")

        if i % 50 == 0:
            skip_pct = skipped / attempted * 100
            print(f"[auto_screener]   {i}/{len(all_tickers)} attempted, "
                  f"{len(data_cache)} valid, {skipped} skipped ({skip_pct:.1f}%)")

    skip_pct = skipped / attempted * 100 if attempted > 0 else 0
    print(f"[auto_screener] Fetch complete: {len(data_cache)} valid, "
          f"{skipped} skipped ({skip_pct:.1f}%)")

    # Warn if skip rate is high — indicates rate limiting or token issues
    if skip_pct > 5.0:
        print(
            f"[auto_screener] WARNING: {skipped}/{attempted} stocks skipped "
            f"({skip_pct:.1f}%) — universe coverage may be incomplete. "
            f"Check Kite API rate limit or access token."
        )

    print(f"[auto_screener] {len(data_cache)} stocks with >= {MIN_BARS} bars")

    # ── Step 2: Screen candidates (exclude current universe) ──────────────────
    candidates = [t for t in data_cache if t not in current_universe and t in nifty500]
    print(f"[auto_screener] Screening {len(candidates)} candidate stocks...")

    trending_strong = []
    for ticker in candidates:
        df = data_cache[ticker]

        # Price filter: too expensive to size correctly at current capital
        last_price = df["close"].iloc[-1]
        if last_price > MAX_PRICE:
            continue

        # Liquidity filter: skip illiquid stocks (avg daily turnover < ₹1 crore)
        if df["volume"].tail(20).mean() * last_price < MIN_LIQUIDITY:
            continue

        h  = compute_hurst(df["close"].values)
        if h <= HURST_THRESHOLD:
            continue
        a = compute_adx(df)
        if a <= ADX_THRESHOLD:
            continue
        gap_long  = compute_sma_gap(df)   # 2-year window: used for regime classification
        gap_short = compute_sma_gap_short(ticker)  # 80-day window: what signal_runner sees
        vol = compute_vol(df)
        avg_corr, max_corr = compute_correlation(ticker, current_universe, data_cache)

        if avg_corr > MAX_CORR_AVG or max_corr > MAX_CORR_PAIR:
            continue

        # Divergence: regime confirmed on 2-year data but cross direction differs
        # on the 80-day window the live trading system will actually use.
        divergent = (
            gap_long  is not None and
            gap_short is not None and
            (gap_long > 0) != (gap_short > 0)
        )

        trending_strong.append({
            "ticker":    ticker,
            "industry":  nifty500.get(ticker, "Unknown"),
            "hurst":     round(h, 3),
            "adx":       round(a, 1),
            # Legacy key kept for backward compatibility with emailer/removes
            "gap":       round(gap_long, 2) if gap_long is not None else None,
            "gap_long":  round(gap_long,  2) if gap_long  is not None else None,
            "gap_short": round(gap_short, 2) if gap_short is not None else None,
            "divergent": divergent,
            "vol":       round(vol, 1),
            "avg_corr":  round(avg_corr, 3),
            "max_corr":  round(max_corr, 3),
            # Cross direction determined by the SHORT window — this is what matters
            # for ADD/MONITOR categorisation since the system acts on the short window.
            "cross":     "GOLDEN" if (gap_short is not None and gap_short > 0) else "DEATH",
        })

    print(f"[auto_screener] {len(trending_strong)} stocks passed all filters")

    # ── Step 3: Split into ADD / MONITOR / WATCH ─────────────────────────────
    # ADD:     death cross closest to golden flip (gap < 0, closest to 0). Top 5.
    # MONITOR: golden cross already confirmed — missed entry, wait for next cycle. Top 5.
    # WATCH:   remaining death cross stocks (positions 5–10 by gap proximity).
    death_cross_sorted = sorted(
        [s for s in trending_strong if s["cross"] == "DEATH"],
        key=lambda x: -(x["gap_short"] if x["gap_short"] is not None else -float("inf"))  # sort by 80-day gap — what signal_runner sees
    )
    adds    = death_cross_sorted[:5]
    watches = death_cross_sorted[5:10]

    monitors = sorted(
        [s for s in trending_strong if s["cross"] == "GOLDEN"],
        key=lambda x: (x["gap_short"] if x["gap_short"] is not None else float("inf"))  # sort by 80-day gap — what signal_runner sees
    )[:5]

    # ── Step 4: Check existing universe for regime degradation ────────────────
    tracker   = load_degradation_tracker()
    today_str = date.today().isoformat()

    # Clean stale tracker entries — remove stocks no longer in universe
    stale_tickers = [t for t in tracker if t != "_meta" and t not in current_universe]
    for ticker in stale_tickers:
        print(f"[tracker] Removing stale entry for {ticker} (no longer in universe)")
        del tracker[ticker]

    removes = []
    for ticker in current_universe:
        if ticker not in data_cache:
            continue

        df        = data_cache[ticker]
        h         = compute_hurst(df["close"].values)
        a         = compute_adx(df)
        adx_trend = compute_adx_trend(df)
        gap       = compute_sma_gap(df)

        # Check if open position exists — never recommend removal for open positions
        try:
            with open(PORTFOLIO_STATE) as f:
                port_state = json.load(f)
            has_open_position = port_state["positions"].get(ticker, {}).get("shares", 0) > 0
        except Exception:
            has_open_position = False

        # Determine if this stock is degraded:
        #   - Hurst below removal threshold, OR
        #   - ADX below removal threshold, OR
        #   - ADX is falling AND already below entry threshold (approaching removal)
        is_degraded = (
            h < HURST_DEGRADE or
            a < ADX_DEGRADE or
            (adx_trend == "FALLING" and a < ADX_THRESHOLD)
        )

        # Get or create tracker entry for this stock
        if ticker not in tracker:
            tracker[ticker] = {
                "consecutive_flags": 0,
                "last_flagged":      today_str,
                "last_screen_date":  today_str,
                "flag_history":      [],
            }

        entry = tracker[ticker]
        last_screen = entry.get("last_screen_date", today_str)

        # Check if last screen was more than 5 days ago
        # If so, this is not a consecutive flag — reset counter
        try:
            from datetime import datetime as dt
            days_since_last_screen = (dt.strptime(today_str, "%Y-%m-%d") -
                                       dt.strptime(last_screen, "%Y-%m-%d")).days
        except Exception:
            days_since_last_screen = 0

        if days_since_last_screen > 5 and entry["consecutive_flags"] > 0:
            print(f"[tracker] {ticker}: resetting consecutive flags — last screen was {days_since_last_screen} days ago (not consecutive)")
            entry["consecutive_flags"] = 0
            entry["flag_history"]      = []
            entry["flag_annotations"]  = {}

        # Update tracker based on current health
        if is_degraded:
            entry["consecutive_flags"] += 1
            entry["last_flagged"]       = today_str
            # Add to flag history (keep last 10 only)
            entry["flag_history"].append(today_str)
            entry["flag_history"] = entry["flag_history"][-10:]
            # Annotate if this flag falls within REGIME_TRANSITION_WINDOW days of a
            # NIFTY regime transition — annotation only, never suppresses the flag.
            _trans_days = _days_since_regime_transition(date.fromisoformat(today_str))
            if _trans_days is not None and 0 <= _trans_days <= REGIME_TRANSITION_WINDOW:
                entry.setdefault("flag_annotations", {})[today_str] = {
                    "regime_transition_nearby": True,
                    "days_since_transition":    _trans_days,
                }
        else:
            # Stock is healthy — reset counter and all annotations
            if entry["consecutive_flags"] > 0:
                print(f"[tracker] {ticker}: regime healthy again — resetting consecutive flags from {entry['consecutive_flags']} to 0")
            entry["consecutive_flags"] = 0
            entry["flag_history"]      = []
            entry["flag_annotations"]  = {}

        # Update last_screen_date regardless of health
        entry["last_screen_date"] = today_str

        # Only recommend REMOVE if:
        # 1. Flagged for 2+ consecutive screens
        # 2. No open position
        if entry["consecutive_flags"] >= 2:
            if has_open_position:
                print(f"[tracker] {ticker}: would REMOVE but has open position — skipping (will re-evaluate after close)")
            else:
                if h < HURST_DEGRADE:
                    reason = f"H={h:.3f} < {HURST_DEGRADE}"
                elif a < ADX_DEGRADE:
                    reason = f"ADX={a:.1f} < {ADX_DEGRADE} (trend: {adx_trend})"
                else:
                    reason = f"ADX={a:.1f} falling toward threshold (trend: {adx_trend})"
                removes.append({
                    "ticker":            ticker,
                    "hurst":             round(h, 3),
                    "adx":               round(a, 1),
                    "adx_trend":         adx_trend,
                    "gap":               round(gap, 2) if gap is not None else None,
                    "reason":            reason,
                    "consecutive_flags": entry["consecutive_flags"],
                    "flag_history":      entry["flag_history"],
                    "flag_annotations":  entry.get("flag_annotations", {}),
                })

    # Save tracker with all updates — must happen before return
    save_degradation_tracker(tracker)
    print(f"[tracker] Saved — {len([k for k in tracker if k != '_meta'])} stocks tracked, screen #{tracker['_meta']['screen_count']}")

    return {
        "adds":     adds,
        "monitors": monitors,
        "watches":  watches,
        "removes":  removes,
        "meta": {
            "run_date":         datetime.now().strftime("%Y-%m-%d %H:%M IST"),
            "screened":         len(candidates),
            "passed_filters":   len(trending_strong),
            "current_universe": current_universe,
            "data_coverage":    len(data_cache),
            "used_cache":       used_cache,
        }
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print report without sending email")
    args = parser.parse_args()

    results = run_screen()

    # Import emailer here to avoid circular imports
    from screener.emailer import send_report

    if args.dry_run:
        print("\n" + "="*70)
        print("DRY RUN — Report would be emailed to aaravpagarwal07@gmail.com")
        print("="*70)

        print(f"\nADD ({len(results['adds'])}) — death cross on 80-day window, closest to golden flip:")
        for s in results['adds']:
            divflag = "  ⚠ DIVERGENT (2yr=GOLDEN, 80d=DEATH — verify)" if s.get('divergent') else ""
            print(
                f"  {s['ticker']:<22} H={s['hurst']}  ADX={s['adx']}"
                f"  Gap-Short={s['gap_short']}%  Gap-Long={s['gap_long']}%"
                f"  Corr={s['avg_corr']}{divflag}"
            )

        print(f"\nMONITOR ({len(results.get('monitors', []))}) — golden cross on 80-day window (missed entry, wait for next cycle):")
        for s in results.get('monitors', []):
            divflag = "  ⚠ DIVERGENT (2yr=DEATH, 80d=GOLDEN — verify)" if s.get('divergent') else ""
            print(
                f"  {s['ticker']:<22} H={s['hurst']}  ADX={s['adx']}"
                f"  Gap-Short=+{s['gap_short']}%  Gap-Long={'+' if (s['gap_long'] or 0) > 0 else ''}{s['gap_long']}%"
                f"  Corr={s['avg_corr']}{divflag}"
            )

        print(f"\nWATCH ({len(results['watches'])}) — death cross on 80-day window, further from flip:")
        for s in results['watches']:
            print(
                f"  {s['ticker']:<22} H={s['hurst']}  ADX={s['adx']}"
                f"  Gap-Short={s['gap_short']}%  Gap-Long={s['gap_long']}%"
            )

        print(f"\nREMOVE ({len(results['removes'])}):")
        for s in results['removes']:
            flags = s.get('consecutive_flags', '?')
            print(f"  {s['ticker']:<22} {s['reason']} — flagged {flags} consecutive screen(s)")
            annotated = [
                (d, a) for d, a in s.get('flag_annotations', {}).items()
                if a.get('regime_transition_nearby')
            ]
            if annotated:
                dates_str = ', '.join(d for d, _ in annotated)
                print(
                    f"  ⚠️  CAUTION: {len(annotated)} of {flags} flags for "
                    f"{s['ticker']} occurred within {REGIME_TRANSITION_WINDOW} days of a "
                    f"NIFTY regime transition ({dates_str}). ADX often dips mechanically "
                    f"during transitions — verify this reflects genuine decay before removing."
                )

        print(f"\nMeta: {results['meta']}")
    else:
        send_report(results)
        _write_candidates_file(results)
