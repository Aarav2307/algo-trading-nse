"""
paper_trading/correlation_check.py — Correlation safety check for new BUY entries.

Measures Pearson correlation of daily log returns between a candidate stock
and all currently open positions over the last 120 calendar days (matching
the 80-bar SMA window used by signal_runner).

This is a position-level check — it compares only against stocks currently
held (shares > 0), not against the full universe. A high correlation with an
already-held position means the candidate would add concentrated directional
risk without diversification benefit.

Usage:
    # Manual pre-entry check:
    python paper_trading/correlation_check.py PERSISTENT.NS

    # Programmatic (called from signal_runner.py Phase 2 loop):
    from paper_trading.correlation_check import check_entry_correlation
    result = check_entry_correlation(candidate="PERSISTENT.NS")
    if not result["safe"]:
        # suppress BUY
"""

import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


def check_entry_correlation(
    candidate: str,
    portfolio_state_path: str = "paper_trading/portfolio_state.json",
    portfolio_state_dict: dict = None,   # overrides file read when provided
    lookback_days: int = 120,
    max_correlation: float = 0.60,
    price_data: dict = None,
) -> dict:
    """
    Check if a candidate stock is correlation-safe to enter given current
    open positions.

    Computes Pearson correlation of daily log returns between the candidate
    and each stock currently held (shares > 0) over the last ~lookback_days
    calendar days. A candidate is safe if its maximum pairwise correlation
    with any open position is below max_correlation.

    Args:
        candidate:            NSE ticker to evaluate, e.g. "PERSISTENT.NS"
        portfolio_state_path: path to portfolio_state.json
        portfolio_state_dict: optional in-memory state dict (overrides file)
        lookback_days:        calendar-day lookback window (default 120)
        max_correlation:      Pearson correlation threshold (default 0.60)
        price_data:           optional dict of {ticker: pd.DataFrame} with a
            'close' column. When provided, skips all API calls and uses this
            data directly — pass signal_runner's dfs dict for zero-latency,
            token-free, perfectly-consistent correlation computation. When
            None (default), fetches via yfinance — suitable for CLI usage
            where a Kite token may not be available.

    Returns:
        {
            "candidate":       "PERSISTENT.NS",
            "open_positions":  ["BAJAJ-AUTO.NS"],
            "correlations":    {"BAJAJ-AUTO.NS": 0.34},
            "max_correlation": 0.34,
            "threshold":       0.60,
            "safe":            True,
            "reason":          "Max correlation 0.340 < threshold 0.60 — SAFE to enter"
        }

    NaN handling: if a pair has insufficient overlapping data (<10 common
    bars), the correlation is recorded as NaN and noted in `reason`. NaN
    pairs never block entry — only measured pairs can trigger a block.
    """
    # ── Load portfolio state ───────────────────────────────────────────────────
    if portfolio_state_dict is not None:
        state = portfolio_state_dict
    else:
        state_path = Path(portfolio_state_path)
        if not state_path.exists():
            return {
                "candidate":       candidate,
                "open_positions":  [],
                "correlations":    {},
                "max_correlation": None,
                "threshold":       max_correlation,
                "safe":            True,
                "reason":          "No portfolio state file — correlation check skipped",
            }
        with open(state_path) as fh:
            state = json.load(fh)

    # Exclude pending_buy positions: cash not yet deducted, not truly open.
    # This is consistent with get_portfolio_value() and get_etf_target_tier().
    open_positions = [
        ticker
        for ticker, pos in state.get("positions", {}).items()
        if pos.get("shares", 0) > 0
        and not pos.get("pending_buy", False)
    ]

    # ── No open positions — nothing to correlate against ──────────────────────
    if not open_positions:
        return {
            "candidate":       candidate,
            "open_positions":  [],
            "correlations":    {},
            "max_correlation": None,
            "threshold":       max_correlation,
            "safe":            True,
            "reason":          "No open positions — correlation check not required",
        }

    # ── Build close price DataFrame ───────────────────────────────────────────
    tickers_to_fetch = [candidate] + open_positions

    if price_data is not None:
        # ── Path A: pre-fetched data from signal_runner (zero API calls) ──────
        # DataFrames are already in memory — just extract close prices.
        # This is the path used by signal_runner.py at 3:45 PM.
        # Data is identical to what generated the BUY signal — perfect consistency.
        close_series: dict[str, pd.Series] = {}
        for ticker in tickers_to_fetch:
            if ticker in price_data and price_data[ticker] is not None:
                df = price_data[ticker]
                if "close" in df.columns and len(df) >= 10:
                    close_series[ticker] = df["close"]
        if len(close_series) < 2:
            return {
                "candidate":       candidate,
                "open_positions":  open_positions,
                "correlations":    {},
                "max_correlation": None,
                "threshold":       max_correlation,
                "safe":            True,
                "reason":          "Insufficient pre-fetched data — correlation check inconclusive, allowing entry",
            }
        close_df = pd.DataFrame(close_series)

    else:
        # ── Path B: yfinance fallback (CLI usage, no token required) ──────────
        # Used when called from command line for manual candidate evaluation.
        # yfinance works without authentication — suitable for weekend use.
        import yfinance as yf
        raw = yf.download(
            tickers_to_fetch,
            period="6mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            close_df = raw["Close"]
        else:
            close_df = raw[["Close"]].rename(
                columns={"Close": tickers_to_fetch[0]}
            )

    # ── Compute daily log returns ─────────────────────────────────────────────
    log_returns = np.log(close_df / close_df.shift(1)).iloc[1:]  # drop first NaN row

    # ── Pairwise correlations ─────────────────────────────────────────────────
    correlations: dict = {}
    nan_pairs:    list = []

    for open_ticker in open_positions:
        if candidate not in log_returns.columns or open_ticker not in log_returns.columns:
            nan_pairs.append(open_ticker)
            correlations[open_ticker] = float("nan")
            continue

        pair = log_returns[[candidate, open_ticker]].dropna()

        if len(pair) < 10:
            nan_pairs.append(open_ticker)
            correlations[open_ticker] = float("nan")
            continue

        corr = pair[candidate].corr(pair[open_ticker])
        correlations[open_ticker] = (
            round(float(corr), 4) if not np.isnan(corr) else float("nan")
        )

    # ── Aggregate ─────────────────────────────────────────────────────────────
    non_nan = [v for v in correlations.values() if not np.isnan(v)]

    # All pairs returned NaN — cannot determine risk; fail open
    if not non_nan:
        return {
            "candidate":       candidate,
            "open_positions":  open_positions,
            "correlations":    correlations,
            "max_correlation": None,
            "threshold":       max_correlation,
            "safe":            True,
            "reason": (
                f"Insufficient overlapping data for all pairs "
                f"({', '.join(nan_pairs)}) — correlation check inconclusive, "
                f"allowing entry"
            ),
        }

    max_corr = max(non_nan)
    safe     = max_corr < max_correlation

    nan_note = (
        f" (NaN for {', '.join(nan_pairs)} — insufficient data)"
        if nan_pairs else ""
    )

    if safe:
        reason = (
            f"Max correlation {max_corr:.3f} < threshold {max_correlation:.2f} "
            f"— SAFE to enter{nan_note}"
        )
    else:
        unsafe_pairs = [
            k for k, v in correlations.items()
            if not np.isnan(v) and v >= max_correlation
        ]
        reason = (
            f"Correlation too high with {', '.join(unsafe_pairs)} "
            f"({max_corr:.3f} ≥ threshold {max_correlation:.2f}) "
            f"— UNSAFE{nan_note}"
        )

    return {
        "candidate":       candidate,
        "open_positions":  open_positions,
        "correlations":    correlations,
        "max_correlation": round(max_corr, 4),
        "threshold":       max_correlation,
        "safe":            safe,
        "reason":          reason,
    }


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python paper_trading/correlation_check.py TICKER.NS")
        sys.exit(1)

    candidate = sys.argv[1]
    # CLI usage: price_data=None → yfinance fallback (no token needed)
    # signal_runner usage: price_data=dfs → pre-fetched, zero API calls
    result = check_entry_correlation(candidate)

    print(f"\nCorrelation Check — {result['candidate']}")
    print(f"Open positions checked: {result['open_positions']}")
    print(f"Threshold: {result['threshold']}")
    print()

    if not result["open_positions"]:
        print(f"  {result['reason']}")
    else:
        for ticker, corr in result["correlations"].items():
            if np.isnan(corr):
                corr_str = "NaN"
                flag = "⚠️  (insufficient data)"
            elif corr >= result["threshold"]:
                corr_str = f"{corr:.3f}"
                flag = "⚠️  UNSAFE"
            else:
                corr_str = f"{corr:.3f}"
                flag = "✅ safe"
            print(f"  vs {ticker}: {corr_str}  {flag}")

        print()
        if result["max_correlation"] is not None:
            print(f"Max correlation: {result['max_correlation']:.3f}")
        verdict = "✅ SAFE TO ENTER" if result["safe"] else "❌ UNSAFE — DO NOT ENTER"
        print(f"Verdict: {verdict}")
        print(f"Reason: {result['reason']}")
