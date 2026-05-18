from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimestampTimezoneConverterMixin:
    def convert_timestamps_to_user_timezone(self, data, timezone_str: str = None):
        if not timezone_str or timezone_str == "UTC":
            timezone_str = "UTC"

        try:
            user_tz = ZoneInfo(timezone_str)
        except (ZoneInfoNotFoundError, ValueError):
            user_tz = timezone.utc

        def convert_timestamp(value):
            if isinstance(value, str):
                try:
                    if value.endswith("Z"):
                        value = value[:-1] + "+00:00"

                    dt = datetime.fromisoformat(value)

                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)

                    return dt.astimezone(user_tz).isoformat()
                except ValueError:
                    return value

            elif isinstance(value, datetime):
                try:
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    return value.astimezone(user_tz).isoformat()
                except Exception:
                    return value

            return value

        def traverse(obj):
            if isinstance(obj, dict):
                new_obj = {}
                for k, v in obj.items():
                    if k.lower() in (
                            "timestamp",
                            "time",
                            "dt",
                            "datetime",
                            "created_at",
                    ):
                        new_obj[k] = convert_timestamp(v)
                    else:
                        new_obj[k] = traverse(v)
                return new_obj

            elif isinstance(obj, list):
                return [traverse(item) for item in obj]

            return obj

        return traverse(data)
