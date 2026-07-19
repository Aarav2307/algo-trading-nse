"""
validation/etf_tier_grid_search.py — ETF tier configuration grid search.

Tests 6 NIFTYBEES tier configurations against the OOS window (2023-01-01 → today)
to find the optimal tier percentage table.

Runs per-stock backtests ONCE, then re-simulates for each tier config using
the cached trade logs (no re-fetching). The ETF overlay simulation is fast;
all cost is in the initial 349-stock OOS backtest.

Does NOT modify etf_overlay_backtest.py or etf_overlay_result.json.

Usage:
    python validation/etf_tier_grid_search.py           # full 349-stock run
    python validation/etf_tier_grid_search.py --quick   # first 50 stocks only
"""

import json
import sys
import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.portfolio_backtest import (
    run_single_stock,
    fetch_nifty_benchmark,
    compute_nifty_return,
    OOS_START, OOS_END,
    UNIVERSE_FILE,
)
from validation.etf_overlay_backtest import (
    fetch_niftybees,
    compute_strategy_only_metrics,
    _aggregate_equity_curves,
    INITIAL_CAPITAL,
    MAX_POSITIONS,
    ETF_BUY_COST_PCT,
    ETF_SELL_COST_PCT,
)


# =============================================================================
# TIER CONFIGURATIONS
# =============================================================================

CONFIGS = {
    "A_current":    {0: 1.0,  1: 0.6,  2: 0.6,  3: 0.3,  4: 0.0},
    "B_high":       {0: 1.0,  1: 0.7,  2: 0.7,  3: 0.4,  4: 0.0},
    "C_low":        {0: 1.0,  1: 0.5,  2: 0.5,  3: 0.25, 4: 0.0},
    "D_aggressive": {0: 1.0,  1: 0.8,  2: 0.8,  3: 0.5,  4: 0.0},
    "E_binary":     {0: 1.0,  1: 0.6,  2: 0.6,  3: 0.0,  4: 0.0},
    "F_moderate":   {0: 0.8,  1: 0.5,  2: 0.5,  3: 0.2,  4: 0.0},
}

# Go/no-go thresholds (matching etf_overlay_backtest.py)
SHARPE_MIN_DELTA   = 0.15    # overlay Sharpe must exceed strategy-only by ≥ 0.15
DD_BUFFER_PP       = 2.0     # overlay DD must be ≥ NIFTYBEES DD − 2pp (signed %)
MAX_COST_PCT_ANN   = 0.30    # ETF transaction cost < 0.30% of portfolio per year


# =============================================================================
# Parameterised simulation (etf_tiers injected, not global)
# =============================================================================

def _simulate_stock_scenario_cfg(
    ticker: str,
    trades: pd.DataFrame,
    niftybees_ret: pd.Series,
    calendar: pd.DatetimeIndex,
    initial_capital: float,
    etf_tiers: dict,
) -> dict:
    """
    Simulate one stock scenario with a given ETF tier config.

    Copied exactly from etf_overlay_backtest._simulate_stock_scenario() with
    the single change that etf_tiers is a parameter instead of a module global.
    apply_overlay is always True here (called only for overlay runs).
    """
    trade_intervals = []
    for _, t in trades.iterrows():
        entry_ts = pd.Timestamp(t["entry_date"]).normalize()
        exit_ts  = pd.Timestamp(t["exit_date"]).normalize()
        pos_frac = float(t["entry_price"]) * float(t["shares"]) / initial_capital
        pos_frac = min(pos_frac, 1.0)
        trade_intervals.append((entry_ts, exit_ts, pos_frac, float(t["net_pnl"])))

    exit_pnl: dict = {}
    for entry_ts, exit_ts, _, pnl in trade_intervals:
        exit_pnl.setdefault(exit_ts, 0.0)
        exit_pnl[exit_ts] += pnl

    capital           = initial_capital
    equity_values     = []
    etf_transactions  = 0
    etf_cost_total    = 0.0
    days_in_etf       = 0
    days_in_stocks    = 0
    prev_etf_frac     = 0.0
    current_etf_tier  = None   # None forces allocation on first bar (rebalance guard)

    for day in calendar:
        n_active = sum(1 for (e, x, _, _) in trade_intervals if e <= day < x)
        stock_weight = sum(pf for (e, x, pf, _) in trade_intervals if e <= day < x)
        stock_weight     = min(stock_weight, 1.0)
        remaining_weight = max(0.0, 1.0 - stock_weight)

        new_tier = min(n_active, MAX_POSITIONS)
        etf_frac = etf_tiers.get(new_tier, 0.0) * remaining_weight

        # Rebalance guard: only transact when the TIER changes (or first bar)
        if current_etf_tier is None or new_tier != current_etf_tier:
            delta = abs(etf_frac - prev_etf_frac) * capital
            if delta > 0:
                cost = delta * (
                    ETF_BUY_COST_PCT if etf_frac >= prev_etf_frac else ETF_SELL_COST_PCT
                )
                etf_cost_total   += cost
                capital          -= cost
                etf_transactions += 1
            current_etf_tier = new_tier

        prev_etf_frac = etf_frac

        etf_daily = float(niftybees_ret.get(day, 0.0))
        capital  *= (1.0 + etf_frac * etf_daily)

        if day in exit_pnl:
            capital += exit_pnl[day]

        if n_active > 0:
            days_in_stocks += 1
        if etf_frac > 0.001:
            days_in_etf += 1

        equity_values.append(capital)

    return {
        "ticker":           ticker,
        "equity_curve":     pd.Series(equity_values, index=calendar, name=ticker),
        "etf_transactions": etf_transactions,
        "etf_cost_total":   etf_cost_total,
        "days_in_stocks":   days_in_stocks,
        "days_in_etf":      days_in_etf,
    }


def simulate_overlay_cfg(
    stock_results: list,
    niftybees_df: pd.DataFrame,
    start: str,
    end: str,
    etf_tiers: dict,
    initial_capital: float = INITIAL_CAPITAL,
) -> dict:
    """
    Run overlay simulation for one tier config using cached trade logs.
    No data fetching — purely computational.
    """
    if niftybees_df is None or len(niftybees_df) < 10:
        return {"error": "NIFTYBEES data unavailable"}

    nb = niftybees_df.copy()
    nb.index = pd.to_datetime(nb.index).normalize()
    calendar = nb.index[
        (nb.index >= pd.Timestamp(start)) & (nb.index < pd.Timestamp(end))
    ]
    if len(calendar) < 10:
        return {"error": f"Insufficient calendar: {len(calendar)} days"}

    niftybees_ret = nb["close"].pct_change().fillna(0.0)

    scenario_curves  = []
    etf_cost_total   = 0.0
    etf_transactions = 0
    days_in_stocks   = 0
    days_in_etf      = 0

    for r in stock_results:
        if "error" in r or r.get("trades_df") is None:
            continue
        trades = r["trades_df"]
        empty_trades = pd.DataFrame() if (hasattr(trades, "empty") and trades.empty) else trades

        sim = _simulate_stock_scenario_cfg(
            r["ticker"], empty_trades, niftybees_ret,
            calendar, initial_capital, etf_tiers,
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


# =============================================================================
# Reporting
# =============================================================================

def _pass_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


def print_comparison_table(
    config_results: dict,
    strategy_sharpe: float,
    niftybees_max_dd: float,
    oos_start: str,
    oos_end: str,
) -> None:
    """Print the 6-config comparison table."""
    W = 93
    print()
    print("═" * W)
    print(f"  ETF Tier Grid Search Results  (OOS: {oos_start} → {oos_end})")
    print(f"  Strategy-only Sharpe: {strategy_sharpe:.3f}  "
          f"│  NIFTYBEES max DD: {niftybees_max_dd:.1f}%  "
          f"│  Sharpe threshold: +{SHARPE_MIN_DELTA}")
    print("═" * W)
    hdr = (
        f"  {'Config':<14} │ {'Sharpe':^7} │ {'Total Ret':^10} │ "
        f"{'CAGR':^7} │ {'Max DD':^8} │ {'Txns':^6} │ "
        f"{'Cost%/yr':^9} │ {'PASS?':^6}"
    )
    print(hdr)
    print("  " + "─" * 13 + "─┼─" + "─" * 7 + "─┼─" + "─" * 10 + "─┼─" +
          "─" * 7 + "─┼─" + "─" * 8 + "─┼─" + "─" * 6 + "─┼─" +
          "─" * 9 + "─┼─" + "─" * 6)

    for cfg_name, r in config_results.items():
        if "error" in r:
            print(f"  {cfg_name:<14} │ {'ERROR':^90}")
            continue
        icon = _pass_icon(r["all_pass"])
        print(
            f"  {cfg_name:<14} │ "
            f"{r['sharpe']:^7.3f} │ "
            f"{r['total_return']:^+10.1f}% │ "  # noqa (width includes %)
            f"{r['cagr']:^+7.1f}% │ "
            f"{r['max_dd']:^+8.1f}% │ "
            f"{r['transactions']:^6,} │ "
            f"{r['cost_pct_per_year']:^9.3f}% │ "
            f"  {icon}"
        )
    print("═" * W)


def print_winner(
    winner_name: str,
    winner: dict,
    current: dict,
    strategy_sharpe: float,
) -> None:
    print()
    print("═" * 60)
    print(f"  WINNER: {winner_name}  —  Sharpe {winner['sharpe']:.3f}, "
          f"DD {winner['max_dd']:.1f}%, cost {winner['cost_pct_per_year']:.3f}%/yr")
    print("═" * 60)
    tiers = winner["tiers"]
    print(f"\n  Recommended tier table:")
    print(f"    0 positions  → {tiers[0]*100:.0f}% ETF")
    print(f"    1-2 positions → {tiers[1]*100:.0f}% ETF  "
          f"(1: {tiers[1]*100:.0f}%, 2: {tiers[2]*100:.0f}%)")
    print(f"    3 positions  → {tiers[3]*100:.0f}% ETF")
    print(f"    4 positions  → {tiers[4]*100:.0f}% ETF")

    if winner_name != "A_current" and "error" not in current:
        sharpe_delta = winner["sharpe"] - current["sharpe"]
        dd_delta     = winner["max_dd"] - current["max_dd"]
        cost_delta   = winner["cost_pct_per_year"] - current["cost_pct_per_year"]
        print(f"\n  Comparison vs A_current:")
        print(f"    Sharpe delta : {sharpe_delta:+.3f}")
        print(f"    DD delta     : {dd_delta:+.1f}pp")
        print(f"    Cost delta   : {cost_delta:+.3f}%/yr")
    else:
        print(f"\n  A_current is the winner — no change to tier config recommended.")
    print("═" * 60)


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETF tier grid search — 6 configs × OOS window",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validation/etf_tier_grid_search.py           # full 349-stock run
  python validation/etf_tier_grid_search.py --quick   # first 50 stocks only
        """,
    )
    parser.add_argument("--quick", action="store_true",
                        help="Run on first 50 stocks only (for testing)")
    args = parser.parse_args()

    today_str = date.today().isoformat()

    # ── Load universe ─────────────────────────────────────────────────────────
    with open(UNIVERSE_FILE) as f:
        universe = json.load(f)

    if args.quick:
        universe = universe[:50]
        print(f"[quick mode] Using first {len(universe)} stocks")

    print(f"\nUniverse: {len(universe)} stocks")
    print(f"OOS window: {OOS_START} → {OOS_END}  (today = {today_str})")
    print(f"Configs to test: {list(CONFIGS.keys())}")

    # ── Fetch benchmarks ──────────────────────────────────────────────────────
    print("\nFetching NIFTY 50 benchmark (OOS)...")
    nifty_oos     = fetch_nifty_benchmark(OOS_START, OOS_END)
    nifty_oos_ret = compute_nifty_return(nifty_oos)
    if nifty_oos_ret:
        print(f"  NIFTY 50 OOS return: {nifty_oos_ret:+.1f}%")

    print("\nFetching NIFTYBEES data (OOS)...")
    niftybees_oos = fetch_niftybees(OOS_START, OOS_END)

    niftybees_oos_ret    = None
    niftybees_oos_max_dd = None
    if not niftybees_oos.empty:
        niftybees_oos_ret = (
            niftybees_oos["close"].iloc[-1] / niftybees_oos["close"].iloc[0] - 1
        ) * 100.0
        _p = niftybees_oos["close"]
        niftybees_oos_max_dd = float(((_p - _p.cummax()) / _p.cummax() * 100.0).min())
        print(f"  NIFTYBEES return:  {niftybees_oos_ret:+.1f}%")
        print(f"  NIFTYBEES max DD:  {niftybees_oos_max_dd:.1f}%")

    # ── Per-stock OOS backtests (ONCE — reused for all 6 configs) ─────────────
    print(f"\n{'='*60}")
    print(f"Running OOS per-stock backtests ({len(universe)} stocks)...")
    print(f"(This is the expensive step — runs once, all 6 configs reuse these results)")
    print("=" * 60)

    oos_results = []
    for i, ticker in enumerate(universe, 1):
        if i == 1 or i % 50 == 0 or i == len(universe):
            print(f"  [{i:>3}/{len(universe)}] {ticker}...", flush=True)
        r = run_single_stock(ticker, OOS_START, OOS_END, nifty_oos)
        if "error" not in r:
            oos_results.append(r)

    print(f"\n  {len(oos_results)}/{len(universe)} stocks completed OOS backtest")

    # ── Strategy-only baseline (no ETF, once) ─────────────────────────────────
    print("\nSimulating strategy-only baseline (no ETF)...")
    strat_only     = compute_strategy_only_metrics(
        oos_results, niftybees_oos, OOS_START, OOS_END
    )
    strategy_sharpe = float(strat_only.get("sharpe") or 0.0)
    print(f"  Strategy-only Sharpe:      {strategy_sharpe:.3f}")
    print(f"  Strategy-only total return: {strat_only.get('total_ret', 0):+.1f}%")
    print(f"  Strategy-only max DD:       {strat_only.get('max_dd', 0):.1f}%")

    # ── Grid search: 6 configs ────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Running ETF tier grid search (6 configurations)...")
    print("=" * 60)

    config_results: dict = {}
    for cfg_name, tiers in CONFIGS.items():
        tier_str = "  ".join(f"{k}→{v*100:.0f}%" for k, v in sorted(tiers.items()))
        print(f"\n  [{cfg_name}]  {tier_str}")

        result = simulate_overlay_cfg(
            oos_results, niftybees_oos, OOS_START, OOS_END, tiers,
        )

        if "error" in result:
            print(f"    ERROR: {result['error']}")
            config_results[cfg_name] = {"error": result["error"], "tiers": tiers}
            continue

        sharpe   = float(result.get("sharpe") or 0.0)
        total_ret = float(result.get("total_ret") or 0.0)
        cagr     = float(result.get("cagr") or 0.0)
        max_dd   = float(result.get("max_dd") or 0.0)
        txns     = int(result.get("etf_transactions") or 0)
        cost_pct = float(result.get("etf_cost_pct_ann") or 0.0)

        sharpe_pass = sharpe > (strategy_sharpe + SHARPE_MIN_DELTA)
        if niftybees_oos_max_dd is not None:
            # Both values are negative %; overlay must be ≥ niftybees_dd − 2pp
            dd_pass = max_dd >= (niftybees_oos_max_dd - DD_BUFFER_PP)
        else:
            dd_pass = False
        cost_pass = cost_pct < MAX_COST_PCT_ANN
        all_pass  = sharpe_pass and dd_pass and cost_pass

        config_results[cfg_name] = {
            "tiers":             tiers,
            "sharpe":            round(sharpe, 4),
            "total_return":      round(total_ret, 3),
            "cagr":              round(cagr, 3),
            "max_dd":            round(max_dd, 3),
            "transactions":      txns,
            "cost_pct_per_year": round(cost_pct, 4),
            "sharpe_pass":       sharpe_pass,
            "dd_pass":           dd_pass,
            "cost_pass":         cost_pass,
            "all_pass":          all_pass,
        }

        icon = _pass_icon(all_pass)
        print(
            f"    Sharpe {sharpe:.3f}  ret {total_ret:+.1f}%  "
            f"DD {max_dd:.1f}%  txns {txns:,}  cost {cost_pct:.3f}%/yr  {icon}"
        )
        print(
            f"    sharpe_pass={sharpe_pass}  "
            f"dd_pass={dd_pass}  "
            f"cost_pass={cost_pass}  →  all_pass={all_pass}"
        )

    # ── Comparison table ──────────────────────────────────────────────────────
    print_comparison_table(
        config_results,
        strategy_sharpe,
        niftybees_oos_max_dd or 0.0,
        OOS_START,
        today_str,
    )

    # ── Determine winner ──────────────────────────────────────────────────────
    passing = {
        k: v for k, v in config_results.items()
        if "error" not in v and v.get("all_pass")
    }
    valid = {k: v for k, v in config_results.items() if "error" not in v}

    if passing:
        winner_name = max(passing, key=lambda k: passing[k]["sharpe"])
        winner_pool = "all-pass configs"
    else:
        winner_name = max(valid, key=lambda k: valid[k]["sharpe"]) if valid else None
        winner_pool = "best Sharpe (no config passed all criteria)"

    if winner_name:
        winner = config_results[winner_name]
        current = config_results.get("A_current", {})
        print(f"\n  Winner selected from {winner_pool}.")
        print_winner(winner_name, winner, current, strategy_sharpe)
    else:
        print("\n  No valid results — cannot determine winner.")
        winner_name = None
        winner = {}

    # ── Save JSON ─────────────────────────────────────────────────────────────
    out_path = _ROOT / "validation" / "etf_tier_grid_result.json"

    save_configs = {}
    for cfg_name, r in config_results.items():
        if "error" in r:
            save_configs[cfg_name] = {"error": r["error"], "tiers": r.get("tiers", {})}
            continue
        save_configs[cfg_name] = {
            "tiers":             r["tiers"],
            "sharpe":            r["sharpe"],
            "total_return":      r["total_return"],
            "cagr":              r["cagr"],
            "max_dd":            r["max_dd"],
            "transactions":      r["transactions"],
            "cost_pct_per_year": r["cost_pct_per_year"],
            "sharpe_pass":       bool(r["sharpe_pass"]),
            "dd_pass":           bool(r["dd_pass"]),
            "cost_pass":         bool(r["cost_pass"]),
            "all_pass":          bool(r["all_pass"]),
        }

    output = {
        "run_date":              today_str,
        "oos_window":            f"{OOS_START} to {today_str}",
        "n_stocks":              len(oos_results),
        "strategy_only_sharpe":  round(strategy_sharpe, 4),
        "niftybees_max_dd":      round(niftybees_oos_max_dd, 3) if niftybees_oos_max_dd is not None else None,
        "niftybees_oos_ret":     round(niftybees_oos_ret, 3) if niftybees_oos_ret is not None else None,
        "configs":               save_configs,
        "winner":                winner_name,
        "winner_tiers":          winner.get("tiers"),
        "winner_sharpe":         winner.get("sharpe"),
        "winner_all_pass":       winner.get("all_pass"),
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved → {out_path}")
    print()


if __name__ == "__main__":
    main()
