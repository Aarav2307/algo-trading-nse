"""
screener/auto_screener.py — Automated bi-weekly universe expansion screener.

Runs every Wednesday and Sunday at 6 PM IST via cron.
Pipeline:
  1. Fetch NIFTY 500 constituent list from NSE
  2. Filter stocks with >= 744 trading days (2 years) of history
  3. Compute Hurst exponent + ADX for regime classification
  4. Filter TRENDING_STRONG (H > 0.55, ADX > 25)
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
from datetime import datetime, date
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
HURST_THRESHOLD   = 0.55      # minimum Hurst for TRENDING_STRONG
HURST_DEGRADE     = 0.52      # existing stock flagged for removal below this
ADX_THRESHOLD     = 25.0      # minimum ADX for TRENDING_STRONG
ADX_DEGRADE       = 22.0      # existing stock flagged for removal below this
MAX_CORR_AVG      = 0.60      # max avg correlation with existing universe
MAX_CORR_PAIR     = 0.70      # max pairwise correlation
MIN_LIQUIDITY     = 10_000_000  # ₹1 crore avg daily turnover (vol × price, 20-day)
MAX_PRICE         = 15_000    # skip stocks above ₹15,000/share
START_DATE        = "2023-01-01"
END_DATE          = date.today().strftime("%Y-%m-%d")
SMA_FAST          = 20
SMA_SLOW          = 50
DEGRADATION_TRACKER = _ROOT / "screener" / "degradation_tracker.json"

# ── Degradation tracker ───────────────────────────────────────────────────────

def load_degradation_tracker() -> dict:
    try:
        with open(DEGRADATION_TRACKER) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_degradation_tracker(tracker: dict) -> None:
    with open(DEGRADATION_TRACKER, "w") as f:
        json.dump(tracker, f, indent=2)


# ── Metric functions ───────────────────────────────────────────────────────────

def compute_hurst(ts: np.ndarray) -> float:
    """R/S Hurst exponent."""
    try:
        lags = range(2, 20)
        tau = [np.std(np.subtract(ts[lag:], ts[:-lag])) for lag in lags]
        if any(t <= 0 for t in tau):
            return 0.5
        poly = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(poly[0])
    except Exception:
        return 0.5


def compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Median ADX over full window."""
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
        return float(adx.median())
    except Exception:
        return 0.0


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


def compute_vol(df: pd.DataFrame) -> float:
    """Annualised daily return volatility."""
    try:
        return float(df["close"].pct_change().dropna().std() * math.sqrt(252) * 100)
    except Exception:
        return 0.0

# ── NIFTY 500 fetch ───────────────────────────────────────────────────────────

def fetch_nifty500() -> dict[str, str]:
    """Returns {ticker.NS: industry} from NSE CSV."""
    url     = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r  = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        df = pd.read_csv(pd.io.common.StringIO(r.text))
        return {
            str(row["Symbol"]).strip() + ".NS": str(row["Industry"]).strip()
            for _, row in df.iterrows()
        }
    except Exception as e:
        print(f"[auto_screener] NIFTY 500 fetch failed: {e}")
        return {}

# ── Current universe from portfolio state ─────────────────────────────────────

def get_current_universe() -> list[str]:
    """Read current trading universe from portfolio_state.json."""
    try:
        with open(PORTFOLIO_STATE) as f:
            state = json.load(f)
        return list(state["positions"].keys())
    except Exception:
        return []

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

    nifty500        = fetch_nifty500()
    current_universe = get_current_universe()

    print(f"[auto_screener] NIFTY 500: {len(nifty500)} stocks")
    print(f"[auto_screener] Current universe: {len(current_universe)} stocks")

    # ── Step 1: Fetch data for all stocks ─────────────────────────────────────
    data_cache: dict[str, pd.DataFrame] = {}
    all_tickers = list(set(list(nifty500.keys()) + current_universe))

    print(f"[auto_screener] Fetching data for {len(all_tickers)} stocks...")
    for i, ticker in enumerate(all_tickers, 1):
        try:
            df = get_ohlcv(ticker, START_DATE, END_DATE)
            if df is not None and len(df) >= MIN_BARS:
                data_cache[ticker] = df
        except Exception:
            pass
        if i % 50 == 0:
            print(f"[auto_screener]   {i}/{len(all_tickers)} fetched, {len(data_cache)} valid")

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
        gap = compute_sma_gap(df)
        vol = compute_vol(df)
        avg_corr, max_corr = compute_correlation(ticker, current_universe, data_cache)

        if avg_corr > MAX_CORR_AVG or max_corr > MAX_CORR_PAIR:
            continue

        trending_strong.append({
            "ticker":    ticker,
            "industry":  nifty500.get(ticker, "Unknown"),
            "hurst":     round(h, 3),
            "adx":       round(a, 1),
            "gap":       round(gap, 2) if gap is not None else None,
            "vol":       round(vol, 1),
            "avg_corr":  round(avg_corr, 3),
            "max_corr":  round(max_corr, 3),
            "cross":     "GOLDEN" if (gap is not None and gap > 0) else "DEATH",
        })

    print(f"[auto_screener] {len(trending_strong)} stocks passed all filters")

    # ── Step 3: Split into ADD / MONITOR / WATCH ─────────────────────────────
    # ADD:     death cross closest to golden flip (gap < 0, closest to 0). Top 5.
    # MONITOR: golden cross already confirmed — missed entry, wait for next cycle. Top 5.
    # WATCH:   remaining death cross stocks (positions 5–10 by gap proximity).
    death_cross_sorted = sorted(
        [s for s in trending_strong if s["cross"] == "DEATH"],
        key=lambda x: -x["gap"]  # closest to 0 (least negative) first
    )
    adds    = death_cross_sorted[:5]
    watches = death_cross_sorted[5:10]

    monitors = sorted(
        [s for s in trending_strong if s["cross"] == "GOLDEN"],
        key=lambda x: x["gap"]  # lowest gap first = freshest cross
    )[:5]

    # ── Step 4: Check existing universe for regime degradation ────────────────
    # Uses a persistent tracker: only recommend removal after 2 consecutive flags.
    tracker   = load_degradation_tracker()
    today_str = date.today().strftime("%Y-%m-%d")

    # Open positions must close naturally — never force-remove them
    try:
        with open(PORTFOLIO_STATE) as f:
            port_state = json.load(f)
        positions = port_state.get("positions", {})
    except Exception:
        positions = {}

    removes = []
    for ticker in current_universe:
        if ticker not in data_cache:
            continue
        df  = data_cache[ticker]
        h   = compute_hurst(df["close"].values)
        a   = compute_adx(df)
        gap = compute_sma_gap(df)

        entry = tracker.get(ticker, {"consecutive_flags": 0, "last_flagged": None})

        if h < HURST_DEGRADE or a < ADX_DEGRADE:
            entry["consecutive_flags"] = entry.get("consecutive_flags", 0) + 1
            entry["last_flagged"]      = today_str
            tracker[ticker]            = entry

            if entry["consecutive_flags"] >= 2:
                if positions.get(ticker, {}).get("shares", 0) > 0:
                    print(f"[auto_screener] {ticker} flagged for removal but has open position — skipping")
                    continue

                reason_parts = []
                if h < HURST_DEGRADE:
                    reason_parts.append(f"H={h:.3f} < {HURST_DEGRADE}")
                if a < ADX_DEGRADE:
                    reason_parts.append(f"ADX={a:.1f} < {ADX_DEGRADE}")

                removes.append({
                    "ticker":            ticker,
                    "hurst":             round(h, 3),
                    "adx":               round(a, 1),
                    "gap":               round(gap, 2) if gap is not None else None,
                    "reason":            " & ".join(reason_parts),
                    "consecutive_flags": entry["consecutive_flags"],
                })
        else:
            entry["consecutive_flags"] = 0
            tracker[ticker]            = entry

    save_degradation_tracker(tracker)

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

        print(f"\nADD ({len(results['adds'])}) — death cross, closest to golden flip:")
        for s in results['adds']:
            print(f"  {s['ticker']:<22} H={s['hurst']}  ADX={s['adx']}  Gap={s['gap']}%  Corr={s['avg_corr']}")

        print(f"\nMONITOR ({len(results.get('monitors', []))}) — already in golden cross (missed entry, wait for next cycle):")
        for s in results.get('monitors', []):
            gap_str = f"+{s['gap']}%" if s['gap'] and s['gap'] > 0 else f"{s['gap']}%"
            print(f"  {s['ticker']:<22} H={s['hurst']}  ADX={s['adx']}  Gap={gap_str}  Corr={s['avg_corr']}")

        print(f"\nWATCH ({len(results['watches'])}) — death cross, further from flip:")
        for s in results['watches']:
            print(f"  {s['ticker']:<22} H={s['hurst']}  ADX={s['adx']}  Gap={s['gap']}%")

        print(f"\nREMOVE ({len(results['removes'])}):")
        for s in results['removes']:
            flags = s.get('consecutive_flags', '?')
            print(f"  {s['ticker']:<22} {s['reason']} — flagged {flags} consecutive screen(s)")

        print(f"\nMeta: {results['meta']}")
    else:
        send_report(results)
