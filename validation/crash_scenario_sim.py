"""
validation/crash_scenario_sim.py — COVID crash stress test for D_aggressive ETF overlay.

Simulates how the D_aggressive tier config behaves during the 2020 COVID crash
(Jan 2019 – Dec 2020), specifically the ~38% NIFTY fall in Feb–Mar 2020.

DATA SOURCE NOTE:
    Kite Connect historical_data() is limited to ~2000 daily candles (~3 years).
    From June 2026, that window opens only to ~June 2023 — well short of 2019.
    yfinance (data/fetcher.py) has no such limit; it is the same source used by
    all multi-year backtests in this project. Token/auth files are NOT needed here.
    Tickers: NIFTYBEES.NS (Kite token 1346049), ^NSEI (Kite token 256265 / NIFTY 50).

D_aggressive tier config (from etf_tier_grid_search results):
    0 positions  → 100% NIFTYBEES ETF
    1-2 positions →  80% NIFTYBEES ETF
    3 positions  →  50% NIFTYBEES ETF
    4+ positions →   0% NIFTYBEES ETF

Position count: simulated from NIFTY 50 SMA20/SMA50 trend signal since no
real position log exists for 2019-2020. Bull regime → 3-4 positions;
bear regime → 0-1 positions. Smoothed to approximate realistic holding periods.

Crash window: 2020-02-17 → 2020-03-25 (peak to confirmed trough).

Usage:
    python validation/crash_scenario_sim.py
"""

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — no display required
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.fetcher import get_ohlcv  # yfinance — required for pre-2023 data


# =============================================================================
# Configuration
# =============================================================================

START           = "2019-01-01"
END             = "2021-01-01"    # exclusive (yfinance convention)
CRASH_START     = "2020-02-17"
CRASH_END       = "2020-03-25"
INITIAL_CAPITAL = 100.0           # normalised starting value

NIFTYBEES_TICKER = "NIFTYBEES.NS"
NIFTY_TICKER     = "^NSEI"        # NIFTY 50 index on yfinance

MAX_POSITIONS = 4

D_AGGRESSIVE = {0: 1.00, 1: 0.80, 2: 0.80, 3: 0.50, 4: 0.00}

PLOT_PATH   = _ROOT / "validation" / "crash_scenario_plot.png"
RESULT_PATH = _ROOT / "validation" / "crash_scenario_result.json"


# =============================================================================
# Position count simulation
# =============================================================================

def simulate_position_counts(
    nifty_close: pd.Series,
    max_positions: int = MAX_POSITIONS,
    seed: int = 42,
) -> pd.Series:
    """
    Synthesise a realistic daily position count from NIFTY 50 trend regime.

    Logic (mirrors how the SMA 20/50 strategy actually behaves):
      Strong bull  (SMA20/SMA50 gap > +1.5%): base 3.5 positions, noise ±1
      Mild bull    (gap 0% to +1.5%):          base 2.5 positions, noise ±1
      Mild bear    (gap -1.5% to 0%):           base 1.0 position,  noise ±0.8
      Strong bear  (gap < -1.5%):               base 0.3 positions, noise ±0.5

    A 5-day rolling mean then smooths out day-to-day noise, simulating
    positions that persist for at least a week (real positions last 1-3 months,
    but 5d is sufficient for visual realism in this crash window).
    """
    rng   = np.random.default_rng(seed)
    sma20 = nifty_close.rolling(20).mean()
    sma50 = nifty_close.rolling(50).mean()
    gap   = (sma20 - sma50) / sma50 * 100.0

    raw = []
    for i in range(len(nifty_close)):
        g = gap.iloc[i]
        if pd.isna(g):
            raw.append(2)
            continue
        if g > 1.5:
            base, sigma = 3.5, 0.8
        elif g > 0.0:
            base, sigma = 2.5, 0.8
        elif g > -1.5:
            base, sigma = 1.0, 0.7
        else:
            base, sigma = 0.3, 0.5

        count = int(np.clip(round(base + rng.normal(0, sigma)), 0, max_positions))
        raw.append(count)

    raw_s    = pd.Series(raw, index=nifty_close.index, name="pos_count")
    smoothed = raw_s.rolling(5, min_periods=1).mean().round().astype(int).clip(0, max_positions)
    return smoothed


# =============================================================================
# Portfolio simulation
# =============================================================================

def simulate_overlay(
    niftybees_close: pd.Series,
    pos_counts: pd.Series,
    tiers: dict,
    initial: float = INITIAL_CAPITAL,
) -> pd.Series:
    """
    Blended portfolio equity curve: ETF portion earns NIFTYBEES return;
    stock/cash portion earns 0% (stock-side assumed flat to isolate overlay).

    ETF exposure each day = tiers[min(pos_count, 4)] × 100%
    """
    nb_ret = niftybees_close.pct_change().fillna(0.0)
    capital = initial
    values  = [initial]

    for i in range(1, len(niftybees_close)):
        tier_frac  = tiers.get(min(int(pos_counts.iloc[i]), 4), 0.0)
        daily_ret  = float(nb_ret.iloc[i])
        capital   *= (1.0 + tier_frac * daily_ret)
        values.append(capital)

    return pd.Series(values, index=niftybees_close.index, name="overlay")


def simulate_buyandhold(
    niftybees_close: pd.Series,
    initial: float = INITIAL_CAPITAL,
) -> pd.Series:
    """100% NIFTYBEES at all times."""
    return (initial * niftybees_close / niftybees_close.iloc[0]).rename("bah")


# =============================================================================
# Crash window metrics
# =============================================================================

def compute_crash_metrics(
    equity: pd.Series,
    label: str,
    crash_start: str = CRASH_START,
    crash_end:   str = CRASH_END,
) -> dict:
    """
    Compute peak-to-trough drawdown, recovery date, and max single-day loss
    for a given equity curve around the crash window.
    """
    cs = pd.Timestamp(crash_start)
    ce = pd.Timestamp(crash_end)

    # Pre-crash peak: max value in the period BEFORE the crash window
    pre_crash   = equity[equity.index < cs]
    peak_val    = float(pre_crash.max())
    peak_date   = pre_crash.idxmax().date().isoformat()

    # Crash trough: minimum within the declared crash window
    in_crash    = equity[(equity.index >= cs) & (equity.index <= ce)]
    trough_val  = float(in_crash.min())
    trough_date = in_crash.idxmin().date().isoformat()

    # Peak-to-trough drawdown (%)
    ptd_pct = (trough_val / peak_val - 1.0) * 100.0

    # Recovery: first date after the trough where value ≥ pre-crash peak
    post_trough    = equity[equity.index > in_crash.idxmin()]
    recovered      = post_trough[post_trough >= peak_val]
    if not recovered.empty:
        recovery_date = recovered.index[0].date().isoformat()
    else:
        last_date     = equity.index[-1].date().isoformat()
        recovery_date = f"Not recovered by {last_date}"

    # Max single-day loss (%) inside crash window
    daily_rets       = equity.pct_change()
    crash_rets       = daily_rets[(daily_rets.index >= cs) & (daily_rets.index <= ce)]
    max_loss_pct     = float(crash_rets.min() * 100.0)
    max_loss_date    = crash_rets.idxmin().date().isoformat()

    return {
        "label":                   label,
        "pre_crash_peak_value":    round(peak_val,    3),
        "pre_crash_peak_date":     peak_date,
        "crash_trough_value":      round(trough_val,  3),
        "crash_trough_date":       trough_date,
        "peak_to_trough_drawdown_pct": round(ptd_pct, 2),
        "recovery_date":           recovery_date,
        "max_single_day_loss_pct": round(max_loss_pct, 2),
        "max_single_day_loss_date": max_loss_date,
    }


# =============================================================================
# Plotting
# =============================================================================

def plot_crash_scenario(
    bah_curve:     pd.Series,
    overlay_curve: pd.Series,
    pos_counts:    pd.Series,
    etf_exposure:  pd.Series,
    nifty_close:   pd.Series,
    crash_start:   str = CRASH_START,
    crash_end:     str = CRASH_END,
    save_path:     Path = PLOT_PATH,
) -> None:
    cs = pd.Timestamp(crash_start)
    ce = pd.Timestamp(crash_end)

    fig = plt.figure(figsize=(14, 11), facecolor="white")
    gs  = GridSpec(3, 1, figure=fig, height_ratios=[5, 2, 2], hspace=0.40)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)

    crash_kwargs = dict(alpha=0.13, color="crimson", zorder=0)

    # ── Panel 1: Equity curves ────────────────────────────────────────────────
    ax1.plot(bah_curve.index, bah_curve.values,     color="#e74c3c", lw=1.8,
             label="NIFTYBEES Buy & Hold (100%)")
    ax1.plot(overlay_curve.index, overlay_curve.values, color="#27ae60", lw=1.8,
             label="D_aggressive Overlay")
    ax1.axhline(INITIAL_CAPITAL, color="grey", lw=0.6, ls="--", alpha=0.5)
    ax1.axvspan(cs, ce, **crash_kwargs, label=f"COVID Crash Window ({crash_start} → {crash_end})")

    # Annotate the trough dates
    bah_trough_date  = bah_curve[(bah_curve.index >= cs) & (bah_curve.index <= ce)].idxmin()
    ov_trough_date   = overlay_curve[(overlay_curve.index >= cs) & (overlay_curve.index <= ce)].idxmin()
    ax1.annotate(
        f"B&H trough\n{bah_trough_date.date()}\n{bah_curve[bah_trough_date]:.1f}",
        xy=(bah_trough_date, bah_curve[bah_trough_date]),
        xytext=(30, -30), textcoords="offset points",
        fontsize=8, color="#e74c3c",
        arrowprops=dict(arrowstyle="->", color="#e74c3c", lw=0.8),
    )
    ax1.annotate(
        f"Overlay trough\n{ov_trough_date.date()}\n{overlay_curve[ov_trough_date]:.1f}",
        xy=(ov_trough_date, overlay_curve[ov_trough_date]),
        xytext=(-80, -40), textcoords="offset points",
        fontsize=8, color="#27ae60",
        arrowprops=dict(arrowstyle="->", color="#27ae60", lw=0.8),
    )

    ax1.set_ylabel("Portfolio Value (start = 100)", fontsize=10)
    ax1.set_title(
        "COVID Crash Stress Test — D_aggressive ETF Overlay\n"
        "Position-count simulated from NIFTY 50 SMA20/SMA50 regime",
        fontsize=12, fontweight="bold",
    )
    ax1.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax1.grid(True, alpha=0.25)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}"))

    # ── Panel 2: ETF exposure % ───────────────────────────────────────────────
    ax2.fill_between(etf_exposure.index, etf_exposure.values,
                     alpha=0.55, color="#2980b9", step="mid",
                     label="ETF Exposure %")
    ax2.axvspan(cs, ce, **crash_kwargs)
    ax2.set_ylim(-5, 115)
    ax2.set_yticks([0, 20, 50, 80, 100])
    ax2.set_ylabel("ETF Exposure %", fontsize=10)
    ax2.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax2.grid(True, alpha=0.25)
    # Reference lines at tier thresholds
    for pct, lbl in [(100, "Tier 0"), (80, "Tier 1-2"), (50, "Tier 3"), (0, "Tier 4")]:
        ax2.axhline(pct, color="grey", lw=0.5, ls=":", alpha=0.5)

    # ── Panel 3: Position count ───────────────────────────────────────────────
    ax3.fill_between(pos_counts.index, pos_counts.values,
                     alpha=0.55, color="#8e44ad", step="mid",
                     label="Simulated Position Count")
    ax3.axvspan(cs, ce, **crash_kwargs)
    ax3.set_ylim(-0.3, 4.8)
    ax3.set_yticks([0, 1, 2, 3, 4])
    ax3.set_ylabel("Open Positions", fontsize=10)
    ax3.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax3.grid(True, alpha=0.25)

    # ── X-axis formatting (only bottom panel) ─────────────────────────────────
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=9)
    plt.setp(ax1.get_xticklabels(), visible=False)
    plt.setp(ax2.get_xticklabels(), visible=False)

    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  Plot saved → {save_path}")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    today = date.today().isoformat()
    print(f"\n{'='*65}")
    print(f"  COVID Crash Scenario Simulation — D_aggressive ETF Overlay")
    print(f"  Period : {START} → {END}")
    print(f"  Crash  : {CRASH_START} → {CRASH_END}")
    print(f"  Tiers  : {D_AGGRESSIVE}")
    print(f"  Note   : Using yfinance (Kite historical API limited to ~3 yrs)")
    print(f"{'='*65}")

    # ── Step 1: Fetch data ────────────────────────────────────────────────────
    print(f"\n[1/5] Fetching NIFTYBEES data ({NIFTYBEES_TICKER}, yfinance)...")
    nb_df = get_ohlcv(NIFTYBEES_TICKER, START, END)

    print(f"\n[1/5] Fetching NIFTY 50 index data ({NIFTY_TICKER}, yfinance)...")
    nifty_df = get_ohlcv(NIFTY_TICKER, START, END)

    # Align on common trading days
    common_idx  = nb_df.index.intersection(nifty_df.index)
    nb_close    = nb_df["close"].reindex(common_idx)
    nifty_close = nifty_df["close"].reindex(common_idx)
    print(f"  Aligned: {len(common_idx)} common trading days")
    print(f"  NIFTYBEES range: {nb_close.iloc[0]:.2f} → {nb_close.iloc[-1]:.2f} (return {(nb_close.iloc[-1]/nb_close.iloc[0]-1)*100:+.1f}%)")
    print(f"  NIFTY 50  range: {nifty_close.iloc[0]:.2f} → {nifty_close.iloc[-1]:.2f} (return {(nifty_close.iloc[-1]/nifty_close.iloc[0]-1)*100:+.1f}%)")

    # ── Step 2: Simulate position counts ─────────────────────────────────────
    print(f"\n[2/5] Simulating position counts from NIFTY 50 SMA20/SMA50 regime...")
    pos_counts = simulate_position_counts(nifty_close)

    sma20 = nifty_close.rolling(20).mean()
    sma50 = nifty_close.rolling(50).mean()
    bull_days  = int((sma20 > sma50).sum())
    bear_days  = int((sma20 <= sma50).sum())
    print(f"  Bull regime (SMA20>SMA50): {bull_days} days  |  Bear: {bear_days} days")
    print(f"  Avg positions (all period): {pos_counts.mean():.2f}")

    crash_mask  = (pos_counts.index >= pd.Timestamp(CRASH_START)) & \
                  (pos_counts.index <= pd.Timestamp(CRASH_END))
    avg_crash   = pos_counts[crash_mask].mean()
    print(f"  Avg positions during crash window: {avg_crash:.2f}")

    # ── Step 3: Simulate equity curves ───────────────────────────────────────
    print(f"\n[3/5] Simulating equity curves...")
    bah_curve     = simulate_buyandhold(nb_close, INITIAL_CAPITAL)
    overlay_curve = simulate_overlay(nb_close, pos_counts, D_AGGRESSIVE, INITIAL_CAPITAL)

    etf_exposure  = pos_counts.apply(
        lambda c: D_AGGRESSIVE.get(min(int(c), 4), 0.0) * 100.0
    )
    avg_etf_exp   = etf_exposure.mean()
    print(f"  NIFTYBEES B&H final value  : {bah_curve.iloc[-1]:.2f}  ({(bah_curve.iloc[-1]/INITIAL_CAPITAL-1)*100:+.1f}%)")
    print(f"  D_aggressive final value   : {overlay_curve.iloc[-1]:.2f}  ({(overlay_curve.iloc[-1]/INITIAL_CAPITAL-1)*100:+.1f}%)")
    print(f"  Average ETF exposure       : {avg_etf_exp:.1f}%")
    print(f"  Avg ETF exposure in crash  : {etf_exposure[crash_mask].mean():.1f}%")

    # ── Step 4: Compute crash metrics ─────────────────────────────────────────
    print(f"\n[4/5] Computing crash window metrics ({CRASH_START} → {CRASH_END})...")
    bah_metrics     = compute_crash_metrics(bah_curve,     "NIFTYBEES Buy & Hold")
    overlay_metrics = compute_crash_metrics(overlay_curve, "D_aggressive Overlay")

    for m in [bah_metrics, overlay_metrics]:
        print(f"\n  ── {m['label']} ──")
        print(f"     Pre-crash peak : {m['pre_crash_peak_value']:.2f}  ({m['pre_crash_peak_date']})")
        print(f"     Crash trough   : {m['crash_trough_value']:.2f}  ({m['crash_trough_date']})")
        print(f"     Peak→trough DD : {m['peak_to_trough_drawdown_pct']:+.2f}%")
        print(f"     Max 1-day loss : {m['max_single_day_loss_pct']:+.2f}%  ({m['max_single_day_loss_date']})")
        print(f"     Recovery date  : {m['recovery_date']}")

    dd_delta = overlay_metrics["peak_to_trough_drawdown_pct"] - bah_metrics["peak_to_trough_drawdown_pct"]
    print(f"\n  Overlay DD vs B&H : {dd_delta:+.2f}pp  ({'overlay suffered more' if dd_delta < 0 else 'overlay suffered less'})")

    # ── Step 5: Plot ──────────────────────────────────────────────────────────
    print(f"\n[5/5] Generating plot...")
    plot_crash_scenario(
        bah_curve, overlay_curve, pos_counts,
        etf_exposure, nifty_close,
        save_path=PLOT_PATH,
    )

    # ── Save JSON result ───────────────────────────────────────────────────────
    result = {
        "run_date":            today,
        "data_source":         "yfinance (Kite historical API limited to ~3 years; "
                               "NIFTYBEES.NS and ^NSEI fetched via data/fetcher.py)",
        "period":              f"{START} → {END.replace('-01-01', '-12-31').replace('2021','2020')}",
        "crash_window":        f"{CRASH_START} → {CRASH_END}",
        "tier_config":         "D_aggressive",
        "tiers":               D_AGGRESSIVE,
        "data_points":         int(len(common_idx)),
        "simulation": {
            "position_count_seed":         42,
            "avg_positions_full_period":   round(float(pos_counts.mean()), 2),
            "avg_positions_crash_window":  round(float(avg_crash), 2),
            "avg_etf_exposure_pct":        round(float(avg_etf_exp), 1),
            "avg_etf_exposure_crash_pct":  round(float(etf_exposure[crash_mask].mean()), 1),
        },
        "full_period_returns": {
            "niftybees_bah_pct":  round(float((bah_curve.iloc[-1]     / INITIAL_CAPITAL - 1) * 100), 2),
            "overlay_pct":        round(float((overlay_curve.iloc[-1] / INITIAL_CAPITAL - 1) * 100), 2),
        },
        "crash_metrics": {
            "niftybees_bah":   bah_metrics,
            "d_aggressive":    overlay_metrics,
            "overlay_vs_bah_dd_delta_pp": round(dd_delta, 2),
        },
        "plot_path":  str(PLOT_PATH),
        "result_path": str(RESULT_PATH),
    }

    with open(RESULT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  Result saved → {RESULT_PATH}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  CRASH SCENARIO SUMMARY")
    print(f"{'='*65}")
    print(f"  {'Metric':<38} {'B&H':>10}  {'D_agg':>10}")
    print(f"  {'─'*60}")
    print(f"  {'Peak→Trough Drawdown':<38} {bah_metrics['peak_to_trough_drawdown_pct']:>+9.2f}%  {overlay_metrics['peak_to_trough_drawdown_pct']:>+9.2f}%")
    print(f"  {'Crash Trough Date':<38} {bah_metrics['crash_trough_date']:>10}  {overlay_metrics['crash_trough_date']:>10}")
    print(f"  {'Max Single-Day Loss':<38} {bah_metrics['max_single_day_loss_pct']:>+9.2f}%  {overlay_metrics['max_single_day_loss_pct']:>+9.2f}%")
    print(f"  {'Recovery Date':<38} {bah_metrics['recovery_date']:>10}  {overlay_metrics['recovery_date']:>10}")
    print(f"  {'Full Period Return (2019-2020)':<38} {result['full_period_returns']['niftybees_bah_pct']:>+9.2f}%  {result['full_period_returns']['overlay_pct']:>+9.2f}%")
    print(f"  {'Avg ETF Exposure (crash window)':<38} {'100.0%':>10}  {result['simulation']['avg_etf_exposure_crash_pct']:>9.1f}%")
    print(f"{'='*65}")
    print()


if __name__ == "__main__":
    main()
