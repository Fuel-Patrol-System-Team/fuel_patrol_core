import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

import pytz
from core.models import OrgUser
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from core.services.providers.car_move_stop import MileageStopsCalculationService


def get_stops_mileage_report(
    car_id: str,
    start_date: datetime,
    end_date: datetime,
    user: OrgUser | None,
    is_save_bad_data: bool = True,
) -> Tuple[Dict[str, Any], int]:
    try:
        uuid.UUID(str(car_id))
    except (ValueError, TypeError):
        return {"error": "Автомобиль не найден (неверный формат ID)"}, 404

    if (end_date - start_date).days > 60:
        return {"error": "Превышен период в 60 дней"}, 400

    if user and user.is_authenticated and hasattr(user, 'timezone') and user.timezone in pytz.common_timezones:
        target_timezone = ZoneInfo(user.timezone)
    else:
        target_timezone = ZoneInfo("UTC")

    now = datetime.now(start_date.tzinfo) if start_date.tzinfo else datetime.now()
    if start_date > now + timedelta(hours=12):
        return {"error": "Начало периода выше текущей даты"}, 400
    if end_date > now + timedelta(days=1):
        end_date = datetime.now().astimezone(target_timezone) + timedelta(days=1)

    local_start = start_date.astimezone(target_timezone).replace(tzinfo=None)
    local_end = end_date.astimezone(target_timezone).replace(tzinfo=None)

    try:
        result, status_code = MileageStopsCalculationService.calculate_stops_report(
            car_id=car_id,
            start_date=local_start,
            end_date=local_end,
            is_save_bad_data=is_save_bad_data,
        )
        if status_code == 400 and isinstance(result, dict) and "not exist" in str(result.get("error", "")).lower():
            status_code = 404
        return result, status_code
    except (ObjectDoesNotExist, ValidationError):
        return {"error": "Автомобиль не найден в системе"}, 404