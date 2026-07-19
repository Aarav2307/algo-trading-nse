"""
validation/post_screener_pipeline.py — automated WF batch orchestrator

Takes screener ADD/WATCHLIST candidates (from --tickers), runs each through
the WF gate (via walk_forward.py --json), checks crossover state for passers,
and writes a dated markdown report.

READ-ONLY with respect to the live trading system. Never modifies STOCKS,
portfolio_state.json, or any live config. Output is a report file only.

Why not run_screen()?
  run_screen() writes degradation_tracker.json unconditionally and fetches
  500+ stocks at 1.1s/each (~9 min). This tool is meant to run freely between
  screener cycles, so candidates are passed via --tickers from the screener email.

Usage:
  python3 validation/post_screener_pipeline.py --tickers TICKER1.NS TICKER2.NS
  python3 validation/post_screener_pipeline.py --tickers MAPMYINDIA.NS --dry-run
  python3 validation/post_screener_pipeline.py --tickers MAPMYINDIA.NS --limit 1
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from data.kite_fetcher import get_ohlcv

HISTORY_FILE = _ROOT / "validation" / "wf_test_history.json"
REPORT_DIR   = _ROOT / "validation"

# How many months of OHLCV history to fetch for the crossover check.
# SMA50 needs at least 50 trading days; 6 months gives comfortable headroom.
_CROSSOVER_LOOKBACK_MONTHS = 6

# Single-ticker WF validation completes in 1-2 min on live runs; 180s gives
# comfortable headroom without hanging indefinitely on a Kite API stall.
WF_GATE_TIMEOUT_SECONDS = 180


# =============================================================================
# Cache helpers
# =============================================================================

def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_history(history: dict) -> None:
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def is_within_cache(history: dict, ticker: str, retest_after_days: int) -> bool:
    """Return True if ticker was tested within retest_after_days and can be skipped."""
    entry = history.get(ticker)
    if not entry:
        return False
    try:
        last = date.fromisoformat(entry["last_tested"])
        return (date.today() - last).days < retest_after_days
    except (KeyError, ValueError):
        return False


# =============================================================================
# WF gate — subprocess approach (guarantees zero logic duplication)
# =============================================================================

def _log_wf_gate_error(ticker: str, detail: str) -> None:
    """Append full error detail to a persistent log so nothing is lost
    even when the raised exception message is truncated for readability.
    Never raises itself — best-effort logging must never crash the pipeline."""
    log_path = _ROOT / "validation" / "wf_gate_errors.log"
    try:
        with open(log_path, "a") as f:
            f.write(
                f"\n{'='*70}\n"
                f"{datetime.now().isoformat()} — {ticker}\n"
                f"{'='*70}\n"
                f"{detail}\n"
            )
    except OSError:
        pass


def run_wf_gate(ticker: str, no_extended: bool = False) -> dict:
    """
    Run walk_forward.py --ticker X --json as a subprocess and return the
    parsed verdict dict. Subprocess is chosen over in-process reimplementation
    so --json mode remains the single source of truth for gate logic.

    Raises RuntimeError on: timeout, non-zero exit, or no/invalid JSON output.
    Full stderr is always written to validation/wf_gate_errors.log (append
    mode) before raising, so nothing is lost even when the raised exception
    message is truncated for readability.
    """
    cmd = [
        sys.executable,
        str(_ROOT / "validation" / "walk_forward.py"),
        "--ticker", ticker,
        "--json",
    ]
    if no_extended:
        cmd.append("--no-extended")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(_ROOT),
            timeout=WF_GATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        _log_wf_gate_error(ticker, f"TIMEOUT after {WF_GATE_TIMEOUT_SECONDS}s")
        raise RuntimeError(
            f"WF gate subprocess for {ticker} timed out after "
            f"{WF_GATE_TIMEOUT_SECONDS}s (possible Kite API stall or "
            f"network issue). See validation/wf_gate_errors.log for detail."
        ) from e

    if result.returncode != 0:
        _log_wf_gate_error(
            ticker,
            f"exit code {result.returncode}\nSTDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}",
        )
        raise RuntimeError(
            f"WF gate subprocess for {ticker} exited with code "
            f"{result.returncode}. Full output logged to "
            f"validation/wf_gate_errors.log. "
            f"stderr (last 500 chars): {result.stderr[-500:]!r}"
        )

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as e:
                _log_wf_gate_error(
                    ticker,
                    f"malformed JSON line: {stripped}\nerror: {e}",
                )
                raise RuntimeError(
                    f"WF gate for {ticker} produced a line starting with '{{' "
                    f"but it was not valid JSON. Full output logged to "
                    f"validation/wf_gate_errors.log. "
                    f"(preview: {stripped[:200]!r})"
                ) from e

    _log_wf_gate_error(
        ticker,
        f"exit code 0 but no JSON line found\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}",
    )
    raise RuntimeError(
        f"WF gate produced no JSON for {ticker} (exit code 0, but no "
        f"JSON line found). Full output logged to validation/wf_gate_errors.log."
    )


# =============================================================================
# Crossover check
# =============================================================================

def get_crossover(ticker: str) -> dict:
    """
    Fetch recent OHLCV and compute SMA20/SMA50 crossover state.
    Matches the manual check pattern used throughout this project's sessions.
    """
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=_CROSSOVER_LOOKBACK_MONTHS * 31)).strftime("%Y-%m-%d")

    df = get_ohlcv(ticker, start, end)
    if df is None or len(df) < 50:
        return {"status": "ERROR", "gap_pct": None, "sma20": None, "sma50": None,
                "error": f"only {len(df) if df is not None else 0} bars (need ≥50)"}

    sma20 = float(df["close"].rolling(20).mean().iloc[-1])
    sma50 = float(df["close"].rolling(50).mean().iloc[-1])
    gap   = (sma20 - sma50) / sma50 * 100
    return {
        "sma20":   round(sma20, 2),
        "sma50":   round(sma50, 2),
        "gap_pct": round(gap, 2),
        "status":  "GOLDEN" if sma20 > sma50 else "DEATH",
    }


# =============================================================================
# Report generation
# =============================================================================

def _orig_str(wf: dict) -> str:
    orig  = wf.get("original", {})
    score = orig.get("score")
    total = orig.get("total_metrics", 6)
    oos   = orig.get("oos_return_pct")
    s = f"{score}/{total}" if score is not None else "N/A"
    r = f"{oos:+.1f}%" if oos is not None else "N/A"
    return f"{s}, {r}"


def _ext_str(wf: dict) -> str:
    ext    = wf.get("extended", {})
    status = ext.get("status", "?")
    if status in ("NOT_REQUESTED", "SKIPPED"):
        return status
    score = ext.get("score")
    oos   = ext.get("oos_return_pct")
    s = f"{score}/6" if score is not None else "N/A"
    r = f"{oos:+.1f}%" if oos is not None else "N/A"
    return f"{status} ({s}, {r})"


def _xover_str(xover: dict | None) -> str:
    if xover is None:
        return "—"
    status  = xover.get("status", "?")
    gap_pct = xover.get("gap_pct")
    gap_s   = f"{gap_pct:+.2f}%" if gap_pct is not None else ""
    return f"{status} ({gap_s})" if gap_s else status


def generate_report(today: date, results: list[dict]) -> str:
    """
    Build the markdown report from a results list. Each entry:
        {
            "ticker":    str,
            "source":    "fresh" | "cached YYYY-MM-DD",
            "wf":        dict,        # JSON from --json mode (may be {})
            "crossover": dict | None, # from get_crossover(), None if gate failed
            "error":     str | None,
        }
    """
    passers = [r for r in results if r.get("wf", {}).get("gate_pass") is True]
    failers = [r for r in results if r.get("wf", {}).get("gate_pass") is False]
    errors  = [r for r in results if r.get("error")]
    fresh   = sum(1 for r in results if r.get("source") == "fresh")
    cached  = sum(1 for r in results if (r.get("source") or "").startswith("cached"))

    lines = [
        f"# WF Batch Report — {today.isoformat()}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} IST  ",
        f"Candidates: {len(results)} ({fresh} fresh, {cached} cached, {len(errors)} errors)",
        "",
        "## Summary",
        f"- Passed: {len(passers)}",
        f"- Failed: {len(failers)}",
        f"- Errors: {len(errors)}",
        "",
    ]

    if passers:
        lines += [
            "## PASSED — ready for addition decision",
            "",
            "| Ticker | Original | Extended | Crossover | Source |",
            "|--------|----------|----------|-----------|--------|",
        ]
        for r in passers:
            lines.append(
                f"| {r['ticker']} "
                f"| {_orig_str(r['wf'])} "
                f"| {_ext_str(r['wf'])} "
                f"| {_xover_str(r.get('crossover'))} "
                f"| {r['source']} |"
            )
        lines.append("")

    if failers:
        lines += [
            "## FAILED",
            "",
            "| Ticker | Original | Extended | Fail Reasons | Source |",
            "|--------|----------|----------|--------------|--------|",
        ]
        for r in failers:
            reasons = "; ".join(r["wf"].get("reasons", [])) or "—"
            lines.append(
                f"| {r['ticker']} "
                f"| {_orig_str(r['wf'])} "
                f"| {_ext_str(r['wf'])} "
                f"| {reasons} "
                f"| {r['source']} |"
            )
        lines.append("")

    if errors:
        lines += [
            "## ERRORS",
            "",
            "| Ticker | Error |",
            "|--------|-------|",
        ]
        for r in errors:
            lines.append(f"| {r['ticker']} | {r.get('error', '?')} |")
        lines.append("")

    lines += [
        "---",
        "_This report is informational only. Universe changes require manual "
        "review and explicit action._",
    ]
    return "\n".join(lines) + "\n"


# =============================================================================
# Main pipeline
# =============================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Batch WF gate validator for screener candidates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test candidates from your screener email
  python3 validation/post_screener_pipeline.py --tickers MAPMYINDIA.NS COHANCE.NS

  # Dry run — preview scope without API calls
  python3 validation/post_screener_pipeline.py --tickers MAPMYINDIA.NS --dry-run

  # Bounded test run (first 2 candidates only)
  python3 validation/post_screener_pipeline.py --tickers A.NS B.NS C.NS --limit 2
""",
    )
    parser.add_argument(
        "--tickers", "-t",
        nargs="+",
        required=True,
        metavar="TICKER.NS",
        help=(
            "Screener ADD/WATCHLIST candidates to validate. "
            "Copy from your screener email — do not re-run run_screen()."
        ),
    )
    parser.add_argument(
        "--retest-after-days",
        type=int,
        default=3,
        metavar="N",
        help="Re-test a cached result if it's older than N days (default: 3).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show scope (cache hits/misses) without any WF runs or API calls.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Truncate candidate list to the first N tickers (for safe test runs).",
    )
    parser.add_argument(
        "--no-extended",
        action="store_true",
        help="Pass --no-extended to walk_forward.py (skip 2015-19 extended window).",
    )
    args = parser.parse_args(argv)

    # Normalise tickers
    candidates = [t if t.endswith(".NS") else t + ".NS" for t in args.tickers]
    if args.limit is not None:
        candidates = candidates[:args.limit]

    today   = date.today()
    history = load_history()

    # ── Dry-run: show scope and exit ───────────────────────────────────────────
    if args.dry_run:
        print(f"DRY RUN — {today.isoformat()}")
        print(f"  Candidates  : {len(candidates)}")
        print(f"  Cache file  : {HISTORY_FILE}")
        print(f"  Retest after: {args.retest_after_days} days")
        print()
        for ticker in candidates:
            if is_within_cache(history, ticker, args.retest_after_days):
                entry = history[ticker]
                print(f"  CACHED  {ticker:<22} "
                      f"(tested {entry['last_tested']}, "
                      f"gate_pass={entry['gate_pass']})")
            else:
                print(f"  FRESH   {ticker:<22} (will run WF gate)")
        print()
        print("No API calls made. Remove --dry-run to run the real pipeline.")
        return

    # ── Real run ───────────────────────────────────────────────────────────────
    results: list[dict] = []

    for i, ticker in enumerate(candidates):
        print(f"\n[{i+1}/{len(candidates)}] {ticker}")

        # ── Cache hit ──────────────────────────────────────────────────────────
        if is_within_cache(history, ticker, args.retest_after_days):
            entry = history[ticker]
            print(f"  CACHED (tested {entry['last_tested']}) — "
                  f"gate_pass={entry['gate_pass']}")

            wf = {
                "ticker": ticker,
                "original": {
                    "score":         entry.get("original_score"),
                    "total_metrics": 6,
                    "oos_return_pct": entry.get("oos_return_pct"),
                },
                "extended": {
                    "status":        entry.get("extended_status", "UNKNOWN"),
                    "score":         entry.get("extended_score"),
                    "oos_return_pct": entry.get("extended_oos_return_pct"),
                    "error":         entry.get("extended_error"),
                },
                "gate_pass": entry["gate_pass"],
                "reasons":   entry.get("reasons", []),
            }

            crossover = None
            if wf["gate_pass"]:
                print(f"  Checking crossover...")
                try:
                    crossover = get_crossover(ticker)
                    print(f"  Crossover: {crossover['status']} "
                          f"({crossover['gap_pct']:+.2f}%)")
                except Exception as e:
                    crossover = {"status": "ERROR", "gap_pct": None, "error": str(e)}
                    print(f"  Crossover error: {e}")

            results.append({
                "ticker":    ticker,
                "source":    f"cached {entry['last_tested']}",
                "wf":        wf,
                "crossover": crossover,
                "error":     None,
            })
            # Still rate-limit between tickers (crossover check made an API call)
            if wf["gate_pass"] and i < len(candidates) - 1:
                time.sleep(1.1)
            continue

        # ── Fresh WF run ───────────────────────────────────────────────────────
        print(f"  Running WF gate (subprocess)...")
        try:
            wf        = run_wf_gate(ticker, no_extended=args.no_extended)
            gate_pass = wf.get("gate_pass", False)
            print(f"  WF gate: {'PASS' if gate_pass else 'FAIL'}")
            if not gate_pass and wf.get("reasons"):
                print(f"  Reasons: {'; '.join(wf['reasons'])}")

            # Persist to history immediately (survive partial runs)
            history[ticker] = {
                "last_tested":              today.isoformat(),
                "gate_pass":                gate_pass,
                "original_score":           wf.get("original", {}).get("score"),
                "oos_return_pct":           wf.get("original", {}).get("oos_return_pct"),
                "extended_status":          wf.get("extended", {}).get("status"),
                "extended_score":           wf.get("extended", {}).get("score"),
                "extended_oos_return_pct":  wf.get("extended", {}).get("oos_return_pct"),
                "extended_error":           wf.get("extended", {}).get("error"),
                "reasons":                  wf.get("reasons", []),
            }
            save_history(history)

            crossover = None
            if gate_pass:
                print(f"  Checking crossover...")
                try:
                    crossover = get_crossover(ticker)
                    print(f"  Crossover: {crossover['status']} "
                          f"({crossover['gap_pct']:+.2f}%)")
                except Exception as e:
                    crossover = {"status": "ERROR", "gap_pct": None, "error": str(e)}
                    print(f"  Crossover error: {e}")

            results.append({
                "ticker":    ticker,
                "source":    "fresh",
                "wf":        wf,
                "crossover": crossover,
                "error":     None,
            })

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "ticker":    ticker,
                "source":    "fresh",
                "wf":        {},
                "crossover": None,
                "error":     str(e)[:200],
            })

        # Rate-limit between tickers (1.1s matches screener/auto_screener.py pattern)
        if i < len(candidates) - 1:
            time.sleep(1.1)

    # ── Write report ───────────────────────────────────────────────────────────
    report_path = REPORT_DIR / f"wf_batch_report_{today.isoformat()}.md"
    report_text = generate_report(today, results)
    report_path.write_text(report_text)

    print(f"\n{'='*60}")
    print(f"Report: {report_path}")
    print(f"{'='*60}")
    print()
    print(report_text)


if __name__ == "__main__":
    main()
