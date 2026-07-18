"""
screener/compare_screeners.py — Side-by-side comparison of both screeners.

Runs sma_screener and regime_classifier over the identical window defined in
screener/config.py and prints a unified table showing where the tools agree,
where they disagree, and which stocks land in the dual-candidate borderline.

WHY WINDOW ALIGNMENT MATTERS
──────────────────────────────
Before config.py existed, comparing the two tools was meaningless:
  - sma_screener used a rolling "today minus 3 years" window
  - regime_classifier used that same rolling window for Hurst, but then took
    only the last 252 days of the ADX series for its regime signal

Any disagreement between the tools could simply reflect the fact that one was
measuring BHEL over 2020-2023 while the other was measuring it over 2022-2024.
After alignment, any remaining disagreement is a real signal about the stock —
the two tools are genuinely seeing different things about the same price history.

CONSENSUS CATEGORIES
─────────────────────
  AGREE_TREND   sma "Strong fit"   + regime "TRENDING_STRONG"
                → both recommend SMA crossover; high conviction signal

  AGREE_AVOID   sma "Poor fit"     + regime "MEAN_REVERTING"
                → both say don't use SMA; use mean reversion instead

  AGREE_HOLD    sma "Poor fit"     + regime "COMPOUNDER"
                → both say don't use SMA; buy and hold

  BORDERLINE    Hurst ∈ (0.50, 0.54] AND ADX < 20
                → weak trending character, no confirmed trend; dual candidate.
                  Both SMA crossover AND mean reversion may work (see Marico
                  backtest: +95% MR despite H=0.529). Needs further testing.

  DISAGREE      Tools point to conflicting strategies
                → the real signal: one tool is measuring something the other
                  cannot see. Investigate before deploying capital.

  UNCLEAR       Moderate fit OR UNCLASSIFIED → neither tool commits

EFFICIENCY
───────────
Each stock is downloaded exactly once. The same DataFrame is piped to both
tools' pure functions, halving network calls vs running the screeners separately.

API SURFACE
────────────
  run_comparison(universe, start, end, benchmark)  → pd.DataFrame
  classify_consensus(sma_verdict, regime, hurst, adx)  → str
  print_comparison(df)  → None
  validate_aligned(df)  → None
"""

import io
import os
import sys
import warnings
from contextlib import redirect_stdout, redirect_stderr

import numpy as np
import pandas as pd

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Shared config ─────────────────────────────────────────────────────────────
try:
    from screener.config import (
        START_DATE, END_DATE, BENCHMARK_TICKER, HURST_BORDERLINE_UPPER
    )
except ImportError:
    from config import (
        START_DATE, END_DATE, BENCHMARK_TICKER, HURST_BORDERLINE_UPPER
    )

# ── SMA screener (pure functions only — no printing) ──────────────────────────
try:
    from screener.sma_screener import (
        UNIVERSE, compute_metrics, compute_scores,
        _fetch_quiet, _fetch_benchmark,
    )
except ImportError:
    from sma_screener import (
        UNIVERSE, compute_metrics, compute_scores,
        _fetch_quiet, _fetch_benchmark,
    )

# ── Regime classifier (pure functions only — no printing) ─────────────────────
try:
    from screener.regime_classifier import (
        compute_regime_metrics, ADX_NOTCH_THRESH, VALIDATION_CASES
    )
except ImportError:
    from regime_classifier import (
        compute_regime_metrics, ADX_NOTCH_THRESH, VALIDATION_CASES
    )

# ── Consensus classification ──────────────────────────────────────────────────

# Mapping from (sma_verdict, regime) → consensus bucket.
# "Strong fit" means sma_screener recommends SMA crossover.
# "Poor fit"   means sma_screener recommends against SMA crossover.
_CONSENSUS_MAP: dict[tuple[str, str], str] = {
    ("Strong fit", "TRENDING_STRONG"):  "AGREE_TREND",
    ("Poor fit",   "MEAN_REVERTING"):   "AGREE_AVOID",
    ("Poor fit",   "COMPOUNDER"):       "AGREE_HOLD",
    ("Strong fit", "MEAN_REVERTING"):   "DISAGREE",
    ("Strong fit", "COMPOUNDER"):       "DISAGREE",
    ("Poor fit",   "TRENDING_STRONG"):  "DISAGREE",
}

_CONSENSUS_MARKER = {
    "AGREE_TREND":  "✓▲",
    "AGREE_AVOID":  "✓◄",
    "AGREE_HOLD":   "✓→",
    "BORDERLINE":   "⊕ ",
    "DISAGREE":     "✗ ",
    "UNCLEAR":      "~  ",
}


def classify_consensus(
    sma_verdict: str,
    regime: str,
    hurst: float,
    adx: float,
) -> str:
    """
    Determine the consensus bucket for one stock given both tools' outputs.

    Borderline check takes priority over the lookup table: a stock with
    H ∈ (0.50, HURST_BORDERLINE_UPPER] AND ADX < ADX_NOTCH_THRESH is a
    dual candidate regardless of what the sma_screener verdict says, because
    the Hurst signal is too weak to distinguish trending from random walk.

    Args:
        sma_verdict:  "Strong fit" | "Moderate fit" | "Poor fit"
        regime:       "TRENDING_STRONG" | "MEAN_REVERTING" | "COMPOUNDER" | "UNCLASSIFIED"
        hurst:        Hurst exponent (0 < H < 1)
        adx:          full-window median ADX

    Returns:
        One of: AGREE_TREND, AGREE_AVOID, AGREE_HOLD, BORDERLINE, DISAGREE, UNCLEAR
    """
    # Borderline takes priority: H barely above 0.5 and no confirmed trend
    if 0.50 < hurst <= HURST_BORDERLINE_UPPER and adx < ADX_NOTCH_THRESH:
        return "BORDERLINE"

    # Direct lookup for the four strong-commitment combinations
    key = (sma_verdict, regime)
    if key in _CONSENSUS_MAP:
        return _CONSENSUS_MAP[key]

    # Everything else: Moderate fit, or UNCLASSIFIED regime — no clear signal
    return "UNCLEAR"


# ── Core comparison pipeline ──────────────────────────────────────────────────

def run_comparison(
    universe: dict[str, str],
    start: str,
    end: str,
    benchmark_ticker: str = BENCHMARK_TICKER,
) -> pd.DataFrame:
    """
    Download each stock ONCE, run both tools' pure metric functions on the
    same DataFrame, and return a unified comparison DataFrame.

    This is the intended API endpoint for a future frontend — it returns pure
    data with no printing.

    Args:
        universe:          {ticker: sector} dict
        start:             ISO date string (inclusive)
        end:               ISO date string (exclusive)
        benchmark_ticker:  benchmark index for beta calculation

    Returns:
        DataFrame indexed by ticker with columns:
            sector, sma_score, sma_verdict,
            hurst, adx, regime,
            consensus
        Sorted by consensus bucket then by sma_score descending within bucket.
    """
    # ── Step 1: benchmark (needed by sma_screener for beta) ───────────────
    print(f"  [{benchmark_ticker}] Fetching benchmark...", end="  ", flush=True)
    nifty_df = _fetch_benchmark(benchmark_ticker, start, end)
    if nifty_df is None:
        raise RuntimeError(f"Could not download benchmark {benchmark_ticker}")
    print(f"✓  ({len(nifty_df)} days)")

    # ── Step 2: per-stock — ONE download, both tools ───────────────────────
    total    = len(universe)
    sma_recs = []     # raw metrics for sma_screener.compute_scores()
    reg_recs = []     # processed metrics from regime_classifier
    skipped  = []

    for idx, (ticker, sector) in enumerate(universe.items(), 1):
        print(f"  [{idx:>2}/{total}] {ticker:<18}", end="  ", flush=True)

        # Single download — same df goes to both tools below
        df = _fetch_quiet(ticker, start, end)
        if df is None:
            print("✗  download failed")
            skipped.append(ticker)
            continue

        # SMA screener metrics (volatility, beta, drawdown, trend_duration, mr_score)
        sma_m = compute_metrics(df, nifty_df)

        # Regime classifier metrics (hurst, adx_series, adx_regime, regime, strategy)
        reg_m = compute_regime_metrics(df)

        if sma_m is None or reg_m is None:
            print("✗  insufficient data")
            skipped.append(ticker)
            continue

        print(
            f"✓  SMA-vol {sma_m['volatility']*100:.1f}%"
            f"  H={reg_m['hurst']:.3f}"
            f"  ADX={reg_m['adx_regime']:.1f}"
            f"  {reg_m['regime']}"
        )

        sma_recs.append({"ticker": ticker, "sector": sector, **sma_m})
        reg_recs.append({
            "ticker":  ticker,
            "hurst":   reg_m["hurst"],
            "adx":     reg_m["adx_regime"],
            "regime":  reg_m["regime"],
        })

    if skipped:
        print(f"\n  Skipped: {', '.join(skipped)}")

    if not sma_recs:
        raise RuntimeError("No stocks returned valid metrics.")

    # ── Step 3: score SMA metrics within the peer group ───────────────────
    sma_raw = pd.DataFrame(sma_recs).set_index("ticker")
    sma_scored = compute_scores(sma_raw)   # adds composite_score, verdict

    # ── Step 4: join into unified DataFrame ───────────────────────────────
    reg_df = pd.DataFrame(reg_recs).set_index("ticker")

    combined = sma_scored[["sector", "composite_score", "verdict"]].join(
        reg_df[["hurst", "adx", "regime"]], how="inner"
    )
    combined = combined.rename(columns={"composite_score": "sma_score",
                                        "verdict": "sma_verdict"})

    # ── Step 5: consensus ─────────────────────────────────────────────────
    combined["consensus"] = combined.apply(
        lambda r: classify_consensus(r["sma_verdict"], r["regime"],
                                     r["hurst"], r["adx"]),
        axis=1,
    )

    # Sort: consensus bucket order, then SMA score descending within bucket
    bucket_order = {
        "AGREE_TREND": 0, "AGREE_AVOID": 1, "AGREE_HOLD": 2,
        "BORDERLINE": 3, "UNCLEAR": 4, "DISAGREE": 5,
    }
    combined["_o"] = combined["consensus"].map(bucket_order).fillna(6)
    combined = (combined
                .sort_values(["_o", "sma_score"], ascending=[True, False])
                .drop(columns="_o"))

    return combined


# ── Output ────────────────────────────────────────────────────────────────────

_W = 108

def _rule(c="─"):
    return c * _W


def print_comparison(df: pd.DataFrame) -> None:
    """
    Print the unified side-by-side comparison table, grouped by consensus bucket,
    followed by three summary sections: agreement, disagreement, borderline.
    """
    print("\n" + "=" * _W)
    print(f"  SCREENER COMPARISON  |  Window: {START_DATE} → {END_DATE}  (identical for both tools)")
    print(f"  sma_screener composite score  vs  regime_classifier Hurst + ADX")
    print("=" * _W)

    header = (
        f"  {'Ticker':<18}  {'Sector':<14}  "
        f"{'SMA Score':>9}  {'SMA Verdict':<14}  "
        f"{'H':>6}  {'ADX':>6}  {'Regime':<18}  Consensus"
    )

    # ── Per-bucket sections ───────────────────────────────────────────────────
    bucket_labels = {
        "AGREE_TREND":  "✓▲  AGREEMENT — TRENDING_STRONG  (SMA crossover)",
        "AGREE_AVOID":  "✓◄  AGREEMENT — AVOID SMA / use mean reversion",
        "AGREE_HOLD":   "✓→  AGREEMENT — COMPOUNDER / buy and hold",
        "BORDERLINE":   "⊕   BORDERLINE — dual candidate (H barely above 0.5, ADX < 20)",
        "UNCLEAR":      "~   UNCLEAR — moderate fit or UNCLASSIFIED regime",
        "DISAGREE":     "✗   DISAGREEMENT — tools conflict (manual review required)",
    }

    for bucket, label in bucket_labels.items():
        subset = df[df["consensus"] == bucket]
        if subset.empty:
            continue
        print(f"\n  {label}  [{len(subset)} stocks]")
        print(_rule())
        print(header)
        print(_rule())
        for ticker, row in subset.iterrows():
            h_flag = "!" if abs(row["hurst"] - 0.5) < 0.03 else " "
            print(
                f"  {ticker:<18}  {row['sector']:<14}  "
                f"{row['sma_score']:>9.1f}  {row['sma_verdict']:<14}  "
                f"{row['hurst']:>5.3f}{h_flag}  {row['adx']:>6.1f}"
                f"  {row['regime']:<18}  {row['consensus']}"
            )

    # ── Summary: counts ───────────────────────────────────────────────────────
    print("\n" + _rule("═"))
    print("  SUMMARY")
    print(_rule("═"))
    total = len(df)
    counts = df["consensus"].value_counts()
    for bucket in ["AGREE_TREND", "AGREE_AVOID", "AGREE_HOLD",
                   "BORDERLINE", "UNCLEAR", "DISAGREE"]:
        n   = counts.get(bucket, 0)
        pct = n / total * 100
        bar = "█" * int(pct / 2.5)
        print(f"  {bucket:<16}  {n:>3}  {bar:<40}  {pct:.0f}%")

    agree_n = sum(counts.get(b, 0) for b in ["AGREE_TREND", "AGREE_AVOID", "AGREE_HOLD"])
    print(f"\n  Total agreement (all buckets): {agree_n}/{total} ({agree_n/total*100:.0f}%)")
    print(f"  Disagreements requiring manual review: {counts.get('DISAGREE', 0)}")
    print(f"  Borderline dual-candidates: {counts.get('BORDERLINE', 0)}")

    # ── Action list: the 8 highest-conviction stocks ──────────────────────────
    print("\n" + _rule())
    print("  HIGH-CONVICTION CALLS (AGREE_TREND + AGREE_AVOID, sorted by SMA score)")
    print(_rule())
    hc = df[df["consensus"].isin(["AGREE_TREND", "AGREE_AVOID"])].nlargest(10, "sma_score")
    if hc.empty:
        print("  None found.")
    else:
        for ticker, row in hc.iterrows():
            rec = "→ SMA crossover" if row["consensus"] == "AGREE_TREND" else "→ Mean reversion"
            print(
                f"  {ticker:<18}  [{row['sector']:<14}]  "
                f"Score {row['sma_score']:>5.1f}  H={row['hurst']:.3f}  ADX={row['adx']:.1f}"
                f"  {rec}"
            )

    print(_rule())


def validate_aligned(df: pd.DataFrame) -> None:
    """
    Check the same 8 validation stocks over the aligned window and compare
    against the expected regimes from prior backtests.

    Also prints a before/after note: previous mismatches were caused by window
    misalignment; remaining mismatches after alignment are genuine.
    """
    print("\n" + _rule("═"))
    print("  VALIDATION — ALIGNED WINDOW RESULTS vs EXPECTED REGIME")
    print(f"  Window: {START_DATE} → {END_DATE} (identical for both metrics)")
    print(_rule("═"))
    print(f"  {'Ticker':<18}  {'Expected':<18}  {'SMA Verdict':<14}  "
          f"{'H':>6}  {'ADX':>6}  {'Regime':<18}  {'Consensus':<14}  Match?")
    print(_rule())

    matches = 0
    total   = 0

    for ticker, expected_regime in VALIDATION_CASES.items():
        total += 1
        if ticker not in df.index:
            print(f"  {ticker:<18}  {expected_regime:<18}  {'NOT IN UNIVERSE':<14}")
            continue

        row = df.loc[ticker]

        # A "match" means the regime_classifier landed on the expected regime.
        # We also check whether the consensus bucket is consistent.
        classified = row["regime"]
        consensus  = row["consensus"]
        is_match   = (classified == expected_regime)
        if is_match:
            matches += 1

        # Additionally flag "functional match": BORDERLINE stocks expected to be
        # MEAN_REVERTING are a partial match — the dual-candidate label
        # acknowledges they can work for MR even if not purely classified so.
        functional = is_match or (
            consensus == "BORDERLINE" and expected_regime == "MEAN_REVERTING"
        )
        flag = "✓" if is_match else ("≈" if functional else "✗")

        print(
            f"  {ticker:<18}  {expected_regime:<18}  {row['sma_verdict']:<14}  "
            f"{row['hurst']:>5.3f}  {row['adx']:>6.1f}  {classified:<18}"
            f"  {consensus:<14}  {flag}"
        )

    print(_rule())
    strict_pct    = matches / total * 100
    print(f"  Strict match (exact regime): {matches}/{total} ({strict_pct:.0f}%)")

    # Functional matches include BORDERLINE stocks expected to be MEAN_REVERTING
    functional_matches = sum(
        1 for t, exp in VALIDATION_CASES.items()
        if t in df.index and (
            df.loc[t, "regime"] == exp or
            (df.loc[t, "consensus"] == "BORDERLINE" and exp == "MEAN_REVERTING")
        )
    )
    print(f"  Functional match (includes BORDERLINE-as-MR): {functional_matches}/{total} ({functional_matches/total*100:.0f}%)")
    print()
    print("  INTERPRETATION:")
    print("  ─  Strict mismatches that remain after window alignment are GENUINE")
    print("     disagreements between the two tools — not measurement artifacts.")
    print("  ─  BORDERLINE stocks (H 0.50–0.54, ADX < 20) are not wrong; they are")
    print("     the stocks where both SMA and MR have shown empirical evidence of")
    print("     working (see Marico backtest: +95% MR, H=0.529).")
    print("  ─  UNCLASSIFIED regime means ADX fell in the 20–25 dead zone — the")
    print("     classifier correctly refused to commit, not a failure.")
    print(_rule("═"))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nScreener Comparison  |  {len(UNIVERSE)} stocks")
    print(f"Window: {START_DATE} → {END_DATE}  (shared, see screener/config.py)")
    print(f"Benchmark: {BENCHMARK_TICKER}")
    print(_rule())
    print("Downloading data (one pass — same DataFrame fed to both tools)...\n")

    results = run_comparison(UNIVERSE, START_DATE, END_DATE)
    print_comparison(results)
    validate_aligned(results)
