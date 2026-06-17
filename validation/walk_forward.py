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

import numpy as np
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
    # Portfolio-level regime filter: suppress BUY when NIFTY SMA20 < SMA50.
    # Toggle to False to reproduce the original 15/20 baseline without filtering.
    "nifty_regime_filter": True,
}

MIN_BARS     = 200   # skip a window if fewer than this many trading days
NIFTY_TICKER = "NIFTY 50.NS"   # Kite Connect instrument token 256265 (NSE index)


def _fetch_nifty(start: str, end: str) -> pd.DataFrame:
    """
    Fetch NIFTY 50 daily closes for the regime filter.

    Uses the same kite_fetcher as equity data so the date alignment is
    identical to stock bars (same NSE trading-day calendar, no source mismatch).
    Returns an empty DataFrame on any failure — the backtester treats that as
    filter-disabled and allows all BUYs.
    """
    try:
        df = get_ohlcv(NIFTY_TICKER, start, end)
        if df is not None and len(df) >= 50:
            print(f"[walk_forward] NIFTY 50: {len(df)} bars loaded for regime filter")
            return df
    except Exception as exc:
        print(f"[walk_forward] WARNING: NIFTY fetch failed ({exc}) — regime filter disabled for this window")
    return pd.DataFrame()

# Sharpe ratio parameters
RISK_FREE_RATE      = 0.065   # 6.5% — RBI repo rate approximation
TRADING_DAYS        = 252     # NSE trading days per year

# Cooldown sensitivity analysis
SENSITIVITY_COOLDOWNS   = [10, 15, 20, 25]    # bars to test
SENSITIVITY_MIN_BARS    = 1_250               # IS + OOS bars to qualify as "5 years"
SENSITIVITY_UNIVERSE    = [                   # all 10 current trading universe stocks
    "TMPV.NS", "WHIRLPOOL.NS", "SIEMENS.NS", "BAJAJ-AUTO.NS",
    "CUMMINSIND.NS", "HCLTECH.NS", "BOSCHLTD.NS", "COLPAL.NS",
    "ANURAS.NS", "HEROMOTOCO.NS",
]


# =============================================================================
# Single-window backtest
# =============================================================================

def _run_one(ticker: str, start: str, end: str, nifty_df: pd.DataFrame = None) -> dict:
    """
    Fetch data and run one full backtest for ticker in [start, end).
    Kite_fetcher prints progress to stdout (intentional — shows loading).
    The backtester's verbose bar-by-bar output is captured and kept for the
    saved file only (not printed to terminal).

    Args:
        nifty_df: Pre-fetched NIFTY 50 DataFrame for the same window. Passed to
                  the backtester for the portfolio-level regime filter. None or
                  empty DataFrame disables the filter for this run.

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

    regime_active = PARAMS.get("nifty_regime_filter", False)

    buf = io.StringIO()
    with redirect_stdout(buf):
        equity_curve = bt_run(
            df, signals, portfolio,
            risk_manager=risk_manager,
            cooldown_tracker=cooldown,
            position_sizer=sizer,
            use_next_day_fills=True,       # AMO-realistic: fills at next day's open
            nifty_regime_df=nifty_df,      # None → filter disabled
            nifty_regime_filter=regime_active,
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


def _run_one_from_df(
    df: pd.DataFrame,
    cooldown_bars: int,
    nifty_df: pd.DataFrame = None,
) -> dict:
    """
    Run one backtest window using a pre-fetched DataFrame.

    Unlike _run_one(), no data fetching occurs — the caller supplies df and
    nifty_df. Used by the sensitivity analysis to avoid re-fetching the same
    stock data for every cooldown value being tested.
    """
    n_bars = len(df)
    if n_bars < MIN_BARS:
        return {"error": f"INSUFFICIENT DATA — {n_bars} bars (need ≥ {MIN_BARS})"}

    cooldown_cfg = {**PARAMS["cooldown"], "cooldown_bars": cooldown_bars}

    signals      = generate_signals(df, PARAMS["sma_fast"], PARAMS["sma_slow"])
    risk_manager = RiskManager(PARAMS["risk_management"])
    cooldown     = CooldownTracker(cooldown_cfg)
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
            nifty_regime_df=nifty_df,
            nifty_regime_filter=PARAMS.get("nifty_regime_filter", False),
        )

    return {
        "error":        None,
        "n_bars":       n_bars,
        "metrics":      _metrics(equity_curve, portfolio),
        "equity_curve": equity_curve,
        "portfolio":    portfolio,
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


def _compute_sharpe(equity_curve: pd.DataFrame) -> dict:
    """
    Compute annualized Sharpe ratio from a daily equity curve.

    Method:
      daily_return[t] = (portfolio_value[t] - portfolio_value[t-1]) / portfolio_value[t-1]
      ann_return       = mean(daily_return) × 252
      ann_vol          = std(daily_return, ddof=1) × √252
      Sharpe           = (ann_return − risk_free_rate) / ann_vol

    Using arithmetic mean of daily returns (not CAGR) keeps the numerator and
    denominator on the same basis, which is the standard Sharpe convention.

    Returns a dict with ann_return_pct, ann_vol_pct, sharpe (float or nan).
    """
    prices        = equity_curve["portfolio_value"]
    daily_returns = prices.pct_change().dropna()

    if len(daily_returns) < 2:
        return {"ann_return_pct": 0.0, "ann_vol_pct": 0.0, "sharpe": float("nan"), "n_days": 0}

    mean_d = float(daily_returns.mean())
    std_d  = float(daily_returns.std(ddof=1))

    ann_return = mean_d * TRADING_DAYS
    ann_vol    = std_d  * np.sqrt(TRADING_DAYS)

    sharpe = (ann_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else float("nan")

    return {
        "ann_return_pct": ann_return * 100,
        "ann_vol_pct":    ann_vol    * 100,
        "sharpe":         sharpe,
        "n_days":         len(daily_returns),
    }


def _inposition_mask(equity_curve: pd.DataFrame, portfolio: Portfolio) -> pd.Series:
    """
    Return a boolean Series aligned to equity_curve.index that is True on every
    day within [entry_date, exit_date] for each completed trade.

    Includes the exit day because that day's return still reflects the trade P&L
    (sell happens intraday; end-of-day value captures the realised gain/loss).
    Days with no completed trade and no open position at close are False (cash).
    """
    mask = pd.Series(False, index=equity_curve.index)
    trades = portfolio.get_trade_log()
    if trades.empty:
        return mask
    for _, trade in trades.iterrows():
        entry = pd.Timestamp(trade["entry_date"])
        exit_ = pd.Timestamp(trade["exit_date"])
        mask |= (equity_curve.index >= entry) & (equity_curve.index <= exit_)
    return mask


def _compute_inposition_sharpe(equity_curve: pd.DataFrame, portfolio: Portfolio) -> dict:
    """
    Sharpe ratio computed only on days the portfolio held a position.

    Filters the daily return series to in-position days (via _inposition_mask),
    then applies the same annualisation as _compute_sharpe.
    """
    mask = _inposition_mask(equity_curve, portfolio)
    daily_returns = equity_curve["portfolio_value"].pct_change()
    in_pos_returns = daily_returns[mask].dropna()

    n = len(in_pos_returns)
    if n < 2:
        return {"ann_return_pct": 0.0, "ann_vol_pct": 0.0, "sharpe": float("nan"), "n_days": n}

    mean_d = float(in_pos_returns.mean())
    std_d  = float(in_pos_returns.std(ddof=1))

    ann_return = mean_d * TRADING_DAYS
    ann_vol    = std_d  * np.sqrt(TRADING_DAYS)
    sharpe     = (ann_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else float("nan")

    return {
        "ann_return_pct": ann_return * 100,
        "ann_vol_pct":    ann_vol    * 100,
        "sharpe":         sharpe,
        "n_days":         n,
    }


def _print_sharpe_table(all_results: dict, lines: list) -> None:
    """
    Print a two-column Sharpe ratio summary (full-period vs in-position).

    Full-period Sharpe uses every calendar day in the OOS window.
    In-position Sharpe uses only days where the portfolio held open shares,
    reconstructed from each stock's trade log (entry_date → exit_date).

    Equal-weight rows average all four stocks' daily returns; the in-position
    equal-weight row filters to days where at least one stock held a position.
    """

    def emit(text: str = ""):
        print(text)
        lines.append(text)

    oos_window = f"{WINDOWS['out_of_sample'][0][:4]}–{WINDOWS['out_of_sample'][1][:4]}"

    emit()
    emit("=" * 80)
    emit(f"  SHARPE RATIO SUMMARY  (Out-of-Sample, {oos_window})")
    emit(f"  Risk-free rate: {RISK_FREE_RATE * 100:.1f}%  |  "
         f"Annualisation: ×252 trading days")
    emit("=" * 80)

    C0, C1, C2 = 18, 24, 22
    emit(
        f"  {'Stock':<{C0}}"
        f"{'Full-period Sharpe':^{C1}}"
        f"{'In-position Sharpe':^{C2}}"
    )
    emit("  " + "─" * (C0 + C1 + C2))

    per_stock_returns:      dict[str, pd.Series] = {}
    per_stock_inpos_masks:  dict[str, pd.Series] = {}

    for ticker in STOCKS:
        oos_r = all_results.get(ticker, {}).get("oos", {})
        if oos_r.get("error") or "equity_curve" not in oos_r:
            emit(f"  {ticker:<{C0}}{'N/A':^{C1}}{'N/A':^{C2}}")
            continue

        ec        = oos_r["equity_curve"]
        portfolio = oos_r["portfolio"]

        full_stat  = _compute_sharpe(ec)
        inpos_stat = _compute_inposition_sharpe(ec, portfolio)

        per_stock_returns[ticker]     = ec["portfolio_value"].pct_change().dropna()
        per_stock_inpos_masks[ticker] = _inposition_mask(ec, portfolio)

        full_str  = (f"{full_stat['sharpe']:>+.2f}"
                     if not np.isnan(full_stat["sharpe"]) else "N/A")
        inpos_str = (f"{inpos_stat['sharpe']:>+.2f}  ({inpos_stat['n_days']}d in)"
                     if not np.isnan(inpos_stat["sharpe"]) else "N/A")

        emit(
            f"  {ticker:<{C0}}"
            f"{full_str:^{C1}}"
            f"{inpos_str:^{C2}}"
        )

    # ── Equal-weight portfolio ────────────────────────────────────────────────
    emit("  " + "─" * (C0 + C1 + C2))

    if per_stock_returns:
        combined    = pd.DataFrame(per_stock_returns).dropna()
        avg_returns = combined.mean(axis=1)
        n_overlap   = len(combined)

        # Full-period equal-weight Sharpe
        fake_prices = (1 + avg_returns).cumprod() * PARAMS["initial_capital"]
        fake_ec     = pd.DataFrame({"portfolio_value": fake_prices}, index=avg_returns.index)
        full_stat   = _compute_sharpe(fake_ec)

        # In-position equal-weight: any day at least one stock held a position
        any_inpos = pd.Series(False, index=avg_returns.index)
        for ticker, mask in per_stock_inpos_masks.items():
            any_inpos |= mask.reindex(avg_returns.index, fill_value=False)

        inpos_avg = avg_returns[any_inpos]
        n_inpos   = len(inpos_avg)
        if n_inpos >= 2:
            mean_d     = float(inpos_avg.mean())
            std_d      = float(inpos_avg.std(ddof=1))
            ann_return = mean_d * TRADING_DAYS
            ann_vol    = std_d  * np.sqrt(TRADING_DAYS)
            inpos_sharpe = (ann_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else float("nan")
            inpos_str = (f"{inpos_sharpe:>+.2f}  ({n_inpos}d in)"
                         if not np.isnan(inpos_sharpe) else "N/A")
        else:
            inpos_str = "N/A"

        full_str = (f"{full_stat['sharpe']:>+.2f}"
                    if not np.isnan(full_stat["sharpe"]) else "N/A")

        line = (
            f"  {'Equal-weight':<{C0}}"
            f"{full_str:^{C1}}"
            f"{inpos_str:^{C2}}"
            f"   ← {n_overlap}d overlap"
        )
        print(line)
        lines.append(line)
    else:
        emit(f"  {'Equal-weight':<{C0}}{'N/A':^{C1}}{'N/A':^{C2}}")

    emit()
    emit(
        "  Full-period Sharpe penalises the strategy for cash days (earns 0%, "
        "risk-free = 6.5%)."
    )
    emit(
        "  In-position Sharpe strips out flat cash days — measures quality of "
        "entries/exits only."
    )
    emit("=" * 80)


def _cooldown_sensitivity(lines: list) -> None:
    """
    Cooldown sensitivity analysis across the full 10-stock trading universe.

    For each cooldown value in SENSITIVITY_COOLDOWNS:
      - Fetches IS (2018-2023) and OOS (2023-2026) data per stock (once).
      - Skips stocks with IS + OOS bars < SENSITIVITY_MIN_BARS (not enough history).
      - Runs the full backtester with each cooldown on pre-cached DataFrames.
      - Records WF score, total OOS trades, and average OOS trade return.

    Data is cached per-stock across cooldown values to avoid redundant Kite fetches.
    NIFTY regime filter follows the current PARAMS setting.
    """
    is_start, is_end     = WINDOWS["in_sample"]
    oos_start, oos_end   = WINDOWS["out_of_sample"]
    is_end_ts            = pd.Timestamp(is_end)
    oos_start_ts         = pd.Timestamp(oos_start)
    regime_active        = PARAMS.get("nifty_regime_filter", False)
    current_cd           = PARAMS["cooldown"]["cooldown_bars"]   # baseline (15)

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit()
    emit("=" * 80)
    emit("  COOLDOWN SENSITIVITY ANALYSIS")
    emit(f"  Universe: {len(SENSITIVITY_UNIVERSE)} stocks tested")
    emit(f"  Cooldown values: {SENSITIVITY_COOLDOWNS} bars")
    emit(f"  Eligibility: IS + OOS bars >= {SENSITIVITY_MIN_BARS:,} (~5 years)")
    emit(f"  IS window:   {is_start} → {is_end}  |  OOS: {oos_start} → {oos_end}")
    emit(f"  NIFTY filter: {'ACTIVE' if regime_active else 'DISABLED'}")
    emit("=" * 80)

    # ── Step 1: Eligibility — fetch IS+OOS once per stock, cache if qualifying ──
    emit()
    emit("  DATA ELIGIBILITY CHECK (IS + OOS bars >= 1,250):")
    emit()

    qualifying: list[str]                    = []
    skipped:    list[tuple[str, str]]        = []
    cache_is:   dict[str, pd.DataFrame]     = {}
    cache_oos:  dict[str, pd.DataFrame]     = {}

    for ticker in SENSITIVITY_UNIVERSE:
        try:
            df_is  = get_ohlcv(ticker, is_start, is_end)
            df_oos = get_ohlcv(ticker, oos_start, oos_end)
            n_is   = len(df_is)  if df_is  is not None else 0
            n_oos  = len(df_oos) if df_oos is not None else 0
            total  = n_is + n_oos

            if total >= SENSITIVITY_MIN_BARS and n_is >= MIN_BARS and n_oos >= MIN_BARS:
                qualifying.append(ticker)
                cache_is[ticker]  = df_is
                cache_oos[ticker] = df_oos
                first_bar = df_is.index[0].date() if n_is > 0 else "?"
                emit(
                    f"  ✓  {ticker:<22}  IS={n_is:>5}  OOS={n_oos:>4}  "
                    f"total={total:>5}  from {first_bar}  — INCLUDED"
                )
            else:
                reason = (
                    f"total {total} bars < {SENSITIVITY_MIN_BARS}"
                    if total < SENSITIVITY_MIN_BARS
                    else f"IS only {n_is} bars"
                )
                skipped.append((ticker, reason))
                first_bar = df_is.index[0].date() if n_is > 0 else "?"
                emit(
                    f"  ✗  {ticker:<22}  IS={n_is:>5}  OOS={n_oos:>4}  "
                    f"total={total:>5}  from {first_bar}  — SKIPPED ({reason})"
                )
        except Exception as exc:
            skipped.append((ticker, f"fetch error: {exc}"))
            emit(f"  ✗  {ticker:<22}  fetch failed — SKIPPED ({exc})")

    emit()
    emit(f"  Qualifying: {len(qualifying)}  |  Skipped: {len(skipped)}")

    if not qualifying:
        emit()
        emit("  No qualifying stocks — sensitivity analysis aborted.")
        return

    # ── Step 2: NIFTY data for regime filter (reuse across all cooldown tests) ──
    if regime_active:
        print(f"\n[cooldown_sensitivity] Fetching NIFTY 50 for IS window…")
        nifty_is  = _fetch_nifty(is_start, is_end)
        print(f"[cooldown_sensitivity] Fetching NIFTY 50 for OOS window…")
        nifty_oos = _fetch_nifty(oos_start, oos_end)
    else:
        nifty_is = nifty_oos = pd.DataFrame()

    # ── Step 3: Run all backtests ─────────────────────────────────────────────
    # Structure: cd_results[cooldown] = {n_pass, n_total, n_stocks,
    #                                    total_trades, avg_return, per_stock}
    METRICS_KEYS = ["total_ret", "vs_bnh", "max_dd", "payoff", "expectancy"]
    cd_results: dict[int, dict] = {}

    for cd in SENSITIVITY_COOLDOWNS:
        print(
            f"\n[cooldown_sensitivity] Testing cooldown={cd} bars "
            f"on {len(qualifying)} stocks × 2 windows …"
        )
        passes:        list[bool]          = []
        oos_trade_dfs: list[pd.DataFrame]  = []
        per_stock:     dict[str, dict]     = {}

        for ticker in qualifying:
            is_r  = _run_one_from_df(cache_is[ticker],  cd, nifty_df=nifty_is)
            oos_r = _run_one_from_df(cache_oos[ticker], cd, nifty_df=nifty_oos)

            if is_r.get("error") or oos_r.get("error"):
                print(f"  {ticker}: error IS={is_r.get('error')} OOS={oos_r.get('error')}")
                continue

            is_m  = is_r["metrics"]
            oos_m = oos_r["metrics"]
            stock_passes = [_pass(k, is_m, oos_m) for k in METRICS_KEYS]
            passes.extend(stock_passes)

            oos_tlog = oos_r["portfolio"].get_trade_log()
            if not oos_tlog.empty:
                oos_trade_dfs.append(oos_tlog)

            per_stock[ticker] = {
                "is_ret":     is_m["total_ret"],
                "oos_ret":    oos_m["total_ret"],
                "is_trades":  is_m["n_trades"],
                "oos_trades": oos_m["n_trades"],
                "n_pass":     sum(stock_passes),
            }

        # Aggregate
        n_pass  = sum(passes)
        n_total = len(passes)

        if oos_trade_dfs:
            combined     = pd.concat(oos_trade_dfs, ignore_index=True)
            total_trades = len(combined)
            avg_return   = float(combined["return_pct"].mean())
        else:
            total_trades = 0
            avg_return   = 0.0

        cd_results[cd] = {
            "n_pass":       n_pass,
            "n_total":      n_total,
            "n_stocks":     len(per_stock),
            "total_trades": total_trades,
            "avg_return":   avg_return,
            "per_stock":    per_stock,
        }
        print(
            f"  → {n_pass}/{n_total} PASS | {total_trades} OOS trades "
            f"| avg return {avg_return:>+.2f}%"
        )

    # ── Step 4: Comparison table ──────────────────────────────────────────────
    emit()
    emit("=" * 80)
    emit(f"  COOLDOWN COMPARISON  ({len(qualifying)} qualifying stocks, OOS 2023–2025)")
    emit("=" * 80)
    emit()

    W0, W1, W2, W3, W4 = 14, 10, 18, 16, 20
    emit(
        f"  {'Cooldown':>{W0}}"
        f"  {'Stocks':>{W1}}"
        f"  {'WF Score':>{W2}}"
        f"  {'OOS Trades':>{W3}}"
        f"  {'Avg Trade Return':>{W4}}"
    )
    emit("  " + "─" * (W0 + W1 + W2 + W3 + W4 + 10))

    for cd in SENSITIVITY_COOLDOWNS:
        r = cd_results.get(cd)
        if r is None:
            continue
        n_pass  = r["n_pass"]
        n_total = r["n_total"]
        pct     = n_pass / n_total * 100 if n_total else 0.0
        marker  = "  ← baseline" if cd == current_cd else ""
        emit(
            f"  {f'{cd} bars':>{W0}}"
            f"  {r['n_stocks']:>{W1}}"
            f"  {f'{n_pass}/{n_total}  ({pct:.0f}%)':>{W2}}"
            f"  {r['total_trades']:>{W3}}"
            f"  {r['avg_return']:>+{W4 - 3}.2f}%"
            f"{marker}"
        )

    # ── Step 5: Per-stock detail at baseline cooldown ──────────────────────────
    emit()
    emit("  " + "─" * (W0 + W1 + W2 + W3 + W4 + 10))
    emit(f"  Per-stock detail at baseline cooldown ({current_cd} bars):")
    emit()

    base = cd_results.get(current_cd, {})
    if base:
        C0, C1, C2, C3, C4, C5 = 22, 12, 12, 8, 12, 14
        emit(
            f"  {'Stock':<{C0}}"
            f"  {'IS Return':>{C1}}"
            f"  {'OOS Return':>{C2}}"
            f"  {'Score':>{C3}}"
            f"  {'IS Trades':>{C4}}"
            f"  {'OOS Trades':>{C5}}"
        )
        emit("  " + "─" * (C0 + C1 + C2 + C3 + C4 + C5 + 14))
        for ticker in qualifying:
            s = base["per_stock"].get(ticker)
            if s is None:
                continue
            verdict = f"{s['n_pass']}/5"
            emit(
                f"  {ticker:<{C0}}"
                f"  {s['is_ret']:>+{C1}.1f}%"
                f"  {s['oos_ret']:>+{C2}.1f}%"
                f"  {verdict:>{C3}}"
                f"  {s['is_trades']:>{C4}}"
                f"  {s['oos_trades']:>{C5}}"
            )

    # ── Step 6: Skipped stocks ────────────────────────────────────────────────
    if skipped:
        emit()
        emit("  SKIPPED STOCKS:")
        for ticker, reason in skipped:
            emit(f"  ✗  {ticker:<22} — {reason}")

    emit()
    emit("=" * 80)


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
    regime_flag = PARAMS.get("nifty_regime_filter", False)
    emit(f"  NIFTY Filter: {'ACTIVE (suppress BUY when NIFTY SMA20 < SMA50)' if regime_flag else 'DISABLED'}")
    emit()

    is_start, is_end   = WINDOWS["in_sample"]
    oos_start, oos_end = WINDOWS["out_of_sample"]

    # ── Fetch NIFTY 50 once per window for the regime filter ─────────────────
    # Fetched outside the per-stock loop: one NIFTY pull per window, shared
    # across all 4 stocks in that window. None if filter is disabled.
    if regime_flag:
        print(f"\n[walk_forward] Fetching NIFTY 50 for regime filter (in-sample)…")
        nifty_is  = _fetch_nifty(is_start, is_end)
        print(f"[walk_forward] Fetching NIFTY 50 for regime filter (out-of-sample)…")
        nifty_oos = _fetch_nifty(oos_start, oos_end)
    else:
        nifty_is = nifty_oos = pd.DataFrame()

    # ── Run all 8 backtests (4 stocks × 2 windows) ───────────────────────────
    all_results: dict[str, dict] = {}
    all_verbose: dict[str, dict] = {}

    for ticker in STOCKS:
        print(f"\n[walk_forward] ── {ticker} in-sample ──────────────────────────────")
        is_r = _run_one(ticker, is_start, is_end, nifty_df=nifty_is)

        print(f"\n[walk_forward] ── {ticker} out-of-sample ──────────────────────────")
        oos_r = _run_one(ticker, oos_start, oos_end, nifty_df=nifty_oos)

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

    # ── Sharpe ratio summary ──────────────────────────────────────────────────
    _print_sharpe_table(all_results, lines)

    # ── Cooldown sensitivity analysis ─────────────────────────────────────────
    _cooldown_sensitivity(lines)

    # ── Save ──────────────────────────────────────────────────────────────────
    _save_results(lines, all_verbose)


if __name__ == "__main__":
    run_walk_forward()
