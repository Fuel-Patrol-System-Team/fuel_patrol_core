import logging
import time
import requests
import orjson
import pytz
import polars as pl
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from core.services.providers.data_provider_base import BaseDataProvider
from core.services.providers.rate_limited_provider import RateLimitedProvider
from core.services.providers.rate_limiter import GlobalRateLimiter
from core.helpers.decorators import retry_on_status

logger = logging.getLogger(__name__)


class GlonassSoftDataProvider(RateLimitedProvider):
    """Провайдер для получения данных terminalMessages по одной машине"""

    def __init__(self, metadata: Dict[str, Any], car_id: str):
        super().__init__(metadata, car_id)
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.request_count = 0


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

        days_per_chunk = 15

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
            schema = {
                "auto": pl.Utf8,
                "timestamp": pl.Utf8,
                "latitude": pl.Float64,
                "longitude": pl.Float64,
                "satellites": pl.Int64,
                "calc_sensors_voltage": pl.Float64,
                "calc_sensors_fuel_level": pl.Float64,
                "pos_s": pl.Float64,
                "rpm": pl.Float64,
                "ign": pl.Int64,
                "mileage": pl.Float64,
                "motohours": pl.Float64,
                "engine_temp": pl.Float64,
                "amtr": pl.Int64,
            }

            df = pl.DataFrame(processed_data, schema=schema)

            logger.info(f"Создан DataFrame с базовой схемой: {df.shape}")
            logger.info(f"Типы колонок: {df.schema}")

            df = df.with_columns(
                [
                    pl.col("timestamp")
                    .str.strptime(
                        pl.Datetime, format="%Y-%m-%dT%H:%M:%S%.fZ", strict=False
                    )
                    .alias("timestamp"),
                    pl.col("auto").cast(pl.Categorical),
                    pl.col("calc_sensors_voltage").cast(pl.Float32),
                    pl.col("calc_sensors_fuel_level").cast(pl.Float32),
                    pl.col("rpm").cast(pl.Float32),
                    pl.col("ign").cast(pl.Int8),
                    pl.col("pos_s").cast(pl.Float32),
                    pl.col("latitude").cast(pl.Float32),
                    pl.col("longitude").cast(pl.Float32),
                    pl.col("satellites").cast(pl.Int16),
                    pl.col("amtr").cast(pl.Int32),
                    pl.col("mileage").cast(pl.Float32),
                    pl.col("motohours").cast(pl.Float32),
                    pl.col("engine_temp").cast(pl.Float32),
                ]
            )

            logger.info(f"DataFrame после преобразования типов: {df.shape}")
            logger.info(f"Типы колонок после преобразования: {df.schema}")

            null_counts = df.null_count()
            logger.info(f"Количество null значений по колонкам: {null_counts}")

            if not df.is_empty():
                logger.info(f"Пример данных timestamp: {df['timestamp'].head(3)}")
                logger.info(
                    f"Пример данных fuel_level: {df['calc_sensors_fuel_level'].head(3)}"
                )
                logger.info(f"Пример данных speed: {df['pos_s'].head(3)}")

            return df

        except Exception as e:
            logger.error(f"Ошибка создания DataFrame: {e}", exc_info=True)

            if processed_data:
                logger.error(f"Всего processed_data: {len(processed_data)} записей")

                type_analysis = {}
                for i, row in enumerate(processed_data[:5]):
                    logger.error(f"Строка {i}:")
                    for key, value in row.items():
                        if key not in type_analysis:
                            type_analysis[key] = set()
                        type_analysis[key].add(type(value).__name__)
                        logger.error(f"  {key}: {value} (type: {type(value).__name__})")

                logger.error(f"Анализ типов по колонкам:")
                for key, types in type_analysis.items():
                    logger.error(f"  {key}: {list(types)}")

                try:
                    logger.info("Пробуем создать DataFrame с infer_schema_length=1000")
                    df_fallback = pl.DataFrame(processed_data, infer_schema_length=1000)
                    logger.info(
                        f"Успешно создан DataFrame с fallback: {df_fallback.shape}"
                    )
                    logger.info(f"Схема fallback: {df_fallback.schema}")
                    return df_fallback
                except Exception as e2:
                    logger.error(f"Fallback также не сработал: {e2}")

            return pl.DataFrame()

    def _extract_message_data(
        self, message: Dict[str, Any], sensors_mapping: Dict[str, str], car_guid: str
    ) -> Optional[Dict[str, Any]]:
        """Извлекает данные из сообщения по маппингу сенсоров"""
        try:
            row = {
                "auto": str(car_guid),
                "timestamp": str(message.get("deviceTime", "")),
                "latitude": float(message.get("latitude", 0) or 0),
                "longitude": float(message.get("longitude", 0) or 0),
                "satellites": int(message.get("satellites", 0) or 0),
                "calc_sensors_voltage": float(message.get("voltage", 0) or 0),
            }

            def get_nested_value(data: dict, path: str, default=0.0):
                if not path or path == "":
                    return default
                keys = path.split(".")
                current = data
                for key in keys:
                    if isinstance(current, dict) and key in current:
                        current = current[key]
                    else:
                        return default
                try:
                    return float(current) if current is not None else default
                except (ValueError, TypeError):
                    return default

            fuel_path = sensors_mapping.get("calc_sensors_fuel_level", "")
            row["calc_sensors_fuel_level"] = get_nested_value(message, fuel_path)
            row["pos_s"] = get_nested_value(
                message, sensors_mapping.get("speed", "speed")
            )
            row["rpm"] = get_nested_value(message, sensors_mapping.get("rpm", ""))
            row["mileage"] = get_nested_value(
                message, sensors_mapping.get("mileage", "")
            )
            row["motohours"] = get_nested_value(
                message, sensors_mapping.get("motohours", "")
            )
            row["engine_temp"] = get_nested_value(
                message, sensors_mapping.get("engine_temp", "")
            )

            ign_value = get_nested_value(message, sensors_mapping.get("ign", ""))
            row["ign"] = 1 if ign_value > 0 else 0

            try:
                amtr_x = float(message.get("amtr_x", 0) or 0)
                amtr_y = float(message.get("amtr_y", 0) or 0)
                amtr_z = float(message.get("amtr_z", 0) or 0)
                row["amtr"] = int(abs(amtr_x) + abs(amtr_y) + abs(amtr_z))
            except (ValueError, TypeError):
                row["amtr"] = 0

            if (
                row["calc_sensors_fuel_level"] == 0
                and row["pos_s"] == 0
                and row["rpm"] == 0
            ):
                return None

            return row

        except Exception as e:
            logger.debug(f"Ошибка обработки сообщения: {e}")
            return None

    @staticmethod
    def prepare_auto_data(car) -> pl.DataFrame:
        """Подготавливает auto DataFrame с дополнительными полями для утечек"""
        try:
            ##TODO: Юлик глянь сюда
            ign_working = True
            norm_speed = 60.0

            auto_data = [
                {
                    "id": str(car.id),
                    "auto": str(car.id),
                    "input": float(car.input) if car.input else 1.0,
                    "output": float(car.output) if car.output else 1.0,
                    "name": car.name,
                    "engine_type": float(car.engine_type) if car.engine_type else 0.0,
                    "is_tarrified": car.is_tarrified,
                    "ign_working": ign_working,
                    "norm_speed": norm_speed,
                }
            ]

            auto_df = pl.DataFrame(auto_data).with_columns(
                [
                    pl.col("id").cast(pl.Categorical),
                    pl.col("auto").cast(pl.Categorical),
                    pl.col("input").cast(pl.Float32),
                    pl.col("output").cast(pl.Float32),
                ]
            )

            return auto_df

        except Exception as e:
            logger.error(f"Ошибка подготовки auto данных: {e}")
            raise
