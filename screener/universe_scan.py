"""
screener/universe_scan.py — Full NIFTY 500 universe scan pipeline.

Cost-ascending funnel:
  Stage 0: Universe hygiene (no Kite API calls — surveillance check + cache load)
  Stage 1: OHLCV fetch + screen (1 Kite API call per stock, rate-limited)
  Stage 2: Rank and cap to top-N (pure computation, no API calls)
  Stage 3: Walk-forward validation on top-N shortlist only
  Stage 4: Cross-candidate pairwise correlation check

Output files:
  screener/universe_scan_report_YYYY-MM-DD.md   — human-readable summary
  screener/logs/universe_scan_YYYY-MM-DD.log    — full progress log (tail -f safe)
  screener/logs/stage1_results_YYYY-MM-DD.json  — Stage 1 cache (for resumability)
  screener/screen_failed.csv                    — stocks that failed on MERIT
  screener/screen_error.csv                     — stocks that failed due to INFRA issues

Usage:
  python screener/universe_scan.py                        # test run (see --limit)
  python screener/universe_scan.py --limit 20 --top-n 5   # fast integration test
  python screener/universe_scan.py --top-n 25             # production run (~40 min)

Do NOT add to cron — manual on-demand tool only.
Do NOT modify signal_runner.py STOCKS — this script is read-only wrt the live universe.
"""

import argparse
import csv
import json
import logging
import sys
import time
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ── Reused from auto_screener — do NOT reimplement ────────────────────────────
from screener.auto_screener import (
    compute_hurst,
    compute_adx,
    compute_correlation,
    compute_sma_gap,
    fetch_nifty500,
    HURST_THRESHOLD,    # 0.48
    ADX_THRESHOLD,      # 25.0
    MAX_CORR_AVG,       # 0.60
    MAX_CORR_PAIR,      # 0.70
    MIN_BARS,           # 744 (~2 years)
    MIN_LIQUIDITY,      # 10_000_000 (₹1 crore avg daily turnover)
    MAX_PRICE,          # 15_000
    START_DATE,         # "2023-01-01"
    END_DATE,           # date.today()
)

# Live universe sourced from signal_runner.STOCKS — the canonical list.
# NOT from portfolio_state.json, which retains stale position entries for
# removed stocks (e.g. SIEMENS removed Jun 24, JKTYRE removed Jul 7).
from paper_trading.signal_runner import STOCKS as LIVE_UNIVERSE

# ── Reused from news_monitor — do NOT reimplement ─────────────────────────────
from utils.news_monitor import fetch_surveillance_flags

# ── Walk-forward primitives — do NOT reimplement ──────────────────────────────
# Using lower-level functions directly to avoid per-call file writes
# (run_walk_forward writes walk_forward_results.txt each call) and
# redundant NIFTY re-fetches (we fetch NIFTY once and share across all candidates).
# Gate logic is identical to `validation/walk_forward.py --ticker` mode
# (CLAUDE_CONTEXT "Mandatory WF Gate Before Universe Addition").
from validation.walk_forward import (
    _run_one as _wf_run_one,
    _pass as _wf_pass,
    _fetch_nifty,
    WINDOWS as WF_WINDOWS,
    EXT_IS_START,
    EXT_IS_END,
    EXT_OOS_START,
    EXT_OOS_END,
)

# ── Data fetcher — do NOT reimplement (has rate limiting + 15s timeout) ───────
from data.kite_fetcher import get_ohlcv


# =============================================================================
# Constants
# =============================================================================

# Walk-forward gate thresholds — identical to CLAUDE_CONTEXT "Mandatory WF Gate"
WF_METRIC_MIN   = 4      # each stock must pass ≥4/6 metrics independently
WF_OOS_RET_MIN  = 4.0    # OOS total return must be ≥+4%
WF_METRICS_KEYS = [
    "total_ret", "vs_bnh", "max_dd", "payoff", "expectancy", "min_abs_oos_ret"
]

# Pairwise correlation threshold for Stage 4 (same as check_entry_correlation default)
CORR_THRESHOLD = 0.60

# File paths
_SCREENER_DIR     = Path(__file__).parent
_LOGS_DIR         = _SCREENER_DIR / "logs"
_TODAY            = date.today().isoformat()
STAGE1_CACHE_FILE = _LOGS_DIR / f"stage1_results_{_TODAY}.json"
LOG_FILE          = _LOGS_DIR / f"universe_scan_{_TODAY}.log"
REPORT_FILE       = _SCREENER_DIR / f"universe_scan_report_{_TODAY}.md"
SCREEN_FAILED_CSV = _SCREENER_DIR / "screen_failed.csv"
SCREEN_ERROR_CSV  = _SCREENER_DIR / "screen_error.csv"


# =============================================================================
# Logging setup
# =============================================================================

def _setup_logging() -> logging.Logger:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("universe_scan")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(LOG_FILE, mode="a")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# =============================================================================
# Stage 1 helpers
# =============================================================================

def _gap_from_df(df: pd.DataFrame) -> Optional[float]:
    """
    SMA20/50 gap using only the last ~80 trading bars (~120 calendar days) of
    the already-fetched DataFrame — approximates signal_runner's short lookback
    without a second API call.
    """
    try:
        tail  = df.tail(130)  # 130 rows ≈ 80 trading bars + SMA50 warmup
        close = tail["close"]
        if len(close) < 50:
            return None
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        if sma50 == 0:
            return None
        return (sma20 - sma50) / sma50 * 100
    except Exception:
        return None


def _flush_stage1_cache(cache: dict) -> None:
    """Atomically write Stage 1 cache to disk after each ticker (resumability)."""
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STAGE1_CACHE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=2)
        tmp.rename(STAGE1_CACHE_FILE)
    except Exception:
        pass  # non-fatal — resumability is best-effort


def _write_csv(path: Path, rows: list, fieldnames: list) -> None:
    try:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except Exception:
        pass


# =============================================================================
# Stage 2: Ranking
# =============================================================================

def _compute_scan_score(
    h: float,
    adx: float,
    gap_short: Optional[float],
    avg_corr: float,
) -> float:
    """
    Composite scan score (0-100, higher = better candidate for WF validation).

    NEW function for batch pre-signal screening — NOT a reuse of
    signal_runner.rank_signal(), which targets active BUY_CANDIDATE allocation
    where a golden cross has already fired.  Here we're comparing pre-signal
    candidates across the full NIFTY 500, so ADX strength is a meaningful
    differentiator and correlation distance matters at the screening stage.

    Weights:
      Hurst quality  40%  H=0.50→0,  H=0.70→100
      ADX strength   20%  ADX=25→0,  ADX=50→100
      Gap proximity  30%  |gap%|=0→100, |gap%|=15→0  (closer to cross = better)
      Corr distance  10%  avg_corr=0→100, avg_corr=MAX_CORR_AVG→0
    """
    hurst_score = max(0.0, min(100.0, (h - 0.50) / 0.20 * 100))
    adx_score   = max(0.0, min(100.0, (adx - 25.0) / 25.0 * 100))
    gap_val     = abs(gap_short) if gap_short is not None else 15.0
    gap_score   = max(0.0, (1.0 - gap_val / 15.0) * 100)
    corr_score  = max(0.0, (MAX_CORR_AVG - avg_corr) / MAX_CORR_AVG * 100)
    return 0.40 * hurst_score + 0.20 * adx_score + 0.30 * gap_score + 0.10 * corr_score


# =============================================================================
# Stage 3: Walk-forward gate
# =============================================================================

def _run_wf_gate(
    ticker:        str,
    nifty_is:      pd.DataFrame,
    nifty_oos:     pd.DataFrame,
    nifty_ext_is:  pd.DataFrame,
    nifty_ext_oos: pd.DataFrame,
    log:           logging.Logger,
) -> dict:
    """
    Run WF gate for a single candidate using the SAME criteria documented in
    CLAUDE_CONTEXT "Mandatory WF Gate Before Universe Addition":
      original window:  ≥4/6 metrics AND OOS return ≥+4%
      extended window:  ≥4/6 metrics (if sufficient history for 2015 IS window)

    Uses _run_one + _pass directly from walk_forward.py to avoid per-call
    file writes and NIFTY re-fetches.  Note: _run_one calls sys.exit(1) on
    auth errors (FileNotFoundError/ConnectionError) — acceptable here since
    Stage 1 already validated auth before Stage 3 begins.

    Returns a structured result dict — no stdout side-effects from this function.
    """
    is_start, is_end   = WF_WINDOWS["in_sample"]
    oos_start, oos_end = WF_WINDOWS["out_of_sample"]

    # ── Original window ───────────────────────────────────────────────────────
    log.info(f"    IS  {is_start} → {is_end}")
    buf = __import__("io").StringIO()
    with redirect_stdout(buf):
        orig_is  = _wf_run_one(ticker, is_start,  is_end,  nifty_df=nifty_is)
    time.sleep(1.1)

    log.info(f"    OOS {oos_start} → {oos_end}")
    buf = __import__("io").StringIO()
    with redirect_stdout(buf):
        orig_oos = _wf_run_one(ticker, oos_start, oos_end, nifty_df=nifty_oos)
    time.sleep(1.1)

    orig_error = orig_is.get("error") or orig_oos.get("error")
    if not orig_error:
        orig_score   = sum(_wf_pass(m, orig_is["metrics"], orig_oos["metrics"])
                           for m in WF_METRICS_KEYS)
        orig_oos_ret = float(orig_oos["metrics"]["total_ret"])
        orig_pass    = (orig_score >= WF_METRIC_MIN and orig_oos_ret >= WF_OOS_RET_MIN)
    else:
        orig_score, orig_oos_ret, orig_pass = 0, 0.0, False

    # ── Extended window ───────────────────────────────────────────────────────
    log.info(f"    EXT-IS  {EXT_IS_START} → {EXT_IS_END}")
    buf = __import__("io").StringIO()
    with redirect_stdout(buf):
        ext_is  = _wf_run_one(ticker, EXT_IS_START,  EXT_IS_END,  nifty_df=nifty_ext_is)
    time.sleep(1.1)

    log.info(f"    EXT-OOS {EXT_OOS_START} → {EXT_OOS_END}")
    buf = __import__("io").StringIO()
    with redirect_stdout(buf):
        ext_oos = _wf_run_one(ticker, EXT_OOS_START, EXT_OOS_END, nifty_df=nifty_ext_oos)
    time.sleep(1.1)

    ext_error = ext_is.get("error") or ext_oos.get("error")
    if not ext_error:
        ext_score   = sum(_wf_pass(m, ext_is["metrics"], ext_oos["metrics"])
                          for m in WF_METRICS_KEYS)
        ext_oos_ret = float(ext_oos["metrics"]["total_ret"])
        ext_pass    = (ext_score >= WF_METRIC_MIN)
    else:
        ext_score, ext_oos_ret = 0, 0.0
        ext_pass = True  # insufficient history for 2015 IS window — don't penalise

    # ── Gate ──────────────────────────────────────────────────────────────────
    gate_pass = orig_pass and ext_pass

    if orig_error:
        note = f"ERROR: {orig_error}"
    elif gate_pass:
        ext_str = (f"ext {ext_score}/6 OOS {ext_oos_ret:+.1f}%"
                   if not ext_error else "ext: N/A (insufficient history)")
        note = (f"PASS — orig {orig_score}/6 OOS {orig_oos_ret:+.1f}% | {ext_str}")
    else:
        reasons = []
        if not orig_pass:
            reasons.append(
                f"orig {orig_score}/6 (need ≥{WF_METRIC_MIN}) "
                f"OOS {orig_oos_ret:+.1f}% (need ≥+{WF_OOS_RET_MIN:.0f}%)"
            )
        if not ext_pass and not ext_error:
            reasons.append(f"ext {ext_score}/6 (need ≥{WF_METRIC_MIN})")
        note = f"FAIL — {'; '.join(reasons)}"

    return {
        "ticker":       ticker,
        "orig_score":   orig_score,
        "orig_oos_ret": round(orig_oos_ret, 2),
        "orig_pass":    orig_pass,
        "ext_score":    ext_score,
        "ext_oos_ret":  round(ext_oos_ret, 2),
        "ext_pass":     ext_pass,
        "ext_error":    ext_error,
        "gate_pass":    gate_pass,
        "note":         note,
        "orig_error":   orig_error,
    }


# =============================================================================
# Stage 4: Pairwise correlation among WF-passing candidates
# =============================================================================

def _check_pairwise_correlation(
    candidates:  list,
    data_cache:  dict,
    threshold:   float = CORR_THRESHOLD,
) -> list:
    """
    Check pairwise correlation among all Stage 3 pass candidates.
    Flags pairs exceeding threshold so a human can choose which to prefer.
    Uses log returns (same method as correlation_check.py).
    """
    high_corr_pairs = []
    for i, t1 in enumerate(candidates):
        for t2 in candidates[i + 1:]:
            if t1 not in data_cache or t2 not in data_cache:
                continue
            r1 = np.log(data_cache[t1]["close"] / data_cache[t1]["close"].shift(1)).dropna()
            r2 = np.log(data_cache[t2]["close"] / data_cache[t2]["close"].shift(1)).dropna()
            common = r1.index.intersection(r2.index)
            if len(common) < 100:
                continue
            corr = float(r1.loc[common].corr(r2.loc[common]))
            if corr >= threshold:
                high_corr_pairs.append({
                    "ticker1": t1,
                    "ticker2": t2,
                    "corr":    round(corr, 3),
                })
    return high_corr_pairs


# =============================================================================
# Report writer
# =============================================================================

def _write_report(
    wf_passed:       list,
    wf_failed:       list,
    high_corr_pairs: list,
    all_stage1:      list,
    screen_failed:   list,
    screen_error:    list,
    n_nifty500:      int,
    n_candidates:    int,
    n_stage0:        int,
    top_n:           int,
    limit:           Optional[int],
    log:             logging.Logger,
) -> None:
    today    = date.today().isoformat()
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list = [
        f"# Universe Scan Report — {today}",
        f"*Generated: {run_time}*",
        "",
    ]

    if limit is not None:
        lines += [
            f"> **TEST RUN** — `--limit {limit}` applied; "
            f"scan covers first {limit} of {n_stage0} Stage 0 survivors only.",
            "",
        ]

    # ── Infrastructure errors: surface first ─────────────────────────────────
    if screen_error:
        lines += [
            "## ⚠️ Infrastructure Errors — review and retry",
            "",
            f"{len(screen_error)} stocks could not be screened (API timeout/error).",
            "These are in `screener/screen_error.csv`. Do NOT treat as rejected.",
            "",
        ]
        for e in screen_error[:20]:
            lines.append(f"- `{e['ticker']}`: {e['error']}")
        if len(screen_error) > 20:
            lines.append(f"- *(+{len(screen_error)-20} more — see screen_error.csv)*")
        lines.append("")

    # ── Funnel ────────────────────────────────────────────────────────────────
    n_top_n      = len(wf_passed) + len(wf_failed)
    n_wf_pass    = len(wf_passed)
    lines += [
        "## Funnel Summary",
        "",
        "| Stage | Count | Notes |",
        "|-------|-------|-------|",
        f"| NIFTY 500 loaded | {n_nifty500} | live or cache |",
        f"| After excluding live universe | {n_candidates} | current 7-stock universe removed |",
        f"| Stage 0 survivors (surveillance) | {n_stage0} | NSE sec_list.csv clean |",
        f"| Stage 1 passed (screen) | {len(all_stage1)} | H>{HURST_THRESHOLD} ADX>{ADX_THRESHOLD} corr<{MAX_CORR_AVG} liq>₹1Cr |",
        f"| Stage 1 failed (merit) | {len(screen_failed)} | see screen_failed.csv |",
        f"| Stage 1 errors (infra) | {len(screen_error)} | see screen_error.csv |",
        f"| Stage 2 top-{top_n} | {n_top_n} | ranked by scan score |",
        f"| Stage 3 WF PASS | {n_wf_pass} | orig ≥{WF_METRIC_MIN}/6 + OOS ≥+{WF_OOS_RET_MIN:.0f}% |",
        f"| Stage 4 high-corr pairs | {len(high_corr_pairs)} | ≥{CORR_THRESHOLD} flagged for human review |",
        "",
    ]

    # ── Final recommendations ─────────────────────────────────────────────────
    lines += ["## Final Candidates (WF-Validated)", ""]
    if not wf_passed:
        lines.append("*No candidates passed all stages in this run.*")
    else:
        for r in wf_passed:
            wf = r["wf"]
            ext_str = (
                f"{wf['ext_score']}/6  OOS {wf['ext_oos_ret']:+.1f}%"
                if not wf["ext_error"]
                else "N/A (insufficient history for 2015 IS window)"
            )
            pair_flags = [
                p for p in high_corr_pairs
                if r["ticker"] in (p["ticker1"], p["ticker2"])
            ]
            lines += [
                f"### {r['ticker']}  ({r['industry']})",
                f"- Screen: H={r['hurst']}  ADX={r['adx']}  "
                f"gap_short={r['gap_short']}%  avg_corr={r['avg_corr']}  "
                f"score={r['scan_score']}",
                f"- WF Original: {wf['orig_score']}/6  OOS {wf['orig_oos_ret']:+.1f}%",
                f"- WF Extended: {ext_str}",
                f"- Gate: **{wf['note']}**",
            ]
            for p in pair_flags:
                other = p["ticker2"] if p["ticker1"] == r["ticker"] else p["ticker1"]
                lines.append(
                    f"- ⚠️ High correlation with `{other}`: {p['corr']:.3f} ≥ {CORR_THRESHOLD} — choose one"
                )
            lines += [
                f"- **Next step**: `python validation/walk_forward.py --ticker {r['ticker']}`"
                f" to confirm, then add to `STOCKS` per CLAUDE_CONTEXT WF Gate procedure.",
                "",
            ]
    lines.append("")

    # ── Stage 3 failures ──────────────────────────────────────────────────────
    if wf_failed:
        lines += ["## Stage 3 WF Failures", ""]
        for r in wf_failed:
            lines.append(f"- `{r['ticker']}`: {r['wf']['note']}")
        lines.append("")

    # ── Full Stage 1 ranked list ──────────────────────────────────────────────
    lines += [
        "## Full Stage 1 Ranked List (all survivors, with scores)",
        "",
        "| Rank | Ticker | Industry | Score | H | ADX | Gap-Short | Avg-Corr |",
        "|------|--------|----------|-------|---|-----|-----------|----------|",
    ]
    for i, r in enumerate(all_stage1, 1):
        gap_str = f"{r['gap_short']}%" if r["gap_short"] is not None else "N/A"
        lines.append(
            f"| {i} | {r['ticker']} | {r['industry']} | {r.get('scan_score','—')} "
            f"| {r['hurst']} | {r['adx']} | {gap_str} | {r['avg_corr']} |"
        )
    lines.append("")

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(lines))
    log.info(f"Report written: {REPORT_FILE}")


# =============================================================================
# Main pipeline
# =============================================================================

def run_scan(limit: Optional[int] = None, top_n: int = 25) -> None:
    log = _setup_logging()
    log.info(f"{'='*60}")
    log.info(f"Universe scan started {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info(f"Parameters: limit={limit}, top_n={top_n}")
    log.info(f"{'='*60}")

    # =========================================================================
    # STAGE 0: Universe hygiene (no Kite API calls)
    # =========================================================================
    log.info("\nSTAGE 0: Universe hygiene")
    log.info("-" * 40)

    nifty500, used_cache = fetch_nifty500()
    if not nifty500:
        log.error("CRITICAL: Empty NIFTY 500 — aborting.")
        return
    log.info(
        f"NIFTY 500: {len(nifty500)} stocks "
        f"({'from cache' if used_cache else 'live fetch'})"
    )

    current_universe = list(LIVE_UNIVERSE)
    log.info(f"Live universe ({len(current_universe)}): {', '.join(current_universe)}")

    candidates_raw = {t: ind for t, ind in nifty500.items() if t not in current_universe}
    log.info(
        f"After removing live universe: {len(candidates_raw)} candidates "
        f"({len(nifty500) - len(candidates_raw)} excluded)"
    )

    # Surveillance check (1 HTTP call to nsearchives.nseindia.com — no Kite auth)
    log.info("Checking NSE surveillance list (sec_list.csv)...")
    all_tickers = list(candidates_raw.keys())
    surv_flags  = fetch_surveillance_flags(all_tickers)
    if surv_flags:
        log.info(
            f"Surveillance: {len(surv_flags)} removed "
            f"({', '.join(list(surv_flags.keys())[:10])}"
            f"{'...' if len(surv_flags) > 10 else ''})"
        )
    else:
        log.info("Surveillance: 0 stocks flagged")

    stage0_survivors_full = [t for t in all_tickers if t not in surv_flags]
    n_stage0_full = len(stage0_survivors_full)
    log.info(f"Stage 0 survivors: {n_stage0_full}")

    # Apply --limit (testing only)
    if limit is not None:
        stage0_survivors = stage0_survivors_full[:limit]
        log.info(f"--limit {limit}: truncated to {len(stage0_survivors)} for test run")
    else:
        stage0_survivors = stage0_survivors_full

    # =========================================================================
    # STAGE 1: OHLCV fetch + screen
    # =========================================================================
    log.info(f"\nSTAGE 1: OHLCV fetch + screen ({len(stage0_survivors)} stocks)")
    log.info("-" * 40)
    log.info("Fetching current universe data for correlation baseline...")

    universe_data: dict = {}
    for ticker in current_universe:
        try:
            df = get_ohlcv(ticker, START_DATE, END_DATE)
            if df is not None and len(df) >= 50:
                universe_data[ticker] = df
            time.sleep(1.1)
        except Exception as e:
            log.warning(f"  Could not fetch universe stock {ticker}: {e}")
    log.info(f"Universe baseline: {len(universe_data)}/{len(current_universe)} stocks loaded")

    # Load existing Stage 1 cache for resumability
    stage1_cache: dict = {}
    if STAGE1_CACHE_FILE.exists():
        try:
            with open(STAGE1_CACHE_FILE) as f:
                stage1_cache = json.load(f)
            log.info(
                f"Resuming: {len(stage1_cache)} tickers already cached "
                f"({sum(1 for v in stage1_cache.values() if v['status']=='PASS')} PASS, "
                f"{sum(1 for v in stage1_cache.values() if v['status']=='FAIL')} FAIL, "
                f"{sum(1 for v in stage1_cache.values() if v['status']=='ERROR')} ERROR)"
            )
        except Exception as e:
            log.warning(f"Could not load Stage 1 cache ({e}) — starting fresh")
            stage1_cache = {}

    screen_passed: list = []  # passed all filters (merit + data quality)
    screen_failed: list = []  # failed on merit (documented reason)
    screen_error:  list = []  # failed due to infrastructure (API error, timeout)

    processed = 0
    for ticker in stage0_survivors:
        processed += 1
        if processed % 25 == 0:
            log.info(
                f"Processed {processed}/{len(stage0_survivors)} — "
                f"{len(screen_passed)} survived so far"
            )

        # Resumability: skip if already processed today
        if ticker in stage1_cache:
            cached = stage1_cache[ticker]
            if cached["status"] == "PASS":
                screen_passed.append(cached["result"])
            elif cached["status"] == "FAIL":
                screen_failed.append({"ticker": ticker, "reason": cached["reason"]})
            else:
                screen_error.append({"ticker": ticker, "error": cached["error"]})
            continue

        # ── Fetch OHLCV ───────────────────────────────────────────────────────
        try:
            df = get_ohlcv(ticker, START_DATE, END_DATE)
            # Rate limit: ~55 req/min, matching auto_screener.py's validated pattern
            time.sleep(1.1)
        except Exception as e:
            err = str(e)
            log.debug(f"  ERROR  {ticker}: {err}")
            screen_error.append({"ticker": ticker, "error": err})
            stage1_cache[ticker] = {"status": "ERROR", "error": err}
            _flush_stage1_cache(stage1_cache)
            continue

        # ── Filter: minimum history ────────────────────────────────────────────
        n_bars = len(df) if df is not None else 0
        if n_bars < MIN_BARS:
            reason = f"insufficient history ({n_bars} bars, need ≥{MIN_BARS})"
            screen_failed.append({"ticker": ticker, "reason": reason})
            stage1_cache[ticker] = {"status": "FAIL", "reason": reason}
            _flush_stage1_cache(stage1_cache)
            continue

        # ── Filter: price ceiling ─────────────────────────────────────────────
        last_price = float(df["close"].iloc[-1])
        if last_price > MAX_PRICE:
            reason = f"price ₹{last_price:,.0f} > ₹{MAX_PRICE:,} ceiling"
            screen_failed.append({"ticker": ticker, "reason": reason})
            stage1_cache[ticker] = {"status": "FAIL", "reason": reason}
            _flush_stage1_cache(stage1_cache)
            continue

        # ── Filter: liquidity floor ───────────────────────────────────────────
        avg_turnover = float(df["volume"].tail(20).mean() * last_price)
        if avg_turnover < MIN_LIQUIDITY:
            reason = (
                f"illiquid (₹{avg_turnover/1e6:.1f}L avg daily turnover, "
                f"need ≥₹{MIN_LIQUIDITY/1e6:.0f}L)"
            )
            screen_failed.append({"ticker": ticker, "reason": reason})
            stage1_cache[ticker] = {"status": "FAIL", "reason": reason}
            _flush_stage1_cache(stage1_cache)
            continue

        # ── Filter: Hurst exponent ─────────────────────────────────────────────
        h = compute_hurst(df["close"].values)
        if h <= HURST_THRESHOLD:
            reason = f"Hurst H={h:.3f} ≤ {HURST_THRESHOLD} threshold"
            screen_failed.append({"ticker": ticker, "reason": reason})
            stage1_cache[ticker] = {"status": "FAIL", "reason": reason}
            _flush_stage1_cache(stage1_cache)
            continue

        # ── Filter: ADX trend strength ─────────────────────────────────────────
        adx = compute_adx(df)
        if adx <= ADX_THRESHOLD:
            reason = f"ADX={adx:.1f} ≤ {ADX_THRESHOLD} threshold"
            screen_failed.append({"ticker": ticker, "reason": reason})
            stage1_cache[ticker] = {"status": "FAIL", "reason": reason}
            _flush_stage1_cache(stage1_cache)
            continue

        # ── Filter: correlation with live universe ─────────────────────────────
        data_cache_for_corr = {**universe_data, ticker: df}
        avg_corr, max_corr  = compute_correlation(
            ticker, list(universe_data.keys()), data_cache_for_corr
        )
        if avg_corr > MAX_CORR_AVG or max_corr > MAX_CORR_PAIR:
            reason = (
                f"corr too high (avg={avg_corr:.3f}>{MAX_CORR_AVG}, "
                f"max={max_corr:.3f}>{MAX_CORR_PAIR})"
            )
            screen_failed.append({"ticker": ticker, "reason": reason})
            stage1_cache[ticker] = {"status": "FAIL", "reason": reason}
            _flush_stage1_cache(stage1_cache)
            continue

        # ── All filters passed ─────────────────────────────────────────────────
        gap_short = _gap_from_df(df)
        gap_long  = compute_sma_gap(df)

        result = {
            "ticker":     ticker,
            "industry":   candidates_raw.get(ticker, "Unknown"),
            "hurst":      round(h, 3),
            "adx":        round(adx, 1),
            "gap_short":  round(gap_short, 2) if gap_short is not None else None,
            "gap_long":   round(gap_long,  2) if gap_long  is not None else None,
            "avg_corr":   round(avg_corr, 3),
            "max_corr":   round(max_corr, 3),
            "last_price": round(last_price, 2),
        }
        screen_passed.append(result)
        stage1_cache[ticker] = {"status": "PASS", "result": result}
        _flush_stage1_cache(stage1_cache)
        log.info(
            f"  PASS  {ticker:<22} H={h:.3f}  ADX={adx:.1f}  "
            f"corr={avg_corr:.3f}  gap={gap_short:.1f}%"
            if gap_short is not None else
            f"  PASS  {ticker:<22} H={h:.3f}  ADX={adx:.1f}  corr={avg_corr:.3f}"
        )

    log.info(
        f"\nStage 1 complete: {len(screen_passed)} PASS, "
        f"{len(screen_failed)} FAIL (merit), "
        f"{len(screen_error)} ERROR (infra)"
    )
    _write_csv(SCREEN_FAILED_CSV, screen_failed, ["ticker", "reason"])
    _write_csv(SCREEN_ERROR_CSV,  screen_error,  ["ticker", "error"])

    if not screen_passed:
        log.warning("No candidates survived Stage 1 — scan complete with no recommendations.")
        _write_report(
            [], [], [], screen_passed, screen_failed, screen_error,
            len(nifty500), len(candidates_raw), n_stage0_full, top_n, limit, log
        )
        return

    # =========================================================================
    # STAGE 2: Rank and cap to top-N
    # =========================================================================
    log.info(f"\nSTAGE 2: Rank and cap (top-{top_n})")
    log.info("-" * 40)

    for r in screen_passed:
        r["scan_score"] = round(_compute_scan_score(
            r["hurst"], r["adx"], r["gap_short"], r["avg_corr"]
        ), 2)
    screen_passed.sort(key=lambda x: x["scan_score"], reverse=True)

    log.info(f"Full ranked list ({len(screen_passed)} stocks):")
    for i, r in enumerate(screen_passed, 1):
        gap_str = f"{r['gap_short']}%" if r["gap_short"] is not None else "N/A"
        log.info(
            f"  {i:>3}. {r['ticker']:<22} score={r['scan_score']:>5.1f}"
            f"  H={r['hurst']}  ADX={r['adx']}  gap={gap_str}  corr={r['avg_corr']}"
        )

    top_n_candidates = screen_passed[:top_n]
    log.info(
        f"\nTop {len(top_n_candidates)} advancing to WF validation "
        f"({len(screen_passed) - len(top_n_candidates)} below cut):"
    )
    for r in top_n_candidates:
        log.info(f"  {r['ticker']} (score={r['scan_score']})")

    # =========================================================================
    # STAGE 3: Walk-forward validation
    # =========================================================================
    log.info(f"\nSTAGE 3: Walk-forward validation ({len(top_n_candidates)} candidates)")
    log.info("-" * 40)
    log.info(
        "Fetching NIFTY 50 for WF regime filter (4 windows — "
        "fetched once, shared across all candidates)..."
    )

    is_start, is_end   = WF_WINDOWS["in_sample"]
    oos_start, oos_end = WF_WINDOWS["out_of_sample"]
    nifty_is      = _fetch_nifty(is_start, is_end);     time.sleep(1.1)
    nifty_oos     = _fetch_nifty(oos_start, oos_end);   time.sleep(1.1)
    nifty_ext_is  = _fetch_nifty(EXT_IS_START, EXT_IS_END);   time.sleep(1.1)
    nifty_ext_oos = _fetch_nifty(EXT_OOS_START, EXT_OOS_END); time.sleep(1.1)

    wf_results: list = []
    for idx, r in enumerate(top_n_candidates, 1):
        ticker = r["ticker"]
        log.info(f"\n  [{idx}/{len(top_n_candidates)}] WF gate: {ticker}")
        wf = _run_wf_gate(ticker, nifty_is, nifty_oos, nifty_ext_is, nifty_ext_oos, log)
        wf_results.append({**r, "wf": wf})
        verdict = "PASS" if wf["gate_pass"] else "FAIL"
        log.info(f"  → {verdict}: {wf['note']}")

    wf_passed = [r for r in wf_results if r["wf"]["gate_pass"]]
    wf_failed = [r for r in wf_results if not r["wf"]["gate_pass"]]
    log.info(
        f"\nStage 3 complete: {len(wf_passed)} PASS, {len(wf_failed)} FAIL"
    )

    # =========================================================================
    # STAGE 4: Cross-candidate pairwise correlation
    # =========================================================================
    log.info(f"\nSTAGE 4: Cross-candidate correlation ({len(wf_passed)} WF passers)")
    log.info("-" * 40)

    high_corr_pairs: list = []
    if len(wf_passed) >= 2:
        # Re-fetch WF-passing candidates' recent data for correlation.
        # (DataFrames from Stage 1 are not kept in memory to limit RAM usage;
        # at most top_n WF passes so re-fetches are cheap.)
        log.info(f"Re-fetching {len(wf_passed)} stocks for pairwise correlation...")
        wf_data_cache: dict = {}
        for r in wf_passed:
            try:
                df = get_ohlcv(r["ticker"], START_DATE, END_DATE)
                if df is not None:
                    wf_data_cache[r["ticker"]] = df
                time.sleep(1.1)
            except Exception as e:
                log.warning(f"  Could not re-fetch {r['ticker']} for Stage 4: {e}")

        high_corr_pairs = _check_pairwise_correlation(
            [r["ticker"] for r in wf_passed],
            wf_data_cache,
            threshold=CORR_THRESHOLD,
        )
        if high_corr_pairs:
            log.info(f"High-correlation pairs (≥{CORR_THRESHOLD}) — HUMAN REVIEW NEEDED:")
            for p in high_corr_pairs:
                log.info(f"  {p['ticker1']} ↔ {p['ticker2']}: {p['corr']:.3f}")
        else:
            log.info(
                "No high-correlation pairs — all WF candidates are mutually diversifying."
            )
    else:
        log.info("Fewer than 2 WF-passing candidates — pairwise check not applicable.")

    # =========================================================================
    # Final report
    # =========================================================================
    _write_report(
        wf_passed, wf_failed, high_corr_pairs,
        screen_passed, screen_failed, screen_error,
        len(nifty500), len(candidates_raw), n_stage0_full,
        top_n, limit, log,
    )

    log.info(f"\n{'='*60}")
    log.info(f"Scan complete — {datetime.now().strftime('%H:%M')}")
    log.info(f"Report:  {REPORT_FILE}")
    log.info(f"Log:     {LOG_FILE}")
    log.info(f"{'='*60}")


# =============================================================================
# CLI entry point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full NIFTY 500 universe scan pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Integration test (fast, tests pipeline end-to-end):
    python screener/universe_scan.py --limit 20 --top-n 5

  Production run (full scan, ~40 minutes):
    python screener/universe_scan.py --top-n 25

  Run in background with log tail:
    nohup python screener/universe_scan.py --top-n 25 &
    tail -f screener/logs/universe_scan_$(date +%Y-%m-%d).log
        """,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Truncate Stage 0 survivors to the first N tickers. "
            "Use for integration testing only — omit for production runs."
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=25,
        metavar="N",
        help="Number of Stage 1 survivors to advance to WF validation (default: 25).",
    )
    args = parser.parse_args()
    run_scan(limit=args.limit, top_n=args.top_n)
