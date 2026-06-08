"""
run_backtest.py — entry point for Phase 1 backtesting.

Usage:
    python run_backtest.py

Tweak the CONFIG block below to change ticker, strategy, date range, or capital.

Supported strategies (CONFIG["strategy"]):
    "sma_crossover"   — uses sma_fast / sma_slow from CONFIG
    "mean_reversion"  — uses PARAMS from strategies/mean_reversion.py (no extra CONFIG keys)

Phase 2 migration path:
    Replace `from data.fetcher import get_ohlcv` with your Kite fetcher.
    Everything below that line stays the same.
"""

import importlib

import matplotlib
matplotlib.use("Agg")   # non-interactive backend (no display needed)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

# ── Data source — Phase 2: live Kite Connect data ────────────────────────────
from data.kite_fetcher import get_ohlcv

# ── Engine (strategy-agnostic) ────────────────────────────────────────────────
from engine.portfolio import Portfolio
from engine.backtester import run, print_summary, print_cooldown_analysis


# =============================================================================
# CONFIG — edit these to experiment
# =============================================================================
CONFIG = {
    "ticker":          "TMPV.NS",         # NSE ticker (append .NS for yfinance)
    "strategy":        "sma_crossover",   # "sma_crossover" | "mean_reversion"
    "start":           "2023-01-01",      # aligned window (see screener/config.py)
    "end":             "2026-01-01",      # aligned window end
    "initial_capital": 100_000,           # virtual capital in ₹

    # Used only when strategy = "sma_crossover"
    "sma_fast": 20,
    "sma_slow": 50,

    # ── Fill mode ─────────────────────────────────────────────────────────────
    # True  = AMO-realistic: signal fires at day N close, fill at day N+1 open.
    # False = legacy: signal and fill both at day N close (pre-AMO behaviour).
    "use_next_day_fills": True,

    # ── Risk management ───────────────────────────────────────────────────────
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

    # ── Cooldown gate ─────────────────────────────────────────────────────────
    # Suppresses BUY signals for N bars after a risk-manager exit.
    # Set "enabled": False to run without cooldown (for A/B comparison).
    "cooldown": {
        "enabled":                True,
        "cooldown_bars":          7,
        "cooldown_after_reasons": ["HARD_STOP", "CHANDELIER", "TIME_STOP"],
        "reset_on_strategy_exit": True,
    },

    # ── Position sizing ───────────────────────────────────────────────────────
    # Fixed-fractional: risk risk_per_trade_pct of portfolio per trade.
    # Stop distance = entry_price − chandelier_stop (fallback: fallback_stop_pct).
    # Set "enabled": False to revert to all-in for A/B comparisons.
    "position_sizing": {
        "enabled":            True,
        "method":             "fixed_fractional",
        "risk_per_trade_pct": 0.015,   # risk 1.5% of portfolio per trade
        "max_position_pct":   0.20,    # single position ≤ 20% of portfolio
        "fallback_stop_pct":  0.20,    # used when Chandelier unavailable (< 23 bars)
    },
}
# =============================================================================


def _load_strategy(name: str):
    """
    Dynamically import a strategy module by name and return its generate_signals
    function. Adding a new strategy requires only a new file in strategies/ with
    a generate_signals(df, ...) function — no other changes needed here.
    """
    supported = {"sma_crossover", "mean_reversion"}
    if name not in supported:
        raise ValueError(f"Unknown strategy '{name}'. Supported: {supported}")
    module = importlib.import_module(f"strategies.{name}")
    return module.generate_signals


def _build_signals(df: pd.DataFrame, strategy: str) -> pd.Series:
    """Dispatch signal generation to the correct strategy with its parameters."""
    generate_signals = _load_strategy(strategy)

    if strategy == "sma_crossover":
        return generate_signals(df, CONFIG["sma_fast"], CONFIG["sma_slow"])
    elif strategy == "mean_reversion":
        # mean_reversion uses its own PARAMS dict; pass no overrides (uses defaults)
        return generate_signals(df)

    raise ValueError(f"Unhandled strategy dispatch for '{strategy}'")


# ── Chart helpers ─────────────────────────────────────────────────────────────

def _add_signal_markers(ax, df: pd.DataFrame, signals: pd.Series):
    """Scatter buy (▲) and sell (▼) markers on a price axis."""
    buy_dates  = signals[signals ==  1].index
    sell_dates = signals[signals == -1].index
    if len(buy_dates):
        ax.scatter(buy_dates,  df.loc[buy_dates,  "close"],
                   marker="^", color="#4CAF50", s=80, zorder=5, label="Buy")
    if len(sell_dates):
        ax.scatter(sell_dates, df.loc[sell_dates, "close"],
                   marker="v", color="#F44336", s=80, zorder=5, label="Sell")


def _plot_equity_and_drawdown(ax_eq, ax_dd, equity_curve: pd.DataFrame):
    """Shared equity-curve and drawdown panels for both strategies."""
    port_idx = equity_curve["portfolio_value"] / equity_curve["portfolio_value"].iloc[0] * 100
    bm_idx   = equity_curve["benchmark_value"]  / equity_curve["benchmark_value"].iloc[0]  * 100

    ax_eq.plot(equity_curve.index, port_idx, color="#2196F3", linewidth=1.2, label="Strategy")
    ax_eq.plot(equity_curve.index, bm_idx,   color="#9E9E9E", linewidth=1.0,
               linestyle="--", label="Buy & Hold")
    ax_eq.axhline(100, color="black", linewidth=0.5, linestyle=":")
    ax_eq.set_ylabel("Value (indexed to 100)")
    ax_eq.legend(loc="upper left", fontsize=8)
    ax_eq.grid(True, alpha=0.3)

    ax_dd.fill_between(equity_curve.index, equity_curve["drawdown"], 0,
                       color="#F44336", alpha=0.4, label="Drawdown")
    ax_dd.set_ylabel("Drawdown (%)")
    ax_dd.set_xlabel("Date")
    ax_dd.legend(loc="lower left", fontsize=8)
    ax_dd.grid(True, alpha=0.3)
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_dd.xaxis.set_major_locator(mdates.YearLocator())
    plt.xticks(rotation=45)


def plot_results(
    df: pd.DataFrame,
    signals: pd.Series,
    equity_curve: pd.DataFrame,
    ticker: str,
    strategy: str,
):
    """
    Save a multi-panel chart to disk.

    sma_crossover  → 3 panels: price+SMAs, equity curve, drawdown
    mean_reversion → 4 panels: price+BB bands, RSI with thresholds, equity curve, drawdown
    """
    if strategy == "sma_crossover":
        _plot_sma(df, signals, equity_curve, ticker)
    else:
        _plot_mean_reversion(df, signals, equity_curve, ticker)


def _plot_sma(df, signals, equity_curve, ticker):
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True)
    fig.suptitle(f"SMA Crossover Backtest — {ticker}", fontsize=14, fontweight="bold")

    ax1 = axes[0]
    fast_sma = df["close"].rolling(CONFIG["sma_fast"]).mean()
    slow_sma = df["close"].rolling(CONFIG["sma_slow"]).mean()
    ax1.plot(df.index, df["close"], color="#555555", linewidth=0.8, label="Close", alpha=0.7)
    ax1.plot(df.index, fast_sma, color="#2196F3", linewidth=1.2,
             label=f"SMA {CONFIG['sma_fast']}")
    ax1.plot(df.index, slow_sma, color="#FF5722", linewidth=1.2,
             label=f"SMA {CONFIG['sma_slow']}")
    _add_signal_markers(ax1, df, signals)
    ax1.set_ylabel("Price (₹)")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(True, alpha=0.3)

    _plot_equity_and_drawdown(axes[1], axes[2], equity_curve)
    plt.tight_layout()
    out = f"backtest_{ticker.replace('.', '_')}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n[chart] Saved to {out}")
    plt.close()


def _plot_mean_reversion(df, signals, equity_curve, ticker):
    """4-panel chart: price + Bollinger Bands, RSI, equity curve, drawdown."""
    from strategies.mean_reversion import (
        _compute_rsi, _compute_bollinger_bands, PARAMS
    )

    fig, axes = plt.subplots(4, 1, figsize=(14, 16), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.5, 2, 1.2]})
    fig.suptitle(f"RSI + Bollinger Band Mean Reversion — {ticker}",
                 fontsize=14, fontweight="bold")

    # ── Panel 1: Price + Bollinger Bands ──────────────────────────────────
    ax1 = axes[0]
    bb_upper, bb_mid, bb_lower = _compute_bollinger_bands(
        df["close"], PARAMS["bb_period"], PARAMS["bb_std_dev"]
    )
    ax1.plot(df.index, df["close"], color="#555555", linewidth=0.9,
             label="Close", alpha=0.8)
    ax1.plot(df.index, bb_mid,   color="#2196F3", linewidth=1.0,
             label=f"BB mid ({PARAMS['bb_period']}d SMA)", linestyle="--")
    ax1.plot(df.index, bb_upper, color="#FF9800", linewidth=1.0,
             label=f"BB upper (+{PARAMS['bb_std_dev']}σ)")
    ax1.plot(df.index, bb_lower, color="#FF9800", linewidth=1.0,
             label=f"BB lower (−{PARAMS['bb_std_dev']}σ)")
    ax1.fill_between(df.index, bb_lower, bb_upper, alpha=0.06, color="#FF9800")
    _add_signal_markers(ax1, df, signals)
    ax1.set_ylabel("Price (₹)")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # ── Panel 2: RSI ──────────────────────────────────────────────────────
    ax2 = axes[1]
    rsi = _compute_rsi(df["close"], PARAMS["rsi_period"])
    ax2.plot(df.index, rsi, color="#9C27B0", linewidth=1.0, label="RSI")
    ax2.axhline(PARAMS["rsi_overbought"], color="#F44336", linewidth=0.8,
                linestyle="--", label=f"Overbought ({PARAMS['rsi_overbought']})")
    ax2.axhline(PARAMS["rsi_oversold"],   color="#4CAF50", linewidth=0.8,
                linestyle="--", label=f"Oversold ({PARAMS['rsi_oversold']})")
    ax2.fill_between(df.index, rsi, PARAMS["rsi_oversold"],
                     where=(rsi < PARAMS["rsi_oversold"]),
                     alpha=0.3, color="#4CAF50", label="Oversold zone")
    ax2.fill_between(df.index, rsi, PARAMS["rsi_overbought"],
                     where=(rsi > PARAMS["rsi_overbought"]),
                     alpha=0.3, color="#F44336", label="Overbought zone")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("RSI")
    ax2.legend(loc="upper left", fontsize=7, ncol=2)
    ax2.grid(True, alpha=0.3)

    _plot_equity_and_drawdown(axes[2], axes[3], equity_curve)
    plt.tight_layout()
    out = f"backtest_{ticker.replace('.', '_')}_mr.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n[chart] Saved to {out}")
    plt.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def _make_risk_manager(rm_cfg: dict):
    """Instantiate RiskManager from config dict, or return None if disabled."""
    if not rm_cfg.get("enabled", False):
        return None
    from engine.risk_manager import RiskManager
    return RiskManager(rm_cfg)


def _make_cooldown_tracker(ct_cfg: dict):
    """Instantiate CooldownTracker from config dict (always returns an instance)."""
    from engine.cooldown import CooldownTracker
    return CooldownTracker(ct_cfg)


def _make_position_sizer(ps_cfg: dict | None):
    """Return a PositionSizer or None (None → backtester uses all-in path)."""
    if not ps_cfg or not ps_cfg.get("enabled", False):
        return None
    from engine.position_sizer import PositionSizer
    return PositionSizer(ps_cfg)


def _run_single(
    label: str,
    df, signals,
    rm_cfg: dict,
    ct_cfg: dict,
    ticker: str,
    strategy: str,
    ps_cfg: dict | None = None,
    use_next_day_fills: bool = True,
):
    """
    Run one labelled backtest variant and print all output.
    Returns (equity_curve, portfolio, cooldown_tracker, position_sizer).
    position_sizer is None when ps_cfg is None or disabled.
    """
    print(f"\n{'#'*70}")
    print(f"  {label}")
    print(f"{'#'*70}")

    risk_manager     = _make_risk_manager(rm_cfg)
    cooldown_tracker = _make_cooldown_tracker(ct_cfg)
    position_sizer   = _make_position_sizer(ps_cfg)
    portfolio        = Portfolio(CONFIG["initial_capital"])

    equity_curve = run(
        df, signals, portfolio,
        risk_manager=risk_manager,
        cooldown_tracker=cooldown_tracker,
        position_sizer=position_sizer,
        use_next_day_fills=use_next_day_fills,
    )
    print_summary(equity_curve, portfolio)

    if ct_cfg.get("enabled", False):
        print_cooldown_analysis(cooldown_tracker, df, ticker)

    plot_results(df, signals, equity_curve, ticker, strategy)
    return equity_curve, portfolio, cooldown_tracker, position_sizer


def validate_cooldown():
    """
    Run all 4 validation backtests on TMPV and print a comparison table.

    Run A — Baseline (no RM, no cooldown)
    Run B — RM on, cooldown OFF  (reproduces the re-entry bug)
    Run C — RM on, cooldown 7 bars  (proposed fix)
    Run D — RM on, cooldown 15 bars  (more conservative)
    """
    ticker   = CONFIG["ticker"]
    strategy = CONFIG["strategy"]

    df      = get_ohlcv(ticker, CONFIG["start"], CONFIG["end"])
    signals = _build_signals(df, strategy)

    rm_full = CONFIG["risk_management"]
    rm_off  = {**rm_full, "enabled": False}
    ct_off  = {"enabled": False, "cooldown_bars": 0,
               "cooldown_after_reasons": [], "reset_on_strategy_exit": True}
    ct_7    = {**CONFIG["cooldown"], "cooldown_bars": 7}
    ct_15   = {**CONFIG["cooldown"], "cooldown_bars": 15}

    runs = [
        ("Run A — Baseline (no RM, no cooldown)",          rm_off,  ct_off),
        ("Run B — RM on, cooldown OFF (re-entry bug)",     rm_full, ct_off),
        ("Run C — RM on, cooldown 7-bar  (proposed fix)",  rm_full, ct_7),
        ("Run D — RM on, cooldown 15-bar (conservative)",  rm_full, ct_15),
    ]

    results = []
    for label, rm_cfg, ct_cfg in runs:
        eq, port, ct, _ = _run_single(label, df, signals, rm_cfg, ct_cfg, ticker, strategy)
        results.append((label, eq, port, ct))

    # ── Master comparison table ───────────────────────────────────────────────
    _print_comparison_table(results)


def _print_comparison_table(results: list) -> None:
    """Side-by-side metrics across all validation runs."""
    print(f"\n{'='*90}")
    print("  MASTER COMPARISON TABLE")
    print(f"{'='*90}")

    labels = [r[0].split("—")[0].strip() for r in results]
    w = 18

    def row(label, values):
        print(f"  {label:<26}" + "".join(f"{str(v):>{w}}" for v in values))

    row("", [f"[{l}]" for l in labels])
    print("  " + "-"*88)

    def metric(label, fn):
        row(label, [fn(r[1], r[2], r[3]) for r in results])

    metric("Total return",       lambda eq, p, ct: f"{(eq['portfolio_value'].iloc[-1]/p.initial_capital-1)*100:>+.2f}%")
    metric("Buy & hold",         lambda eq, p, ct: f"{(eq['benchmark_value'].iloc[-1]/eq['benchmark_value'].iloc[0]-1)*100:>+.2f}%")
    metric("Max drawdown",       lambda eq, p, ct: f"{eq['drawdown'].min():.2f}%")

    def _n_trades(eq, p, ct):
        t = p.get_trade_log()
        return str(len(t)) if not t.empty else "0"

    def _win_rate(eq, p, ct):
        t = p.get_trade_log()
        if t.empty: return "—"
        n = len(t); w = (t["net_pnl"]>0).sum()
        return f"{w/n*100:.1f}% ({w}W/{n-w}L)"

    def _payoff(eq, p, ct):
        t = p.get_trade_log()
        if t.empty: return "—"
        aw = t.loc[t["net_pnl"]>0, "net_pnl"].mean() if (t["net_pnl"]>0).any() else 0
        al = t.loc[t["net_pnl"]<=0,"net_pnl"].mean() if (t["net_pnl"]<=0).any() else 0
        return f"{abs(aw/al):.2f}:1" if al != 0 else "∞"

    def _expectancy(eq, p, ct):
        t = p.get_trade_log()
        if t.empty: return "—"
        n = len(t); wr = (t["net_pnl"]>0).sum()/n
        aw = t.loc[t["net_pnl"]>0, "net_pnl"].mean() if (t["net_pnl"]>0).any() else 0
        al = t.loc[t["net_pnl"]<=0,"net_pnl"].mean() if (t["net_pnl"]<=0).any() else 0
        return f"₹{wr*aw+(1-wr)*al:>+,.0f}"

    def _cooldown(eq, p, ct):
        if ct is None or not ct.enabled: return "—"
        return f"{len(ct.cooldown_history)} triggers / {len(ct.suppressed_signals)} suppressed"

    metric("Trades",             lambda eq, p, ct: _n_trades(eq, p, ct))
    metric("Win rate",           lambda eq, p, ct: _win_rate(eq, p, ct))
    metric("Payoff ratio",       lambda eq, p, ct: _payoff(eq, p, ct))
    metric("Expectancy/trade",   lambda eq, p, ct: _expectancy(eq, p, ct))
    metric("Cooldown activity",  lambda eq, p, ct: _cooldown(eq, p, ct))

    print(f"{'='*90}")

    # ── Oct 2025 crash analysis ───────────────────────────────────────────────
    print("\n  OCT 2025 CRASH ANALYSIS (trade that entered ~₹715, crashed to ₹395)")
    print("  " + "-"*70)
    for label, eq, port, ct in results:
        trades = port.get_trade_log()
        if trades.empty:
            print(f"  {label.split('—')[0].strip():<10}: no trades")
            continue
        # Find the last trade that was related to the ~Sep 2025 entry
        crash_trades = trades[trades["entry_price"].between(600, 800)]
        if crash_trades.empty:
            print(f"  {label.split('—')[0].strip():<10}: no crash-window trades")
        else:
            last = crash_trades.iloc[-1]
            reason = last.get("exit_reason", "?") if "exit_reason" in last else "?"
            print(
                f"  {label.split('—')[0].strip():<10}: entered ₹{last['entry_price']:.0f}"
                f" → exited ₹{last['exit_price']:.0f}"
                f"  P&L ₹{last['net_pnl']:>+,.0f} ({last['return_pct']:>+.1f}%)"
                f"  [{reason}]"
            )
    print(f"{'='*90}")


def validate_position_sizing():
    """
    Run A/B/C validation to measure the impact of fixed-fractional sizing.

    Run A — all-in baseline (sizing OFF) — should match Task 1 Kite results
    Run B — fixed-fractional, 1.5% risk per trade
    Run C — fixed-fractional, 1.0% risk per trade (more conservative)

    All three: TMPV.NS, SMA 20/50, 2023-01-01 → 2026-01-01, ₹100 000 capital,
    risk management ON (all 4 layers), 15-bar cooldown ON.
    """
    ticker   = CONFIG["ticker"]
    strategy = CONFIG["strategy"]

    df      = get_ohlcv(ticker, CONFIG["start"], CONFIG["end"])
    signals = _build_signals(df, strategy)

    rm_cfg = CONFIG["risk_management"]
    ct_cfg = {**CONFIG["cooldown"], "cooldown_bars": 15}   # validated 15-bar window

    ps_off = {"enabled": False}
    ps_b   = {"enabled": True, "method": "fixed_fractional",
               "risk_per_trade_pct": 0.015, "max_position_pct": 0.20, "fallback_stop_pct": 0.20}
    ps_c   = {"enabled": True, "method": "fixed_fractional",
               "risk_per_trade_pct": 0.010, "max_position_pct": 0.20, "fallback_stop_pct": 0.20}

    runs = [
        ("Run A — All-in baseline (sizing OFF)",        rm_cfg, ct_cfg, ps_off),
        ("Run B — Fixed-fractional, 1.5% risk/trade",  rm_cfg, ct_cfg, ps_b),
        ("Run C — Fixed-fractional, 1.0% risk/trade",  rm_cfg, ct_cfg, ps_c),
    ]

    results = []
    for label, rm, ct, ps in runs:
        eq, port, ct_tracker, ps_inst = _run_single(
            label, df, signals, rm, ct, ticker, strategy, ps_cfg=ps
        )
        results.append((label, eq, port, ct_tracker, ps_inst))

    _print_position_sizing_comparison(results)


def _print_position_sizing_comparison(results: list) -> None:
    """Master comparison table for the A/B/C position sizing validation."""
    print(f"\n{'='*92}")
    print("  POSITION SIZING — MASTER COMPARISON TABLE")
    print(f"{'='*92}")

    labels = [r[0].split("—")[0].strip() for r in results]
    w = 22

    def row(label, values):
        print(f"  {label:<26}" + "".join(f"{str(v):>{w}}" for v in values))

    row("", [f"[{l}]" for l in labels])
    print("  " + "-"*90)

    def _total_return(eq, p, ct, ps):
        return f"{(eq['portfolio_value'].iloc[-1]/p.initial_capital-1)*100:>+.2f}%"

    def _bnh(eq, p, ct, ps):
        return f"{(eq['benchmark_value'].iloc[-1]/eq['benchmark_value'].iloc[0]-1)*100:>+.2f}%"

    def _max_dd(eq, p, ct, ps):
        return f"{eq['drawdown'].min():.2f}%"

    def _final_val(eq, p, ct, ps):
        return f"₹{eq['portfolio_value'].iloc[-1]:>,.0f}"

    def _n_trades(eq, p, ct, ps):
        t = p.get_trade_log()
        return str(len(t)) if not t.empty else "0"

    def _win_rate(eq, p, ct, ps):
        t = p.get_trade_log()
        if t.empty: return "—"
        n = len(t); nw = (t["net_pnl"] > 0).sum()
        return f"{nw/n*100:.1f}% ({nw}W/{n-nw}L)"

    def _avg_win(eq, p, ct, ps):
        t = p.get_trade_log()
        if t.empty or not (t["net_pnl"] > 0).any(): return "—"
        return f"₹{t.loc[t['net_pnl']>0,'net_pnl'].mean():>+,.0f}"

    def _avg_loss(eq, p, ct, ps):
        t = p.get_trade_log()
        if t.empty or not (t["net_pnl"] <= 0).any(): return "—"
        return f"₹{t.loc[t['net_pnl']<=0,'net_pnl'].mean():>+,.0f}"

    def _payoff(eq, p, ct, ps):
        t = p.get_trade_log()
        if t.empty: return "—"
        aw = t.loc[t["net_pnl"]>0,  "net_pnl"].mean() if (t["net_pnl"]>0).any()  else 0
        al = t.loc[t["net_pnl"]<=0, "net_pnl"].mean() if (t["net_pnl"]<=0).any() else 0
        return f"{abs(aw/al):.2f}:1" if al != 0 else "∞"

    def _expectancy(eq, p, ct, ps):
        t = p.get_trade_log()
        if t.empty: return "—"
        n = len(t); wr = (t["net_pnl"] > 0).sum() / n
        aw = t.loc[t["net_pnl"]>0,  "net_pnl"].mean() if (t["net_pnl"]>0).any()  else 0
        al = t.loc[t["net_pnl"]<=0, "net_pnl"].mean() if (t["net_pnl"]<=0).any() else 0
        return f"₹{wr*aw + (1-wr)*al:>+,.0f}"

    def _avg_shares(eq, p, ct, ps):
        t = p.get_trade_log()
        if t.empty: return "—"
        return f"{t['shares'].mean():.1f} shr"

    def _skipped(eq, p, ct, ps):
        return str(ps.skipped_count) if ps is not None else "—"

    for label_str, fn in [
        ("Total return",      _total_return),
        ("Buy & hold",        _bnh),
        ("Max drawdown",      _max_dd),
        ("Final value",       _final_val),
        ("Trades",            _n_trades),
        ("Win rate",          _win_rate),
        ("Avg winner",        _avg_win),
        ("Avg loser",         _avg_loss),
        ("Payoff ratio",      _payoff),
        ("Expectancy/trade",  _expectancy),
        ("Avg shares/trade",  _avg_shares),
        ("Skipped (sizing)",  _skipped),
    ]:
        row(label_str, [fn(r[1], r[2], r[3], r[4]) for r in results])

    print(f"{'='*92}")

    # ── Answer the 4 key questions ────────────────────────────────────────────
    print("\n  KEY QUESTIONS")
    print("  " + "-"*70)

    dd_a = results[0][1]["drawdown"].min()
    dd_b = results[1][1]["drawdown"].min()
    dd_c = results[2][1]["drawdown"].min()
    q1   = "YES" if (dd_b > dd_a or dd_c > dd_a) else "NO — sizing increased drawdown"
    print(f"\n  Q1. Did sizing reduce max drawdown vs all-in?")
    print(f"      Run A: {dd_a:.2f}%  |  Run B: {dd_b:.2f}%  |  Run C: {dd_c:.2f}%  →  {q1}")

    def _exp_val(port):
        t = port.get_trade_log()
        if t.empty: return 0.0
        n = len(t); wr = (t["net_pnl"] > 0).sum() / n
        aw = t.loc[t["net_pnl"]>0,  "net_pnl"].mean() if (t["net_pnl"]>0).any()  else 0.0
        al = t.loc[t["net_pnl"]<=0, "net_pnl"].mean() if (t["net_pnl"]<=0).any() else 0.0
        return wr * aw + (1 - wr) * al

    exp_b = _exp_val(results[1][2])
    exp_c = _exp_val(results[2][2])
    print(f"\n  Q2. Did sizing preserve positive expectancy?")
    print(f"      Run B (1.5%): {'YES' if exp_b > 0 else 'NO'}  ₹{exp_b:>+,.0f}/trade")
    print(f"      Run C (1.0%): {'YES' if exp_c > 0 else 'NO'}  ₹{exp_c:>+,.0f}/trade")

    cap = CONFIG["initial_capital"]
    print(f"\n  Q3. Rupee risk per trade (starting capital ₹{cap:,}):")
    print(f"      Run B (1.5%): ₹{cap*0.015:>6,.0f} at entry, scales with portfolio value")
    print(f"      Run C (1.0%): ₹{cap*0.010:>6,.0f} at entry, scales with portfolio value")

    t_a = results[0][2].get_trade_log()
    t_b = results[1][2].get_trade_log()
    t_c = results[2][2].get_trade_log()
    avg_a = t_a["shares"].mean() if not t_a.empty else 0
    avg_b = t_b["shares"].mean() if not t_b.empty else 0
    avg_c = t_c["shares"].mean() if not t_c.empty else 0
    print(f"\n  Q4. Average shares per trade vs all-in:")
    print(f"      Run A (all-in): {avg_a:.1f} shares")
    if avg_a > 0:
        print(f"      Run B (1.5%):   {avg_b:.1f} shares  ({avg_b/avg_a*100:.1f}% of all-in)")
        print(f"      Run C (1.0%):   {avg_c:.1f} shares  ({avg_c/avg_a*100:.1f}% of all-in)")
    else:
        print(f"      Run B (1.5%):   {avg_b:.1f} shares")
        print(f"      Run C (1.0%):   {avg_c:.1f} shares")

    print(f"\n{'='*92}")


def main():
    """Single-run mode — uses CONFIG as-is."""
    ticker   = CONFIG["ticker"]
    strategy = CONFIG["strategy"]

    df      = get_ohlcv(ticker, CONFIG["start"], CONFIG["end"])
    signals = _build_signals(df, strategy)

    risk_manager     = _make_risk_manager(CONFIG.get("risk_management", {}))
    cooldown_tracker = _make_cooldown_tracker(CONFIG.get("cooldown", {"enabled": False}))
    position_sizer   = _make_position_sizer(CONFIG.get("position_sizing"))

    portfolio    = Portfolio(CONFIG["initial_capital"])
    equity_curve = run(df, signals, portfolio,
                       risk_manager=risk_manager,
                       cooldown_tracker=cooldown_tracker,
                       position_sizer=position_sizer,
                       use_next_day_fills=CONFIG.get("use_next_day_fills", True))

    print_summary(equity_curve, portfolio)

    if cooldown_tracker.enabled:
        print_cooldown_analysis(cooldown_tracker, df, ticker)

    plot_results(df, signals, equity_curve, ticker, strategy)


if __name__ == "__main__":
    validate_position_sizing()
