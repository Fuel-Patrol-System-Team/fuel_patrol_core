import json
from datetime import datetime, date, timedelta
from decimal import Decimal
from uuid import UUID
import polars as pl


class JSONSerializer(json.JSONEncoder):
    """Кастомный JSON encoder для обработки специальных типов данных"""

    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif isinstance(obj, timedelta):
            return str(obj)
        elif isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, UUID):
            return str(obj)
        elif isinstance(obj, pl.DataFrame):
            return obj.to_dicts()
        elif isinstance(obj, pl.Series):
            return obj.to_list()
        elif hasattr(obj, '__dict__'):
            return obj.__dict__

        return super().default(obj)


def serialize_for_json(data):
    """Сериализует данные для сохранения в JSON поле"""
    try:
        return json.loads(json.dumps(data, cls=JSONSerializer))
    except Exception as e:
        return str(data)