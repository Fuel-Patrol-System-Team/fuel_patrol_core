from enum import Enum
import logging
import string
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

from attr import dataclass
import orjson
import polars as pl
import pytz
import requests
from django.utils import timezone

from core.models import Car, SensorsValues
from core.services.providers.glonass.constants import GL_ACTION_KEYS, GL_PARAM_KEYS as GP, GLOBAL_GLONASS_ACTIONS, GLOBAL_GLONASS_PARAMS, GlonassAfterParsingProtocol
from core.services.providers.rate_limiter import global_rate_limiter

logger = logging.getLogger(__name__)

class CarSensorsRawParser:
    """
    Парсер для получения сырых данных по машинам для построения графиков
    Поддерживает 3 режима работы:
    1. Пробег: timestamp, mileage, pos_s, ign, rpm
    2. Топливо: timestamp, calc_sensors_fuel_level, pos_s, rpm
    3. Моточасы: timestamp, motohours, pos_s, rpm, ign
    """

    def __init__(self, car_id: str, start_date: datetime, end_date: datetime, mode: str = "mileage"):
        self.car_id = car_id
        self.start_date = start_date.replace(tzinfo=pytz.UTC)
        self.end_date = end_date.replace(tzinfo=pytz.UTC)
        self.mode = mode


        if mode not in ["mileage", "fuel", "fuel_charts", "motohours"]:
            raise ValueError(f"Недопустимый режим: {mode}. Допустимые: mileage, fuel, motohours")


        try:
            self.car = Car.objects.select_related('car_unit').get(id=car_id)

            self.provider = self.car.data_providers.first()
            if not self.provider:
                raise ValueError(f"У машины {car_id} нет привязанного провайдера")

            self.metadata = self.provider.metadata or {}
        except Car.DoesNotExist:
            raise ValueError(f"Машина с ID {car_id} не найдена")

        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.auth_token = None
        self.default_period_days = 90


        self.sensors_mapping_cache = {}


        self.total_messages = 0
        self.processed_messages = 0

    def _enforce_rate_limit(self) -> None:
        """Соблюдение rate limit (1 запрос в секунду)"""
        global_rate_limiter.wait_for_rate_limit()

    def _get_sensors_mapping(self) -> Dict[str, str]:
        """Получает маппинг сенсоров для машины"""
        if not self.sensors_mapping_cache:
            try:
                sensors_mapping = {
                    sv.key.key: sv.value
                    for sv in SensorsValues.objects.filter(car_id=self.car).select_related("key")
                }
                self.sensors_mapping_cache = sensors_mapping
                logger.debug(f"Загружен маппинг для {self.car.name}: {len(sensors_mapping)} сенсоров")
            except Exception as e:
                logger.error(f"Ошибка загрузки маппинга для {self.car.name}: {e}")
                self.sensors_mapping_cache = {}

        return self.sensors_mapping_cache

    def _get_nested_value(
            self,
            data: dict,
            path: str,
            default: Optional[float] = None
    ) -> Optional[float]:
        """
        Получает значение из вложенного словаря по пути вида "key1.key2.key3"
        """
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
            if current is None:
                return default
            return float(current)
        except (ValueError, TypeError):
            return default

    def authenticate(self) -> bool:
        """Аутентификация в GlonassSoft API"""
        self._enforce_rate_limit()

        url = f"{self.base_url}/auth/login"
        payload = {
            "login": self.metadata.get("login"),
            "password": self.metadata.get("password"),
        }

        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = orjson.loads(response.content)
            self.auth_token = data.get("AuthId")

            if not self.auth_token:
                logger.error("AuthId не найден в ответе")
                return False

            logger.info("Аутентификация успешна")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка аутентификации: {e}")
            return False

    def parse_raw_data(self) -> List[Dict[str, Any]]:
        """Основной метод парсинга данных"""
        if not self.authenticate():
            raise ValueError("Ошибка аутентификации в API провайдера")


        sensors_mapping = self._get_sensors_mapping()


        all_messages = self._get_all_messages_for_period()

        if not all_messages:
            logger.warning(f"Нет данных для машины {self.car.id_in_provider_system}")
            return []


        if self.mode == "mileage":
            result = self._process_general(all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.mileage, GP.satellites, GP.rpm, GP.ignition])
        elif self.mode == "fuel":
            result = self._process_general(all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.fuel_level, GP.satellites, GP.ignition, GP.voltage], [GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.tarify_car)])
        elif self.mode == "motohours":
            result = self._process_general(all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.motohours, GP.satellites, GP.rpm, GP.ignition])
        else:
            raise ValueError(f"Неизвестный режим: {self.mode}")

        logger.info(f"Обработано {self.processed_messages} сообщений из {self.total_messages} для режима {self.mode}")
        return result

    def _get_all_messages_for_period(self) -> List[Dict[str, Any]]:
        """Получает все сообщения за период с адаптивными запросами"""
        all_messages = []
        current_start = self.start_date
        vehicle_id = self.car.id_in_provider_system

        while current_start < self.end_date:
            period_days = min(self.default_period_days, (self.end_date - current_start).days)
            if period_days < 1:
                period_days = 1

            current_end = min(current_start + timezone.timedelta(days=period_days), self.end_date)

            try:
                messages = self._fetch_messages_for_period(vehicle_id, current_start, current_end)

                if messages is not None:
                    all_messages.extend(messages)
                    current_start = current_end + timezone.timedelta(seconds=1)
                else:

                    if self.default_period_days > 1:
                        self.default_period_days = max(self.default_period_days // 2, 1)
                        logger.warning(f"Уменьшаем период до {self.default_period_days} дней")
                        continue
                    else:
                        logger.error(f"Не удалось получить данные для {vehicle_id} даже за 1 день")
                        break

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:

                    if self.default_period_days > 1:
                        self.default_period_days = max(self.default_period_days // 2, 1)
                    logger.warning(f"Rate limit (429). Уменьшаем период до {self.default_period_days} дней")
                    time.sleep(5)
                    continue
                else:
                    raise

        self.total_messages = len(all_messages)
        return all_messages

    def _fetch_messages_for_period(
            self,
            vehicle_id: int,
            start_date: datetime,
            end_date: datetime
    ) -> Optional[List[Dict[str, Any]]]:
        """Запрашивает сообщения за конкретный период"""
        self._enforce_rate_limit()

        url = f"{self.base_url}/terminalMessages"
        payload = {
            "vehicleId": vehicle_id,
            "from": start_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "to": end_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
        }
        headers = {"X-Auth": self.auth_token}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()

            data = orjson.loads(response.content)
            messages = data.get("messages", [])

            if not isinstance(messages, list):
                logger.error(f"Некорректный формат ответа для {vehicle_id}")
                return None

            logger.info(
                f"Получено {len(messages)} сообщений для {vehicle_id} за период {start_date.date()} - {end_date.date()}")
            return messages

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса данных для {vehicle_id}: {e}")
            return None

    def _build_use_cols(self, required_columns, sensors_mapping):
        usecols = []
        for col in required_columns:
            param = GLOBAL_GLONASS_PARAMS.get(col)
            if param:
                path_to_param = ""
                if param.is_dynamic:
                    path_to_param = sensors_mapping.get(col.value, param.default_key)
                else:
                    path_to_param = param.default_key
                if path_to_param == "":
                    continue
                usecols.append(path_to_param)
        return usecols
                    

    def _process_general(self, messages: List[Dict[str, Any]], sensors_mapping: Dict[str, str], required_columns: List[GP], required_actions: List[GlonassAfterParsingProtocol | None] | None = None) -> List[Dict[str, Any]]:
        result = pl.DataFrame(messages, infer_schema_length=None)
        fields = list(map(lambda x: f"parameters.{x}" , result["parameters"].struct.fields))
        result = result.with_columns(pl.col("parameters").struct.rename_fields(fields)).unnest("parameters") # разбить на части
        use_cols = self._build_use_cols(required_columns, sensors_mapping)
        result = result.select(use_cols)

        for col in required_columns:
            param = GLOBAL_GLONASS_PARAMS.get(col)
            if param:
                if param.is_dynamic:
                    path_to_param = sensors_mapping.get(col.value, param.default_key)
                    
                else:
                    path_to_param = param.default_key
                if path_to_param == "":
                    # заменить спец значением весь столбец
                    result = result.with_columns(pl.lit(param.default_on_absence).alias(param.label))
                else:
                    # переименовываем столбец
                    result = result.rename({path_to_param: param.label})
                    if param.cast: # если есть каст, кастуем
                        result = param.cast(result)
                    if param.default_value is not None:
                        result = result.with_columns(pl.col(param.label).fill_null(param.default_value))
                    if param.filter_on_absence:
                        result = result.filter(pl.col(param.label).is_not_null())
        mapped_required_columns = list(map(lambda x: GLOBAL_GLONASS_PARAMS.get(x).label ,required_columns))
        result = result.select(mapped_required_columns)
        if required_actions is not None:
            for action in required_actions:
                if action is not None:
                    result = action(result, self.car, mapped_required_columns)
        self.processed_messages += result.shape[0]
        return result.to_dicts()