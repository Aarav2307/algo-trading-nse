"""
screener/universe_expansion.py — Full Nifty 100 universe expansion pipeline.

Runs SMA suitability screening → regime classification (top 30) → correlation
filter to surface the best new additions to the current 8-stock watchlist.

Usage:
    python screener/universe_expansion.py

Data source note:
    sma_screener.py and regime_classifier.py use data.fetcher (yfinance) internally
    and cannot be modified. Using kite_fetcher for correlation while those modules
    use yfinance would mix data sources and corrupt correlation numbers.
    data.fetcher (yfinance) is used throughout for consistency.

Output files:
    screener/universe_expansion_results.txt — full analysis
    screener/skipped.txt                    — stocks skipped due to data issues
"""

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ── Screener module imports ────────────────────────────────────────────────────
try:
    from screener.sma_screener import (
        compute_metrics, compute_scores,
        _fetch_quiet, _fetch_benchmark,
        BENCHMARK_TICKER,
    )
    from screener.regime_classifier import compute_regime_metrics
except ImportError:
    from sma_screener import (
        compute_metrics, compute_scores,
        _fetch_quiet, _fetch_benchmark,
        BENCHMARK_TICKER,
    )
    from regime_classifier import compute_regime_metrics


# =============================================================================
# Configuration
# =============================================================================

START = "2023-01-01"
END   = "2026-01-01"

MIN_BARS         = 200     # skip any stock with fewer trading days
TOP_N            = 30      # run regime classifier on top N by SMA score
SCORE_THRESH     = 65.0    # minimum SMA suitability score for recommendation
CORR_THRESH      = 0.60    # maximum average correlation with existing watchlist
CORR_FLAG_HIGH   = 0.70    # flag pair as high-corr if above this

CURRENT_WATCHLIST = [
    "TMPV.NS", "WHIRLPOOL.NS", "SIEMENS.NS", "BAJAJ-AUTO.NS",
    "BHEL.NS", "VEDL.NS", "PNB.NS", "BPCL.NS",
]

# Map tickers that are effectively the same as a watchlist holding
# (TATAMOTORS pre-demerger parent → TMPV post-demerger PV entity)
WATCHLIST_ALIASES: dict[str, str] = {
    "TATAMOTORS.NS": "TMPV.NS",
}

WATCHLIST_SECTORS: dict[str, str] = {
    "TMPV.NS":       "Auto",
    "BAJAJ-AUTO.NS": "Auto",
    "SIEMENS.NS":    "Capital Goods",
    "BHEL.NS":       "Capital Goods",
    "WHIRLPOOL.NS":  "Consumer",
    "VEDL.NS":       "Metals",
    "PNB.NS":        "Banking",
    "BPCL.NS":       "Energy",
}

EXPANDED_UNIVERSE: dict[str, str] = {
    # ── Auto ──────────────────────────────────────────────────────────────────
    "MARUTI.NS":      "Auto",
    "EICHERMOT.NS":   "Auto",
    "HEROMOTOCO.NS":  "Auto",
    "M&M.NS":         "Auto",
    "TATAMOTORS.NS":  "Auto",
    # ── Banking ───────────────────────────────────────────────────────────────
    "HDFCBANK.NS":    "Banking",
    "ICICIBANK.NS":   "Banking",
    "KOTAKBANK.NS":   "Banking",
    "AXISBANK.NS":    "Banking",
    "SBIN.NS":        "Banking",
    "INDUSINDBK.NS":  "Banking",
    "BANKBARODA.NS":  "Banking",
    "CANBK.NS":       "Banking",
    "IDFCFIRSTB.NS":  "Banking",
    # ── Finance / Insurance ───────────────────────────────────────────────────
    "BAJFINANCE.NS":  "Finance",
    "BAJAJFINSV.NS":  "Finance",
    "MUTHOOTFIN.NS":  "Finance",
    "CHOLAFIN.NS":    "Finance",
    "SBILIFE.NS":     "Insurance",
    "HDFCLIFE.NS":    "Insurance",
    # ── IT ────────────────────────────────────────────────────────────────────
    "INFY.NS":        "IT",
    "TCS.NS":         "IT",
    "WIPRO.NS":       "IT",
    "HCLTECH.NS":     "IT",
    "TECHM.NS":       "IT",
    # ── Consumer / FMCG ──────────────────────────────────────────────────────
    "HINDUNILVR.NS":  "FMCG",
    "ITC.NS":         "FMCG",
    "NESTLEIND.NS":   "FMCG",
    "BRITANNIA.NS":   "FMCG",
    "DABUR.NS":       "FMCG",
    "MARICO.NS":      "FMCG",
    "COLPAL.NS":      "FMCG",
    "GODREJCP.NS":    "FMCG",
    "TATACONSUM.NS":  "FMCG",
    "MCDOWELL-N.NS":  "Consumer",
    # ── Pharma ────────────────────────────────────────────────────────────────
    "SUNPHARMA.NS":   "Pharma",
    "DRREDDY.NS":     "Pharma",
    "CIPLA.NS":       "Pharma",
    "DIVISLAB.NS":    "Pharma",
    "AUROPHARMA.NS":  "Pharma",
    "LUPIN.NS":       "Pharma",
    "TORNTPHARM.NS":  "Pharma",
    "ALKEM.NS":       "Pharma",
    "BIOCON.NS":      "Pharma",
    # ── Capital Goods / Industrial ────────────────────────────────────────────
    "LT.NS":          "Capital Goods",
    "HAVELLS.NS":     "Capital Goods",
    "ABB.NS":         "Capital Goods",
    "CUMMINSIND.NS":  "Capital Goods",
    "THERMAX.NS":     "Capital Goods",
    # ── Cement ────────────────────────────────────────────────────────────────
    "ULTRACEMCO.NS":  "Cement",
    "SHREECEM.NS":    "Cement",
    "AMBUJACEM.NS":   "Cement",
    "ACC.NS":         "Cement",
    "GRASIM.NS":      "Cement",
    # ── Metals & Mining ───────────────────────────────────────────────────────
    "JSWSTEEL.NS":    "Metals",
    "TATASTEEL.NS":   "Metals",
    "HINDALCO.NS":    "Metals",
    "SAIL.NS":        "Metals",
    "NMDC.NS":        "Metals",
    "COALINDIA.NS":   "Metals",
    # ── Energy & PSU ─────────────────────────────────────────────────────────
    "RELIANCE.NS":    "Energy",
    "ONGC.NS":        "Energy",
    "NTPC.NS":        "Energy",
    "POWERGRID.NS":   "Energy",
    "GAIL.NS":        "Energy",
    "IOC.NS":         "Energy",
    "HPCL.NS":        "Energy",
    "TATAPOWER.NS":   "Energy",
    "ADANIENT.NS":    "Conglomerate",
    "ADANIPORTS.NS":  "Infra",
    # ── Paints & Specialty ───────────────────────────────────────────────────
    "ASIANPAINT.NS":  "Paints",
    "BERGEPAINT.NS":  "Paints",
    "PIDILITIND.NS":  "Chemicals",
    # ── Consumer Discretionary ────────────────────────────────────────────────
    "TITAN.NS":       "Consumer",
    "APOLLOHOSP.NS":  "Healthcare",
    "INDIGO.NS":      "Aviation",
    "ZOMATO.NS":      "Consumer Tech",
    # ── Telecom ───────────────────────────────────────────────────────────────
    "BHARTIARTL.NS":  "Telecom",
}


# =============================================================================
# Helpers
# =============================================================================

_ALREADY_HELD = set(CURRENT_WATCHLIST) | set(WATCHLIST_ALIASES.keys())

_REGIME_REASON = {
    "COMPOUNDER":     "COMPOUNDER regime — buy-and-hold beats crossover",
    "MEAN_REVERTING": "MEAN_REVERTING regime — mean-reversion strategy is better",
    "UNCLASSIFIED":   "UNCLASSIFIED regime — manual review needed",
}


def _sector_exposure(watchlist_sectors: list[str], add_sectors: list[str]) -> str:
    from collections import Counter
    counts = Counter(watchlist_sectors + add_sectors)
    n = sum(counts.values())
    return " | ".join(
        f"{s} {v/n*100:.0f}%"
        for s, v in sorted(counts.items(), key=lambda x: -x[1])
    )


# =============================================================================
# Main pipeline
# =============================================================================

def run() -> None:
    out_lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        out_lines.append(text)

    n_universe = len(EXPANDED_UNIVERSE)
    est_minutes = round(n_universe * 0.8 / 60, 1)

    emit()
    emit("=" * 84)
    emit("  UNIVERSE EXPANSION ANALYSIS — Nifty 100")
    emit(f"  Data window: {START} → {END}  (3-year OOS window, matches walk-forward)")
    emit(f"  Universe: {n_universe} stocks  |  Screening: SMA score → Regime → Correlation")
    emit(f"  Estimated runtime: ~{est_minutes}–{est_minutes + 1} minutes")
    emit("=" * 84)

    # =========================================================================
    # Step 1 — SMA suitability screening (full universe)
    # =========================================================================

    emit()
    emit(f"  [1/4]  SMA SUITABILITY SCREENING  ({n_universe} stocks)  ...")
    emit()

    print(f"  Fetching Nifty benchmark ({BENCHMARK_TICKER}) ...", end="  ", flush=True)
    benchmark_df = _fetch_benchmark(BENCHMARK_TICKER, START, END)
    if benchmark_df is None:
        print("\n  FATAL: could not fetch Nifty benchmark. Exiting.")
        raise SystemExit(1)
    print(f"✓  ({len(benchmark_df)} days)")

    all_data:  dict[str, pd.DataFrame] = {}
    records:   list[dict]              = []
    skipped:   list[tuple[str, str]]   = []

    for i, (ticker, sector) in enumerate(EXPANDED_UNIVERSE.items(), 1):
        print(f"  [{i:>2}/{n_universe}] {ticker:<20}", end="  ", flush=True)

        df = _fetch_quiet(ticker, START, END)
        if df is None:
            print("✗  download failed")
            skipped.append((ticker, "download failed"))
            continue

        n_bars = len(df)
        if n_bars < MIN_BARS:
            print(f"✗  only {n_bars} bars (need ≥ {MIN_BARS})")
            skipped.append((ticker, f"only {n_bars} bars (need ≥ {MIN_BARS})"))
            continue

        metrics = compute_metrics(df, benchmark_df)
        if metrics is None:
            print("✗  metrics failed (insufficient data)")
            skipped.append((ticker, "compute_metrics returned None"))
            continue

        print(f"✓  {n_bars}d | vol {metrics['volatility']*100:.1f}% | β {metrics['beta']:.2f}")
        all_data[ticker] = df
        records.append({"ticker": ticker, "sector": sector, **metrics})

    if not records:
        print("  FATAL: no stocks produced valid metrics. Exiting.")
        raise SystemExit(1)

    results_df = pd.DataFrame(records).set_index("ticker")
    scored_df  = compute_scores(results_df)
    scored_df  = scored_df.sort_values("composite_score", ascending=False)
    scored_df["universe_rank"] = range(1, len(scored_df) + 1)

    n_ok = len(scored_df)
    emit(f"\n  SMA screening complete: {n_ok} scored, {len(skipped)} skipped.")

    # =========================================================================
    # Step 2 — Regime classification (top 30 only)
    # =========================================================================

    emit()
    emit(f"  [2/4]  REGIME CLASSIFICATION  (top {TOP_N} by SMA score)  ...")
    emit()

    top30 = scored_df.head(TOP_N).copy()
    regime_data: dict[str, dict] = {}

    for ticker in top30.index:
        if ticker not in all_data:
            print(f"  {ticker:<20}  ✗  no cached data")
            continue
        print(f"  {ticker:<20}", end="  ", flush=True)
        rm = compute_regime_metrics(all_data[ticker])
        if rm is None:
            print("✗  insufficient data for regime metrics")
            continue
        regime_data[ticker] = rm
        print(
            f"  H={rm['hurst']:.3f}  ADX={rm['adx_regime']:>5.1f}"
            f"  →  {rm['regime']}"
        )

    top30["regime"]     = top30.index.map(
        lambda t: regime_data.get(t, {}).get("regime",     "UNCLASSIFIED")
    )
    top30["hurst"]      = top30.index.map(
        lambda t: regime_data.get(t, {}).get("hurst",      float("nan"))
    )
    top30["adx_regime"] = top30.index.map(
        lambda t: regime_data.get(t, {}).get("adx_regime", float("nan"))
    )

    n_trending = int((top30["regime"] == "TRENDING_STRONG").sum())
    emit(f"\n  Regime done: {n_trending} TRENDING_STRONG in top {TOP_N}.")

    # =========================================================================
    # Step 3 — Correlation filter (TRENDING_STRONG candidates)
    # =========================================================================

    emit()
    emit("  [3/4]  CORRELATION FILTER  (TRENDING_STRONG candidates vs watchlist)  ...")
    emit()

    trending_tickers = top30[top30["regime"] == "TRENDING_STRONG"].index.tolist()

    # Fetch watchlist closing prices (same source as screener for consistency)
    watchlist_closes: dict[str, pd.Series] = {}
    for wt in CURRENT_WATCHLIST:
        print(f"  [watchlist]  {wt:<22}", end="  ", flush=True)
        df = _fetch_quiet(wt, START, END)
        if df is not None and len(df) >= MIN_BARS:
            watchlist_closes[wt] = df["close"]
            print(f"✓  {len(df)}d")
        else:
            print("✗  fetch failed — excluded from correlation")

    # Build a single closes DataFrame: watchlist + TRENDING_STRONG candidates
    all_closes: dict[str, pd.Series] = dict(watchlist_closes)
    for t in trending_tickers:
        if t in all_data:
            all_closes[t] = all_data[t]["close"]

    # Align on common dates, forward-fill up to 3 days for minor gaps
    closes_df = pd.DataFrame(all_closes).ffill(limit=3).dropna()
    returns_df = closes_df.pct_change().dropna()
    n_overlap   = len(closes_df)
    corr_matrix = returns_df.corr() if n_overlap > 30 else pd.DataFrame()

    # Per-candidate stats
    corr_stats: dict[str, dict] = {}
    wl_in_corr = [w for w in CURRENT_WATCHLIST if w in corr_matrix.columns]

    for t in trending_tickers:
        if t not in corr_matrix.index or not wl_in_corr:
            continue
        vals   = [corr_matrix.loc[t, w] for w in wl_in_corr]
        max_i  = int(np.argmax(vals))
        corr_stats[t] = {
            "avg_corr":      float(np.mean(vals)),
            "max_corr":      float(vals[max_i]),
            "max_corr_peer": wl_in_corr[max_i],
        }

    top30["avg_corr"] = top30.index.map(
        lambda t: corr_stats.get(t, {}).get("avg_corr", float("nan"))
    )
    top30["max_corr"] = top30.index.map(
        lambda t: corr_stats.get(t, {}).get("max_corr", float("nan"))
    )

    emit(f"\n  Correlation computed on {n_overlap} overlapping trading days.")

    # =========================================================================
    # Step 4 — Build recommendation lists
    # =========================================================================

    # Already-held or aliased stocks are filtered out before recommending
    new_candidates = top30[~top30.index.isin(_ALREADY_HELD)]

    # TRENDING_STRONG subset of new candidates
    ts_candidates = new_candidates[new_candidates["regime"] == "TRENDING_STRONG"]

    # Final: score >= threshold AND avg_corr < threshold
    def _passes(row) -> bool:
        if row["composite_score"] < SCORE_THRESH:
            return False
        corr_v = row.get("avg_corr", float("nan"))
        if not np.isnan(corr_v) and corr_v >= CORR_THRESH:
            return False
        return True

    recommended = ts_candidates[ts_candidates.apply(_passes, axis=1)].copy()

    # =========================================================================
    # Print: TOP 30 table
    # =========================================================================

    emit()
    emit("=" * 84)
    emit(f"  TOP {TOP_N} BY SMA SCORE  (all regimes)")
    emit("=" * 84)

    W_RK, W_ST, W_SC, W_RE, W_CO = 5, 17, 7, 22, 20
    emit(
        f"  {'#':>{W_RK}}  {'Stock':<{W_ST}}  {'Score':>{W_SC}}"
        f"  {'Regime':<{W_RE}}  {'Avg Corr (existing)':<{W_CO}}"
    )
    emit("  " + "─" * (W_RK + W_ST + W_SC + W_RE + W_CO + 10))

    for _, row in top30.iterrows():
        ticker   = row.name
        corr_v   = row.get("avg_corr", float("nan"))
        corr_str = f"{corr_v:.2f}" if not np.isnan(corr_v) else "–"
        held     = " ← HELD" if ticker in _ALREADY_HELD else ""
        emit(
            f"  {int(row['universe_rank']):>{W_RK}}  {ticker:<{W_ST}}"
            f"  {row['composite_score']:>{W_SC}.1f}"
            f"  {row['regime']:<{W_RE}}  {corr_str:<{W_CO}}{held}"
        )

    # =========================================================================
    # Print: RECOMMENDED NEW ADDITIONS
    # =========================================================================

    emit()
    emit("=" * 84)
    emit(
        f"  RECOMMENDED NEW ADDITIONS"
        f"  (TRENDING_STRONG | score ≥ {SCORE_THRESH:.0f} | avg corr < {CORR_THRESH:.2f})"
    )
    emit("=" * 84)

    W_S, W_SC2, W_RE2, W_SEC, W_CO2, W_VE = 17, 7, 18, 16, 10, 7
    emit(
        f"  {'Stock':<{W_S}}  {'Score':>{W_SC2}}  {'Regime':<{W_RE2}}"
        f"  {'Sector':<{W_SEC}}  {'Avg Corr':>{W_CO2}}  {'Verdict':<{W_VE}}"
    )
    emit("  " + "─" * (W_S + W_SC2 + W_RE2 + W_SEC + W_CO2 + W_VE + 14))

    if recommended.empty:
        emit("  (no stocks met all criteria)")
    else:
        for _, row in recommended.iterrows():
            corr_v   = row.get("avg_corr", float("nan"))
            corr_str = f"{corr_v:.2f}" if not np.isnan(corr_v) else "–"
            emit(
                f"  {row.name:<{W_S}}  {row['composite_score']:>{W_SC2}.1f}"
                f"  {row['regime']:<{W_RE2}}  {row['sector']:<{W_SEC}}"
                f"  {corr_str:>{W_CO2}}  {'✓ ADD':<{W_VE}}"
            )

    # =========================================================================
    # Print: EXCLUDED (top 30, not held, didn't pass)
    # =========================================================================

    emit()
    emit("=" * 84)
    emit("  EXCLUDED  (top-30 candidates that didn't meet all criteria)")
    emit("=" * 84)

    any_excluded = False
    for ticker, row in top30.iterrows():
        # Skip current holdings — not "excluded", just already owned
        if ticker in _ALREADY_HELD:
            continue
        # Skip if it made the recommended list
        if ticker in recommended.index:
            continue

        regime = row.get("regime", "UNCLASSIFIED")
        score  = row["composite_score"]
        corr_v = row.get("avg_corr", float("nan"))
        max_c  = row.get("max_corr", float("nan"))

        reasons: list[str] = []
        if regime != "TRENDING_STRONG":
            reasons.append(_REGIME_REASON.get(regime, f"{regime} regime"))
        else:
            if score < SCORE_THRESH:
                reasons.append(f"SMA score below {SCORE_THRESH:.0f} ({score:.1f})")
            if not np.isnan(corr_v) and corr_v >= CORR_THRESH:
                peer  = corr_stats.get(ticker, {}).get("max_corr_peer", "existing")
                tag   = "VERY HIGH CORR" if (not np.isnan(max_c) and max_c >= 0.85) else "HIGH CORR"
                reasons.append(
                    f"{tag} with {peer} (r={max_c:.2f}), avg corr = {corr_v:.2f}"
                )

        for reason in reasons:
            emit(f"  {ticker:<22}  — {reason}")
            any_excluded = True

    if not any_excluded:
        emit("  (none — all top-30 non-held stocks passed through to recommended)")

    # =========================================================================
    # Print: SKIPPED
    # =========================================================================

    emit()
    emit("=" * 84)
    emit("  SKIPPED  (data issues — full list in screener/skipped.txt)")
    emit("=" * 84)

    if skipped:
        for ticker, reason in skipped:
            emit(f"  {ticker:<22}  — {reason}")
    else:
        emit("  (none)")

    # =========================================================================
    # Print: SECTOR EXPOSURE
    # =========================================================================

    emit()
    emit("=" * 84)
    emit("  SECTOR EXPOSURE")
    emit("=" * 84)

    current_sector_list = list(WATCHLIST_SECTORS.values())
    new_sector_list     = [row["sector"] for _, row in recommended.iterrows()]
    n_after             = len(current_sector_list) + len(new_sector_list)

    emit(f"  Current 8:    {_sector_exposure(current_sector_list, [])}")

    if new_sector_list:
        emit(f"  After adding: {_sector_exposure(current_sector_list, new_sector_list)}")
        emit()
        emit(f"  Stocks added: {len(new_sector_list)}")
        emit(f"  Total universe size: {n_after}")

        from collections import Counter
        combined = Counter(current_sector_list + new_sector_list)
        flagged  = [s for s, c in combined.items() if c / n_after >= 0.25]
        if flagged:
            emit(f"  ⚠ Sectors at ≥ 25% concentration: {', '.join(flagged)}")
        else:
            emit("  ✓ All sectors below 25% concentration threshold.")
    else:
        emit("  (no additions — sector breakdown unchanged)")

    emit()
    emit("=" * 84)

    # =========================================================================
    # Save outputs
    # =========================================================================

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results_path = Path(__file__).parent / "universe_expansion_results.txt"
    with open(results_path, "w") as fh:
        fh.write(f"Universe Expansion Analysis — generated {ts}\n\n")
        for line in out_lines:
            fh.write(line + "\n")
    print(f"\n  Results saved → {results_path}")

    skipped_path = Path(__file__).parent / "skipped.txt"
    with open(skipped_path, "w") as fh:
        fh.write(f"Skipped stocks — {ts}\n\n")
        for ticker, reason in skipped:
            fh.write(f"{ticker}: {reason}\n")
    if skipped:
        print(f"  Skipped log saved → {skipped_path}")


if __name__ == "__main__":
    run()
