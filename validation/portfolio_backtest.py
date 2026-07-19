"""
validation/portfolio_backtest.py — Portfolio-level walk-forward backtest.

Runs SMA 20/50 crossover strategy across ALL 349 qualifying NIFTY 500 stocks
simultaneously as a single portfolio, then compares against NIFTY 50 index.

This addresses the sample size problem identified in the quant audit:
- Per-stock walk-forward: 4 stocks = 24 data points (too small)
- Portfolio walk-forward: 349 stocks = potentially 500-2000 completed trades

Key difference from walk_forward.py:
- Stocks are NOT pre-selected by Hurst/ADX regime
- ALL qualifying stocks are included (unbiased universe)
- Results compared against NIFTY 50 index, not individual stock B&H

Usage:
    python validation/portfolio_backtest.py
    python validation/portfolio_backtest.py --is-only      # IS period only
    python validation/portfolio_backtest.py --oos-only     # OOS period only
    python validation/portfolio_backtest.py --quick        # first 50 stocks only
"""

import io
import json
import sys
import argparse
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.kite_fetcher import get_ohlcv
from engine.backtester import run as bt_run
from engine.cooldown import CooldownTracker
from engine.portfolio import Portfolio
from engine.position_sizer import PositionSizer
from engine.risk_manager import RiskManager
from strategies.sma_crossover import generate_signals


# =============================================================================
# Windows
# =============================================================================

IS_START  = "2018-01-01"
IS_END    = "2023-01-01"
OOS_START = "2023-01-01"
OOS_END   = date.today().strftime("%Y-%m-%d")


# =============================================================================
# Strategy parameters — identical to walk_forward.py PARAMS
# =============================================================================

PARAMS = {
    "sma_fast": 20,
    "sma_slow": 50,
    "initial_capital": 100_000,
    "risk_management": {
        "enabled":                True,
        "hard_stop_pct":         -0.20,
        "atr_period":             22,
        "atr_multiplier":          3.0,
        "max_bars_held":          60,
        "round_number_offset_pct": 0.01,
        "enable_layer_1":         True,
        "enable_layer_2":         True,
        "enable_layer_3":         True,
        "enable_layer_4":         True,
    },
    "cooldown": {
        "enabled":                True,
        "cooldown_bars":          15,
        "cooldown_after_reasons": ["HARD_STOP", "CHANDELIER", "TIME_STOP"],
        "reset_on_strategy_exit": True,
    },
    "position_sizing": {
        "enabled":            True,
        "method":             "fixed_fractional",
        "risk_per_trade_pct": 0.015,
        "max_position_pct":   0.20,
        "fallback_stop_pct":  0.20,
    },
    "nifty_regime_filter": True,
}

MIN_BARS     = 200
NIFTY_TICKER = "NIFTY 50.NS"
UNIVERSE_FILE = _ROOT / "validation" / "qualified_universe.json"


# =============================================================================
# Data helpers
# =============================================================================

def fetch_nifty_benchmark(start: str, end: str) -> pd.DataFrame:
    """
    Fetch NIFTY 50 daily OHLCV via kite_fetcher.
    Returns empty DataFrame on failure.
    """
    try:
        df = get_ohlcv(NIFTY_TICKER, start, end)
        if df is not None and len(df) >= 50:
            print(f"  NIFTY 50: {len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
            return df
    except Exception as exc:
        print(f"  WARN: NIFTY fetch failed ({exc}) — benchmark unavailable")
    return pd.DataFrame()


def compute_nifty_return(nifty_df: pd.DataFrame) -> Optional[float]:
    """
    Total return % of NIFTY 50 over the DataFrame's full date range.
    Returns None if data is insufficient.
    """
    if nifty_df is None or len(nifty_df) < 2:
        return None
    first = float(nifty_df["close"].iloc[0])
    last  = float(nifty_df["close"].iloc[-1])
    if first <= 0:
        return None
    return (last / first - 1) * 100.0


# =============================================================================
# Single-stock backtest
# =============================================================================

def run_single_stock(
    ticker: str,
    start: str,
    end: str,
    nifty_df: pd.DataFrame,
) -> dict:
    """
    Run the full strategy pipeline on one stock for [start, end).
    All backtester output is suppressed to keep terminal clean.

    Returns:
        On success: {ticker, total_ret, n_trades, win_rate, avg_win, avg_loss,
                     payoff, expectancy, max_dd, n_bars, trades_df}
        On failure: {ticker, error}
    """
    try:
        df = get_ohlcv(ticker, start, end)
    except (FileNotFoundError, ConnectionError) as exc:
        print(f"\nFATAL: {exc}")
        sys.exit(1)
    except ValueError as exc:
        return {"ticker": ticker, "error": f"No data — {exc}"}
    except Exception as exc:
        return {"ticker": ticker, "error": f"Fetch error — {exc}"}

    n_bars = len(df)
    if n_bars < MIN_BARS:
        return {"ticker": ticker, "error": f"INSUFFICIENT DATA — {n_bars} bars (need ≥ {MIN_BARS})"}

    try:
        signals      = generate_signals(df, PARAMS["sma_fast"], PARAMS["sma_slow"])
        risk_manager = RiskManager(PARAMS["risk_management"])
        cooldown     = CooldownTracker(PARAMS["cooldown"])
        sizer        = PositionSizer(PARAMS["position_sizing"])
        portfolio    = Portfolio(PARAMS["initial_capital"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            equity_curve = bt_run(
                df, signals, portfolio,
                risk_manager=risk_manager,
                cooldown_tracker=cooldown,
                position_sizer=sizer,
                use_next_day_fills=True,
                nifty_regime_df=nifty_df if nifty_df is not None and len(nifty_df) > 0 else None,
                nifty_regime_filter=PARAMS.get("nifty_regime_filter", False),
            )

        initial   = portfolio.initial_capital
        final     = float(equity_curve["portfolio_value"].iloc[-1])
        total_ret = (final / initial - 1) * 100.0
        max_dd    = float(equity_curve["drawdown"].min())

        trades = portfolio.get_trade_log()

        if trades.empty:
            return {
                "ticker": ticker, "total_ret": total_ret, "n_bars": n_bars,
                "n_trades": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "payoff": 0.0, "expectancy": 0.0, "max_dd": max_dd,
                "trades_df": trades,
            }

        n       = len(trades)
        n_wins  = int((trades["net_pnl"] > 0).sum())
        wr      = n_wins / n if n > 0 else 0.0
        avg_win = float(trades.loc[trades["net_pnl"] > 0, "net_pnl"].mean()) if n_wins > 0 else 0.0
        avg_los = float(trades.loc[trades["net_pnl"] <= 0, "net_pnl"].mean()) if n - n_wins > 0 else 0.0
        payoff  = abs(avg_win / avg_los) if avg_los != 0 else float("inf")
        exp     = wr * avg_win + (1 - wr) * avg_los

        return {
            "ticker":     ticker,
            "total_ret":  total_ret,
            "n_bars":     n_bars,
            "n_trades":   n,
            "win_rate":   wr * 100.0,
            "avg_win":    avg_win,
            "avg_loss":   avg_los,
            "payoff":     payoff,
            "expectancy": exp,
            "max_dd":     max_dd,
            "trades_df":  trades,
        }

    except Exception as exc:
        return {"ticker": ticker, "error": f"Backtest error — {exc}"}


# =============================================================================
# Portfolio aggregation
# =============================================================================

def run_portfolio_backtest(
    universe: list,
    start: str,
    end: str,
    nifty_df: pd.DataFrame,
    label: str = "Portfolio",
) -> dict:
    """
    Run the strategy on all stocks independently, then aggregate into
    portfolio-level metrics across all stocks and all trades.

    Per-stock results are equal-weighted — no capital allocation across stocks.
    This is an unbiased test of signal quality, not a live capital simulation.
    """
    stock_results = []
    all_trades    = []
    n_errors      = 0
    n_stocks      = 0

    for i, ticker in enumerate(universe, 1):
        if i % 25 == 0 or i == 1:
            print(f"  [{i:>3}/{len(universe)}] {ticker}…", flush=True)

        r = run_single_stock(ticker, start, end, nifty_df)

        if "error" in r and r["error"]:
            n_errors += 1
            continue

        n_stocks += 1
        # Attach ticker to each trade row for later analysis
        trades = r.pop("trades_df")
        if not trades.empty:
            trades = trades.copy()
            trades["ticker"] = ticker
            all_trades.append(trades)
        stock_results.append(r)

    print(f"  Done: {n_stocks} stocks with data, {n_errors} errors/skipped")

    # ── Aggregate across all trades ──────────────────────────────────────────
    if all_trades:
        combined = pd.concat(all_trades, ignore_index=True)
    else:
        combined = pd.DataFrame()

    total_trades = len(combined)

    if total_trades > 0:
        n_wins  = int((combined["net_pnl"] > 0).sum())
        wr      = n_wins / total_trades
        avg_win = float(combined.loc[combined["net_pnl"] > 0, "net_pnl"].mean()) if n_wins > 0 else 0.0
        avg_los = float(combined.loc[combined["net_pnl"] <= 0, "net_pnl"].mean()) if total_trades - n_wins > 0 else 0.0
        payoff  = abs(avg_win / avg_los) if avg_los != 0 else float("inf")
        exp     = wr * avg_win + (1 - wr) * avg_los

        # Return % per trade (using entry_price × shares as position size)
        if "return_pct" in combined.columns:
            avg_ret_pct_win = float(combined.loc[combined["net_pnl"] > 0, "return_pct"].mean()) if n_wins > 0 else 0.0
            avg_ret_pct_los = float(combined.loc[combined["net_pnl"] <= 0, "return_pct"].mean()) if total_trades - n_wins > 0 else 0.0
        else:
            avg_ret_pct_win = avg_ret_pct_los = 0.0
    else:
        wr = avg_win = avg_los = payoff = exp = 0.0
        n_wins = 0
        avg_ret_pct_win = avg_ret_pct_los = 0.0

    # ── Per-stock return statistics ──────────────────────────────────────────
    returns = [r["total_ret"] for r in stock_results]
    if returns:
        equal_wtd_ret = float(np.mean(returns))
        median_ret    = float(np.median(returns))
        pct_positive  = float(sum(1 for r in returns if r > 0) / len(returns) * 100)
        best  = max(stock_results, key=lambda x: x["total_ret"])
        worst = min(stock_results, key=lambda x: x["total_ret"])
    else:
        equal_wtd_ret = median_ret = pct_positive = 0.0
        best = worst = None

    return {
        "label":           label,
        "n_stocks":        n_stocks,
        "n_errors":        n_errors,
        "total_trades":    total_trades,
        "n_wins":          n_wins,
        "win_rate":        wr * 100.0,
        "avg_win":         avg_win,
        "avg_loss":        avg_los,
        "avg_win_pct":     avg_ret_pct_win,
        "avg_loss_pct":    avg_ret_pct_los,
        "payoff":          payoff,
        "expectancy":      exp,
        "equal_wtd_ret":   equal_wtd_ret,
        "median_ret":      median_ret,
        "pct_positive":    pct_positive,
        "best_ticker":     best["ticker"] if best else "N/A",
        "best_ret":        best["total_ret"] if best else 0.0,
        "worst_ticker":    worst["ticker"] if worst else "N/A",
        "worst_ret":       worst["total_ret"] if worst else 0.0,
        "stock_results":   stock_results,
        "all_trades":      combined,
    }


# =============================================================================
# Report
# =============================================================================

def print_portfolio_report(
    is_r:         Optional[dict],
    oos_r:        Optional[dict],
    nifty_is_ret: Optional[float],
    nifty_oos_ret: Optional[float],
) -> None:
    """Print formatted side-by-side IS vs OOS comparison."""

    W = 74

    def hr(char="="):
        print(char * W)

    def row(label, is_val, oos_val, width=26):
        is_s  = is_val  if is_val  is not None else "—"
        oos_s = oos_val if oos_val is not None else "—"
        print(f"  {label:<{width}} {str(is_s):^22}  {str(oos_s):^22}")

    def fmt_pct(v, decimals=1):
        if v is None:
            return "—"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.{decimals}f}%"

    def fmt_inr(v):
        if v is None or v == 0.0:
            return "—"
        sign = "+" if v >= 0 else ""
        return f"₹{sign}{v:,.0f}"

    def fmt_ratio(v):
        if v is None:
            return "—"
        if v == float("inf"):
            return "∞"
        return f"{v:.2f}x"

    print()
    hr()
    print("  PORTFOLIO-LEVEL WALK-FORWARD BACKTEST")
    print("  SMA 20/50 Crossover — 349 qualifying NIFTY 500 stocks (unbiased universe)")
    print(f"  Comparison: strategy equal-weighted avg return vs NIFTY 50 index")
    hr()
    print(f"  {'':26} {'IN-SAMPLE (2018–2023)':^22}  {'OUT-OF-SAMPLE (2023–today)':^22}")
    print("  " + "-" * (W - 2))

    is_s  = is_r["n_stocks"]  if is_r  else None
    oos_s = oos_r["n_stocks"] if oos_r else None
    is_t  = f"{is_r['total_trades']:,}"   if is_r  else None
    oos_t = f"{oos_r['total_trades']:,}"  if oos_r else None

    row("Stocks tested",        "349",  "349")
    row("Stocks with data",     is_s,   oos_s)
    row("Stocks errored/skipped",
        is_r["n_errors"]  if is_r  else None,
        oos_r["n_errors"] if oos_r else None)
    row("Total completed trades", is_t, oos_t)
    print()

    row("Win rate",
        fmt_pct(is_r["win_rate"]  if is_r  else None),
        fmt_pct(oos_r["win_rate"] if oos_r else None))
    row("Avg winning trade (₹)",
        fmt_inr(is_r["avg_win"]  if is_r  else None),
        fmt_inr(oos_r["avg_win"] if oos_r else None))
    row("Avg losing trade (₹)",
        fmt_inr(is_r["avg_loss"]  if is_r  else None),
        fmt_inr(oos_r["avg_loss"] if oos_r else None))
    row("Avg win return %",
        fmt_pct(is_r["avg_win_pct"]  if is_r  else None),
        fmt_pct(oos_r["avg_win_pct"] if oos_r else None))
    row("Avg loss return %",
        fmt_pct(is_r["avg_loss_pct"]  if is_r  else None),
        fmt_pct(oos_r["avg_loss_pct"] if oos_r else None))
    row("Payoff ratio",
        fmt_ratio(is_r["payoff"]  if is_r  else None),
        fmt_ratio(oos_r["payoff"] if oos_r else None))
    row("Expectancy (₹/trade)",
        fmt_inr(is_r["expectancy"]  if is_r  else None),
        fmt_inr(oos_r["expectancy"] if oos_r else None))
    print()

    row("Equal-wtd avg return",
        fmt_pct(is_r["equal_wtd_ret"]  if is_r  else None),
        fmt_pct(oos_r["equal_wtd_ret"] if oos_r else None))
    row("Median stock return",
        fmt_pct(is_r["median_ret"]  if is_r  else None),
        fmt_pct(oos_r["median_ret"] if oos_r else None))
    row("% stocks with +ve return",
        fmt_pct(is_r["pct_positive"]  if is_r  else None),
        fmt_pct(oos_r["pct_positive"] if oos_r else None))
    print()

    is_best  = f"{is_r['best_ticker']}  {fmt_pct(is_r['best_ret'])}"   if is_r  else None
    oos_best = f"{oos_r['best_ticker']} {fmt_pct(oos_r['best_ret'])}"  if oos_r else None
    is_wrst  = f"{is_r['worst_ticker']} {fmt_pct(is_r['worst_ret'])}"  if is_r  else None
    oos_wrst = f"{oos_r['worst_ticker']} {fmt_pct(oos_r['worst_ret'])}" if oos_r else None
    row("Best stock",  is_best,  oos_best)
    row("Worst stock", is_wrst,  oos_wrst)
    print()

    hr("-")
    print("  BENCHMARK COMPARISON")
    hr("-")
    row("NIFTY 50 return",
        fmt_pct(nifty_is_ret),
        fmt_pct(nifty_oos_ret))
    row("Strategy equal-wtd return",
        fmt_pct(is_r["equal_wtd_ret"]  if is_r  else None),
        fmt_pct(oos_r["equal_wtd_ret"] if oos_r else None))

    is_alpha  = (is_r["equal_wtd_ret"]  - nifty_is_ret)  if (is_r  and nifty_is_ret  is not None) else None
    oos_alpha = (oos_r["equal_wtd_ret"] - nifty_oos_ret) if (oos_r and nifty_oos_ret is not None) else None
    row("Alpha vs NIFTY 50",
        fmt_pct(is_alpha),
        fmt_pct(oos_alpha))
    print()

    # ── Verdict ────────────────────────────────────────────────────────────────
    if oos_r is None:
        print("  OOS not run — no verdict.")
        hr()
        return

    hr("-")
    print("  VERDICT (OOS metrics only)")
    hr("-")

    checks = []

    def check(label, passed, detail=""):
        icon = "✅ PASS" if passed else "❌ FAIL"
        checks.append(passed)
        suffix = f"  ({detail})" if detail else ""
        print(f"  {icon}  {label}{suffix}")

    # Degrade check: OOS win rate should not fall more than 5pp below IS
    if is_r:
        wr_drop = oos_r["win_rate"] - is_r["win_rate"]
        check(
            "OOS win rate not severely degraded vs IS (< -5pp drop)",
            wr_drop >= -5.0,
            f"Δ = {wr_drop:+.1f}pp"
        )
    else:
        check("OOS win rate > 45%", oos_r["win_rate"] > 45.0,
              f"{oos_r['win_rate']:.1f}%")

    check("OOS payoff ratio > 1.5",
          oos_r["payoff"] > 1.5,
          fmt_ratio(oos_r["payoff"]))

    check("OOS expectancy > 0 (positive edge)",
          oos_r["expectancy"] > 0,
          fmt_inr(oos_r["expectancy"]))

    check("OOS breadth > 50% stocks profitable",
          oos_r["pct_positive"] > 50.0,
          f"{oos_r['pct_positive']:.1f}% profitable")

    check("OOS equal-wtd return > NIFTY 50 (alpha)",
          oos_alpha is not None and oos_alpha > 0,
          f"Alpha = {fmt_pct(oos_alpha)}")

    n_pass = sum(checks)
    print()
    print(f"  Overall: {n_pass}/{len(checks)} OOS metrics pass")

    if n_pass == len(checks):
        print("  ✅ STRATEGY VALIDATED — positive edge on unbiased universe, beats NIFTY")
    elif n_pass >= 3:
        print("  ⚠️  PARTIAL — some edge present but strategy does not consistently beat NIFTY")
    else:
        print("  ❌ NOT VALIDATED — insufficient edge on unbiased universe")

    hr()

    # ── Top/bottom stock distribution ─────────────────────────────────────────
    if oos_r and oos_r["stock_results"]:
        by_ret = sorted(oos_r["stock_results"], key=lambda x: x["total_ret"], reverse=True)
        print("\n  OOS TOP 10 STOCKS BY TOTAL RETURN:")
        print(f"  {'Ticker':<20} {'Return':>10} {'Trades':>8} {'Win%':>8} {'Payoff':>8}")
        print("  " + "-" * 58)
        for r in by_ret[:10]:
            pay_s = f"{r['payoff']:.2f}" if r['payoff'] != float('inf') else "∞"
            print(f"  {r['ticker']:<20} {r['total_ret']:>+9.1f}% {r['n_trades']:>8} {r['win_rate']:>7.1f}% {pay_s:>8}")

        print("\n  OOS BOTTOM 10 STOCKS BY TOTAL RETURN:")
        print(f"  {'Ticker':<20} {'Return':>10} {'Trades':>8} {'Win%':>8} {'Payoff':>8}")
        print("  " + "-" * 58)
        for r in by_ret[-10:][::-1]:
            pay_s = f"{r['payoff']:.2f}" if r['payoff'] != float('inf') else "∞"
            print(f"  {r['ticker']:<20} {r['total_ret']:>+9.1f}% {r['n_trades']:>8} {r['win_rate']:>7.1f}% {pay_s:>8}")

    print()


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Portfolio-level walk-forward backtest across NIFTY 500 universe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validation/portfolio_backtest.py              # full IS + OOS
  python validation/portfolio_backtest.py --quick      # first 50 stocks only
  python validation/portfolio_backtest.py --is-only    # IS period only
  python validation/portfolio_backtest.py --oos-only   # OOS period only
        """,
    )
    parser.add_argument("--quick",   action="store_true", help="Run first 50 stocks only")
    parser.add_argument("--is-only", action="store_true", dest="is_only",  help="IS period only")
    parser.add_argument("--oos-only",action="store_true", dest="oos_only", help="OOS period only")
    args = parser.parse_args()

    # ── Load universe ─────────────────────────────────────────────────────────
    with open(UNIVERSE_FILE) as f:
        universe = json.load(f)

    if args.quick:
        universe = universe[:50]
        print(f"[quick mode] Running first {len(universe)} stocks only")

    print(f"\nUniverse: {len(universe)} stocks")
    print(f"IS:  {IS_START} → {IS_END}")
    print(f"OOS: {OOS_START} → {OOS_END}")

    # ── Fetch NIFTY benchmark ─────────────────────────────────────────────────
    print("\nFetching NIFTY 50 benchmark data...")
    nifty_is  = fetch_nifty_benchmark(IS_START,  IS_END)
    nifty_oos = fetch_nifty_benchmark(OOS_START, OOS_END)

    nifty_is_ret  = compute_nifty_return(nifty_is)
    nifty_oos_ret = compute_nifty_return(nifty_oos)

    if nifty_is_ret is not None:
        print(f"  NIFTY 50 IS  return: {nifty_is_ret:+.1f}%")
    if nifty_oos_ret is not None:
        print(f"  NIFTY 50 OOS return: {nifty_oos_ret:+.1f}%")

    is_results  = None
    oos_results = None

    # ── IS run ────────────────────────────────────────────────────────────────
    if not args.oos_only:
        print(f"\nRunning IS backtest ({IS_START} → {IS_END})...")
        is_results = run_portfolio_backtest(
            universe, IS_START, IS_END,
            nifty_df=nifty_is,
            label="In-Sample",
        )
        print(
            f"IS complete: {is_results['total_trades']:,} trades "
            f"across {is_results['n_stocks']} stocks"
        )

    # ── OOS run ───────────────────────────────────────────────────────────────
    if not args.is_only:
        print(f"\nRunning OOS backtest ({OOS_START} → {OOS_END})...")
        oos_results = run_portfolio_backtest(
            universe, OOS_START, OOS_END,
            nifty_df=nifty_oos,
            label="Out-of-Sample",
        )
        print(
            f"OOS complete: {oos_results['total_trades']:,} trades "
            f"across {oos_results['n_stocks']} stocks"
        )

    # ── Report ────────────────────────────────────────────────────────────────
    print_portfolio_report(is_results, oos_results, nifty_is_ret, nifty_oos_ret)
