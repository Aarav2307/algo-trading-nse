"""
utils/test_market_calendar.py — Tests for the NSE holiday/trading-day calendar.

Added Jul 21 2026 after a coverage pass turned up that this module — despite
being load-bearing for every date decision in paper trading (fill checks,
corp-action windows, bars_held, WF bar counts) — had zero direct tests.
Everything it currently gets to run was only exercised incidentally by other
modules' tests. In particular, before these tests, is_trading_day()'s
weekend-detection branch and next_trading_day()'s skip-loop had never
actually executed under test.

Dates used below are pinned to the real 2026 NSE_HOLIDAYS_2026 list so these
tests double as a check that the hardcoded calendar and the lookup logic
agree with each other.
"""
from datetime import date
from unittest.mock import patch

from utils.market_calendar import (
    count_trading_days_since,
    get_trading_days_between,
    is_trading_day,
    next_trading_day,
    verify_holiday_coverage,
)


# ── is_trading_day() ──────────────────────────────────────────────────────────

def test_is_trading_day_false_for_saturday():
    assert is_trading_day(date(2026, 7, 18)) is False


def test_is_trading_day_false_for_sunday():
    assert is_trading_day(date(2026, 7, 19)) is False


def test_is_trading_day_true_for_ordinary_weekday():
    assert is_trading_day(date(2026, 7, 20)) is True   # Monday, not a holiday


def test_is_trading_day_false_for_weekday_holiday():
    """Jan 26 2026 (Republic Day) is a Monday — this exercises the holiday-set
    lookup specifically, not just the weekend check."""
    assert is_trading_day(date(2026, 1, 26)) is False


def test_is_trading_day_true_day_after_weekday_holiday():
    assert is_trading_day(date(2026, 1, 27)) is True


def test_is_trading_day_fails_open_when_api_and_cache_both_miss(capsys):
    """For a year with no hardcoded list (e.g. 2027) and no cached/fetched
    holidays, is_trading_day() must fail OPEN (treat it as a trading day)
    rather than silently blocking every day of that year."""
    with patch("utils.market_calendar.fetch_nse_holidays", return_value=[]):
        result = is_trading_day(date(2027, 3, 10))   # Wednesday

    assert result is True
    assert "WARNING" in capsys.readouterr().out


def test_is_trading_day_uses_fetched_holidays_for_non_hardcoded_year():
    """When the API/cache does return holidays for a non-hardcoded year,
    is_trading_day() must actually use them, not just fail open."""
    fetched = [date(2027, 3, 10)]
    with patch("utils.market_calendar.fetch_nse_holidays", return_value=fetched):
        assert is_trading_day(date(2027, 3, 10)) is False   # in fetched list
        assert is_trading_day(date(2027, 3, 11)) is True    # not in fetched list


# ── next_trading_day() ────────────────────────────────────────────────────────

def test_next_trading_day_skips_plain_weekend():
    assert next_trading_day(date(2026, 7, 17)) == date(2026, 7, 20)   # Fri -> Mon


def test_next_trading_day_skips_weekend_and_weekday_holiday():
    """Regression test for the skip-loop: Jan 23 2026 (Fri) -> Jan 26 is
    Republic Day (Mon) -> first real trading day is Jan 27 (Tue)."""
    assert next_trading_day(date(2026, 1, 23)) == date(2026, 1, 27)


# ── get_trading_days_between() ────────────────────────────────────────────────

def test_get_trading_days_between_excludes_weekend():
    result = get_trading_days_between(date(2026, 7, 17), date(2026, 7, 20))
    assert result == [date(2026, 7, 17), date(2026, 7, 20)]


def test_get_trading_days_between_excludes_weekday_holiday():
    result = get_trading_days_between(date(2026, 1, 23), date(2026, 1, 27))
    assert result == [date(2026, 1, 23), date(2026, 1, 27)]


def test_get_trading_days_between_single_trading_day_is_inclusive():
    result = get_trading_days_between(date(2026, 7, 20), date(2026, 7, 20))
    assert result == [date(2026, 7, 20)]


# ── verify_holiday_coverage() ─────────────────────────────────────────────────

def test_verify_holiday_coverage_no_warnings_for_2026():
    """All 4 fixed holidays are present in NSE_HOLIDAYS_2026 (Independence Day
    falls on a Saturday that year and is correctly skipped by the weekend guard)."""
    assert verify_holiday_coverage(2026) == []


def test_verify_holiday_coverage_flags_missing_fixed_holidays():
    """2025 isn't in the hardcoded calendar at all, so every fixed holiday that
    doesn't fall on a weekend that year must be reported missing. Republic Day
    2025-01-26 is a Sunday, so it must NOT appear (weekend guard applies first)."""
    warnings = verify_holiday_coverage(2025)

    assert any("Independence Day" in w for w in warnings)
    assert any("Gandhi Jayanti" in w for w in warnings)
    assert any("Christmas" in w for w in warnings)
    assert not any("Republic Day" in w for w in warnings)


# ── count_trading_days_since() ────────────────────────────────────────────────

def test_count_trading_days_since_matches_get_trading_days_between():
    mocked_today = date(2026, 7, 20)   # Monday
    entry_date   = date(2026, 7, 17)   # Friday — spans the weekend

    with patch("utils.market_calendar.date") as mock_date:
        mock_date.today.return_value = mocked_today
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        count = count_trading_days_since(entry_date)

    # Fri 17 (trading) + Sat/Sun (skipped) + Mon 20 (trading) = 2
    assert count == 2
