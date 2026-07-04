"""
utils/market_calendar.py — NSE equity market holiday calendar and trading day utilities.

IMPORTANT: Festival-linked holidays shift every year with the lunar calendar.
Dates marked (tentative) are best estimates only.

VERIFY ALL DATES against the official NSE circular before live deployment:
  https://www.nseindia.com/resources/exchange-communication-holidays

Usage:
    from utils.market_calendar import is_trading_day, next_trading_day

    if not is_trading_day(date.today()):
        print("Market closed today")
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List


# =============================================================================
# 2026 NSE Equity Segment Holidays
# Source: NSE India official holiday circular (verify before live deployment)
# =============================================================================

NSE_HOLIDAYS_2026: List[date] = [
    # IMPORTANT: NEVER remove past holidays from this list.
    # The _HOLIDAY_SET lookup is O(1) — there is no performance reason to purge entries.
    # Removing past holidays causes is_trading_day() to incorrectly return True
    # for known market closure dates, breaking corporate action danger windows,
    # next_trading_day() calculations, and walk-forward bar counts.
    #
    # Source: NSE India official holiday circular
    # Verify lunar calendar holidays (Holi, Eid, Diwali) against:
    # https://www.nseindia.com/resources/exchange-communication-holidays

    # ── Jan–May (restored — previously and incorrectly removed) ──────────────
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 26),   # Maha Shivaratri
    date(2026, 3, 25),   # Holi
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 4, 30),   # Shri Ram Navami (tentative — verify against NSE circular)
    date(2026, 5, 1),    # Maharashtra Day / Labour Day

    # ── Jun–Dec ───────────────────────────────────────────────────────────────
    date(2026, 6, 26),   # Muharram (verified Jun 2026)
    date(2026, 8, 15),   # Independence Day (falls on Saturday — in set for completeness)
    date(2026, 8, 27),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 10, 20),  # Diwali Laxmi Pujan (verify — changes yearly)
    date(2026, 10, 21),  # Diwali Balipratipada (verify)
    date(2026, 11, 5),   # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
]

NSE_HOLIDAYS_2027: List[date] = []  # Populate before December 2026

# Convert to a set for O(1) lookup — kept for verify_holiday_coverage() backward compat
_HOLIDAY_SET: set = set(NSE_HOLIDAYS_2026) | set(NSE_HOLIDAYS_2027)

# Path for caching NSE holiday data fetched from official API
_HOLIDAY_CACHE_FILE = Path(__file__).parent.parent / "utils" / "nse_holiday_cache.json"

# Years with complete, verified hardcoded holiday lists.
# 2024 and 2025 are intentionally excluded — they were never hardcoded here,
# so those years fall through to the live API fetch (which is correct).
_HARDCODED_YEARS = {2026}


# =============================================================================
# NSE holiday API fetch + cache
# =============================================================================

def fetch_nse_holidays(year: int) -> list[date]:
    """
    Fetch official NSE trading holidays for a given year from
    NSE's holiday API. Caches result locally to avoid repeated
    API calls.

    API: https://www.nseindia.com/api/holiday-master?type=trading
    Segment: CM (Capital Market / Equity) — the segment this system trades.

    Returns list of date objects for trading holidays in the given year.
    Returns empty list on failure (caller should use hardcoded fallback).
    """
    # Check cache first
    cache = _load_holiday_cache()
    cache_key = str(year)
    if cache_key in cache:
        cached_dates = [
            date.fromisoformat(d) for d in cache[cache_key]["dates"]
        ]
        print(f"[market_calendar] {year} holidays: {len(cached_dates)} "
              f"(cached {cache[cache_key]['fetched_at']})")
        return cached_dates

    # Fetch from NSE API
    try:
        import requests
        session = requests.Session()
        # NSE requires a session cookie — hit homepage first
        session.get(
            "https://www.nseindia.com",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        r = session.get(
            "https://www.nseindia.com/api/holiday-master?type=trading",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept":     "application/json",
                "Referer":    "https://www.nseindia.com",
            },
            timeout=15
        )
        r.raise_for_status()
        data = r.json()

        # CM = Capital Market (equity segment)
        cm_holidays = data.get("CM", [])
        if not cm_holidays:
            print("[market_calendar] WARNING: CM segment empty in NSE response")
            return []

        # Parse and filter for the requested year
        holidays = []
        for item in cm_holidays:
            try:
                # Format: "26-Jan-2026"
                d = datetime.strptime(
                    item["tradingDate"], "%d-%b-%Y"
                ).date()
                if d.year == year:
                    holidays.append(d)
            except (KeyError, ValueError):
                continue

        if not holidays:
            print(f"[market_calendar] WARNING: No CM holidays found "
                  f"for {year} in NSE response. "
                  f"NSE may not have published {year} list yet.")
            return []

        # Save to cache
        holidays.sort()
        cache[cache_key] = {
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count":      len(holidays),
            "dates":      [d.isoformat() for d in holidays],
        }
        _save_holiday_cache(cache)
        print(f"[market_calendar] {year} holidays: {len(holidays)} "
              f"(fetched from NSE API, cached)")
        return holidays

    except Exception as e:
        print(f"[market_calendar] NSE holiday API failed for {year}: {e}")
        return []


def _load_holiday_cache() -> dict:
    """Load holiday cache from disk. Returns empty dict on missing/corrupt."""
    try:
        if _HOLIDAY_CACHE_FILE.exists():
            with open(_HOLIDAY_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_holiday_cache(cache: dict) -> None:
    """Save holiday cache to disk. Silently ignores errors."""
    try:
        _HOLIDAY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_HOLIDAY_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[market_calendar] WARNING: Could not save holiday cache: {e}")


# =============================================================================
# Core utilities
# =============================================================================

def is_trading_day(d: date) -> bool:
    """
    Return True if the NSE equity market is open on date d.

    For 2026: uses the hardcoded verified list from the NSE circular.
    For all other years: fetches from the NSE official holiday API with
    local cache (utils/nse_holiday_cache.json). On API failure with no
    cache, fails-open (treats the day as a trading day) with a warning.

    Args:
        d: date to check

    Returns:
        bool — True if the market is open on this date.
    """
    # weekday() returns 0=Monday … 6=Sunday
    if d.weekday() >= 5:
        return False

    # Build holiday set for this specific year
    if d.year in _HARDCODED_YEARS:
        year_holidays = {
            2026: NSE_HOLIDAYS_2026,
        }[d.year]
    else:
        # Fetch from NSE API (cached after first call per year)
        year_holidays = fetch_nse_holidays(d.year)
        if not year_holidays:
            # API failed and no cache — fail-open with warning
            print(
                f"[market_calendar] WARNING: Could not determine "
                f"holidays for {d.year}. Treating {d} as trading day. "
                f"Check NSE connectivity."
            )
            return True

    return d not in set(year_holidays)


def next_trading_day(d: date) -> date:
    """
    Return the next NSE trading day strictly after d.

    Example: next_trading_day(date(2026, 1, 23)) → date(2026, 1, 26)
    (Jan 24 Saturday, Jan 25 Sunday, Jan 26 Republic Day → Jan 27 is first open day)

    Args:
        d: starting date (not included in the search)

    Returns:
        The first trading day after d.
    """
    candidate = d + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def get_trading_days_between(start: date, end: date) -> List[date]:
    """
    Return a list of all NSE trading days in the closed interval [start, end].

    Both start and end are included if they are trading days.

    Args:
        start: first date of the range (inclusive)
        end:   last date of the range  (inclusive)

    Returns:
        List of date objects in ascending order.

    Example:
        get_trading_days_between(date(2026, 1, 23), date(2026, 1, 28))
        → [date(2026, 1, 27), date(2026, 1, 28)]   (Jan 24-25 weekend, Jan 26 holiday)
    """
    result: List[date] = []
    current = start
    while current <= end:
        if is_trading_day(current):
            result.append(current)
        current += timedelta(days=1)
    return result


def verify_holiday_coverage(year: int) -> list:
    """
    Check that known fixed NSE holidays for a given year are in the holiday set.
    Returns list of warning strings for any missing fixed holidays.
    Used as a sanity check — call at startup in signal_runner.py.

    Fixed holidays (same date every year):
    - Jan 26: Republic Day
    - Aug 15: Independence Day
    - Oct 2:  Gandhi Jayanti
    - Dec 25: Christmas

    Weekends are skipped — if a fixed holiday falls on Saturday/Sunday, the
    weekday() check in is_trading_day() already handles it correctly.
    """
    warnings = []
    fixed_holidays = {
        date(year, 1, 26): "Republic Day",
        date(year, 8, 15): "Independence Day",
        date(year, 10, 2): "Gandhi Jayanti",
        date(year, 12, 25): "Christmas",
    }
    for d, name in fixed_holidays.items():
        if d.weekday() >= 5:
            continue   # falls on weekend — weekday guard handles it, no entry needed
        if d not in _HOLIDAY_SET:
            warnings.append(f"WARNING: {name} ({d}) is not in NSE holiday calendar")
    return warnings


def count_trading_days_since(d: date) -> int:
    """
    Count how many trading days have elapsed from d up to and including today.
    Used by the paper portfolio to track bars_held across weekends/holidays.

    Args:
        d: the entry date (inclusive in count)

    Returns:
        Number of trading days from d to today, inclusive.
    """
    return len(get_trading_days_between(d, date.today()))


def refresh_holiday_cache(years: list[int] = None) -> dict:
    """
    Fetch and cache holiday data for given years from NSE API.
    Call this once manually to pre-populate the cache.

    Usage:
        python3 -c "
        from utils.market_calendar import refresh_holiday_cache
        refresh_holiday_cache([2027])
        "

    Returns {year: count} of holidays fetched.
    """
    if years is None:
        current_year = date.today().year
        years = [current_year, current_year + 1]

    results = {}
    for year in years:
        holidays = fetch_nse_holidays(year)
        results[year] = len(holidays)
        if holidays:
            print(f"  {year}: {len(holidays)} holidays cached")
        else:
            print(f"  {year}: fetch failed or not yet published by NSE")
    return results
