"""
screener/correlation_check.py — Correlation & sector check before universe expansion.

Fetches 2 years of closing prices for 4 existing + 4 candidate stocks, computes
Pearson correlation on daily returns, flags high-correlation pairs, assesses sector
concentration, and prints a final add/reconsider recommendation for each candidate.

Usage:
    python screener/correlation_check.py

Requires a fresh Kite access token: run auth/kite_login.py first.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.kite_fetcher import get_ohlcv


# =============================================================================
# Configuration
# =============================================================================

START = "2024-01-01"
END   = "2026-01-01"

EXISTING   = ["TMPV.NS", "WHIRLPOOL.NS", "SIEMENS.NS", "BAJAJ-AUTO.NS"]
CANDIDATES = ["BOSCHLTD.NS", "COLPAL.NS", "ANURAS.NS", "HEROMOTOCO.NS"]
ALL_STOCKS = EXISTING + CANDIDATES

# Short display labels for the correlation matrix header
SHORT = {
    "TMPV.NS":       "TMPV",
    "WHIRLPOOL.NS":  "WHIRL",
    "SIEMENS.NS":    "SIEM",
    "BAJAJ-AUTO.NS": "BAJAJ",
    "BHEL.NS":       "BHEL",
    "VEDL.NS":       "VEDL",
    "PNB.NS":        "PNB",
    "BPCL.NS":       "BPCL",
}

# (sector_group, display_name)
SECTORS: dict[str, tuple[str, str]] = {
    "TMPV.NS":       ("Auto",          "Auto / 2W"),
    "BAJAJ-AUTO.NS": ("Auto",          "Auto / 2W"),
    "SIEMENS.NS":    ("Capital Goods", "Capital Goods"),
    "BHEL.NS":       ("Capital Goods", "Capital Goods / PSU"),
    "WHIRLPOOL.NS":  ("Consumer",      "Consumer Durables"),
    "VEDL.NS":       ("Metals/Mining", "Metals & Mining"),
    "PNB.NS":        ("Banking/PSU",   "PSU Bank"),
    "BPCL.NS":       ("Energy PSU",    "Energy / PSU"),
}

CORR_HIGH       = 0.70   # warn threshold
CORR_VERY_HIGH  = 0.85   # strong warn threshold
SECTOR_FLAG_PCT = 25.0   # flag if a sector reaches ≥ this % of the universe
MAX_FFILL_DAYS  = 3      # forward-fill limit for data gaps


# =============================================================================
# Step 1 — Fetch data
# =============================================================================

def fetch_closes() -> pd.DataFrame:
    """
    Fetch daily closing prices for all stocks. Forward-fills up to MAX_FFILL_DAYS
    to handle exchange holidays / corporate-action gaps. Exits on auth failure.
    Returns a DataFrame with one column per successfully fetched ticker.
    """
    closes: dict[str, pd.Series] = {}

    for ticker in ALL_STOCKS:
        try:
            df = get_ohlcv(ticker, START, END)
            closes[ticker] = df["close"]
        except (FileNotFoundError, ConnectionError) as exc:
            print(f"\n[correlation_check] FATAL: {exc}")
            print("  Run auth/kite_login.py first to generate a fresh access token.")
            sys.exit(1)
        except ValueError as exc:
            print(f"[correlation_check] WARNING: {ticker} — {exc}. Skipping.")
        except Exception as exc:
            print(f"[correlation_check] WARNING: {ticker} — unexpected error: {exc}. Skipping.")

    if not closes:
        print("[correlation_check] No data fetched. Exiting.")
        sys.exit(1)

    prices = pd.DataFrame(closes)

    # Forward-fill gaps (e.g. exchange holidays, missing bars) up to limit
    prices = prices.ffill(limit=MAX_FFILL_DAYS)

    # Report remaining unfillable gaps
    remaining = prices.isna().sum()
    for col, n in remaining[remaining > 0].items():
        print(
            f"[correlation_check] WARNING: {col} still has {n} NaN rows after "
            f"{MAX_FFILL_DAYS}-day forward-fill. These rows will be dropped."
        )

    # Drop rows where ANY stock is missing (keeps the overlap period)
    prices = prices.dropna()

    print(
        f"\n[correlation_check] Using {len(prices)} common trading days "
        f"({prices.index[0].date()} → {prices.index[-1].date()}) "
        f"across {len(prices.columns)} stocks.\n"
    )
    return prices


# =============================================================================
# Step 2 — Correlation matrix
# =============================================================================

def compute_corr(prices: pd.DataFrame) -> pd.DataFrame:
    """Pearson correlation on daily percentage returns."""
    returns = prices.pct_change().dropna()
    return returns.corr()


# =============================================================================
# Step 3 — Print correlation table
# =============================================================================

def print_corr_table(corr: pd.DataFrame) -> None:
    tickers = corr.columns.tolist()
    labels  = [SHORT.get(t, t[:6]) for t in tickers]

    ROW_W = 14   # left column (stock name)
    COL_W = 7    # each data column

    sep = "  " + "─" * (ROW_W + len(labels) * (COL_W + 2))

    print()
    print("=" * 80)
    print(f"  DAILY RETURNS CORRELATION MATRIX  ({START[:4]}–{END[:4]})")
    print("=" * 80)

    # Header row
    header = f"  {' ' * ROW_W}"
    for lbl in labels:
        header += f"  {lbl:^{COL_W}}"
    print(header)
    print(sep)

    for i, ticker in enumerate(tickers):
        row_lbl = SHORT.get(ticker, ticker)
        line = f"  {row_lbl:<{ROW_W}}"
        for j in range(len(tickers)):
            val  = corr.iloc[i, j]
            cell = f"{val:.2f}"
            line += f"  {cell:^{COL_W}}"
        print(line)

    print()


# =============================================================================
# Step 4 — Flag high-correlation pairs
# =============================================================================

def flag_high_correlations(corr: pd.DataFrame) -> None:
    tickers = corr.columns.tolist()
    flags: list[tuple[float, str, str, str]] = []

    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            val = corr.iloc[i, j]
            t1, t2 = tickers[i], tickers[j]
            if val >= CORR_VERY_HIGH:
                flags.append((val, t1, t2, "VERY HIGH — strongly consider dropping one"))
            elif val >= CORR_HIGH:
                flags.append((val, t1, t2, "HIGH CORRELATION — consider dropping one"))

    if not flags:
        print("  No high-correlation pairs (all pairs < 0.70).")
    else:
        flags.sort(reverse=True)
        for val, t1, t2, msg in flags:
            s1 = SHORT.get(t1, t1)
            s2 = SHORT.get(t2, t2)
            print(f"  {s1} ↔ {s2}:  r = {val:.2f}  — {msg}")
    print()


# =============================================================================
# Step 5 — Sector concentration
# =============================================================================

def sector_check(available_tickers: list[str]) -> None:
    n = len(available_tickers)
    sector_map: dict[str, list[str]] = {}

    for ticker in available_tickers:
        grp, _ = SECTORS.get(ticker, ("Unknown", "Unknown"))
        sector_map.setdefault(grp, []).append(ticker)

    print(f"  Universe: {n} stocks\n")
    any_flagged = False

    for sector in sorted(sector_map):
        stocks = sector_map[sector]
        pct    = len(stocks) / n * 100
        names  = ", ".join(SHORT.get(t, t) for t in stocks)
        flag   = " ← ⚠ EXCEEDS 25% THRESHOLD" if pct >= SECTOR_FLAG_PCT else ""
        print(f"  {sector:<20}  {pct:>5.1f}%  ({names}){flag}")
        if pct >= SECTOR_FLAG_PCT:
            any_flagged = True

    if any_flagged:
        print(
            "\n  ⚠ At least one sector reaches ≥25% of the universe. "
            "Consider swapping one stock for a different sector."
        )
    else:
        print("\n  All sectors are within the 25% concentration limit.")
    print()


# =============================================================================
# Step 6 — Recommendation
# =============================================================================

def recommend(corr: pd.DataFrame, available_tickers: list[str]) -> None:
    existing_avail   = [t for t in EXISTING   if t in available_tickers]
    candidate_avail  = [t for t in CANDIDATES if t in available_tickers]

    if not candidate_avail:
        print("  No candidate data available. Cannot generate recommendation.")
        return

    # For each candidate: avg correlation with existing stocks
    stats: list[dict] = []
    for cand in candidate_avail:
        corrs_with_existing = [
            corr.loc[cand, ex] for ex in existing_avail if ex in corr.index
        ]
        if not corrs_with_existing:
            continue
        avg_corr = float(np.mean(corrs_with_existing))
        max_corr = float(np.max(corrs_with_existing))
        max_corr_peer = existing_avail[int(np.argmax(corrs_with_existing))]

        # Intra-candidate: highest correlation with another candidate
        intra = [
            (corr.loc[cand, other], other)
            for other in candidate_avail if other != cand and other in corr.index
        ]
        max_intra_val  = max((v for v, _ in intra), default=0.0)
        max_intra_peer = next(
            (t for v, t in intra if v == max_intra_val), None
        ) if intra else None

        cand_sector, cand_sector_name = SECTORS.get(cand, ("Unknown", "Unknown"))
        existing_same_sector = [
            t for t in existing_avail
            if SECTORS.get(t, ("?",))[0] == cand_sector
        ]
        new_sector = len(existing_same_sector) == 0

        stats.append({
            "ticker":            cand,
            "avg_corr":          avg_corr,
            "max_corr":          max_corr,
            "max_corr_peer":     max_corr_peer,
            "max_intra_val":     max_intra_val,
            "max_intra_peer":    max_intra_peer,
            "sector":            cand_sector,
            "sector_name":       cand_sector_name,
            "new_sector":        new_sector,
            "existing_same":     existing_same_sector,
        })

    # Sort by avg correlation ascending (lower = better diversifier)
    stats.sort(key=lambda x: x["avg_corr"])

    # Classify: reconsider if avg >= CORR_HIGH or max with any existing >= CORR_VERY_HIGH
    recommended = [
        s for s in stats
        if s["avg_corr"] < CORR_HIGH and s["max_corr"] < CORR_VERY_HIGH
    ]
    reconsider = [
        s for s in stats
        if s["avg_corr"] >= CORR_HIGH or s["max_corr"] >= CORR_VERY_HIGH
    ]

    def _corr_note(s: dict) -> str:
        avg = s["avg_corr"]
        mx  = s["max_corr"]
        peer = SHORT.get(s["max_corr_peer"], s["max_corr_peer"])
        if avg < 0.40:
            return f"low avg correlation with existing ({avg:.2f})"
        if avg < 0.60:
            return f"moderate avg correlation with existing ({avg:.2f})"
        return f"avg corr {avg:.2f}, highest with {peer} ({mx:.2f})"

    def _sector_note(s: dict) -> str:
        if s["new_sector"]:
            return f"{s['sector']} sector not currently represented — adds diversification"
        same = ", ".join(SHORT.get(t, t) for t in s["existing_same"])
        return f"{s['sector']} already represented by {same} — sector overlap"

    def _intra_note(s: dict) -> str:
        if s["max_intra_peer"] and s["max_intra_val"] >= CORR_HIGH:
            peer = SHORT.get(s["max_intra_peer"], s["max_intra_peer"])
            return f"  ⚠ Also highly correlated with candidate {peer} (r={s['max_intra_val']:.2f}) — avoid adding both"
        return ""

    print("  RECOMMENDED ADDITIONS:")
    if recommended:
        for s in recommended:
            t     = s["ticker"]
            short = SHORT.get(t, t)
            print(f"  ✓ {short:<14}— {_corr_note(s)}, {_sector_note(s)}")
            extra = _intra_note(s)
            if extra:
                print(f"  {extra}")
    else:
        print("  (none — all candidates show elevated correlation with existing universe)")

    print()
    print("  STOCKS TO RECONSIDER:")
    if reconsider:
        for s in reconsider:
            t     = s["ticker"]
            short = SHORT.get(t, t)
            peer  = SHORT.get(s["max_corr_peer"], s["max_corr_peer"])
            if s["max_corr"] >= CORR_VERY_HIGH:
                reason = (
                    f"VERY HIGH correlation with {peer} (r={s['max_corr']:.2f}), "
                    f"avg {s['avg_corr']:.2f}"
                )
            else:
                reason = (
                    f"avg correlation with existing too high ({s['avg_corr']:.2f}), "
                    f"highest with {peer} ({s['max_corr']:.2f})"
                )
            print(f"  ✗ {short:<14}— {reason}")
            extra = _intra_note(s)
            if extra:
                print(f"  {extra}")
    else:
        print("  (none — all candidates are safe to add)")

    print()
    print(
        "  Ranking by avg correlation with existing 4 stocks "
        "(lower = better diversifier):"
    )
    for rank, s in enumerate(stats, 1):
        short = SHORT.get(s["ticker"], s["ticker"])
        star  = "✓" if s in recommended else "✗"
        print(
            f"    {rank}. {star} {short:<12}  avg r={s['avg_corr']:.2f}  "
            f"max r={s['max_corr']:.2f}  sector: {s['sector']}"
        )
    print()


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    print()
    print("=" * 80)
    print("  CORRELATION CHECK — NSE Universe Expansion")
    print(f"  Period: {START} → {END}  |  Existing: {len(EXISTING)}  |  Candidates: {len(CANDIDATES)}")
    print("=" * 80)

    # Step 1: fetch
    prices = fetch_closes()
    available = prices.columns.tolist()

    # Step 2: compute
    corr = compute_corr(prices)

    # Save CSV
    out_path = Path(__file__).parent / "correlation_results.csv"
    corr.to_csv(out_path)
    print(f"[correlation_check] Full correlation matrix saved → {out_path}\n")

    # Step 3: print table
    print_corr_table(corr)

    # Step 4: flag pairs
    print("=" * 80)
    print("  HIGH CORRELATION FLAGS")
    print("=" * 80)
    flag_high_correlations(corr)

    # Step 5: sector check
    print("=" * 80)
    print("  SECTOR CONCENTRATION  (8-stock expanded universe)")
    print("=" * 80)
    sector_check(available)

    # Step 6: recommendation
    print("=" * 80)
    print("  RECOMMENDATION")
    print("=" * 80)
    recommend(corr, available)

    print("=" * 80)


if __name__ == "__main__":
    main()
