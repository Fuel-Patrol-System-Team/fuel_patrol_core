from typing import Optional, Tuple


def validate_agg(agg) -> Tuple[Optional[int], Optional[str]]:
    if agg is None or agg == "":
        return None, None

    try:
        agg = int(agg)
    except (TypeError, ValueError):
        return None, "Параметр agg должен быть целым числом (минут)"

    if agg <= 0:
        return None, "Параметр agg должен быть положительным числом минут (больше 0)"

    return agg, None