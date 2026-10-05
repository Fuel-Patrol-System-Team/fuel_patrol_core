from datetime import datetime, timedelta

import pytz


def midnight_utc(value: datetime) -> datetime:
    """Приводит datetime к полуночи UTC того же дня."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=pytz.UTC)
    return value.astimezone(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def compute_mileage_span_days(
    now: datetime,
    marker: datetime | None,
    start_manual: datetime | None = None,
    last_manual: datetime | None = None,
    force: bool = False,
    bootstrap_days: int = 1,
) -> list[datetime]:
    """
    Тулза для построения периода парсинга для таска
    """
    now_midnight = midnight_utc(now)

    # Конец диапазона: явный ручной конец, но завершенные дни — никогда
    # не дальше "вчера" (now_midnight); будущие дни отсекаются всегда.
    if last_manual is not None:
        span_end = min(midnight_utc(last_manual), now_midnight)
    else:
        span_end = now_midnight

    # Начало диапазона.
    if start_manual is not None:
        span_start = midnight_utc(start_manual)
    elif force:
        span_start = span_end - timedelta(days=1)
    elif marker is not None:
        span_start = midnight_utc(marker)
    else:
        span_start = span_end - timedelta(days=bootstrap_days)

    if span_start >= span_end:
        # Включая steady state (маркер == вчерашней полуночи = конец-эксклюзив):
        # незавершенный день не считается никогда.
        return []

    return [span_start + timedelta(days=i) for i in range((span_end - span_start).days)]