"""
utils/news_monitor.py — Nightly pre-trade risk monitor.

Runs at 7 PM IST after market close. Checks all universe stocks for:
  1. NSE surveillance flags (ASM/ESM/GSM) — auto-block entries
  2. Board meetings within 5 trading days — earnings risk warning

Output: utils/news_flags.json consumed by signal_runner.py

Cron: 0 13 * * 1-5  /home/ubuntu/algo-trading/utils/run_news_monitor.sh
(7 PM IST = 13:30 UTC, run Mon-Fri)
"""

import io
import json
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from utils.market_calendar import is_trading_day

NEWS_FLAGS_FILE    = Path(__file__).parent / "news_flags.json"
MANUAL_BLOCKS_FILE = Path(__file__).parent / "manual_blocks.json"
ERROR_LOG_FILE     = Path(__file__).parent / "news_monitor_errors.log"
SURVEILLANCE_BANDS = {"2", "5"}   # tight circuit = under surveillance
EARNINGS_DAYS_AHEAD = 5           # flag if board meeting within 5 trading days


def _get_universe() -> list:
    """Return the live STOCKS list from signal_runner.py via direct import."""
    from paper_trading.signal_runner import STOCKS
    return list(STOCKS)


def _log_crash(exc: Exception) -> None:
    """Append a full traceback to ERROR_LOG_FILE. Fail-open: OSError is silenced."""
    detail = traceback.format_exc()
    try:
        with open(ERROR_LOG_FILE, "a") as f:
            f.write(
                f"\n{'=' * 70}\n"
                f"{datetime.now().isoformat()}\n"
                f"{'=' * 70}\n"
                f"{detail}\n"
            )
    except OSError:
        pass


def fetch_surveillance_flags(universe: list[str]) -> dict:
    """
    Check universe stocks against NSE surveillance list.
    Returns {ticker: {type, detail, auto_block}} for flagged stocks only.
    Clean stocks not included in return value.
    """
    url   = "https://nsearchives.nseindia.com/content/equities/sec_list.csv"
    flags = {}
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/csv,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()

        import pandas as pd
        df = pd.read_csv(io.StringIO(r.text))

        for ticker in universe:
            symbol = ticker.replace(".NS", "")
            row    = df[df["Symbol"] == symbol]
            if row.empty:
                continue
            band    = str(row["Band"].iloc[0]).strip()
            remarks = str(row["Remarks"].iloc[0]).strip()
            is_surveillance = band in SURVEILLANCE_BANDS or "GSM" in remarks.upper()
            if is_surveillance:
                flags[ticker] = {
                    "type":       "SURVEILLANCE",
                    "detail":     f"Band={band}, Remarks={remarks}",
                    "auto_block": True,
                    "source":     "NSE sec_list.csv",
                }

    except Exception as e:
        print(
            f"[news_monitor] WARNING: Surveillance fetch failed: {e} "
            f"— returning empty (fail-open)"
        )

    return flags


def fetch_earnings_flags(universe: list[str], trading_days_ahead: int = EARNINGS_DAYS_AHEAD) -> dict:
    """
    Check universe stocks for board meetings within N trading days.
    Returns {ticker: {type, detail, auto_block, meeting_date}} for flagged stocks only.
    """
    flags = {}
    try:
        session = requests.Session()
        session.get(
            "https://www.nseindia.com",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )

        today    = date.today()
        end      = today + timedelta(days=30)
        from_str = today.strftime("%d-%m-%Y")
        to_str   = end.strftime("%d-%m-%Y")

        url = (
            "https://www.nseindia.com/api/corporate-board-meetings"
            f"?index=equities&from_date={from_str}&to_date={to_str}"
        )
        r = session.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept":     "application/json",
                "Referer":    "https://www.nseindia.com",
            },
            timeout=15,
        )
        r.raise_for_status()
        meetings = r.json()

        for meeting in meetings:
            symbol = meeting.get("bm_symbol", "")
            ticker = symbol + ".NS"
            if ticker not in universe:
                continue

            desc       = meeting.get("bm_desc", "").lower()
            is_results = any(
                kw in desc for kw in
                ["financial results", "quarterly", "unaudited", "audited financial"]
            )
            if not is_results:
                continue

            try:
                meeting_date = datetime.strptime(meeting["bm_date"], "%d-%b-%Y").date()
            except (KeyError, ValueError):
                continue

            trading_days = sum(
                1 for i in range((meeting_date - today).days + 1)
                if is_trading_day(today + timedelta(days=i))
            )

            if trading_days <= trading_days_ahead:
                flags[ticker] = {
                    "type":              "EARNINGS_RISK",
                    "detail":            f"Board meeting {meeting['bm_date']} — Q results",
                    "auto_block":        False,
                    "meeting_date":      meeting["bm_date"],
                    "trading_days_away": trading_days,
                    "source":            "NSE corporate-board-meetings API",
                }

    except Exception as e:
        print(
            f"[news_monitor] WARNING: Earnings fetch failed: {e} "
            f"— returning empty (fail-open)"
        )

    return flags


def run_monitor(universe: list[str]) -> dict:
    """
    Main entry point. Fetches all flags, merges with manual blocks,
    writes news_flags.json. Returns the flags dict.
    """
    print(f"[news_monitor] Checking {len(universe)} stocks: {', '.join(universe)}")

    surv_flags     = fetch_surveillance_flags(universe)
    earnings_flags = fetch_earnings_flags(universe)

    # Surveillance flags take priority over earnings flags
    merged_flags = {**earnings_flags, **surv_flags}

    # Load manual blocks — manual always wins
    today_str = date.today().isoformat()
    if MANUAL_BLOCKS_FILE.exists():
        try:
            with open(MANUAL_BLOCKS_FILE) as f:
                raw_blocks = json.load(f)
            for ticker, block in raw_blocks.items():
                blocked_until = block.get("blocked_until", "")
                if blocked_until >= today_str:
                    merged_flags[ticker] = {
                        "type":       "MANUAL_BLOCK",
                        "detail":     block.get("reason", "Manual block"),
                        "auto_block": True,
                        "source":     "manual_blocks.json",
                    }
        except Exception as e:
            print(f"[news_monitor] WARNING: Could not load manual blocks: {e}")

    n_surv   = sum(1 for f in merged_flags.values() if f["type"] == "SURVEILLANCE")
    n_earn   = sum(1 for f in merged_flags.values() if f["type"] == "EARNINGS_RISK")
    n_manual = sum(1 for f in merged_flags.values() if f["type"] == "MANUAL_BLOCK")

    result = {
        "generated_at":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe_checked": universe,
        "flags":            merged_flags,
    }

    # Atomic write: write to .tmp then rename
    tmp_path = NEWS_FLAGS_FILE.with_suffix(".tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(result, f, indent=2)
        tmp_path.rename(NEWS_FLAGS_FILE)
    except Exception as e:
        print(f"[news_monitor] ERROR: Could not write flags file: {e}")
        if tmp_path.exists():
            tmp_path.unlink()

    print(
        f"[news_monitor] Checked {len(universe)} stocks — "
        f"{n_surv} surveillance, {n_earn} earnings risk, {n_manual} manual blocks"
    )
    return result


if __name__ == "__main__":
    try:
        universe = _get_universe()
        print(f"[news_monitor] Universe: {universe}")
        result = run_monitor(universe)
        print(json.dumps(result, indent=2, default=str))
    except Exception as exc:
        _log_crash(exc)
        from utils.alerts import send_crash_alert
        send_crash_alert("news_monitor.py", exc)
        raise
