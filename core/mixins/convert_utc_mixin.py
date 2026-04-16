import pytz
from datetime import datetime


class TimestampTimezoneConverterMixin:

    def convert_timestamps_to_user_timezone(self, data, timezone_str: str = None):
        if not timezone_str or timezone_str == "UTC":
            return data

        try:
            user_tz = pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            return data

        def convert_timestamp(value):
            if isinstance(value, str):
                try:
                    if value.endswith("Z"):
                        value = value[:-1] + "+00:00"
                    dt = datetime.fromisoformat(value)
                    if dt.tzinfo is None:
                        dt = pytz.UTC.localize(dt)
                    return dt.astimezone(user_tz).isoformat()
                except:
                    return value

            elif isinstance(value, datetime):
                if value.tzinfo is None:
                    value = pytz.UTC.localize(value)
                return value.astimezone(user_tz).isoformat()

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
