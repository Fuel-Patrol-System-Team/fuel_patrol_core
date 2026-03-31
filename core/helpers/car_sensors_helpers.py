import logging
from typing import Dict, Tuple, Optional
from datetime import datetime
import pytz
import polars as pl

from rest_framework import status

from app.tasks import GlonassGeneralProvider
from core.models import Car, DataProvider
from core.services.providers.car_sensors_raw_parser import CarSensorsRawParser

logger = logging.getLogger(__name__)


class CarSensorsHelper:
    """Хелпер для работы с сырыми данными датчиков автомобилей."""

    VALID_MODES = ['mileage', 'fuel', "fuel_charts", 'motohours']
    MAX_PERIOD_DAYS = 30 
    DATE_FORMAT = "%Y-%m-%d"

    MODE_NAMES = {
        'mileage': 'Пробег',
        'fuel': 'Топливо',
        'motohours': 'Моточасы'
    }

    FIELDS_DESCRIPTION = {
        'mileage': {
            "timestamp": "Временная метка (ISO 8601)",
            "mileage": "Пробег (км или мили)",
            "pos_s": "Скорость",
            "ign": "Зажигание (0 - выключено, 1 - включено)",
            "rpm": "Обороты двигателя (об/мин)"
        },
        'fuel': {
            "timestamp": "Временная метка (ISO 8601)",
            "calc_sensors_fuel_level": "Уровень топлива",
            "pos_s": "Скорость",
            "rpm": "Обороты двигателя (об/мин)"
        },
        'motohours': {
            "timestamp": "Временная метка (ISO 8601)",
            "motohours": "Моточасы",
            "pos_s": "Скорость",
            "rpm": "Обороты двигателя (об/мин)",
            "ign": "Зажигание (0 - выключено, 1 - включено)"
        }
    }

    @staticmethod
    def validate_required_params(car_id: str, start_date: str, end_date: str) -> Tuple[bool, Optional[str]]:
        """Валидация обязательных параметров."""
        if not all([car_id, start_date, end_date]):
            return False, "Требуются параметры: car_id, start_date, end_date"
        return True, None

    @staticmethod
    def validate_mode(mode: str) -> Tuple[bool, Optional[str]]:
        """Валидация режима работы."""
        if mode not in CarSensorsHelper.VALID_MODES:
            valid_modes_str = ', '.join(CarSensorsHelper.VALID_MODES)
            return False, f"Недопустимый режим. Допустимые: {valid_modes_str}"
        return True, None

    @staticmethod
    def parse_and_validate_dates(start_date_str: str, end_date_str: str) -> Tuple[
        Optional[datetime], Optional[datetime], Optional[str]]:
        """Парсинг и валидация дат."""
        try:
            start_date = datetime.strptime(start_date_str, CarSensorsHelper.DATE_FORMAT).replace(tzinfo=pytz.UTC)
            end_date = datetime.strptime(end_date_str, CarSensorsHelper.DATE_FORMAT).replace(tzinfo=pytz.UTC)

            if start_date >= end_date:
                return None, None, "start_date должен быть раньше end_date"

            period_days = (end_date - start_date).days
            if period_days > CarSensorsHelper.MAX_PERIOD_DAYS:
                return None, None, f"Период не должен превышать {CarSensorsHelper.MAX_PERIOD_DAYS} дней"

            return start_date, end_date, None

        except ValueError:
            return None, None, "Неверный формат даты. Используйте YYYY-MM-DD"

    @staticmethod
    def get_car_for_user(car_id: str, user) -> Tuple[Optional[Car], Optional[str]]:
        """Получение автомобиля с проверкой прав доступа."""
        try:
            car = Car.objects.get(
                id=car_id,
                data_providers__org_id=user.org.id
            )
            return car, None
        except Car.DoesNotExist:
            return None, "Машина не найдена или не принадлежит вашей организации"
        except Exception as e:
            logger.error(f"Ошибка получения автомобиля {car_id}: {e}")
            return None, "Ошибка получения данных об автомобиле"
    @staticmethod
    def preprocess_car_data_charts(df: pl.DataFrame, agg: str | None):
        if agg is not None and agg != "":
            col_agg_required = pl.col("timestamp").dt.truncate(f"{agg}")
            df = df.with_columns(
                pl.col("calc_sensors_fuel_level").mean().over(["auto", col_agg_required])
            )
        return df
    @staticmethod
    def parse_raw_data(
            car: Car,
            provider: DataProvider,
            start_date: datetime,
            end_date: datetime,
            agg: str,
            mode: str
    ) -> Tuple[Optional[Dict], Optional[CarSensorsRawParser], Optional[str]]:
        """Парсинг сырых данных с использованием CarSensorsRawParser."""
        try:
            parser = GlonassGeneralProvider(None, car, provider, start_date, end_date, mode)

            status, result = parser.parse_raw_data(mode, True, car)
            result = CarSensorsHelper.preprocess_car_data_charts(result, agg)
            result = result.to_dicts()
            return result, parser, None

        except ValueError as e:
            return None, None, str(e)
        except Exception as e:
            logger.error(f"Ошибка парсинга данных для машины {car.id}: {e}", exc_info=True)
            return None, None, f"Ошибка получения данных: {str(e)}"

    @staticmethod
    def build_response_data(
            car_id: str,
            car_name: str,
            mode: str,
            start_date_str: str,
            end_date_str: str,
            result: Dict,
            parser: CarSensorsRawParser
    ) -> Dict:
        """Построение структурированного ответа."""
        start_date = parser.start_date
        end_date = parser.end_date

        response_data = {
            "success": True,
            "car_id": car_id,
            "car_name": car_name,
            "mode": mode,
            "mode_name": CarSensorsHelper.MODE_NAMES.get(mode, mode),
            "start_date": start_date_str,
            "end_date": end_date_str,
            "result": result,
            "statistics": CarSensorsHelper._build_statistics(
                parser, start_date, end_date
            ),
            "fields": CarSensorsHelper._get_fields_for_mode(mode)
        }

        return response_data

    @staticmethod
    def _build_statistics(parser: CarSensorsRawParser, start_date: datetime, end_date: datetime) -> Dict:
        """Построение статистики для ответа."""
        total_messages = parser.total_messages
        processed_messages = parser.processed_messages

        return {
            "total_messages": total_messages,
            "processed_messages": processed_messages,
            "skipped_messages": total_messages - processed_messages,
            "period_days": (end_date - start_date).days
        }

    @staticmethod
    def _get_fields_for_mode(mode: str) -> Dict[str, str]:
        """Получение описания полей для режима."""
        return CarSensorsHelper.FIELDS_DESCRIPTION.get(mode, {})

    @staticmethod
    def handle_general_exception(e: Exception) -> Tuple[Dict, int]:
        """Обработка общих исключений."""
        logger.error(f"Неожиданная ошибка в CarSensorsRawDataAPIView: {e}", exc_info=True)
        return {"error": "Внутренняя ошибка сервера"}, status.HTTP_500_INTERNAL_SERVER_ERROR
