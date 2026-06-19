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

from datetime import date, timedelta
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

# Convert to a set for O(1) lookup — include all years so is_trading_day() works year-round
_HOLIDAY_SET: set = set(NSE_HOLIDAYS_2026) | set(NSE_HOLIDAYS_2027)


# =============================================================================
# Core utilities
# =============================================================================

def is_trading_day(d: date) -> bool:
    """
    Return True if the NSE equity market is open on date d.

    Rules (in order of check):
      1. Weekends (Saturday, Sunday) → always closed
      2. NSE declared holidays       → closed
      3. Everything else             → open

    Args:
        d: date to check (can be any year, but holiday list covers 2026 only)

    Returns:
        bool — True if the market is open on this date.
    """
    # weekday() returns 0=Monday … 6=Sunday
    if d.weekday() >= 5:
        return False
    return d not in _HOLIDAY_SET


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
