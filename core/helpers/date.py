from datetime import datetime
from zoneinfo import ZoneInfo


def _parse_to_aware(value: str, target_timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=target_timezone)
    return parsed
