import logging
import requests
import orjson
import pytz
import polars as pl
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from functools import lru_cache

from core.helpers.decorators import retry_on_status
from core.models import Car, SensorsValues
from core.services.providers.rate_limited_provider import RateLimitedProvider

logger = logging.getLogger(__name__)


class GlonassSoftMotohoursProvider(RateLimitedProvider):
    """Оптимизированный провайдер для получения данных моточасов"""

    def __init__(self, metadata: Dict[str, Any], car_id: str):
        super().__init__(metadata, car_id)
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def authenticate(self) -> bool:
        self._enforce_rate_limit()
        url = f"{self.base_url}/auth/login"
        payload = {
            "login": self.metadata.get("login"),
            "password": self.metadata.get("password"),
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = orjson.loads(response.content)
            self.auth_token = data.get("AuthId")

            if not self.auth_token:
                logger.error("AuthId не найден в ответе.")
                return False

            logger.info("Авторизация успешна.")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка аутентификации: {e}")
            return False

    def get_car_data(
            self,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None
    ) -> Optional[pl.DataFrame]:
        """Получает данные моточасов для машины"""
        if not self.auth_token:
            logger.error("Токен отсутствует. Выполните аутентификацию.")
            return None

        try:
            car = Car.objects.get(id=self.car_id)
            vehicle_id = car.id_in_provider_system
        except Car.DoesNotExist:
            logger.error(f"Car with id {self.car_id} not found")
            return None

        if not end_date:
            end_date = datetime.now(pytz.UTC)
        if not start_date:
            start_date = end_date - timedelta(days=30)

        logger.info(f"Получение motohours данных для vehicle_id={vehicle_id} с {start_date} по {end_date}")


        motohours_key_path = self._get_sensor_key_path(vehicle_id, "motohours")
        ign_key_path = self._get_sensor_key_path(vehicle_id, "ign")

        if not motohours_key_path and not ign_key_path:
            logger.warning(f"Нет маппинга для motohours и ign для vehicle_id={vehicle_id}")
            return None

        parsed_data = self._get_motohours_data(
            vehicle_id, start_date, end_date, motohours_key_path, ign_key_path, str(car.id)
        )

        if not parsed_data:
            logger.warning(f"Нет motohours данных для vehicle_id={vehicle_id}")
            return None


        df = pl.DataFrame(parsed_data,schema={
            "timestamp": pl.String,
            "motohours": pl.Float64,
            "ign": pl.Int8,
            "auto": pl.Categorical,
        })
        df = df.with_columns(
            pl.col("timestamp").cast(pl.Datetime)
        )
        if df.is_empty():
            return df

        logger.info(f"Создан DataFrame motohours: {df.shape}")
        return df

    @lru_cache(maxsize=100)
    def _get_sensor_key_path(self, vehicle_id: int, sensor_key: str) -> Optional[List[str]]:
        """Получает путь к данным сенсора"""
        try:
            car = Car.objects.only("id").get(id_in_provider_system=vehicle_id)
            sensor_value = (
                SensorsValues.objects.filter(car_id=car, key__key=sensor_key)
                .select_related("key")
                .first()
            )

            if not sensor_value or not sensor_value.value:
                logger.debug(f"Нет маппинга для {sensor_key} для vehicleId={vehicle_id}.")
                return None

            key_path = sensor_value.value.split(".")
            return key_path if key_path and key_path != [""] else None

        except Car.DoesNotExist:
            logger.error(f"Автомобиль vehicleId={vehicle_id} не найден.")
            return None

    def _get_motohours_data(
            self,
            vehicle_id: int,
            start_date: datetime,
            end_date: datetime,
            motohours_key_path: Optional[List[str]],
            ign_key_path: Optional[List[str]],
            car_guid: str
    ) -> List[Dict[str, Any]]:
        """Получает данные моточасов за период"""
        try:
            data = self._fetch_motohours_period(
                vehicle_id, start_date, end_date, motohours_key_path, ign_key_path, car_guid
            )
            if data:
                return data
        except Exception as e:
            logger.warning(f"Не удалось получить данные одним запросом, разбиваем на части: {e}")
            return self._get_motohours_data_chunked(
                vehicle_id, start_date, end_date, motohours_key_path, ign_key_path, car_guid
            )

        return []

    def _get_motohours_data_chunked(
            self,
            vehicle_id: int,
            start_date: datetime,
            end_date: datetime,
            motohours_key_path: Optional[List[str]],
            ign_key_path: Optional[List[str]],
            car_guid: str,
            days_per_chunk: int = 30
    ) -> List[Dict[str, Any]]:
        """Получает данные по частям"""
        periods = self._split_period(start_date, end_date, days_per_chunk)
        all_data = []

        for i, (period_start, period_end) in enumerate(periods, 1):
            logger.info(f"Обработка периода {i}/{len(periods)}: {period_start} - {period_end}")

            try:
                data = self._fetch_motohours_period(
                    vehicle_id, period_start, period_end, motohours_key_path, ign_key_path, car_guid
                )
                if data:
                    all_data.extend(data)
                    logger.info(f"Получено {len(data)} записей motohours за период")
            except Exception as e:
                logger.error(f"Ошибка получения данных за период {period_start} - {period_end}: {e}")
                continue

        return all_data

    @retry_on_status(retry_delays=[10, 20, 30], status_codes=[400, 429])
    def _fetch_motohours_period(
            self,
            vehicle_id: int,
            start_date: datetime,
            end_date: datetime,
            motohours_key_path: Optional[List[str]],
            ign_key_path: Optional[List[str]],
            car_guid: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Получает и парсит данные моточасов за период"""
        self._enforce_rate_limit()

        url = f"{self.base_url}/terminalMessages"
        payload = {
            "vehicleId": vehicle_id,
            "from": start_date.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "to": end_date.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
        }
        headers = {"X-Auth": self.auth_token}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)

            if response.status_code == 429:
                logger.warning("Rate limit достигнут")
                raise requests.exceptions.HTTPError("Rate limit exceeded")

            response.raise_for_status()

            data = orjson.loads(response.content)
            messages = data.get("messages", [])

            if not isinstance(messages, list):
                logger.error(f"'Messages' не список: {type(messages)}")
                return []

            logger.info(f"Получено {len(messages)} сообщений")

            parsed_data = self._parse_motohours_messages(
                messages, motohours_key_path, ign_key_path, car_guid
            )
            return parsed_data

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса для vehicleId={vehicle_id}: {e}")
            raise

    def _parse_motohours_messages(
            self,
            messages: List[Dict],
            motohours_key_path: Optional[List[str]],
            ign_key_path: Optional[List[str]],
            car_guid: str
    ) -> List[Dict[str, Any]]:
        """Парсит сообщения и извлекает данные моточасов"""
        parsed_data = []

        for record in messages:
            timestamp_str = record.get("deviceTime")
            if not timestamp_str:
                continue


            motohours = (
                self._extract_sensor_value(record, motohours_key_path)
                if motohours_key_path else None
            )
            ign = (
                self._extract_sensor_value(record, ign_key_path)
                if ign_key_path else None
            )


            if ign is not None:
                try:
                    ign = 1 if int(float(ign)) > 0 else 0
                except (ValueError, TypeError):
                    ign = 0
            else:
                ign = 0

            timestamp = self._parse_timestamp(timestamp_str)
            if timestamp:
                parsed_record = {
                    "auto": car_guid,
                    "timestamp": timestamp,
                    "ign": ign
                }

                if motohours is not None:
                    try:
                        parsed_record["motohours"] = float(motohours)
                    except (ValueError, TypeError):
                        parsed_record["motohours"] = None

                parsed_data.append(parsed_record)

        return parsed_data

    def _extract_sensor_value(self, record: Dict, key_path: List[str]) -> Optional[float]:
        """Извлекает значение сенсора"""
        try:
            current = record
            for key in key_path:
                current = current.get(key) if isinstance(current, dict) else None
                if current is None:
                    return None
            return float(current) if current is not None else None
        except (ValueError, TypeError, AttributeError):
            return None

    def _parse_timestamp(self, timestamp_str: str) -> Optional[str]:
        """Парсит timestamp"""
        try:
            if "." in timestamp_str:
                dt = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%fZ")
            else:
                dt = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
            return dt.replace(tzinfo=pytz.UTC).isoformat()
        except ValueError:
            return None

    def _split_period(self, start_date: datetime, end_date: datetime, days_per_chunk: int) -> List[tuple]:
        """Разбивает период на чанки"""
        periods = []
        current_start = start_date

        while current_start < end_date:
            current_end = min(current_start + timedelta(days=days_per_chunk), end_date)
            periods.append((current_start, current_end))
            current_start = current_end + timedelta(seconds=1)

        return periods
