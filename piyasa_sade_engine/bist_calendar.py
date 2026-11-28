"""
Piyasa Sade — Resmi BIST Piyasa Takvimi ve Seans Durumu
- Hafta sonu ve Türkiye resmi tatillerinde (bayramlar, 1 Ocak, 23 Nisan, 1 Mayıs,
  19 Mayıs, 15 Temmuz, 30 Ağustos, 29 Ekim vb.) borsanın kapalı olduğunu bilir.
- "Açılışa 30 dk" veya "bugün borsa kapattı" gibi hafta sonu/tatil hatalarını
  tamamen engeller.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST_TZ = timezone(timedelta(hours=3))

# 2025 - 2027 Türkiye resmi BIST tam gün tatilleri (YYYY-MM-DD)
BIST_HOLIDAYS: set[str] = {
    # 2025
    "2025-01-01", "2025-03-31", "2025-04-01", "2025-04-23", "2025-05-01",
    "2025-05-19", "2025-06-06", "2025-06-09", "2025-07-15", "2025-08-30",
    "2025-10-29",
    # 2026
    "2026-01-01", "2026-03-20", "2026-04-23", "2026-05-01", "2026-05-19",
    "2026-05-27", "2026-05-28", "2026-05-29", "2026-07-15", "2026-08-30",
    "2026-10-29",
    # 2027
    "2027-01-01", "2027-03-09", "2027-03-10", "2027-03-11", "2027-04-23",
    "2027-05-01", "2027-05-17", "2027-05-18", "2027-05-19", "2027-07-15",
    "2027-08-30", "2027-10-29",
}


def is_bist_holiday(d: date) -> bool:
    return d.isoformat() in BIST_HOLIDAYS


def is_bist_trading_day(d: date) -> bool:
    return d.weekday() < 5 and not is_bist_holiday(d)


def get_market_status(ist_now: datetime) -> str:
    """Returns one of: 'open', 'pre_market', 'closed', 'weekend', 'holiday'."""
    today = ist_now.date()
    if today.weekday() >= 5:
        return "weekend"
    if is_bist_holiday(today):
        return "holiday"
    mins = ist_now.hour * 60 + ist_now.minute
    if (9 * 60 + 55) <= mins <= (18 * 60 + 15):
        return "open"
    if (8 * 60) <= mins < (9 * 60 + 55):
        return "pre_market"
    return "closed"


def is_bist_session_active(ist_now: datetime) -> bool:
    if not is_bist_trading_day(ist_now.date()):
        return False
    mins = ist_now.hour * 60 + ist_now.minute
    # Active refresh window: 09:30 TR through 19:00 TR (includes closing settlement)
    return (9 * 60 + 30) <= mins <= (19 * 60)
