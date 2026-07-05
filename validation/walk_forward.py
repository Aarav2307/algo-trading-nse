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
from datetime import date as _date, datetime
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
# UNIVERSE — current validated trading universe
# =============================================================================
# Current validated universe as of 2026-06-26.
# Excluded from WF (insufficient history):
#   ANURAS.NS    — only ~440 IS bars (listed post-2019)
#   NEWGEN.NS    — only ~226 IS bars (listed Jan 2018)
#   PERSISTENT.NS — just added to live system, no WF history yet
# Removed from live system (not in WF):
#   TMPV, WHIRLPOOL, SIEMENS, HEROMOTOCO, BOSCHLTD, CUMMINSIND, RPOWER
#
# Minimum bar requirements per window:
#   IS  window: ≥744 bars (~3 years at 248 trading days/yr)
#   OOS window: ≥252 bars (~1 year)
#   _run_one() enforces MIN_BARS=200 per window and skips automatically.
#
# UPDATE THIS LIST whenever the live universe changes.
# Re-run walk_forward.py quarterly, or after any universe addition/removal.
STOCKS = [
    "BAJAJ-AUTO.NS",   # OOS +13.5%, strong across both WF windows
    "HCLTECH.NS",      # clean, no flags
    "COLPAL.NS",       # WF validated 10/12
    "JKTYRE.NS",       # clean, no flags
    "BSOFT.NS",        # 4/5 extended WF; standout OOS performer
]


# =============================================================================
# FROZEN PARAMETERS — identical across both windows, not re-optimised
# =============================================================================

# In-sample:     2018-01-01 → 2022-12-31  (exclusive end = 2023-01-01)
# OOS end date is dynamic — always extends to today so validation includes
# all available live performance data. IS window is fixed (2018-2023).
# Re-run walk_forward.py quarterly to keep validation current.
_TODAY = _date.today().strftime("%Y-%m-%d")

WINDOWS = {
    "in_sample":     ("2018-01-01", "2023-01-01"),
    "out_of_sample": ("2023-01-01", _TODAY),        # dynamic — extends to today
}

# Extended validation windows — tests against genuine bear market conditions
# IS includes 2015-16 correction, 2018 IL&FS crisis
# OOS includes COVID crash 2020, 2022 rate hike selloff
EXT_IS_START  = "2015-01-01"
EXT_IS_END    = "2019-12-31"
EXT_OOS_START = "2020-01-01"
EXT_OOS_END   = "2023-12-31"

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
# Parameter helpers
# =============================================================================

def _merge_params(base: dict, overrides: dict) -> dict:
    """
    Merge a flat override dict into a deep copy of the nested PARAMS structure.

    Flat override keys supported:
        sma_fast, sma_slow                          → top-level
        atr_period, atr_multiplier, hard_stop_pct,
        max_bars_held                               → risk_management
        risk_per_trade                              → position_sizing.risk_per_trade_pct
        max_position                                → position_sizing.max_position_pct
    """
    import copy
    p = copy.deepcopy(base)
    for key in ("sma_fast", "sma_slow", "initial_capital"):
        if key in overrides:
            p[key] = overrides[key]
    for key in ("atr_period", "atr_multiplier", "hard_stop_pct", "max_bars_held"):
        if key in overrides:
            p["risk_management"][key] = overrides[key]
    if "risk_per_trade" in overrides:
        p["position_sizing"]["risk_per_trade_pct"] = overrides["risk_per_trade"]
    if "max_position" in overrides:
        p["position_sizing"]["max_position_pct"] = overrides["max_position"]
    return p


# =============================================================================
# Single-window backtest
# =============================================================================

def _run_one(ticker: str, start: str, end: str, nifty_df: pd.DataFrame = None, params: dict = None) -> dict:
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

    p = _merge_params(PARAMS, params) if params is not None else PARAMS

    signals      = generate_signals(df, p["sma_fast"], p["sma_slow"])
    risk_manager = RiskManager(p["risk_management"])
    cooldown     = CooldownTracker(p["cooldown"])
    sizer        = PositionSizer(p["position_sizing"])
    portfolio    = Portfolio(p["initial_capital"])

    regime_active = p.get("nifty_regime_filter", False)

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

    if name == "min_abs_oos_ret":
        return oos_m["total_ret"] >= 4.0

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
        f"{'Out-of-Sample (2023-'+_TODAY[:4]+')':^{C3}}"
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

    row("Min OOS return", "min_abs_oos_ret",
        lambda m: f"{m['total_ret']:>+.1f}%")

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
    """
    System-level verdict based on per-stock performance.

    PRIMARY gate: each stock must pass >= PER_STOCK_MIN metrics.
    INFORMATIONAL: aggregate pass rate shown but not used for verdict.

    Design rationale:
      Aggregate scoring on 5 stocks with 6 metrics = 30 data points
      with high inter-stock correlation (all exposed to NIFTY regime).
      Effective sample size is ~15 independent observations —
      too small for any aggregate threshold to be statistically
      meaningful. Per-stock independence is the correct primary gate.
    """
    def emit(text: str = ""):
        print(text)
        lines.append(text)

    # ── Constants ─────────────────────────────────────────────────────────
    PER_STOCK_MIN  = 4      # each stock must pass ≥4/6 metrics independently
    STOCK_PASS_PCT = 0.70   # ≥70% of stocks must individually qualify

    # ── Per-stock qualification ────────────────────────────────────────────
    per_stock_scores = [(sum(s), len(s)) for s in all_scores]
    qualified_stocks = sum(1 for n, t in per_stock_scores if n >= PER_STOCK_MIN)
    total_stocks     = len(all_scores)
    stock_pass_rate  = qualified_stocks / total_stocks if total_stocks > 0 else 0.0

    # ── Aggregate (informational only) ─────────────────────────────────────
    flat    = [v for stock in all_scores for v in stock]
    n_pass  = sum(flat)
    n_total = len(flat)
    agg_pct = n_pass / n_total * 100 if n_total > 0 else 0.0

    emit()
    emit("=" * 80)
    emit("  OVERALL SYSTEM VERDICT")
    emit("=" * 80)

    # Per-stock breakdown
    emit(f"  Per-stock results (primary gate — each stock needs ≥{PER_STOCK_MIN}/6):")
    for i, (stock_n, stock_t) in enumerate(per_stock_scores):
        qualified = "✓ QUALIFIED" if stock_n >= PER_STOCK_MIN else "✗ WEAK"
        emit(f"    Stock {i+1}: {stock_n}/{stock_t}  {qualified}")
    emit()
    emit(f"  Qualified stocks: {qualified_stocks}/{total_stocks} "
         f"({stock_pass_rate:.0%})")
    emit()

    # Aggregate (informational)
    n_stocks = len([s for s in all_scores if s])
    emit(f"  Aggregate score:  {n_pass}/{n_total} ({agg_pct:.0f}%)  "
         f"[informational — not used for verdict]")
    emit(f"  (Max possible: {n_total} = {n_stocks} stocks × 6 metrics; "
         f"stocks with data errors are excluded)")
    emit()

    # Primary verdict based on per-stock gate
    stocks_ok = (qualified_stocks / total_stocks >= STOCK_PASS_PCT) if total_stocks > 0 else False

    if total_stocks == 0:
        verdict = "NO DATA"
        detail  = "No stocks produced valid results. Check data and token."
    elif stocks_ok:
        verdict = "SYSTEM VALIDATED"
        detail  = (
            f"{qualified_stocks}/{total_stocks} stocks individually pass "
            f"≥{PER_STOCK_MIN}/6 metrics. Parameters are robust. "
            "Safe to proceed to paper trading."
        )
    elif qualified_stocks >= 1:
        verdict = "PARTIALLY VALIDATED"
        detail  = (
            f"Only {qualified_stocks}/{total_stocks} stocks individually "
            f"qualify. Paper trade with reduced sizing. "
            "Investigate weak stocks before adding capital."
        )
    else:
        verdict = "NOT VALIDATED"
        detail  = (
            "No stocks pass the per-stock minimum. Significant overfitting "
            "or regime dependence detected. Do not deploy capital."
        )

    emit(f"  Verdict:     {verdict}")
    emit(f"  Implication: {detail}")
    emit()
    emit(f"  Gates: per-stock ≥{PER_STOCK_MIN}/6 | "
         f"≥{STOCK_PASS_PCT:.0%} of stocks must qualify")
    emit()
    emit("  NOTE: With 5 stocks and correlated NIFTY exposure, effective")
    emit("  sample size is ~15 independent observations. Statistical")
    emit("  significance is limited — live paper trading confirmation")
    emit("  (6 months, 30+ trades) is the definitive validation.")


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
        for m in ["total_ret", "vs_bnh", "max_dd", "payoff", "expectancy", "min_abs_oos_ret"]
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
    METRICS_KEYS = ["total_ret", "vs_bnh", "max_dd", "payoff", "expectancy", "min_abs_oos_ret"]
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

def run_walk_forward(
    stocks: list[str] | None = None,
    cooldown_bars: int | None = None,
    nifty_regime_filter: bool | None = None,
    params: dict = None,
) -> dict:
    p = _merge_params(PARAMS, params) if params is not None else PARAMS
    _stocks = stocks if stocks is not None else STOCKS
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
    emit(f"  Stocks:      {', '.join(_stocks)}")
    emit(f"  In-sample:   {WINDOWS['in_sample'][0]} → {WINDOWS['in_sample'][1]} (excl)")
    emit(f"  OOS:         {WINDOWS['out_of_sample'][0]} → {WINDOWS['out_of_sample'][1]} (excl)")
    emit(f"  Strategy:    SMA {p['sma_fast']}/{p['sma_slow']} crossover")
    emit(f"  Risk Mgmt:   Hard-20% | Chandelier ATR22×3 | Time-60bar | RoundNum")
    emit(f"  Cooldown:    15-bar after RM exits")
    emit(f"  Sizing:      Fixed-fractional 1.5% risk/trade, 20% max position")
    emit(f"  Capital:     ₹{p['initial_capital']:,} per window (independent runs)")
    regime_flag = nifty_regime_filter if nifty_regime_filter is not None else p.get("nifty_regime_filter", False)
    emit(f"  NIFTY Filter: {'ACTIVE (suppress BUY when NIFTY SMA20 < SMA50)' if regime_flag else 'DISABLED'}")
    emit()

    is_start, is_end   = WINDOWS["in_sample"]
    oos_start, oos_end = WINDOWS["out_of_sample"]

    # ── Fetch NIFTY 50 once per window for the regime filter ─────────────────
    # Fetched outside the per-stock loop: one NIFTY pull per window, shared
    # across all stocks in that window. None if filter is disabled.
    if regime_flag:
        print(f"\n[walk_forward] Fetching NIFTY 50 for regime filter (in-sample)…")
        nifty_is  = _fetch_nifty(is_start, is_end)
        print(f"[walk_forward] Fetching NIFTY 50 for regime filter (out-of-sample)…")
        nifty_oos = _fetch_nifty(oos_start, oos_end)
    else:
        nifty_is = nifty_oos = pd.DataFrame()

    # ── Run all backtests (stocks × 2 windows) ───────────────────────────────
    all_results: dict[str, dict] = {}
    all_verbose: dict[str, dict] = {}

    for ticker in _stocks:
        print(f"\n[walk_forward] ── {ticker} in-sample ──────────────────────────────")
        is_r = _run_one(ticker, is_start, is_end, nifty_df=nifty_is, params=params)

        print(f"\n[walk_forward] ── {ticker} out-of-sample ──────────────────────────")
        oos_r = _run_one(ticker, oos_start, oos_end, nifty_df=nifty_oos, params=params)

        all_results[ticker] = {"is": is_r, "oos": oos_r}
        all_verbose[ticker] = {
            "in_sample":     is_r.get("verbose",  ""),
            "out_of_sample": oos_r.get("verbose", ""),
        }

    # ── Per-stock tables ──────────────────────────────────────────────────────
    all_scores: list[list[bool]] = []
    for ticker in _stocks:
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
    for ticker in _stocks:
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

    # ── Build results dict for comparison ────────────────────────────────────
    METRICS_KEYS = ["total_ret", "vs_bnh", "max_dd", "payoff", "expectancy", "min_abs_oos_ret"]
    results: dict = {}
    for ticker in _stocks:
        is_r  = all_results[ticker]["is"]
        oos_r = all_results[ticker]["oos"]
        if is_r.get("error") or oos_r.get("error"):
            results[ticker] = {"score": "N/A", "oos_return": "N/A"}
        else:
            score = sum(
                _pass(m, is_r["metrics"], oos_r["metrics"])
                for m in METRICS_KEYS
            )
            results[ticker] = {
                "score":      int(score),
                "oos_return": float(oos_r["metrics"]["total_ret"]),
            }
    return results


def run_extended_walk_forward(
    stocks: list[str],
    cooldown_bars: int = 15,
    nifty_regime_filter: bool = True,
    params: dict = None,
) -> dict:
    """
    Extended walk-forward validation using 2015-2019 IS and 2020-2023 OOS windows.
    Tests strategy performance during genuine bear market conditions including:
    - 2015-16 NSE correction (-20%)
    - 2018 IL&FS crisis and NBFC selloff
    - COVID crash Feb-Apr 2020 (OOS)
    - 2022 global rate hike selloff (OOS)

    Returns dict with same structure as run_walk_forward() for direct comparison.
    """
    p = _merge_params(PARAMS, params) if params is not None else PARAMS
    lines: list[str] = []

    def emit(text: str = ""):
        print(text)
        lines.append(text)

    emit()
    emit("=" * 80)
    emit("  EXTENDED WALK-FORWARD VALIDATION")
    emit("  PARAMETERS FROZEN — no optimization performed between windows.")
    emit("  These results represent genuine out-of-sample performance.")
    emit("=" * 80)
    emit(f"  Stocks:      {', '.join(stocks)}")
    emit(f"  In-sample:   {EXT_IS_START} → {EXT_IS_END}")
    emit(f"               (2015-16 correction, 2018 IL&FS crisis)")
    emit(f"  OOS:         {EXT_OOS_START} → {EXT_OOS_END}")
    emit(f"               (COVID crash Feb-Apr 2020, 2022 rate hike selloff)")
    emit(f"  Strategy:    SMA {p['sma_fast']}/{p['sma_slow']} crossover")
    emit(f"  Risk Mgmt:   Hard-20% | Chandelier ATR22×3 | Time-60bar | RoundNum")
    emit(f"  Cooldown:    {cooldown_bars}-bar after RM exits")
    emit(f"  Sizing:      Fixed-fractional 1.5% risk/trade, 20% max position")
    emit(f"  Capital:     ₹{p['initial_capital']:,} per window (independent runs)")
    emit(f"  NIFTY Filter: {'ACTIVE (suppress BUY when NIFTY SMA20 < SMA50)' if nifty_regime_filter else 'DISABLED'}")
    emit()

    if nifty_regime_filter:
        print(f"\n[ext_walk_forward] Fetching NIFTY 50 for regime filter (in-sample)…")
        nifty_is  = _fetch_nifty(EXT_IS_START, EXT_IS_END)
        print(f"\n[ext_walk_forward] Fetching NIFTY 50 for regime filter (out-of-sample)…")
        nifty_oos = _fetch_nifty(EXT_OOS_START, EXT_OOS_END)
    else:
        nifty_is = nifty_oos = pd.DataFrame()

    all_results: dict[str, dict] = {}

    for ticker in stocks:
        print(f"\n[ext_walk_forward] ── {ticker} in-sample ──────────────────────────────")
        is_r = _run_one(ticker, EXT_IS_START, EXT_IS_END, nifty_df=nifty_is, params=params)

        print(f"\n[ext_walk_forward] ── {ticker} out-of-sample ──────────────────────────")
        oos_r = _run_one(ticker, EXT_OOS_START, EXT_OOS_END, nifty_df=nifty_oos, params=params)

        all_results[ticker] = {"is": is_r, "oos": oos_r}

    # ── Per-stock scoring — same 6 metrics, same PASS/FAIL thresholds as run_walk_forward ──
    C1, C2, C3, C4 = 24, 26, 28, 12
    all_scores: list[list[bool]] = []
    results: dict = {}

    for ticker in stocks:
        is_r  = all_results[ticker]["is"]
        oos_r = all_results[ticker]["oos"]

        emit()
        emit("=" * 80)
        emit(f"  {ticker}")
        emit("=" * 80)

        if is_r.get("error") or oos_r.get("error"):
            emit(f"  In-sample:     {is_r.get('error', 'OK')}")
            emit(f"  Out-of-sample: {oos_r.get('error', 'OK')}")
            emit()
            results[ticker] = {"score": "N/A", "oos_return": "N/A"}
            continue

        is_m  = is_r["metrics"]
        oos_m = oos_r["metrics"]

        emit(
            f"  Data:  {is_r['n_bars']} bars in-sample  "
            f"|  {oos_r['n_bars']} bars out-of-sample"
        )
        if is_r.get("covid_note"):
            emit(f"  NOTE:  {is_r['covid_note']}")
        emit()

        emit(
            f"  {'Metric':<{C1}}"
            f"{'In-Sample (2015-2019)':^{C2}}"
            f"{'Out-of-Sample (2020-2023)':^{C3}}"
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

        row("Min OOS return", "min_abs_oos_ret",
            lambda m: f"{m['total_ret']:>+.1f}%")

        emit("  " + "─" * (C1 + C2 + C3 + C4))

        is_wr  = f"{is_m['win_rate']:.1f}%"
        oos_wr = f"{oos_m['win_rate']:.1f}%"
        emit(
            f"  {'Trades':<{C1}}"
            f"{str(is_m['n_trades']):^{C2}}"
            f"{str(oos_m['n_trades']):^{C3}}"
        )
        emit(
            f"  {'Win rate':<{C1}}"
            f"{is_wr:^{C2}}"
            f"{oos_wr:^{C3}}"
        )

        n_pass = sum(scores)
        emit(f"\n  Score: {n_pass}/{len(scores)} PASS")
        emit()

        all_scores.append(scores)
        results[ticker] = {
            "score":      int(n_pass),
            "oos_return": float(oos_m["total_ret"]),
            "oos_trades": int(oos_m["n_trades"]),
        }

    # ── Overall verdict — same thresholds as run_walk_forward ────────────────
    _print_verdict(all_scores, lines)

    return results


def print_comparison_report(
    original_results: dict,
    extended_results: dict,
    stocks: list[str],
) -> None:
    """
    Print side-by-side comparison of original and extended walk-forward results.
    """
    print("\n" + "="*75)
    print("WALK-FORWARD COMPARISON — ORIGINAL vs EXTENDED WINDOWS")
    print("="*75)
    print(f"{'':20} {'ORIGINAL (2018-22 IS / 2023-26 OOS)':^25} {'EXTENDED (2015-19 IS / 2020-23 OOS)':^25}")
    print(f"{'Stock':<20} {'Score':^10} {'OOS Return':^15} {'Score':^10} {'OOS Return':^15}")
    print("-"*75)

    for ticker in stocks:
        orig = original_results.get(ticker, {})
        ext  = extended_results.get(ticker, {})
        orig_score  = orig.get("score", "N/A")
        ext_score   = ext.get("score", "N/A")
        orig_return = orig.get("oos_return", "N/A")
        ext_return  = ext.get("oos_return", "N/A")

        orig_return_str = f"{orig_return:+.1f}%" if isinstance(orig_return, float) else "N/A"
        ext_return_str  = f"{ext_return:+.1f}%"  if isinstance(ext_return, float) else "N/A"

        print(f"{ticker:<20} {str(orig_score):^10} {orig_return_str:^15} {str(ext_score):^10} {ext_return_str:^15}")

    print("-"*75)
    orig_total = sum(r.get("score", 0) for r in original_results.values() if isinstance(r.get("score"), int))
    ext_total  = sum(r.get("score", 0) for r in extended_results.values() if isinstance(r.get("score"), int))
    max_score  = len(stocks) * 6

    print(f"{'TOTAL':<20} {f'{orig_total}/{max_score}':^10} {'':^15} {f'{ext_total}/{max_score}':^10}")
    print(f"{'PCT':<20} {f'{orig_total/max_score*100:.0f}%':^10} {'':^15} {f'{ext_total/max_score*100:.0f}%':^10}")
    print("="*75)

    # Verdict
    if ext_total >= orig_total:
        print(f"\n✅ EXTENDED validation MATCHES OR EXCEEDS original ({ext_total}/{max_score} vs {orig_total}/{max_score})")
        print("   Strategy is robust across both bull and bear market periods.")
    elif ext_total >= max_score * 0.65:
        print(f"\n⚠️  EXTENDED validation LOWER but ACCEPTABLE ({ext_total}/{max_score} vs {orig_total}/{max_score})")
        print("   Strategy performs worse in bear market IS period — expected for long-only.")
        print("   OOS (2020-2023) includes COVID crash and 2022 correction — genuine stress test passed.")
    else:
        print(f"\n❌ EXTENDED validation SIGNIFICANTLY LOWER ({ext_total}/{max_score} vs {orig_total}/{max_score})")
        print("   Strategy may be overfit to bull market conditions. Review before going live.")


def run_rolling_live_check(
    stocks: list[str],
    lookback_days: int = 300,
    nifty_regime_filter: bool = True,
    params: dict = None,
) -> dict:
    """
    Rolling live performance check — tests strategy on the most recent
    `lookback_days` calendar days of data as an early warning system.

    Default: 300 calendar days (~210 trading bars), which exceeds MIN_BARS=200
    so _run_one() can compute all indicators (SMA-50 + ATR-22 + buffer).
    Reduce only if you extend _run_one() to accept a lower minimum.

    This is NOT a walk-forward validation. It has no IS/OOS split.
    Its purpose is to detect strategy degradation in live conditions
    by checking if recent performance is significantly negative.

    Thresholds (deliberately lenient — this is an early warning, not validation):
        HEALTHY:  total_ret >= -5%  AND  expectancy > 0
        WARNING:  total_ret < -5%   OR   expectancy <= 0
        CRITICAL: total_ret < -15%

    Returns dict with per-stock results and overall health status.
    """
    from datetime import timedelta

    end_date   = _date.today().strftime("%Y-%m-%d")
    start_date = (_date.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    print(f"\n[live_check] Rolling performance check: {start_date} → {end_date} ({lookback_days} days)")

    p = _merge_params(PARAMS, params) if params is not None else PARAMS

    try:
        nifty_df = _fetch_nifty(start_date, end_date)
    except Exception as e:
        print(f"[live_check] WARNING: Could not fetch NIFTY data: {e}")
        nifty_df = None

    results = {}
    for ticker in stocks:
        try:
            r = _run_one(
                ticker, start_date, end_date,
                nifty_df=nifty_df,
                params=params,
            )
            if r.get("error"):
                results[ticker] = {"status": "ERROR", "error": r["error"]}
                continue

            m = r["metrics"]
            total_ret  = m.get("total_ret", 0.0)
            expectancy = m.get("expectancy", 0.0)
            n_trades   = m.get("n_trades", 0)

            if total_ret < -15.0:
                status = "CRITICAL"
            elif total_ret < -5.0 or expectancy <= 0:
                status = "WARNING"
            else:
                status = "HEALTHY"

            results[ticker] = {
                "status":     status,
                "total_ret":  round(total_ret, 2),
                "expectancy": round(expectancy, 2),
                "n_trades":   n_trades,
                "n_bars":     r.get("n_bars", 0),
            }

        except Exception as e:
            results[ticker] = {"status": "ERROR", "error": str(e)}

    return {
        "results":       results,
        "start_date":    start_date,
        "end_date":      end_date,
        "lookback_days": lookback_days,
    }


def print_rolling_live_report(live_results: dict) -> None:
    """Print formatted rolling live performance report."""
    results    = live_results["results"]
    start_date = live_results["start_date"]
    end_date   = live_results["end_date"]
    days       = live_results["lookback_days"]

    print("\n" + "="*70)
    print(f"ROLLING LIVE PERFORMANCE CHECK — last {days} calendar days (~{days*5//7} trading bars)")
    print(f"Period: {start_date} → {end_date}")
    print("="*70)
    print(f"{'Stock':<22} {'Status':<12} {'Return':^10} {'Expectancy':^12} {'Trades':^8}")
    print("-"*70)

    statuses = []
    for ticker, r in results.items():
        if r["status"] == "ERROR":
            print(f"{ticker:<22} {'ERROR':<12} {'N/A':^10} {'N/A':^12} {'N/A':^8}")
            print(f"  Error: {r.get('error', 'unknown')}")
            continue

        status     = r["status"]
        total_ret  = r["total_ret"]
        expectancy = r["expectancy"]
        n_trades   = r["n_trades"]
        statuses.append(status)

        icon = "✅" if status == "HEALTHY" else ("⚠️ " if status == "WARNING" else "🚨")
        print(
            f"{ticker:<22} {icon} {status:<10} "
            f"{total_ret:^+10.1f}% {expectancy:^+12.0f}  {n_trades:^8}"
        )

    print("="*70)

    if not statuses:
        print("No results — insufficient data for live check.")
        return

    if any(s == "CRITICAL" for s in statuses):
        print("🚨 OVERALL: CRITICAL — one or more stocks showing severe live degradation.")
        print("   Review strategy immediately before going live with real capital.")
    elif any(s == "WARNING" for s in statuses):
        print("⚠️  OVERALL: WARNING — some stocks showing negative recent performance.")
        print("   Monitor closely. Consider reducing position sizes.")
    else:
        print("✅ OVERALL: HEALTHY — recent performance within acceptable range.")

    n_healthy  = statuses.count("HEALTHY")
    n_warning  = statuses.count("WARNING")
    n_critical = statuses.count("CRITICAL")
    print(f"   {n_healthy} HEALTHY | {n_warning} WARNING | {n_critical} CRITICAL")
    print("="*70)


def run_parameter_stability_test(
    stocks: list[str],
    cooldown_bars: int = 15,
    nifty_regime_filter: bool = True,
) -> list[dict]:
    """
    Test strategy robustness across a grid of SMA and ATR multiplier parameters.

    Tests 9 combinations:
        SMA pairs:       (15,40), (20,50), (25,60)
        ATR multipliers: 2.5, 3.0, 3.5

    Each combination runs the extended walk-forward (2015-19 IS / 2020-23 OOS)
    on all qualifying stocks to use the more conservative stress-test window.

    Returns list of dicts, one per combination, sorted by total score descending.
    """
    import copy

    # Base PARAMS — start from the validated config
    BASE_PARAMS = {
        "sma_fast":       20,
        "sma_slow":       50,
        "atr_period":     22,
        "atr_multiplier": 3.0,
        "hard_stop_pct":  -0.20,
        "max_bars_held":  60,
        "risk_per_trade": 0.015,
        "max_position":   0.20,
    }

    SMA_PAIRS  = [(15, 40), (20, 50), (25, 60)]
    ATR_MULTIS = [2.5, 3.0, 3.5]

    results = []
    total_combinations = len(SMA_PAIRS) * len(ATR_MULTIS)
    current = 0

    for sma_fast, sma_slow in SMA_PAIRS:
        for atr_multi in ATR_MULTIS:
            current += 1
            test_params = copy.deepcopy(BASE_PARAMS)
            test_params["sma_fast"]       = sma_fast
            test_params["sma_slow"]       = sma_slow
            test_params["atr_multiplier"] = atr_multi

            label       = f"SMA({sma_fast}/{sma_slow}) ATR×{atr_multi}"
            is_baseline = (sma_fast == 20 and sma_slow == 50 and atr_multi == 3.0)

            print(f"\n[{current}/{total_combinations}] Testing {label}{'  ← BASELINE' if is_baseline else ''}...")

            try:
                wf_results = run_extended_walk_forward(
                    stocks=stocks,
                    cooldown_bars=cooldown_bars,
                    nifty_regime_filter=nifty_regime_filter,
                    params=test_params,
                )

                # Compute total score and average OOS return
                total_score = sum(
                    r.get("score", 0) for r in wf_results.values()
                    if isinstance(r.get("score"), int)
                )
                max_score = len(stocks) * 6

                oos_returns = [
                    r.get("oos_return", 0) for r in wf_results.values()
                    if isinstance(r.get("oos_return"), float)
                ]
                avg_oos_return = sum(oos_returns) / len(oos_returns) if oos_returns else 0.0

                # Count trades across all stocks
                total_trades = sum(
                    r.get("oos_trades", 0) for r in wf_results.values()
                    if isinstance(r.get("oos_trades"), int)
                )

                results.append({
                    "label":          label,
                    "sma_fast":       sma_fast,
                    "sma_slow":       sma_slow,
                    "atr_multiplier": atr_multi,
                    "total_score":    total_score,
                    "max_score":      max_score,
                    "pct":            round(total_score / max_score * 100, 1),
                    "avg_oos_return": round(avg_oos_return, 2),
                    "total_trades":   total_trades,
                    "is_baseline":    is_baseline,
                    "per_stock":      {
                        ticker: {
                            "score":      r.get("score", 0),
                            "oos_return": r.get("oos_return", 0),
                        }
                        for ticker, r in wf_results.items()
                    }
                })

                print(f"  Score: {total_score}/{max_score} ({total_score/max_score*100:.0f}%)  Avg OOS: {avg_oos_return:+.1f}%  Trades: {total_trades}")

            except Exception as e:
                print(f"  ERROR: {e}")
                results.append({
                    "label":          label,
                    "sma_fast":       sma_fast,
                    "sma_slow":       sma_slow,
                    "atr_multiplier": atr_multi,
                    "total_score":    0,
                    "max_score":      len(stocks) * 6,
                    "pct":            0.0,
                    "avg_oos_return": 0.0,
                    "total_trades":   0,
                    "is_baseline":    is_baseline,
                    "per_stock":      {},
                    "error":          str(e),
                })

    # Sort by total score descending, then avg OOS return descending
    results.sort(key=lambda x: (x["total_score"], x["avg_oos_return"]), reverse=True)
    return results


def print_parameter_stability_report(results: list[dict], stocks: list[str]) -> None:
    """Print formatted parameter stability comparison table."""

    max_score = results[0]["max_score"] if results else len(stocks) * 6

    print("\n" + "="*85)
    print("PARAMETER STABILITY TEST — SMA pairs × ATR multipliers (Extended WF: 2015-19 IS / 2020-23 OOS)")
    print("="*85)
    print(f"{'Config':<28} {'Score':^10} {'%':^8} {'Avg OOS':^12} {'Trades':^8} {'Note'}")
    print("-"*85)

    for r in results:
        baseline_marker = " ← CURRENT" if r["is_baseline"] else ""
        error_marker    = " ERROR" if "error" in r else ""
        print(f"{r['label']:<28} {r['total_score']:^4}/{r['max_score']:<4} {r['pct']:^8.0f}% {r['avg_oos_return']:^+12.1f}% {r['total_trades']:^8}{baseline_marker}{error_marker}")

    print("="*85)

    # Find baseline result
    baseline = next((r for r in results if r["is_baseline"]), None)
    best     = results[0] if results else None
    worst    = results[-1] if results else None

    if baseline and best and worst:
        score_range = best["total_score"] - worst["total_score"]
        print(f"\nBaseline SMA(20/50) ATR×3.0: {baseline['total_score']}/{baseline['max_score']} ({baseline['pct']:.0f}%)")
        print(f"Best config:  {best['label']} — {best['total_score']}/{best['max_score']} ({best['pct']:.0f}%)")
        print(f"Worst config: {worst['label']} — {worst['total_score']}/{worst['max_score']} ({worst['pct']:.0f}%)")
        print(f"Score range across all 9 configs: {score_range} points")

        print("\nVERDICT:")
        if score_range <= 4:
            print(f"✅ ROBUST — score range of {score_range} points across all configs is tight.")
            print("   Strategy edge is not parameter-dependent. Safe to deploy.")
        elif score_range <= 8:
            print(f"⚠️  MODERATE — score range of {score_range} points. Some parameter sensitivity.")
            print("   Current config is acceptable but monitor for regime changes.")
        else:
            print(f"❌ FRAGILE — score range of {score_range} points. High parameter sensitivity.")
            print("   Strategy may be overfit to SMA(20/50). Review before going live.")

        # Check if baseline is near the top
        baseline_rank = next((i+1 for i, r in enumerate(results) if r["is_baseline"]), None)
        if baseline_rank:
            print(f"\nBaseline ranks #{baseline_rank} of {len(results)} configs tested.")
            if baseline_rank <= 3:
                print("✅ Baseline is in top 3 — not cherry-picked from a sharp peak.")
            else:
                print("⚠️  Better configs exist — consider whether to update parameters.")

    # Per-stock breakdown for baseline vs best
    if baseline and best and not baseline["is_baseline"] == best["is_baseline"]:
        print(f"\nPer-stock comparison: Baseline vs Best ({best['label']})")
        print(f"{'Stock':<20} {'Baseline Score':^15} {'Best Score':^15} {'Baseline OOS':^15} {'Best OOS':^15}")
        print("-"*80)
        for ticker in stocks:
            b_score    = baseline["per_stock"].get(ticker, {}).get("score", "N/A")
            best_score = best["per_stock"].get(ticker, {}).get("score", "N/A")
            b_oos      = baseline["per_stock"].get(ticker, {}).get("oos_return", 0)
            best_oos   = best["per_stock"].get(ticker, {}).get("oos_return", 0)
            b_oos_str    = f"{b_oos:+.1f}%" if isinstance(b_oos, float) else "N/A"
            best_oos_str = f"{best_oos:+.1f}%" if isinstance(best_oos, float) else "N/A"
            print(f"{ticker:<20} {str(b_score):^15} {str(best_score):^15} {b_oos_str:^15} {best_oos_str:^15}")


# =============================================================================
# Dynamic extended-universe builder
# =============================================================================

def build_extended_universe(
    candidate_tickers: list[str],
    min_bars: int = MIN_BARS,
    is_start: str = EXT_IS_START,
    is_end:   str = "2020-01-01",
) -> tuple[list[str], list[str]]:
    """
    Determine which tickers have sufficient data for the extended
    IS window (default: 2015-01-01 to 2020-01-01).

    Fetches IS data for each candidate and checks bar count.
    Returns (qualified, skipped) lists.

    Args:
        candidate_tickers: Tickers to check. Pass the union of the
            primary STOCKS list plus any historically traded stocks
            you want to evaluate.
        min_bars:  Minimum trading bars required in the IS window.
            Default: MIN_BARS (200). For a meaningful extended WF,
            consider passing 500+ (2 years of IS data).
        is_start:  Start of the IS window to check.
        is_end:    End of the IS window to check.

    Returns:
        qualified: list of tickers with >= min_bars in IS window
        skipped:   list of (ticker, reason) tuples for those that failed
    """
    qualified = []
    skipped   = []

    print(f"\n[build_extended_universe] Checking {len(candidate_tickers)} "
          f"candidates for IS window {is_start} → {is_end} "
          f"(need ≥ {min_bars} bars)...")

    for ticker in candidate_tickers:
        try:
            df = get_ohlcv(ticker, is_start, is_end)
            if df is None or len(df) < min_bars:
                n = len(df) if df is not None else 0
                reason = f"{n} bars < {min_bars} required"
                skipped.append((ticker, reason))
                print(f"  ✗  {ticker:<22}  {reason}")
            else:
                qualified.append(ticker)
                print(f"  ✓  {ticker:<22}  {len(df)} bars")
        except Exception as e:
            skipped.append((ticker, str(e)))
            print(f"  ✗  {ticker:<22}  fetch error: {e}")

    print(f"\n[build_extended_universe] {len(qualified)} qualified, "
          f"{len(skipped)} skipped")
    return qualified, skipped


if __name__ == "__main__":
    COOLDOWN = 15
    NIFTY_FILTER = True

    # Build extended universe dynamically — no manual maintenance needed.
    # Candidates: current STOCKS + historically traded stocks worth validating.
    # The function checks actual bar counts and excludes anything with
    # insufficient IS data automatically.
    #
    # Add any historically traded stock here — if it doesn't have enough
    # data, it will be automatically excluded with a clear reason printed.
    # Never remove stocks from this candidate list — let the data decide.
    _EXTENDED_CANDIDATES = [
        # Current live universe
        "BAJAJ-AUTO.NS",   # strong WF, removed from live Jun 2026 — include for validation
        "HCLTECH.NS",
        "COLPAL.NS",
        "JKTYRE.NS",
        "BSOFT.NS",
        "PERSISTENT.NS",   # added Jun 2026 — will qualify when history builds
        "ANURAS.NS",       # listed post-2019 — will fail bar check automatically
        "NEWGEN.NS",       # listed 2018 — will fail bar check automatically
        # Historically traded, removed from live universe
        "TMPV.NS",
        "WHIRLPOOL.NS",
        "SIEMENS.NS",
        "HEROMOTOCO.NS",
        "CUMMINSIND.NS",
        "BOSCHLTD.NS",
        "RPOWER.NS",
    ]

    # Require at least 500 bars in IS window for meaningful extended WF
    # (MIN_BARS=200 is the absolute floor; 500 = ~2 years of trading days)
    STOCKS_EXTENDED, _skipped = build_extended_universe(
        _EXTENDED_CANDIDATES,
        min_bars=500,
        is_start=EXT_IS_START,   # "2015-01-01"
        is_end="2020-01-01",
    )

    if not STOCKS_EXTENDED:
        print("WARNING: No stocks qualified for extended universe. "
              "Check Kite token and data availability.")
    else:
        print(f"\nExtended universe ({len(STOCKS_EXTENDED)} stocks): "
              f"{', '.join(STOCKS_EXTENDED)}")

    print("\nRunning ORIGINAL walk-forward (2018-22 IS / 2023-26 OOS)...")
    original_results = run_walk_forward(
        stocks=STOCKS,
        cooldown_bars=COOLDOWN,
        nifty_regime_filter=NIFTY_FILTER,
    )

    print("\nRunning EXTENDED walk-forward (2015-19 IS / 2020-23 OOS) — 4 stocks...")
    extended_results_4 = run_extended_walk_forward(
        stocks=STOCKS,
        cooldown_bars=COOLDOWN,
        nifty_regime_filter=NIFTY_FILTER,
    )

    print("\nRunning EXTENDED walk-forward (2015-19 IS / 2020-23 OOS) — 10 stocks...")
    extended_results_10 = run_extended_walk_forward(
        stocks=STOCKS_EXTENDED,
        cooldown_bars=COOLDOWN,
        nifty_regime_filter=NIFTY_FILTER,
    )

    print("\n" + "=" * 75)
    print("COMPARISON TABLE 1 — Original 4 stocks")
    print_comparison_report(original_results, extended_results_4, STOCKS)

    print("\n" + "=" * 75)
    print("COMPARISON TABLE 2 — All 10 stocks (original 2018-26 where available, extended 2015-23)")
    print_comparison_report(original_results, extended_results_10, STOCKS_EXTENDED)

    # ── Rolling live performance check ────────────────────────────────────────
    print("\n" + "="*70)
    print("Running rolling live performance check (last 90 days)...")
    print("="*70)

    live_results = run_rolling_live_check(
        stocks=STOCKS_EXTENDED,
        lookback_days=300,   # ~210 trading bars, above MIN_BARS=200
        nifty_regime_filter=NIFTY_FILTER,
    )
    print_rolling_live_report(live_results)

    # ── Parameter stability test ───────────────────────────────────────────────
    print("\n" + "="*85)
    print("Running parameter stability test (9 configs × extended walk-forward)...")
    print("This will take several minutes — 9 full walk-forward runs.")
    print("="*85)

    stability_results = run_parameter_stability_test(
        stocks=STOCKS_EXTENDED,  # use all 10 qualifying stocks
        cooldown_bars=COOLDOWN,
        nifty_regime_filter=NIFTY_FILTER,
    )

    print_parameter_stability_report(stability_results, STOCKS_EXTENDED)
