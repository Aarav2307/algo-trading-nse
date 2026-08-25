"""
validation/system_health_check.py — Automated system health audit.

Runs 6 read-only checks and prints a single clear status report.
Replaces ~10 manual SSH commands with one invocation.

Usage:
    python3 validation/system_health_check.py

Exit code: 0 if no FAILs, 1 if any FAIL (WARNs do not trigger non-zero exit).
This script is READ-ONLY: it never modifies any state file.
"""

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ── Runtime-state paths (all absolute) ───────────────────────────────────────

LOGS_DIR            = _ROOT / "paper_trading" / "logs"
DEGRADATION_TRACKER = _ROOT / "screener" / "degradation_tracker.json"
CANDIDATES_FILE     = _ROOT / "screener" / "latest_candidates.json"

# Candidates file feature first deployed on this date (commit 7c1e8af).
# The file is created by the first real (non-dry-run) screener run after deployment.
_CANDIDATES_FEATURE_DATE = date(2026, 7, 15)
_CANDIDATES_STALE_DAYS   = 4   # normal Wed→Sun or Sun→Wed gap

# Directories excluded from the relative-path constant scan (Check 6).
_SCAN_EXCLUDE_DIRS = {"venv", "__pycache__", ".git", ".pytest_cache", "node_modules"}

# How far back check_run_completion() looks. Log files are immutable, so an
# all-time scan can never go green again once any run has failed: on 2026-08-25
# this check was permanently RED on screen_2026-07-12 (a NameError fixed weeks
# earlier) and 2026-06-08 (a ModuleNotFoundError from an incomplete deploy).
# A check that can never clear is one people stop reading -- the same dynamic
# that let EMAMILTD.NS sit unreviewed for four screener cycles.
#
# 30 days rather than this repo's shorter precedents (the degradation tracker's
# 5-day consecutive-gap reset, the candidates file's 1-day staleness ceiling)
# because those measure STALENESS of a single latest artifact, whereas this
# measures FAILURE HISTORY and needs enough samples to show a pattern. At the
# current cadence 30 days covers ~8 screener runs and ~22 each of the three
# weekday jobs. Out-of-window logs are COUNTED AND REPORTED, never silently
# dropped, so narrowing the window cannot hide a failure without saying so.
_RUN_COMPLETION_WINDOW_DAYS = 30


def _log_date(path: Path):
    """Extract the YYYY-MM-DD embedded in a log filename, or None."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _within_window(paths, window_days: int = _RUN_COMPLETION_WINDOW_DAYS):
    """Split paths into (in_window, skipped_count) by their filename date."""
    cutoff = date.today() - timedelta(days=window_days)
    keep, skipped = [], 0
    for p in paths:
        d = _log_date(p)
        if d is None or d >= cutoff:
            keep.append(p)          # undated logs kept -- never silently dropped
        else:
            skipped += 1
    return keep, skipped


def _discover_scan_files() -> list[Path]:
    """All .py source files in the repo, excluding venv/caches/test files.

    Test files are excluded because their string-literal test data (e.g.
    write_text('FOO = Path("relative")')) would produce false positives.
    """
    files = []
    for p in _ROOT.rglob("*.py"):
        if any(part in _SCAN_EXCLUDE_DIRS for part in p.parts):
            continue
        if p.name.startswith("test_") or p.name.endswith("_test.py"):
            continue
        files.append(p)
    return sorted(files)

_IST = timedelta(hours=5, minutes=30)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name:    str
    status:  str            # "PASS" | "WARN" | "FAIL"
    message: str
    details: dict = field(default_factory=dict)


# ── Date helpers (no %-d: cross-platform) ─────────────────────────────────────

def _fmt(d: date) -> str:
    return d.strftime("%b") + " " + str(d.day)


# ── Universe helpers (deferred imports so tests can patch them) ───────────────

def _load_stocks() -> list:
    from paper_trading.signal_runner import STOCKS
    return list(STOCKS)


def _load_screener_universe() -> list:
    from screener.auto_screener import get_current_universe
    return get_current_universe()


# ── Check 1: Screener cron cadence ───────────────────────────────────────────

def check_screener_cadence() -> CheckResult:
    """Verify Wed/Sun screener runs with no unexpected gaps."""
    name = "Screener cadence"
    try:
        log_files = sorted(LOGS_DIR.glob("screen_*.log"))
        if not log_files:
            return CheckResult(name, "FAIL", "No screener logs found in logs dir", {})

        dates = []
        for f in log_files:
            m = re.search(r"screen_(\d{4}-\d{2}-\d{2})\.log", f.name)
            if m:
                dates.append(date.fromisoformat(m.group(1)))
        dates.sort()

        first, last = dates[0], dates[-1]

        # All expected Wed (weekday 2) and Sun (weekday 6) dates in [first, last]
        expected, cur = [], first
        while cur <= last:
            if cur.weekday() in (2, 6):
                expected.append(cur)
            cur += timedelta(days=1)

        date_set = set(dates)
        missing  = [d for d in expected if d not in date_set]
        extra    = [d for d in dates if d not in set(expected)]

        span = f"{_fmt(first)} – {_fmt(last)}"
        if missing:
            return CheckResult(
                name, "FAIL",
                f"{len(dates)} runs found, {len(missing)} expected date(s) missing: "
                + ", ".join(str(d) for d in missing),
                {"dates": [str(d) for d in dates], "missing": [str(d) for d in missing]},
            )

        extra_note = (
            f"; {len(extra)} off-schedule run(s): " + ", ".join(str(d) for d in extra)
            if extra else ""
        )
        return CheckResult(
            name, "PASS",
            f"{len(dates)} runs, no gaps ({span}){extra_note}",
            {"dates": [str(d) for d in dates]},
        )
    except Exception as exc:
        return CheckResult(name, "FAIL", f"Exception: {exc}", {})


# ── Check 2: Run completion / exit codes ─────────────────────────────────────

def _last_nonempty_line(path: Path) -> str:
    text = path.read_text().rstrip()
    return text.splitlines()[-1] if text else ""


def check_run_completion() -> CheckResult:
    """Verify each log type ends with its expected completion marker."""
    name = "Run completion"
    try:
        issues = []
        parts  = []

        # Screener logs — last line must contain "Exit code: 0"
        screen_logs, _sk_screen = _within_window(sorted(LOGS_DIR.glob("screen_*.log")))
        screen_bad  = []
        for f in screen_logs:
            ll = _last_nonempty_line(f)
            if "Exit code: 0" not in ll:
                m = re.search(r"Exit code:\s*(\S+)", ll)
                code = m.group(1) if m else "?? (no marker)"
                screen_bad.append(f"{f.stem}: exit {code}")
        if screen_bad:
            issues += [f"Screener {s}" for s in screen_bad]
        parts.append(f"Screener {len(screen_logs) - len(screen_bad)}/{len(screen_logs)} clean")

        # Signal-runner daily logs — YYYY-MM-DD.log (no suffix)
        runner_logs, _sk_runner = _within_window(sorted(
            f for f in LOGS_DIR.glob("*.log")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.log", f.name)
        ))
        runner_bad = []
        for f in runner_logs:
            if "[run_daily] Completed:" not in _last_nonempty_line(f):
                runner_bad.append(f.stem)
        if runner_bad:
            issues += [f"Signal-runner {d}: no completion marker" for d in runner_bad]
        parts.append(
            f"Signal-runner {len(runner_logs) - len(runner_bad)}/{len(runner_logs)} clean"
        )

        # News-monitor logs — YYYY-MM-DD_news.log
        news_logs, _sk_news = _within_window(sorted(LOGS_DIR.glob("*_news.log")))
        news_bad  = []
        for f in news_logs:
            if "News monitor completed:" not in _last_nonempty_line(f):
                news_bad.append(f.stem)
        if news_bad:
            issues += [f"News monitor {d}: no completion marker" for d in news_bad]
        parts.append(
            f"News monitor {len(news_logs) - len(news_bad)}/{len(news_logs)} clean"
        )

        # Morning fill check logs — YYYY-MM-DD_morning.log
        # Previously untracked by this check entirely.
        morning_logs, _sk_morning = _within_window(sorted(LOGS_DIR.glob("*_morning.log")))
        morning_bad  = []
        for f in morning_logs:
            if "Morning fill check completed:" not in _last_nonempty_line(f):
                morning_bad.append(f.stem)
        if morning_bad:
            issues += [f"Morning fill check {d}: no completion marker" for d in morning_bad]
        parts.append(
            f"Morning fill check {len(morning_logs) - len(morning_bad)}/{len(morning_logs)} clean"
        )

        _skipped = _sk_screen + _sk_runner + _sk_news + _sk_morning
        if _skipped:
            parts.append(
                f"{_skipped} log(s) older than {_RUN_COMPLETION_WINDOW_DAYS}d not scanned"
            )

        summary = " | ".join(parts)
        if issues:
            return CheckResult(
                name, "FAIL",
                summary + " — " + "; ".join(issues),
                {"issues": issues},
            )
        return CheckResult(name, "PASS", summary, {})
    except Exception as exc:
        return CheckResult(name, "FAIL", f"Exception: {exc}", {})


# ── Check 3: Universe consistency ─────────────────────────────────────────────

def check_universe_consistency() -> CheckResult:
    """signal_runner.STOCKS must match screener.get_current_universe()."""
    name = "Universe consistency"
    try:
        stocks   = sorted(set(_load_stocks()))
        universe = sorted(set(_load_screener_universe()))

        if stocks == universe:
            return CheckResult(
                name, "PASS",
                f"MATCH — {len(stocks)} stocks in both",
                {"stocks": stocks},
            )

        only_stocks   = sorted(set(stocks)   - set(universe))
        only_universe = sorted(set(universe) - set(stocks))
        return CheckResult(
            name, "FAIL",
            f"MISMATCH — only in STOCKS: {only_stocks or 'none'}; "
            f"only in screener universe: {only_universe or 'none'}",
            {"only_stocks": only_stocks, "only_universe": only_universe},
        )
    except Exception as exc:
        return CheckResult(name, "FAIL", f"Exception: {exc}", {})


# ── Check 4: Degradation tracker health ──────────────────────────────────────

def check_degradation_tracker() -> CheckResult:
    """Cross-reference tracker against live universe; surface stale/missing/flagged entries."""
    name = "Degradation tracker"
    try:
        raw = json.loads(DEGRADATION_TRACKER.read_text())
        tracker = {k: v for k, v in raw.items() if k != "_meta"}

        universe     = sorted(set(_load_stocks()))
        tracker_keys = set(tracker.keys())
        universe_set = set(universe)

        orphaned = sorted(tracker_keys - universe_set)
        missing  = sorted(universe_set - tracker_keys)
        flagged  = sorted(
            t for t, v in tracker.items()
            if v.get("consecutive_flags", 0) >= 2
        )
        watch = [
            (t, v["consecutive_flags"])
            for t, v in tracker.items()
            if 0 < v.get("consecutive_flags", 0) < 2
        ]
        watch.sort()

        detail = {
            "orphaned":            orphaned,
            "missing_from_tracker": missing,
            "flagged_for_remove":  flagged,
            "watch_1_flag":        [f"{t} ({n})" for t, n in watch],
        }

        problems = []
        if orphaned:
            problems.append(f"orphaned entries (not in universe): {orphaned}")
        if missing:
            problems.append(f"universe ticker(s) not yet in tracker: {missing}")
        if flagged:
            problems.append(f"consecutive_flags ≥ 2 (REMOVE candidate): {flagged}")

        if problems:
            status = "FAIL" if (orphaned or flagged) else "WARN"
            note = "; ".join(problems)
            if watch:
                note += f"; also watching: {[f'{t} ({n})' for t, n in watch]}"
            return CheckResult(name, status, note, detail)

        watch_note = (
            "; ".join(f"{t} ({n} consecutive flag)" for t, n in watch)
            if watch else "no flags to watch"
        )
        return CheckResult(
            name, "PASS",
            f"no orphaned entries, no REMOVE candidates; {watch_note} "
            f"({len(universe)} stocks tracked)",
            detail,
        )
    except Exception as exc:
        return CheckResult(name, "FAIL", f"Exception: {exc}", {})


# ── Check 5: Candidates file freshness ────────────────────────────────────────

def check_candidates_freshness() -> CheckResult:
    """latest_candidates.json must exist and not be older than one screener cycle."""
    name = "Candidates file"
    try:
        if not CANDIDATES_FILE.exists():
            days_since = (date.today() - _CANDIDATES_FEATURE_DATE).days
            return CheckResult(
                name, "WARN",
                f"not found — feature deployed {_CANDIDATES_FEATURE_DATE} "
                f"({days_since}d ago); created by the next real screener run",
                {},
            )

        raw         = json.loads(CANDIDATES_FILE.read_text())
        screen_date = date.fromisoformat(raw["screen_date"])
        age_days    = (date.today() - screen_date).days

        if age_days > _CANDIDATES_STALE_DAYS:
            return CheckResult(
                name, "WARN",
                f"stale — screen_date {screen_date} ({age_days}d ago; "
                f"expected ≤ {_CANDIDATES_STALE_DAYS}d for Wed/Sun cadence)",
                {"screen_date": str(screen_date), "age_days": age_days},
            )

        n_add   = len(raw.get("add_tickers",       []))
        n_watch = len(raw.get("watchlist_tickers", []))
        return CheckResult(
            name, "PASS",
            f"screen_date {screen_date} ({age_days}d ago), {n_add} ADD / {n_watch} WATCH",
            {"screen_date": str(screen_date), "age_days": age_days},
        )
    except Exception as exc:
        return CheckResult(name, "FAIL", f"Exception: {exc}", {})


# ── Check 6: Relative-path constant scan ─────────────────────────────────────

# Matches lines like `FOO = Path("relative/path")` or `FOO = Path('relative/path')`.
# Does NOT match: Path("/absolute"), Path(__file__...), _ROOT / "something"
_REL_PATH_RE = re.compile(r"=\s*Path\(['\"](?!/|__)")


def _masked_spans(src: str) -> dict:
    """
    Map line number -> list of (start_col, end_col) spans occupied by STRING or
    COMMENT tokens.

    The scanner below previously skipped only lines starting with "#", so any
    `Path("relative")` appearing inside a DOCSTRING was reported as real code.
    That produced a permanent self-referential WARN: this module's own
    _discover_scan_files() docstring cites `write_text('FOO = Path("relative")')`
    as an example of a false positive, and the scanner then flagged it — the
    tool warning about itself, forever.

    Fixed at the source by tokenising rather than by excluding this filename,
    so it generalises to any docstring in any file. A match is suppressed only
    when its START lies inside a string/comment token; a genuine
    `FOO = Path("x")` still matches, because the regex anchors on the `=` sign,
    which is an OP token outside the string.
    """
    import io
    import tokenize

    spans: dict = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                lo = scol if row == srow else 0
                hi = ecol if row == erow else 10 ** 9
                spans.setdefault(row, []).append((lo, hi))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable file: fall back to no masking. The old "#"-prefix skip
        # below still applies, so behaviour degrades to the previous scanner
        # rather than to silence.
        return {}
    return spans


def check_relative_path_constants() -> CheckResult:
    """Scan source files for module-level relative-path Path() constants."""
    name = "Relative-path scan"
    try:
        hits = []
        scanned = 0
        for fpath in _discover_scan_files():
            if not fpath.exists():
                continue
            scanned += 1
            _src   = fpath.read_text()
            _spans = _masked_spans(_src)
            for lineno, line in enumerate(_src.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                _m = _REL_PATH_RE.search(line)
                if _m and not any(
                    lo <= _m.start() < hi for lo, hi in _spans.get(lineno, ())
                ):
                    try:
                        rel = fpath.relative_to(_ROOT)
                    except ValueError:
                        rel = fpath
                    hits.append(f"{rel}:{lineno}: {stripped}")

        if hits:
            return CheckResult(
                name, "WARN",
                f"{len(hits)} relative-path constant(s) found in {scanned} files "
                f"— review for cwd-dependency risk",
                {"hits": hits},
            )
        return CheckResult(
            name, "PASS",
            f"0 relative-path constants found in {scanned} scanned files",
            {},
        )
    except Exception as exc:
        return CheckResult(name, "FAIL", f"Exception: {exc}", {})


# ── Report ────────────────────────────────────────────────────────────────────

def _render(results: list[CheckResult]) -> None:
    now_ist = datetime.now(timezone.utc).replace(tzinfo=None) + _IST
    print(f"\n=== System Health Check — {now_ist.strftime('%Y-%m-%d %H:%M')} IST ===\n")

    counts: dict[str, int] = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        print(f"[{r.status}] {r.name}: {r.message}")
        for line in r.details.get("hits", []):      # Check 6 — relative-path hits
            print(f"       {line}")
        for issue in r.details.get("issues", []):   # Check 2 — completion failures
            print(f"       {issue}")

    print(
        f"\nOverall: {counts['PASS']} PASS, {counts['WARN']} WARN, "
        f"{counts['FAIL']} FAIL\n"
    )


def main() -> int:
    checks = [
        check_screener_cadence,
        check_run_completion,
        check_universe_consistency,
        check_degradation_tracker,
        check_candidates_freshness,
        check_relative_path_constants,
    ]
    results = [fn() for fn in checks]
    _render(results)
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
