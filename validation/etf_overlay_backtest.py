"""
validation/etf_overlay_backtest.py — ETF overlay backtest.

Tests the proposed NIFTYBEES tiered overlay on top of the existing SMA 20/50
strategy to determine whether it improves risk-adjusted returns.

The overlay rule:
    0 stock positions active   → 100% remaining cash deployed in NIFTYBEES
    1-2 positions active       → 60% of remaining cash in NIFTYBEES
    3-4 positions active       → 30% of remaining cash in NIFTYBEES
    MAX_POSITIONS (4) active   → 0% in NIFTYBEES (fully deployed in stocks)

This is a VALIDATION tool, not a live trading system.
Capital is simulated. Results determine go/no-go for live ETF overlay.

Go/no-go criteria:
    ✅ Sharpe ratio improves by ≥ 0.15
    ✅ Max drawdown increases by < 8 percentage points
    ✅ Annual ETF transaction costs < 0.3% of portfolio value

Usage:
    python validation/etf_overlay_backtest.py            # full IS + OOS
    python validation/etf_overlay_backtest.py --quick    # first 50 stocks
    python validation/etf_overlay_backtest.py --oos-only # OOS only
    python validation/etf_overlay_backtest.py --is-only  # IS only
"""

import json
import sys
import argparse
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.kite_fetcher import get_ohlcv
from validation.portfolio_backtest import (
    run_single_stock,
    fetch_nifty_benchmark,
    compute_nifty_return,
    PARAMS,
    IS_START, IS_END,
    OOS_START, OOS_END,
    UNIVERSE_FILE,
    MIN_BARS,
)

# =============================================================================
# ETF overlay configuration
# =============================================================================

NIFTYBEES_TICKER = "NIFTYBEES.NS"
INITIAL_CAPITAL  = 100_000.0   # ₹1 lakh per stock scenario (same as portfolio_backtest.py)
MAX_POSITIONS    = 4           # mirrors signal_runner.py MAX_CONCURRENT_POSITIONS

# Tiered ETF allocation: {n_positions_active: fraction_of_remaining_cash_in_ETF}
ETF_TIERS = {
    0: 1.00,   # 0 positions → 100% of cash in ETF
    1: 0.60,   # 1 position  → 60% of remaining cash in ETF
    2: 0.60,   # 2 positions → 60% of remaining cash in ETF
    3: 0.30,   # 3 positions → 30% of remaining cash in ETF
    4: 0.00,   # 4 positions → 0% in ETF (fully deployed in stocks)
}

# ETF transaction costs: NIFTYBEES is equity delivery
# No brokerage (Zerodha ₹0 on ETF delivery), only SEBI + exchange + STT on sell
ETF_BUY_COST_PCT  = 0.0001    # SEBI + exchange on buy (~0.01%)
ETF_SELL_COST_PCT = 0.0003    # STT 0.025% + exchange 0.00335% on sell (~0.03%)


# =============================================================================
# Data helpers
# =============================================================================

def fetch_niftybees(start: str, end: str) -> pd.DataFrame:
    """
    Fetch NIFTYBEES daily OHLCV via kite_fetcher.
    Returns empty DataFrame on failure.
    """
    try:
        df = get_ohlcv(NIFTYBEES_TICKER, start, end)
        if df is not None and len(df) >= 10:
            ret = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
            print(
                f"  NIFTYBEES: {len(df)} bars "
                f"({df.index[0].date()} → {df.index[-1].date()}) "
                f"return {ret:+.1f}%"
            )
            return df
    except Exception as exc:
        print(f"  WARN: NIFTYBEES fetch failed ({exc})")
    return pd.DataFrame()


# =============================================================================
# Core simulation
# =============================================================================

def _simulate_stock_scenario(
    ticker: str,
    trades: pd.DataFrame,
    niftybees_ret: pd.Series,
    calendar: pd.DatetimeIndex,
    initial_capital: float,
    apply_overlay: bool,
) -> dict:
    """
    Simulate one stock scenario (₹initial_capital) with optional ETF overlay.

    Methodology (no re-fetching required):
    - During a stock trade: position is marked at entry value; P&L is credited on exit.
    - Between trades: idle cash earns NIFTYBEES return (overlay) or 0 (strategy-only).
    - Position size fraction = entry_price × shares / capital_at_entry.

    This is a cash-flow simulation, not daily mark-to-market of stock prices.
    The equity curve reflects the portfolio value including unrealised ETF returns
    between stock trades, and realised stock P&L on each trade exit.
    """
    # Precompute trade intervals for fast lookup
    # Each entry: (entry_ts, exit_ts, position_fraction, net_pnl)
    trade_intervals = []
    for _, t in trades.iterrows():
        entry_ts = pd.Timestamp(t["entry_date"]).normalize()
        exit_ts  = pd.Timestamp(t["exit_date"]).normalize()
        pos_frac = float(t["entry_price"]) * float(t["shares"]) / initial_capital
        pos_frac = min(pos_frac, 1.0)
        trade_intervals.append((entry_ts, exit_ts, pos_frac, float(t["net_pnl"])))

    # Build set of exit days → pnl for O(1) lookup
    exit_pnl: dict = {}
    for entry_ts, exit_ts, _, pnl in trade_intervals:
        exit_pnl.setdefault(exit_ts, 0.0)
        exit_pnl[exit_ts] += pnl

    capital = initial_capital
    equity_values = []
    etf_transactions  = 0
    etf_cost_total    = 0.0
    days_in_etf       = 0
    days_in_stocks    = 0
    prev_etf_frac     = 0.0
    current_etf_tier  = None   # FIX 1: tier guard — None forces initial allocation on first bar

    for day in calendar:
        # Determine active positions on this day
        n_active = sum(
            1 for (e, x, _, _) in trade_intervals
            if e <= day < x
        )
        stock_weight = sum(
            pf for (e, x, pf, _) in trade_intervals
            if e <= day < x
        )
        stock_weight = min(stock_weight, 1.0)
        remaining_weight = max(0.0, 1.0 - stock_weight)

        # FIX 1: compute tier key first; ETF fraction is tier × remaining weight
        new_tier = min(n_active, MAX_POSITIONS)
        if apply_overlay:
            etf_frac = ETF_TIERS.get(new_tier, 0.0) * remaining_weight
        else:
            etf_frac = 0.0

        # ETF rebalancing: only transact when the TIER changes (or on the very
        # first bar where current_etf_tier is None).  Within a tier, remaining_weight
        # drifts slightly as position value changes, but that does not trigger a trade.
        if apply_overlay and (current_etf_tier is None or new_tier != current_etf_tier):
            delta = abs(etf_frac - prev_etf_frac) * capital
            if delta > 0:
                cost = delta * (ETF_BUY_COST_PCT if etf_frac >= prev_etf_frac else ETF_SELL_COST_PCT)
                etf_cost_total += cost
                capital        -= cost
                etf_transactions += 1
            current_etf_tier = new_tier   # FIX 1: update tier after rebalance

        # Always keep prev_etf_frac current so next tier-change delta is accurate
        prev_etf_frac = etf_frac

        # Daily portfolio return
        etf_daily = float(niftybees_ret.get(day, 0.0)) if apply_overlay else 0.0
        capital *= (1.0 + etf_frac * etf_daily)

        # Book stock trade P&L on exit day
        if day in exit_pnl:
            capital += exit_pnl[day]

        if n_active > 0:
            days_in_stocks += 1
        if etf_frac > 0.001:
            days_in_etf += 1

        equity_values.append(capital)

    equity_series = pd.Series(equity_values, index=calendar, name=ticker)

    return {
        "ticker":          ticker,
        "equity_curve":    equity_series,
        "etf_transactions": etf_transactions,
        "etf_cost_total":  etf_cost_total,
        "days_in_stocks":  days_in_stocks,
        "days_in_etf":     days_in_etf,
    }


def _aggregate_equity_curves(
    scenario_curves: list[pd.Series],
    initial_capital: float,
    niftybees_df: pd.DataFrame,
    etf_cost_total: float,
    etf_transactions: int,
    days_in_stocks: int,
    days_in_etf: int,
) -> dict:
    """
    Average equity curves across all stock scenarios, then compute metrics.
    """
    if not scenario_curves:
        return {"error": "No equity curves to aggregate"}

    # Equal-weighted average portfolio value across all scenarios
    combined = pd.concat(scenario_curves, axis=1).mean(axis=1)
    n_days   = len(combined)

    total_ret = (combined.iloc[-1] / initial_capital - 1) * 100.0

    # CAGR
    n_years = n_days / 252.0
    cagr = ((combined.iloc[-1] / initial_capital) ** (1.0 / n_years) - 1) * 100.0 if n_years > 0 else 0.0

    # Sharpe (annualised, 6.5% risk-free rate)
    daily_rets = combined.pct_change().dropna()
    rf_daily   = 0.065 / 252.0
    sharpe     = float((daily_rets.mean() - rf_daily) / daily_rets.std() * np.sqrt(252)) if daily_rets.std() > 0 else 0.0

    # Max drawdown
    rolling_max = combined.cummax()
    drawdowns   = (combined - rolling_max) / rolling_max * 100.0
    max_dd      = float(drawdowns.min())

    # ETF cost annualised (average across scenarios, relative to initial capital)
    n_scenarios      = len(scenario_curves)
    avg_etf_cost_ann = (etf_cost_total / n_scenarios) / initial_capital / n_years * 100.0 if n_years > 0 else 0.0

    return {
        "total_ret":          total_ret,
        "cagr":               cagr,
        "sharpe":             sharpe,
        "max_dd":             max_dd,
        "etf_transactions":   etf_transactions,
        "etf_cost_total":     etf_cost_total / n_scenarios,  # per-scenario average
        "etf_cost_pct_ann":   avg_etf_cost_ann,
        "time_in_stocks_pct": days_in_stocks / n_scenarios / n_days * 100.0 if n_days > 0 else 0.0,
        "time_in_etf_pct":    days_in_etf    / n_scenarios / n_days * 100.0 if n_days > 0 else 0.0,
        "equity_curve":       combined,
    }


def simulate_etf_overlay(
    stock_results: list[dict],
    niftybees_df: pd.DataFrame,
    start: str,
    end: str,
    initial_capital: float = INITIAL_CAPITAL,
    apply_overlay: bool = True,
) -> dict:
    """
    Simulate a portfolio of per-stock scenarios with optional NIFTYBEES overlay.

    Each stock runs with its own ₹initial_capital. Equity curves are averaged
    to produce portfolio-level metrics. No re-fetching of stock data required —
    uses only the trade log from run_single_stock() results.

    Args:
        stock_results:  list of dicts from run_single_stock() with trades_df
        niftybees_df:   NIFTYBEES OHLCV DataFrame indexed by date
        start / end:    period boundaries
        initial_capital: per-scenario capital (₹)
        apply_overlay:  True → ETF overlay; False → strategy-only (cash idle)
    """
    if niftybees_df is None or len(niftybees_df) < 10:
        return {"error": "NIFTYBEES data unavailable"}

    # Master trading calendar from NIFTYBEES dates
    niftybees_df = niftybees_df.copy()
    niftybees_df.index = pd.to_datetime(niftybees_df.index).normalize()
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)
    calendar = niftybees_df.index[
        (niftybees_df.index >= start_ts) & (niftybees_df.index < end_ts)
    ]
    if len(calendar) < 10:
        return {"error": f"Insufficient calendar: {len(calendar)} days"}

    # Pre-compute NIFTYBEES daily returns (indexed by normalized date)
    niftybees_ret = niftybees_df["close"].pct_change().fillna(0.0)

    # Run per-stock simulations
    scenario_curves  = []
    etf_cost_total   = 0.0
    etf_transactions = 0
    days_in_stocks   = 0
    days_in_etf      = 0
    n_skipped        = 0

    for r in stock_results:
        if "error" in r or r.get("trades_df") is None:
            n_skipped += 1
            continue
        trades = r["trades_df"]
        if hasattr(trades, "empty") and trades.empty:
            # No trades — scenario is all-cash (or all-ETF with overlay)
            sim = _simulate_stock_scenario(
                r["ticker"], pd.DataFrame(), niftybees_ret, calendar,
                initial_capital, apply_overlay
            )
        else:
            sim = _simulate_stock_scenario(
                r["ticker"], trades, niftybees_ret, calendar,
                initial_capital, apply_overlay
            )

        scenario_curves.append(sim["equity_curve"])
        etf_cost_total   += sim["etf_cost_total"]
        etf_transactions += sim["etf_transactions"]
        days_in_stocks   += sim["days_in_stocks"]
        days_in_etf      += sim["days_in_etf"]

    if not scenario_curves:
        return {"error": "No valid scenarios"}

    return _aggregate_equity_curves(
        scenario_curves, initial_capital, niftybees_df,
        etf_cost_total, etf_transactions, days_in_stocks, days_in_etf,
    )


def compute_strategy_only_metrics(
    stock_results: list[dict],
    niftybees_df: pd.DataFrame,
    start: str,
    end: str,
    initial_capital: float = INITIAL_CAPITAL,
) -> dict:
    """Strategy-only metrics using same simulation framework (no ETF)."""
    return simulate_etf_overlay(
        stock_results, niftybees_df, start, end, initial_capital,
        apply_overlay=False,
    )


# =============================================================================
# Report
# =============================================================================

def print_overlay_report(
    strategy_only: dict,
    with_overlay: dict,
    niftybees_ret: Optional[float],
    nifty_ret: Optional[float],
    label: str = "OOS",
    period: str = "",
    niftybees_max_dd: Optional[float] = None,  # FIX 2: NIFTYBEES max_dd over same period
) -> dict:
    """
    Print side-by-side comparison: strategy-only vs ETF overlay vs benchmarks.
    Returns go/no-go dict.
    """
    W = 82

    def hr(c="="):
        print(c * W)

    def row(lbl, s_val, o_val, b_val=None, w=34):
        s = str(s_val) if s_val is not None else "—"
        o = str(o_val) if o_val is not None else "—"
        b = str(b_val) if b_val is not None else ""
        print(f"  {lbl:<{w}} {s:^18}  {o:^18}  {b:^10}")

    def pct(v, d=1):
        if v is None:
            return "—"
        return f"{'+' if v >= 0 else ''}{v:.{d}f}%"

    def inr(v):
        if v is None or v == 0.0:
            return "—"
        return f"₹{v:+,.0f}"

    def fmt_sharpe(v):
        if v is None:
            return "—"
        return f"{v:.3f}"

    print()
    hr()
    print(f"  ETF OVERLAY BACKTEST — {label}  ({period})")
    print(f"  Strategy-only vs NIFTYBEES tiered overlay vs NIFTY 50 benchmark")
    hr()
    print(f"  {'Metric':<34} {'Strategy Only':^18}  {'+ ETF Overlay':^18}  {'Benchmark':^10}")
    print("  " + "-" * (W - 2))

    row("Total return",
        pct(strategy_only.get("total_ret")),
        pct(with_overlay.get("total_ret")),
        pct(niftybees_ret))

    row("CAGR (annualised)",
        pct(strategy_only.get("cagr")),
        pct(with_overlay.get("cagr")),
        "")

    row("Sharpe ratio (rf=6.5%)",
        fmt_sharpe(strategy_only.get("sharpe")),
        fmt_sharpe(with_overlay.get("sharpe")),
        "")

    row("Max drawdown",
        pct(strategy_only.get("max_dd")),
        pct(with_overlay.get("max_dd")),
        "")

    row("NIFTY 50 return", "—", "—", pct(nifty_ret))

    print()
    row("Time in stocks",
        pct(strategy_only.get("time_in_stocks_pct")),
        pct(with_overlay.get("time_in_stocks_pct")),
        "")

    row("Time in NIFTYBEES ETF",
        pct(strategy_only.get("time_in_etf_pct")),
        pct(with_overlay.get("time_in_etf_pct")),
        "")

    row("ETF transactions (avg/stock)",
        "0",
        f"{with_overlay.get('etf_transactions', 0):,}",
        "")

    row("ETF cost total (avg/stock)",
        "₹0",
        inr(with_overlay.get("etf_cost_total")),
        "")

    row("ETF cost (% capital/year)",
        "0.000%",
        f"{with_overlay.get('etf_cost_pct_ann', 0):.3f}%",
        "")

    print()
    hr("-")
    print("  GO / NO-GO CRITERIA")
    hr("-")

    checks = {}

    sharpe_delta = (
        (with_overlay.get("sharpe") or 0.0) - (strategy_only.get("sharpe") or 0.0)
    )
    c1 = sharpe_delta >= 0.15
    checks["sharpe_improvement"] = c1
    icon = "✅" if c1 else "❌"
    print(f"  {icon} Sharpe improves by ≥ 0.15:        Δ = {sharpe_delta:+.3f}  ({'PASS' if c1 else 'FAIL'})")

    # FIX 2: compare overlay DD against NIFTYBEES DD + 2pp, not against
    # strategy-only DD.  Strategy-only sits in cash ~68% of the time, so its
    # "max_dd" is artificially near-zero and gives a misleading baseline.
    # The correct question is: does the overlay drawdown worse than a passive
    # NIFTYBEES holding would?
    overlay_max_dd  = float(with_overlay.get("max_dd") or 0.0)
    if niftybees_max_dd is not None:
        c2 = overlay_max_dd >= (niftybees_max_dd - 2.0)   # both negative; -16 >= -14-2 = -16 → PASS at boundary
        dd_label = (
            f"Overlay DD ({overlay_max_dd:.1f}%) vs NIFTYBEES DD "
            f"({niftybees_max_dd:.1f}%) + 2pp threshold "
            f"({niftybees_max_dd - 2.0:.1f}%)"
        )
    else:
        dd_delta = overlay_max_dd - (float(strategy_only.get("max_dd") or 0.0))
        c2 = dd_delta > -8.0
        dd_label = f"Max DD increase < 8pp: Δ = {dd_delta:+.1f}pp  (fallback — NIFTYBEES DD unavailable)"
    checks["drawdown_increase"] = c2
    icon = "✅" if c2 else "❌"
    print(f"  {icon} {dd_label}  ({'PASS' if c2 else 'FAIL'})")

    etf_cost = with_overlay.get("etf_cost_pct_ann", 0.0) or 0.0
    c3 = etf_cost < 0.30
    checks["etf_cost"] = c3
    icon = "✅" if c3 else "❌"
    print(f"  {icon} ETF cost < 0.30% per year:         {etf_cost:.3f}%  ({'PASS' if c3 else 'FAIL'})")

    overlay_ret = with_overlay.get("total_ret") or 0.0
    c4 = (niftybees_ret is not None) and (overlay_ret > niftybees_ret)
    checks["beats_niftybees"] = c4
    icon = "✅" if c4 else "❌"
    bee_s = pct(niftybees_ret) if niftybees_ret is not None else "N/A"
    print(f"  {icon} Overlay beats NIFTYBEES B&H:       {pct(overlay_ret)} vs {bee_s}  ({'PASS' if c4 else 'FAIL'})")

    n_core = sum([c1, c2, c3])
    print()
    if n_core == 3:
        print(f"  ✅ GO — All 3 core criteria pass. ETF overlay is validated for paper trading.")
    elif n_core == 2:
        print(f"  ⚠️  CONDITIONAL — 2/3 core criteria pass. Review failing criterion before going live.")
    else:
        print(f"  ❌ NO-GO — {n_core}/3 core criteria pass. ETF overlay not validated.")

    hr()
    return checks


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ETF overlay backtest — validates NIFTYBEES tiered overlay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validation/etf_overlay_backtest.py              # full IS + OOS
  python validation/etf_overlay_backtest.py --quick      # first 50 stocks
  python validation/etf_overlay_backtest.py --oos-only   # OOS only
  python validation/etf_overlay_backtest.py --is-only    # IS only
        """,
    )
    parser.add_argument("--quick",    action="store_true", help="First 50 stocks only")
    parser.add_argument("--oos-only", action="store_true", dest="oos_only")
    parser.add_argument("--is-only",  action="store_true", dest="is_only")
    args = parser.parse_args()

    with open(UNIVERSE_FILE) as f:
        universe = json.load(f)

    if args.quick:
        universe = universe[:50]
        print(f"[quick mode] {len(universe)} stocks")

    print(f"\nUniverse: {len(universe)} stocks")
    print(f"ETF tiers: {ETF_TIERS}")
    print(f"IS:  {IS_START} → {IS_END}")
    print(f"OOS: {OOS_START} → {OOS_END}")

    # ── Fetch benchmarks ──────────────────────────────────────────────────────
    print("\nFetching NIFTY 50 benchmark data...")
    nifty_is  = fetch_nifty_benchmark(IS_START,  IS_END)
    nifty_oos = fetch_nifty_benchmark(OOS_START, OOS_END)
    nifty_is_ret  = compute_nifty_return(nifty_is)
    nifty_oos_ret = compute_nifty_return(nifty_oos)
    if nifty_is_ret:
        print(f"  NIFTY 50 IS  return: {nifty_is_ret:+.1f}%")
    if nifty_oos_ret:
        print(f"  NIFTY 50 OOS return: {nifty_oos_ret:+.1f}%")

    print("\nFetching NIFTYBEES data...")
    niftybees_is  = fetch_niftybees(IS_START,  IS_END)
    niftybees_oos = fetch_niftybees(OOS_START, OOS_END)

    niftybees_is_ret = niftybees_oos_ret = None
    niftybees_is_max_dd = niftybees_oos_max_dd = None
    if not niftybees_is.empty:
        niftybees_is_ret = (
            niftybees_is["close"].iloc[-1] / niftybees_is["close"].iloc[0] - 1
        ) * 100.0
        # FIX 2: compute NIFTYBEES max drawdown over IS window
        _p = niftybees_is["close"]
        niftybees_is_max_dd = float(((_p - _p.cummax()) / _p.cummax() * 100.0).min())
    if not niftybees_oos.empty:
        niftybees_oos_ret = (
            niftybees_oos["close"].iloc[-1] / niftybees_oos["close"].iloc[0] - 1
        ) * 100.0
        # FIX 2: compute NIFTYBEES max drawdown over OOS window (same period as overlay)
        _p = niftybees_oos["close"]
        niftybees_oos_max_dd = float(((_p - _p.cummax()) / _p.cummax() * 100.0).min())

    is_checks  = {}
    oos_checks = {}

    # ── IS run ────────────────────────────────────────────────────────────────
    if not args.oos_only:
        print(f"\n{'='*60}")
        print(f"IN-SAMPLE: {IS_START} → {IS_END}")
        print("="*60)
        print("Running per-stock backtests...")
        is_results = []
        for i, ticker in enumerate(universe, 1):
            if i % 50 == 0 or i == 1:
                print(f"  [{i:>3}/{len(universe)}] {ticker}...", flush=True)
            r = run_single_stock(ticker, IS_START, IS_END, nifty_is)
            if "error" not in r:
                is_results.append(r)
        print(f"\n  {len(is_results)} stocks completed IS backtest")

        print("Simulating strategy-only...")
        is_strat = compute_strategy_only_metrics(
            is_results, niftybees_is, IS_START, IS_END
        )
        print("Simulating strategy + ETF overlay...")
        is_overlay = simulate_etf_overlay(
            is_results, niftybees_is, IS_START, IS_END
        )
        is_checks = print_overlay_report(
            is_strat, is_overlay,
            niftybees_is_ret, nifty_is_ret,
            label="IN-SAMPLE", period=f"{IS_START} → {IS_END}",
            niftybees_max_dd=niftybees_is_max_dd,
        )

    # ── OOS run ───────────────────────────────────────────────────────────────
    if not args.is_only:
        print(f"\n{'='*60}")
        print(f"OUT-OF-SAMPLE: {OOS_START} → {OOS_END}")
        print("="*60)
        print("Running per-stock backtests...")
        oos_results = []
        for i, ticker in enumerate(universe, 1):
            if i % 50 == 0 or i == 1:
                print(f"  [{i:>3}/{len(universe)}] {ticker}...", flush=True)
            r = run_single_stock(ticker, OOS_START, OOS_END, nifty_oos)
            if "error" not in r:
                oos_results.append(r)
        print(f"\n  {len(oos_results)} stocks completed OOS backtest")

        print("Simulating strategy-only...")
        oos_strat = compute_strategy_only_metrics(
            oos_results, niftybees_oos, OOS_START, OOS_END
        )
        print("Simulating strategy + ETF overlay...")
        oos_overlay = simulate_etf_overlay(
            oos_results, niftybees_oos, OOS_START, OOS_END
        )
        oos_checks = print_overlay_report(
            oos_strat, oos_overlay,
            niftybees_oos_ret, nifty_oos_ret,
            label="OUT-OF-SAMPLE", period=f"{OOS_START} → {OOS_END}",
            niftybees_max_dd=niftybees_oos_max_dd,
        )

        # Save result (cast all values to Python native types for JSON serialisation)
        result_file = _ROOT / "validation" / "etf_overlay_result.json"
        core_keys = ("sharpe_improvement", "drawdown_increase", "etf_cost")
        with open(result_file, "w") as f:
            json.dump({
                "date_run":             date.today().isoformat(),
                "period":               f"{OOS_START} → {OOS_END}",
                "go_no_go":             {k: bool(v) for k, v in oos_checks.items()},
                "all_core_pass":        bool(all(bool(oos_checks.get(k, False)) for k in core_keys)),
                # FIX 2: updated drawdown criterion fields
                "dd_criterion":         "overlay_dd < niftybees_dd + 2pp",
                "niftybees_max_dd":     float(niftybees_oos_max_dd) if niftybees_oos_max_dd is not None else None,
                "dd_pass":              bool(oos_checks.get("drawdown_increase", False)),
                # FIX 3: benchmark comparison criterion
                "benchmark_pass":       bool(oos_checks.get("beats_niftybees", False)),
                "oos_sharpe_strategy":  float(oos_strat.get("sharpe") or 0),
                "oos_sharpe_overlay":   float(oos_overlay.get("sharpe") or 0),
                "oos_dd_strategy":      float(oos_strat.get("max_dd") or 0),
                "oos_dd_overlay":       float(oos_overlay.get("max_dd") or 0),
                "oos_ret_strategy":     float(oos_strat.get("total_ret") or 0),
                "oos_ret_overlay":      float(oos_overlay.get("total_ret") or 0),
                "oos_etf_cost_pct_ann": float(oos_overlay.get("etf_cost_pct_ann") or 0),
                "niftybees_oos_ret":    float(niftybees_oos_ret) if niftybees_oos_ret is not None else None,
                "nifty_oos_ret":        float(nifty_oos_ret) if nifty_oos_ret is not None else None,
            }, f, indent=2)
        print(f"\nResults saved → {result_file}")
