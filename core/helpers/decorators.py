import time
import logging
import requests
from functools import wraps
from typing import Callable, Dict, Sequence, Tuple, Union

logger = logging.getLogger(__name__)


# Specification of a prerequisite filter: either a bare name
# (the filter is called without extra kwargs) or a (name, kwargs) tuple.
ParamFilterSpec = Union[str, Tuple[str, Dict]]


def _normalize_filter_spec(spec: ParamFilterSpec) -> Tuple[str, Dict]:
    if isinstance(spec, str):
        return spec, {}
    return spec[0], (spec[1] or {})


def with_param_filters(filters: Sequence[ParamFilterSpec]):
    """
    Декоратор для методов сервиса фильтрации.

    Перед вызовом декорируемого метода прогоняет переданный датафрейм
    через набор "параметрических" фильтров (prerequisite filters), цепочкой
    передавая выход одного фильтра на вход следующему. Только после этого
    вызывает сам декорируемый метод с уже отфильтрованным датафреймом.

    Фильтры разрешаются по имени через getattr(self, name) и должны
    возвращать (filtered_df, original_df) либо просто DataFrame.

    Чтобы избежать бесконечной рекурсии и дублирующей фильтрации, если фильтр
    сам имеет with_param_filters, его собственные параметрические фильтры
    выполняются только при прямом вызове, а не когда он запускается как
    чей-то prerequisite (отслеживается через self._param_filters_active).
    """
    normalized = [_normalize_filter_spec(f) for f in filters]

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, df, *args, **kwargs):
            active = getattr(self, "_param_filters_active", set())
            if func.__name__ in active:
                # Нас вызвали как prerequisite — не запускаем свои prerequisites.
                return func(self, df, *args, **kwargs)

            for pf_name, pf_kwargs in normalized:
                if pf_name in active:
                    continue
                self._param_filters_active = active | {pf_name}
                try:
                    result = getattr(self, pf_name)(df, **pf_kwargs)
                finally:
                    self._param_filters_active = active
                df = result[0] if isinstance(result, tuple) else result

            return func(self, df, *args, **kwargs)

        return wrapper

    return decorator

def retry_on_status(max_retries=3, retry_delays=None, status_codes=None):
    if retry_delays is None:
        retry_delays = [5, 10, 20]
    if status_codes is None:
        status_codes = [429]

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while attempt < max_retries:
                try:
                    return func(*args, **kwargs)
                except requests.HTTPError as e:
                    resp = e.response
                    if resp is not None and resp.status_code in status_codes:
                        attempt += 1
                        if attempt < max_retries:
                            delay = retry_delays[attempt - 1]
                            logger.warning(
                                f"Ошибка {resp.status_code}, попытка {attempt}/{max_retries}, жду {delay} сек."
                            )
                            time.sleep(delay)
                            continue
                        else:
                            logger.error("Исчерпаны попытки после ошибки %s", resp.status_code)
                            raise
                    raise
            return None

        return wrapper

    return decorator
