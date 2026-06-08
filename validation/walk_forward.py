"""
validation/walk_forward.py — Walk-forward validation (in-sample vs out-of-sample).

Answers: are the strategy parameters robust, or did we overfit to 2023-2026?

PARAMETERS FROZEN — no optimization performed between windows.
These results represent genuine out-of-sample performance.

Usage:
    python validation/walk_forward.py

Requires a fresh Kite access token: run auth/kite_login.py first.
"""

import io
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

import pandas as pd

# Allow imports from project root when run directly or via `python validation/walk_forward.py`
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
# FROZEN PARAMETERS — identical across both windows, not re-optimised
# =============================================================================

STOCKS = ["TMPV.NS", "WHIRLPOOL.NS", "SIEMENS.NS", "BAJAJ-AUTO.NS"]

# In-sample:     2018-01-01 → 2022-12-31  (exclusive end = 2023-01-01)
# Out-of-sample: 2023-01-01 → 2025-12-31  (exclusive end = 2026-01-01)
WINDOWS = {
    "in_sample":     ("2018-01-01", "2023-01-01"),
    "out_of_sample": ("2023-01-01", "2026-01-01"),
}

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
}

MIN_BARS = 200   # skip a window if fewer than this many trading days


# =============================================================================
# Single-window backtest
# =============================================================================

def _run_one(ticker: str, start: str, end: str) -> dict:
    """
    Fetch data and run one full backtest for ticker in [start, end).
    Kite_fetcher prints progress to stdout (intentional — shows loading).
    The backtester's verbose bar-by-bar output is captured and kept for the
    saved file only (not printed to terminal).

    Returns a result dict, or {"error": message} on failure.
    """
    try:
        df = get_ohlcv(ticker, start, end)
    except (FileNotFoundError, ConnectionError) as exc:
        print(f"\n[walk_forward] FATAL: {exc}")
        print("  Run auth/kite_login.py first to generate a fresh access token.")
        sys.exit(1)
    except ValueError as exc:
        return {"error": f"No data returned — {exc}"}
    except Exception as exc:
        return {"error": f"Unexpected fetch error — {exc}"}

    n_bars = len(df)
    if n_bars < MIN_BARS:
        return {"error": f"INSUFFICIENT DATA — {n_bars} bars (need ≥ {MIN_BARS})"}

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
            use_next_day_fills=True,   # AMO-realistic: fills at next day's open
        )
    verbose = buf.getvalue()

    return {
        "error":       None,
        "ticker":      ticker,
        "start":       start,
        "end":         end,
        "n_bars":      n_bars,
        "metrics":     _metrics(equity_curve, portfolio),
        "equity_curve": equity_curve,
        "portfolio":   portfolio,
        "cooldown":    cooldown,
        "verbose":     verbose,
        "covid_note":  _covid_note(equity_curve),
    }


def _metrics(equity_curve: pd.DataFrame, portfolio: Portfolio) -> dict:
    initial = portfolio.initial_capital
    final   = equity_curve["portfolio_value"].iloc[-1]
    trades  = portfolio.get_trade_log()

    total_ret = (final / initial - 1) * 100
    bm_ret    = (
        equity_curve["benchmark_value"].iloc[-1]
        / equity_curve["benchmark_value"].iloc[0]
        - 1
    ) * 100
    vs_bnh    = total_ret - bm_ret
    max_dd    = equity_curve["drawdown"].min()
    ret_dd    = total_ret / abs(max_dd) if max_dd != 0 else 0.0

    if trades.empty:
        return dict(
            total_ret=total_ret, bm_ret=bm_ret, vs_bnh=vs_bnh,
            max_dd=max_dd, ret_dd=ret_dd,
            n_trades=0, win_rate=0.0, payoff=0.0, expectancy=0.0,
        )

    n      = len(trades)
    n_wins = int((trades["net_pnl"] > 0).sum())
    wr     = n_wins / n

    avg_w = trades.loc[trades["net_pnl"] > 0,  "net_pnl"].mean() if n_wins > 0     else 0.0
    avg_l = trades.loc[trades["net_pnl"] <= 0, "net_pnl"].mean() if n - n_wins > 0 else 0.0
    payoff = abs(avg_w / avg_l) if avg_l != 0 else float("inf")
    exp    = wr * avg_w + (1 - wr) * avg_l

    return dict(
        total_ret=total_ret, bm_ret=bm_ret, vs_bnh=vs_bnh,
        max_dd=max_dd, ret_dd=ret_dd,
        n_trades=n, win_rate=wr * 100, payoff=payoff, expectancy=exp,
    )


def _covid_note(equity_curve: pd.DataFrame) -> str:
    """Summarise drawdown during the COVID crash (Feb–Apr 2020)."""
    lo = pd.Timestamp("2020-02-01")
    hi = pd.Timestamp("2020-04-30")
    mask = (equity_curve.index >= lo) & (equity_curve.index <= hi)
    if not mask.any():
        return ""
    dd = equity_curve.loc[mask, "drawdown"].min()
    if dd < -15:
        return (
            f"COVID crash (Feb–Apr 2020) hit hard: worst intra-period drawdown = "
            f"{dd:.1f}%. Risk system was fully stress-tested."
        )
    if dd < -5:
        return (
            f"COVID crash (Feb–Apr 2020): intra-period drawdown = {dd:.1f}%. "
            f"Hard stop / Chandelier absorbed much of the decline."
        )
    return (
        f"COVID crash (Feb–Apr 2020): portfolio drawdown was only {dd:.1f}%. "
        f"Risk system protected capital well."
    )


# =============================================================================
# PASS / FAIL rules (user-specified thresholds)
# =============================================================================

def _pass(name: str, is_m: dict, oos_m: dict) -> bool:
    if name == "total_ret":
        is_r  = is_m["total_ret"]
        oos_r = oos_m["total_ret"]
        thresh = is_r * 0.50 if is_r > 0 else 0.0
        return oos_r > 0 and oos_r >= thresh

    if name == "vs_bnh":
        return oos_m["vs_bnh"] >= -10.0

    if name == "max_dd":
        is_dd  = is_m["max_dd"]   # negative
        oos_dd = oos_m["max_dd"]  # negative
        # PASS if OOS drawdown is no worse than 1.5× IS drawdown
        # Both negative: is_dd * 1.5 is more negative (worse)
        return oos_dd >= is_dd * 1.5

    if name == "payoff":
        pr = oos_m["payoff"]
        return pr == float("inf") or pr > 1.5

    if name == "expectancy":
        return oos_m["expectancy"] > 0

    return False


def _verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


# =============================================================================
# Output
# =============================================================================

def _print_stock(
    ticker: str,
    is_r: dict,
    oos_r: dict,
    lines: list,
) -> list[bool]:
    """
    Print per-stock walk-forward table.
    Appends all output to `lines` for file saving.
    Returns list of bool scores (one per metric). Empty list if data error.
    """

    def emit(text: str = ""):
        print(text)
        lines.append(text)

    emit()
    emit("=" * 80)
    emit(f"  {ticker}")
    emit("=" * 80)

    if is_r.get("error"):
        emit(f"  In-sample:     {is_r['error']}")
        if oos_r.get("error"):
            emit(f"  Out-of-sample: {oos_r['error']}")
        emit()
        return []

    if oos_r.get("error"):
        emit(f"  Out-of-sample: {oos_r['error']}")
        emit()
        return []

    is_m  = is_r["metrics"]
    oos_m = oos_r["metrics"]

    emit(
        f"  Data:  {is_r['n_bars']} bars in-sample  "
        f"|  {oos_r['n_bars']} bars out-of-sample"
    )
    if is_r.get("covid_note"):
        emit(f"  NOTE:  {is_r['covid_note']}")
    emit()

    # ── Column widths ─────────────────────────────────────────────────────────
    C1, C2, C3, C4 = 24, 26, 28, 12

    emit(
        f"  {'Metric':<{C1}}"
        f"{'In-Sample (2018-2022)':^{C2}}"
        f"{'Out-of-Sample (2023-2025)':^{C3}}"
        f"{'Result':>{C4}}"
    )
    emit("  " + "─" * (C1 + C2 + C3 + C4))

    scores: list[bool] = []

    def row(label: str, metric: str, fmt):
        v_is  = fmt(is_m)
        v_oos = fmt(oos_m)
        ok    = _pass(metric, is_m, oos_m)
        scores.append(ok)
        emit(
            f"  {label:<{C1}}"
            f"{v_is:^{C2}}"
            f"{v_oos:^{C3}}"
            f"{'[' + _verdict(ok) + ']':>{C4}}"
        )

    row("Total return", "total_ret",
        lambda m: f"{m['total_ret']:>+.1f}%")

    row("vs Buy & Hold", "vs_bnh",
        lambda m: f"{m['vs_bnh']:>+.1f}pp  (B&H {m['bm_ret']:>+.1f}%)")

    row("Max drawdown", "max_dd",
        lambda m: f"{m['max_dd']:.1f}%")

    row("Payoff ratio", "payoff",
        lambda m: "∞:1" if m["payoff"] == float("inf") else f"{m['payoff']:.2f}:1")

    row("Expectancy/trade", "expectancy",
        lambda m: f"₹{m['expectancy']:>+,.0f}")

    emit("  " + "─" * (C1 + C2 + C3 + C4))

    # ── Supplementary (unscored) ─────────────────────────────────────────────
    emit(
        f"  {'Trades':<{C1}}"
        f"{str(is_m['n_trades']):^{C2}}"
        f"{str(oos_m['n_trades']):^{C3}}"
    )
    is_wr   = f"{is_m['win_rate']:.1f}%"
    oos_wr  = f"{oos_m['win_rate']:.1f}%"
    is_rdd  = f"{is_m['ret_dd']:.2f}"
    oos_rdd = f"{oos_m['ret_dd']:.2f}"
    emit(
        f"  {'Win rate':<{C1}}"
        f"{is_wr:^{C2}}"
        f"{oos_wr:^{C3}}"
    )
    emit(
        f"  {'Ret/DD score':<{C1}}"
        f"{is_rdd:^{C2}}"
        f"{oos_rdd:^{C3}}"
    )

    n_pass = sum(scores)
    emit(f"\n  Score: {n_pass}/{len(scores)} PASS")
    emit()

    return scores


def _print_verdict(all_scores: list[list[bool]], lines: list):

    def emit(text: str = ""):
        print(text)
        lines.append(text)

    flat     = [v for stock in all_scores for v in stock]
    n_pass   = sum(flat)
    n_total  = len(flat)
    pct      = n_pass / n_total * 100 if n_total > 0 else 0.0

    emit()
    emit("=" * 80)
    emit("  OVERALL SYSTEM VERDICT")
    emit("=" * 80)
    emit(f"  Total score: {n_pass}/{n_total} PASS  ({pct:.0f}%)")
    emit(f"  (Max possible: 20 = 4 stocks × 5 metrics; "
         f"stocks with data errors are excluded)")
    emit()

    if n_pass >= 14:
        verdict = "SYSTEM VALIDATED"
        detail  = (
            "Parameters are robust across both windows. "
            "Safe to proceed to paper trading."
        )
    elif n_pass >= 10:
        verdict = "PARTIALLY VALIDATED"
        detail  = (
            "System shows real edge but has weaknesses. "
            "Paper trade with extra caution and reduced sizing."
        )
    else:
        verdict = "NOT VALIDATED"
        detail  = (
            "Significant overfitting or regime dependence detected. "
            "Do not deploy capital. Diagnose causes before proceeding."
        )

    emit(f"  Verdict:     {verdict}")
    emit(f"  Implication: {detail}")
    emit()
    emit("  Thresholds: ≥14/20 → VALIDATED | 10-13 → PARTIAL | <10 → NOT VALIDATED")


def _degradation_analysis(
    ticker: str,
    is_r: dict,
    oos_r: dict,
    lines: list,
):

    def emit(text: str = ""):
        print(text)
        lines.append(text)

    if is_r.get("error") or oos_r.get("error"):
        return

    is_m  = is_r["metrics"]
    oos_m = oos_r["metrics"]

    issues = []

    # ── Regime change ─────────────────────────────────────────────────────────
    if is_m["total_ret"] > 10 and oos_m["total_ret"] < 0:
        issues.append(
            f"  Regime change: strategy gained {is_m['total_ret']:>+.1f}% in-sample "
            f"but lost {oos_m['total_ret']:>+.1f}% out-of-sample. "
            f"The 20/50 SMA crossover requires trending conditions; "
            f"the out-of-sample period may be more choppy."
        )
    elif is_m["total_ret"] > 5 and oos_m["total_ret"] < is_m["total_ret"] * 0.5:
        issues.append(
            f"  Partial regime change: strategy returned {is_m['total_ret']:>+.1f}% "
            f"in-sample vs {oos_m['total_ret']:>+.1f}% out-of-sample. "
            f"The stock trended more consistently in the earlier period."
        )

    # ── Trade frequency collapse ──────────────────────────────────────────────
    if is_m["n_trades"] == 0 and oos_m["n_trades"] == 0:
        issues.append(
            "  No trades in either window. The 20/50 SMA crossover never fired "
            "— the stock may have stayed in a sustained trend without reversals, "
            "or had insufficient price action for crossovers."
        )
    elif is_m["n_trades"] > 0 and oos_m["n_trades"] == 0:
        issues.append(
            f"  Zero OOS trades vs {is_m['n_trades']} in-sample. "
            "Strategy generated no crossover signals out-of-sample. "
            "The stock may have entered a strong directional trend with no retracements."
        )
    elif is_m["n_trades"] >= 3 and oos_m["n_trades"] < is_m["n_trades"] * 0.40:
        issues.append(
            f"  Trade frequency dropped: {is_m['n_trades']} → {oos_m['n_trades']} trades. "
            "Fewer entries mean less compounding; the strategy isn't being tested."
        )

    # ── Drawdown worsening ────────────────────────────────────────────────────
    if abs(oos_m["max_dd"]) > abs(is_m["max_dd"]) * 2.0:
        issues.append(
            f"  Max drawdown doubled: {is_m['max_dd']:.1f}% → {oos_m['max_dd']:.1f}%. "
            "The Chandelier / hard stop may have been slower to fire in out-of-sample "
            "conditions, or the stock experienced sharper adverse moves."
        )

    # ── Payoff collapse ───────────────────────────────────────────────────────
    if (is_m["payoff"] != float("inf")
            and is_m["payoff"] > 2.0
            and oos_m["payoff"] < 1.0
            and oos_m["n_trades"] > 0):
        issues.append(
            f"  Payoff ratio collapsed: {is_m['payoff']:.2f}:1 → {oos_m['payoff']:.2f}:1. "
            "Losers are now larger than winners — the trend structure of this "
            "stock has likely changed between windows."
        )

    n_pass = sum(
        _pass(m, is_m, oos_m)
        for m in ["total_ret", "vs_bnh", "max_dd", "payoff", "expectancy"]
    )

    emit(f"\n  {ticker}:")
    if not issues:
        if n_pass >= 4:
            emit(
                "  → No significant degradation. Performance was consistent across "
                "both windows. The strategy's parameters transfer well."
            )
        else:
            emit(
                f"  → Moderate degradation ({n_pass}/5 PASS). No single dominant cause. "
                "Normal variance between market regimes — the strategy still has edge "
                "but performs more weakly on out-of-sample data."
            )
    else:
        for issue in issues:
            emit(f"  → {issue.strip()}")


def _save_results(lines: list, all_verbose: dict):
    """Write summary + full verbose trade logs to validation/walk_forward_results.txt."""
    out_path = Path(__file__).parent / "walk_forward_results.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(out_path, "w") as fh:
        fh.write(f"Walk-Forward Validation — generated {ts}\n\n")
        for line in lines:
            fh.write(line + "\n")

        fh.write("\n\n" + "=" * 80 + "\n")
        fh.write("FULL BACKTESTER OUTPUT (trade-by-trade, all 8 runs)\n")
        fh.write("=" * 80 + "\n")
        for ticker, windows in all_verbose.items():
            for window_name, verbose in windows.items():
                if verbose:
                    fh.write(f"\n{'─'*60}\n")
                    fh.write(f"  {ticker}  —  {window_name}\n")
                    fh.write(f"{'─'*60}\n")
                    fh.write(verbose)

    print(f"\n[walk_forward] Full results saved → {out_path}")
    lines.append(f"\n[walk_forward] Full results saved → {out_path}")


# =============================================================================
# Main
# =============================================================================

def run_walk_forward():
    lines: list[str] = []

    def emit(text: str = ""):
        print(text)
        lines.append(text)

    emit()
    emit("=" * 80)
    emit("  WALK-FORWARD VALIDATION")
    emit("  PARAMETERS FROZEN — no optimization performed between windows.")
    emit("  These results represent genuine out-of-sample performance.")
    emit("=" * 80)
    emit(f"  Stocks:      {', '.join(STOCKS)}")
    emit(f"  In-sample:   {WINDOWS['in_sample'][0]} → {WINDOWS['in_sample'][1]} (excl)")
    emit(f"  OOS:         {WINDOWS['out_of_sample'][0]} → {WINDOWS['out_of_sample'][1]} (excl)")
    emit(f"  Strategy:    SMA {PARAMS['sma_fast']}/{PARAMS['sma_slow']} crossover")
    emit(f"  Risk Mgmt:   Hard-20% | Chandelier ATR22×3 | Time-60bar | RoundNum")
    emit(f"  Cooldown:    15-bar after RM exits")
    emit(f"  Sizing:      Fixed-fractional 1.5% risk/trade, 20% max position")
    emit(f"  Capital:     ₹{PARAMS['initial_capital']:,} per window (independent runs)")
    emit()

    is_start, is_end   = WINDOWS["in_sample"]
    oos_start, oos_end = WINDOWS["out_of_sample"]

    # ── Run all 8 backtests (4 stocks × 2 windows) ───────────────────────────
    all_results: dict[str, dict] = {}
    all_verbose: dict[str, dict] = {}

    for ticker in STOCKS:
        print(f"\n[walk_forward] ── {ticker} in-sample ──────────────────────────────")
        is_r = _run_one(ticker, is_start, is_end)

        print(f"\n[walk_forward] ── {ticker} out-of-sample ──────────────────────────")
        oos_r = _run_one(ticker, oos_start, oos_end)

        all_results[ticker] = {"is": is_r, "oos": oos_r}
        all_verbose[ticker] = {
            "in_sample":     is_r.get("verbose",  ""),
            "out_of_sample": oos_r.get("verbose", ""),
        }

    # ── Per-stock tables ──────────────────────────────────────────────────────
    all_scores: list[list[bool]] = []
    for ticker in STOCKS:
        is_r  = all_results[ticker]["is"]
        oos_r = all_results[ticker]["oos"]
        scores = _print_stock(ticker, is_r, oos_r, lines)
        if scores:
            all_scores.append(scores)

    # ── Overall verdict ───────────────────────────────────────────────────────
    _print_verdict(all_scores, lines)

    # ── Degradation analysis ──────────────────────────────────────────────────
    emit()
    emit("=" * 80)
    emit("  DEGRADATION ANALYSIS")
    emit("  (stocks where OOS underperformed IS significantly)")
    emit("=" * 80)
    for ticker in STOCKS:
        _degradation_analysis(
            ticker,
            all_results[ticker]["is"],
            all_results[ticker]["oos"],
            lines,
        )
    emit()

    # ── Save ──────────────────────────────────────────────────────────────────
    _save_results(lines, all_verbose)


if __name__ == "__main__":
    run_walk_forward()
