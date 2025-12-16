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


class GlonassSoftMileageProvider(RateLimitedProvider):
    """Оптимизированный провайдер для получения данных mileage"""

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
        """Получает данные mileage для машины"""
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

        logger.info(f"Получение mileage данных для vehicle_id={vehicle_id} с {start_date} по {end_date}")


        mileage_key_path = self._get_mileage_key_path(vehicle_id)
        if not mileage_key_path:
            logger.warning(f"Нет маппинга для mileage для vehicle_id={vehicle_id}")
            return None


        parsed_data = self._get_mileage_data(vehicle_id, start_date, end_date, mileage_key_path)

        if not parsed_data:
            logger.warning(f"Нет mileage данных для vehicle_id={vehicle_id}")
            return None


        df = pl.DataFrame(parsed_data)
        if df.is_empty():
            return df
        # TODO: должно приводить к ошибкам, если сервер ни разу не отдал mileage в сыром JSON
        df = df.with_columns([
            pl.col("timestamp").str.strptime(
                pl.Datetime,
                format="%Y-%m-%dT%H:%M:%S%z",
                strict=False
            ).alias("timestamp"),
            pl.col("mileage").cast(pl.Float64),
            pl.lit(str(car.id)).alias("auto")
        ])

        logger.info(f"Создан DataFrame mileage: {df.shape}")
        return df

    @lru_cache(maxsize=100)
    def _get_mileage_key_path(self, vehicle_id: int) -> Optional[List[str]]:
        """Получает путь к данным mileage из маппинга сенсоров"""
        try:
            car = Car.objects.only("id").get(id_in_provider_system=vehicle_id)
            sensor_value = (
                SensorsValues.objects.filter(car_id=car, key__key="mileage")
                .select_related("key")
                .first()
            )

            if not sensor_value or not sensor_value.value:
                logger.warning(f"Нет маппинга для mileage для vehicleId={vehicle_id}.")
                return None

            key_path = sensor_value.value.split(".")
            return key_path if key_path and key_path != [""] else None

        except Car.DoesNotExist:
            logger.error(f"Автомобиль vehicleId={vehicle_id} не найден.")
            return None

    def _get_mileage_data(
            self,
            vehicle_id: int,
            start_date: datetime,
            end_date: datetime,
            mileage_key_path: List[str]
    ) -> List[Dict[str, Any]]:
        """Получает данные mileage за период"""
        try:
            data = self._fetch_mileage_period(vehicle_id, start_date, end_date, mileage_key_path)
            if data:
                return data
        except Exception as e:
            logger.warning(f"Не удалось получить данные одним запросом, разбиваем на части: {e}")
            return self._get_mileage_data_chunked(vehicle_id, start_date, end_date, mileage_key_path)

        return []

    def _get_mileage_data_chunked(
            self,
            vehicle_id: int,
            start_date: datetime,
            end_date: datetime,
            mileage_key_path: List[str],
            days_per_chunk: int = 30
    ) -> List[Dict[str, Any]]:
        """Получает данные по частям"""
        periods = self._split_period(start_date, end_date, days_per_chunk)
        all_data = []

        for i, (period_start, period_end) in enumerate(periods, 1):
            logger.info(f"Обработка периода {i}/{len(periods)}: {period_start} - {period_end}")

            try:
                data = self._fetch_mileage_period(vehicle_id, period_start, period_end, mileage_key_path)
                if data:
                    all_data.extend(data)
                    logger.info(f"Получено {len(data)} записей mileage за период")
            except Exception as e:
                logger.error(f"Ошибка получения данных за период {period_start} - {period_end}: {e}")
                continue

        return all_data

    @retry_on_status(retry_delays=[10, 20, 30], status_codes=[400, 429])
    def _fetch_mileage_period(
            self,
            vehicle_id: int,
            start_date: datetime,
            end_date: datetime,
            mileage_key_path: List[str]
    ) -> Optional[List[Dict[str, Any]]]:
        """Получает и парсит данные mileage за указанный период"""
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

            parsed_data = self._parse_mileage_messages(messages, mileage_key_path)
            return parsed_data

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса для vehicleId={vehicle_id}: {e}")
            raise

    def _parse_mileage_messages(
            self,
            messages: List[Dict],
            mileage_key_path: List[str]
    ) -> List[Dict[str, Any]]:
        """Парсит сообщения и извлекает данные mileage"""
        parsed_data = []

        for record in messages:
            timestamp_str = record.get("deviceTime")
            if not timestamp_str:
                continue

            mileage = self._extract_mileage_value(record, mileage_key_path)
            if mileage is None:
                continue

            timestamp = self._parse_timestamp(timestamp_str)
            if timestamp:
                parsed_data.append({
                    "timestamp": timestamp,
                    "mileage": float(mileage)
                })

        return parsed_data

    def _extract_mileage_value(self, record: Dict, mileage_key_path: List[str]) -> Optional[float]:
        """Извлекает значение mileage по пути"""
        try:
            current = record
            for key in mileage_key_path:
                current = current.get(key) if isinstance(current, dict) else None
                if current is None:
                    return None
            return float(current) if current is not None else None
        except (ValueError, TypeError, AttributeError):
            return None

    def _parse_timestamp(self, timestamp_str: str) -> Optional[str]:
        """Парсит timestamp в строковый формат"""
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