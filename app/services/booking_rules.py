from __future__ import annotations

from datetime import date, datetime, timedelta

import jpholiday

# Default consultation slots (MVP fixed)
DEFAULT_SLOTS = [
    "10:00-11:00",
    "11:00-12:00",
    "14:00-15:00",
    "15:00-16:00",
]

# Additional closure dates (MVP固定)
EXTRA_CLOSED_DATES = {
    date(2025, 12, 29),
    date(2025, 12, 30),
    date(2025, 12, 31),
    date(2026, 1, 1),
    date(2026, 1, 2),
    date(2026, 1, 3),
}


def get_jst_today() -> date:
    """Return today's date in JST (UTC+9). Separated for monkeypatching in tests."""
    return (datetime.utcnow() + timedelta(hours=9)).date()


def booking_window(today: date | None = None) -> tuple[date, date]:
    """Return the inclusive booking window (tomorrow ~ 28 days ahead) based on JST today."""
    ref = today or get_jst_today()
    start = ref + timedelta(days=1)
    end = ref + timedelta(days=28)
    return start, end


def is_closed_day(target: date) -> bool:
    """Check if the target date is weekend, Japanese holiday, or additional closure."""
    return target.weekday() >= 5 or jpholiday.is_holiday(target) or target in EXTRA_CLOSED_DATES


def is_within_booking_window(target: date, today: date | None = None) -> bool:
    start, end = booking_window(today)
    return start <= target <= end
