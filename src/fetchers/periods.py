"""
Upper bounds of the periods (weeks / months) to request, judged in JST.

Weekly series use the pair (ISO week-year, ISO week); monthly series use (year, month).
Mixing the calendar year with the ISO week number truncates the current year to W01 when
Dec 29-31 already belong to next year's W01, and requests future weeks of the new year when
Jan 1-3 still belong to last year's W52/53. The clock is JST, as in src/cli/check_missing.py.

Callers must call `periods.now_jst()` through the module so that tests can pin the clock by
patching `src.fetchers.periods.now_jst`.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    """Current time as an aware JST datetime."""
    return datetime.now(JST)


def weeks_in_year(year: int) -> int:
    """Number of ISO weeks in `year` (52 or 53)."""
    return date(year, 12, 28).isocalendar()[1]


def max_period_for_year(year: int, *, monthly: bool, now: datetime) -> int:
    """Largest week number or month of `year` that may be requested at `now` (0 means none).

    Args:
        year: Year of the file name and request parameters (the ISO week-year for weekly series).
        monthly: True for monthly series, False for weekly ones.
        now: Reference time (aware); converted to JST before comparing.
    """
    local = now.astimezone(JST)
    if monthly:
        if year < local.year:
            return 12
        if year == local.year:
            return local.month
        return 0

    iso_year, iso_week, _ = local.isocalendar()
    if year < iso_year:
        return weeks_in_year(year)
    if year == iso_year:
        return iso_week
    return 0
