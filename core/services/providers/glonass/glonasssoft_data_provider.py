import logging
import time
import requests
import orjson
import pytz
import polars as pl
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from core.services.providers.data_provider_base import BaseDataProvider
from core.services.providers.rate_limiter import GlobalRateLimiter
from core.helpers.decorators import retry_on_status

logger = logging.getLogger(__name__)


class GlonassSoftDataProvider(BaseDataProvider):
    """Провайдер для получения данных terminalMessages по одной машине"""

    def __init__(self, metadata: Dict[str, Any], car_id: str):
        super().__init__(metadata, car_id)
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.rate_limiter = GlobalRateLimiter()
        self.request_count = 0

    def _enforce_rate_limit(self) -> None:
        """Использует глобальный rate limiter для всех запросов"""
        self.rate_limiter.wait_for_rate_limit()
        self.request_count += 1
        logger.debug(f"Запрос #{self.request_count} к API")

    @retry_on_status(retry_delays=[5, 10, 20], status_codes=[400, 429, 500, 502, 503])
    def authenticate(self) -> bool:
        self._enforce_rate_limit()
        url = f"{self.base_url}/auth/login"
        payload = {
            "login": self.metadata.get("login"),
            "password": self.metadata.get("password"),
        }

        try:
            logger.debug(f"Аутентификация в {url}")
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()

            data = orjson.loads(response.content)
            self.auth_token = data.get("AuthId")

            if not self.auth_token:
                logger.error("AuthId не найден в ответе.")
                return False

            logger.info("Авторизация успешна.")
            return True

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP ошибка при аутентификации: {e.response.status_code}")
            raise e
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса при аутентификации: {e}")
            raise e

    def get_car_data(
        self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None
    ) -> Optional[pl.DataFrame]:
        """
        Получает данные terminalMessages для конкретной машины и возвращает как polars DataFrame
        """
        if not self.auth_token:
            logger.error("Токен отсутствует. Выполните аутентификацию.")
            return None

        from core.models import Car

        try:
            car = Car.objects.get(id=self.car_id)
            vehicle_id = car.id_in_provider_system
            logger.info(f"Обработка машины: {car.name} (vehicle_id={vehicle_id})")
        except Car.DoesNotExist:
            logger.error(f"Car with id {self.car_id} not found")
            return None

        if not end_date:
            end_date = datetime.now(pytz.UTC)
        if not start_date:
            start_date = end_date - timedelta(days=30)

        logger.info(
            f"Получение данных для vehicle_id={vehicle_id} с {start_date} по {end_date}"
        )

        periods = self._split_period(start_date, end_date)

        all_messages = self._get_data_sequential(vehicle_id, periods)

        if not all_messages:
            logger.warning(f"Нет данных для vehicle_id={vehicle_id}")
            return None

        df = self._convert_to_dataframe(all_messages, vehicle_id)
        return df

    def _split_period(
        self, start_date: datetime, end_date: datetime, days_per_chunk: int = 10
    ) -> List[tuple]:
        """Разбивает период на чанки для последовательной обработки"""
        periods = []
        current_start = start_date

        total_days = (end_date - start_date).days
        logger.info(f"Общий период: {total_days} дней")

        days_per_chunk = 30

        while current_start < end_date:
            current_end = min(current_start + timedelta(days=days_per_chunk), end_date)
            periods.append((current_start, current_end))
            current_start = current_end + timedelta(seconds=1)

        logger.info(f"Разбит период на {len(periods)} чанков по {days_per_chunk} дней")
        return periods

    def _get_data_sequential(
        self, vehicle_id: int, periods: List[tuple]
    ) -> List[Dict[str, Any]]:
        """Последовательно получает данные за все периоды с соблюдением rate limit"""
        all_messages = []
        total_periods = len(periods)

        for i, (period_start, period_end) in enumerate(periods, 1):
            logger.info(
                f"Обработка периода {i}/{total_periods}: {period_start} - {period_end}"
            )

            try:
                messages = self._get_terminal_messages_safe(
                    vehicle_id, period_start, period_end
                )
                if messages:
                    all_messages.extend(messages)
                    logger.info(f"Получено {len(messages)} сообщений за период")
                else:
                    logger.warning(
                        f"Нет сообщений за период {period_start} - {period_end}"
                    )

            except Exception as e:
                logger.error(
                    f"Критическая ошибка получения данных за период {period_start} - {period_end}: {e}"
                )

                continue

        logger.info(f"Всего получено {len(all_messages)} сообщений за все периоды")
        return all_messages

    def _get_terminal_messages_safe(
        self, vehicle_id: int, start_date: datetime, end_date: datetime
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Безопасная версия получения terminalMessages с обработкой ошибок
        """
        try:
            return self._get_terminal_messages(vehicle_id, start_date, end_date)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.error(
                    f"Rate limit превышен для vehicle_id={vehicle_id}, пропускаем период"
                )
                return None
            else:
                logger.error(
                    f"HTTP ошибка {e.response.status_code} для vehicle_id={vehicle_id}"
                )
                return None
        except Exception as e:
            logger.error(
                f"Общая ошибка получения данных для vehicle_id={vehicle_id}: {e}"
            )
            return None

    @retry_on_status(retry_delays=[8, 15, 25], status_codes=[400, 429, 500, 502, 503])
    def _get_terminal_messages(
        self, vehicle_id: int, start_date: datetime, end_date: datetime
    ) -> Optional[List[Dict[str, Any]]]:
        """Получает terminalMessages за указанный период"""
        self._enforce_rate_limit()

        url = f"{self.base_url}/terminalMessages"
        payload = {
            "vehicleId": vehicle_id,
            "from": start_date.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[
                :-3
            ],
            "to": end_date.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
        }
        headers = {"X-Auth": self.auth_token}

        logger.debug(
            f"Запрос данных для vehicle_id={vehicle_id}, период: {start_date} - {end_date}"
        )

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()

            data = orjson.loads(response.content)
            messages = data.get("messages", [])

            return messages if isinstance(messages, list) else []

        except requests.exceptions.HTTPError as e:
            logger.error(
                f"HTTP ошибка {e.response.status_code} для vehicle_id={vehicle_id}"
            )
            raise e
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса для vehicle_id={vehicle_id}: {e}")
            raise e

    def _convert_to_dataframe(
        self, messages: List[Dict[str, Any]], vehicle_id: int
    ) -> pl.DataFrame:
        """Конвертирует сообщения в polars DataFrame"""
        if not messages:
            return pl.DataFrame()

        from core.models import Car, SensorsValues

        try:
            car = Car.objects.get(id_in_provider_system=vehicle_id)
            sensors_mapping = {
                sv.key.key: sv.value
                for sv in SensorsValues.objects.filter(car_id=car).select_related("key")
            }
            logger.info(f"Получен маппинг сенсоров: {len(sensors_mapping)} сенсоров")
        except Exception as e:
            logger.error(f"Ошибка получения маппинга сенсоров: {e}")
            sensors_mapping = {}

        processed_data = []
        skipped_messages = 0

        for i, message in enumerate(messages):
            row = self._extract_message_data(message, sensors_mapping, str(car.id))
            if row:
                processed_data.append(row)
            else:
                skipped_messages += 1

        if skipped_messages > 0:
            logger.warning(f"Пропущено {skipped_messages} сообщений из {len(messages)}")

        if not processed_data:
            logger.warning("Нет обработанных данных для создания DataFrame")
            return pl.DataFrame()

        try:
            df = pl.DataFrame(
                processed_data,
                schema_overrides={
                    "timestamp": pl.Datetime,
                    "auto": pl.Categorical,
                    "calc_sensors_voltage": pl.Int32,
                    "calc_sensors_fuel_level": pl.Float32,
                    "rpm": pl.Int32,
                    "ign": pl.Int8,
                },
            )

            df = df.with_columns(pl.col("timestamp").cast(pl.Datetime))

            logger.info(f"Создан DataFrame: {df.shape}, колонки: {df.columns}")

            return df

        except Exception as e:
            logger.error(f"Ошибка создания DataFrame: {e}")
            return pl.DataFrame()

    def _extract_message_data(
        self, message: Dict[str, Any], sensors_mapping: Dict[str, str], car_guid: str
    ) -> Optional[Dict[str, Any]]:
        """Извлекает данные из сообщения по маппингу сенсоров"""
        try:
            row = {
                # TODO: зачем latitude и longitude
                "auto": car_guid,
                "timestamp": message.get("deviceTime"),
                "latitude": message.get("latitude"),
                "longitude": message.get("longitude"),
                "satellites": message.get("satellites"),
                "calc_sensors_voltage": message.get("voltage"),
            }

            def get_nested_value(data: dict, path: str):
                if not path or path == "":
                    return None
                keys = path.split(".")
                current = data
                for key in keys:
                    if isinstance(current, dict) and key in current:
                        current = current[key]
                    else:
                        return None
                return current

            fuel_path = sensors_mapping.get("calc_sensors_fuel_level", "")
            row["calc_sensors_fuel_level"] = get_nested_value(message, fuel_path)

            row["pos_s"] = get_nested_value(
                message, sensors_mapping.get("speed", "speed")  # это fallback
            )
            row["rpm"] = get_nested_value(message, sensors_mapping.get("rpm", ""))
            row["ign"] = get_nested_value(message, sensors_mapping.get("ign", ""))
            row["mileage"] = get_nested_value(
                message, sensors_mapping.get("mileage", "")
            )
            row["motohours"] = get_nested_value(
                message, sensors_mapping.get("motohours", "")
            )
            row["engine_temp"] = get_nested_value(
                message, sensors_mapping.get("engine_temp", "")
            )

            if row["ign"] is not None:
                try:
                    row["ign"] = 1 if int(float(row["ign"])) > 0 else 0
                except (ValueError, TypeError):
                    row["ign"] = 0
            else:
                row["ign"] = 0

            try:
                amtr_x = float(message.get("amtr_x", 0) or 0)
                amtr_y = float(message.get("amtr_y", 0) or 0)
                amtr_z = float(message.get("amtr_z", 0) or 0)
                row["amtr"] = int(abs(amtr_x) + abs(amtr_y) + abs(amtr_z))
            except (ValueError, TypeError):
                row["amtr"] = 0

            if (
                row["calc_sensors_fuel_level"] is None
                and row["pos_s"] is None
                and row["rpm"] is None
            ):
                return None

            return row

        except Exception as e:
            logger.debug(f"Ошибка обработки сообщения: {e}")
            return None
