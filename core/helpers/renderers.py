import math
from typing import Any, Iterable

from rest_framework.renderers import JSONRenderer

# Округляем только показатели, которые считает наша система. Данные, полученные извне
# (координаты, тарировочные таблицы, метаданные датчиков), отдаём как есть: фронт может
# отредактировать их и отправить обратно, а округление до сотых испортило бы значения.
DEFAULT_FLOAT_DIGITS = 2
DEFAULT_SKIP_KEYS = frozenset({"grades", "metadata", "latitude", "longitude"})


def round_floats(obj: Any, digits: int = DEFAULT_FLOAT_DIGITS, skip_keys: Iterable[str] = DEFAULT_SKIP_KEYS) -> Any:
    skip = skip_keys if isinstance(skip_keys, (set, frozenset)) else frozenset(skip_keys)
    return _round(obj, digits, skip)


def _round(obj: Any, digits: int, skip: frozenset) -> Any:
    if isinstance(obj, float):  # в т.ч. numpy.float64; bool сюда не попадает
        if not math.isfinite(obj):
            return None
        rounded = float(round(float(obj), digits))
        return 0.0 if rounded == 0 else rounded  # без «-0.0»
    if isinstance(obj, dict):
        return {
            key: value if (isinstance(key, str) and key.lower() in skip) else _round(value, digits, skip)
            for key, value in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_round(item, digits, skip) for item in obj]
    return obj


class RoundedJSONRenderer(JSONRenderer):
    digits = DEFAULT_FLOAT_DIGITS
    skip_keys = DEFAULT_SKIP_KEYS

    def render(self, data, accepted_media_type=None, renderer_context=None):
        view = (renderer_context or {}).get("view")
        digits = getattr(view, "float_round_digits", self.digits)
        if digits is not None and data is not None:
            skip = self.skip_keys | frozenset(k.lower() for k in getattr(view, "float_round_skip_keys", ()))
            data = round_floats(data, digits, skip)
        return super().render(data, accepted_media_type, renderer_context)
