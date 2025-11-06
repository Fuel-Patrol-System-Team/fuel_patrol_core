import uuid
import os
import glob
import csv
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import psutil
import pytz
import logging
import orjson
import time
import math
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from itertools import islice

import requests
from django.conf import settings
from django.db import transaction
import textdistance

from core.models import Car, Media, ReportQuery, CarBadData, SensorsMapping
from core.helpers.decorators import retry_on_status

logger = logging.getLogger(__name__)


class GlonassSoftProvider:
    """
    Класс-провайдер для парсинга данных из GlonassSoft API.
    """

    def __init__(self, metadata: Dict[str, Any], report_query_id: str):
        self.metadata = metadata
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.auth_token: Optional[str] = None
        self.last_request_time: float = 0
        self.report_query_id = report_query_id
        self.batch_size = 10
        self.chunk_size = 25_000

        timestamp = datetime.now(tz=pytz.UTC).strftime("%Y%m%d_%H%M%S")
        media_dir = Path(settings.MEDIA_ROOT) / "raw_data"
        tmp_dir = Path(settings.BASE_DIR) / "tmp"

        os.makedirs(media_dir, exist_ok=True)
        os.makedirs(tmp_dir, exist_ok=True)

        self.csv_file_path = media_dir / f"raw_data_{report_query_id}_{timestamp}.csv"
        self.full_json_file_path = (
            media_dir / f"full_terminal_messages_{report_query_id}_{timestamp}.json"
        )
        self.tmp_dir = tmp_dir
        self.csv_initialized = False
        self.default_period_days = 90
        self.min_data_period_days = 30

        self.processed_vehicle_ids = set()
        self.all_terminal_messages: Dict[int, List[Dict[str, Any]]] = {}

    def _enforce_rate_limit(self) -> None:
        """Обеспечивает соблюдение лимита API (1 запрос в секунду)."""
        time.sleep(1.0)
        self.last_request_time = time.time()

    def _log_resources(self, method: str) -> None:
        """Логирует текущее использование RAM и CPU."""
        process = psutil.Process()
        ram_mb = process.memory_info().rss / 1024**2
        cpu_percent = psutil.cpu_percent()
        logger.info(f"[{method}] RAM: {ram_mb:.2f} MB, CPU: {cpu_percent:.1f}%")

    def _get_adaptive_period(self, start_date: datetime, end_date: datetime) -> int:
        """
        Рассчитывает адаптивный период для запроса данных,
        ограничивая его `self.default_period_days`.
        """
        days = (end_date - start_date).days
        period_days = min(self.default_period_days, days)
        return max(period_days, 1)

    def _create_bad_data(self, car: Car, reason: str) -> None:
        """Создаёт запись в CarBadData для указанной машины с причиной."""
        try:
            CarBadData.objects.create(
                car_id=car, reason=reason, datetime=datetime.now(tz=pytz.UTC)
            )
            logger.warning(f"Создана запись CarBadData для car_id={car.id}: {reason}")
        except Exception as e:
            logger.error(f"Ошибка создания CarBadData для car_id={car.id}: {e}")

    def _cleanup_tmp_files(self) -> None:
        """Удаляет временные JSON-файлы, созданные в процессе работы."""
        pattern = str(self.tmp_dir / "terminal_messages_*.json")
        for file_path in glob.glob(pattern):
            try:
                os.remove(file_path)
                logger.info(f"Удалён буферный файл: {file_path}")
            except Exception as e:
                logger.error(f"Ошибка удаления {file_path}: {e}")
        self._log_resources("cleanup_tmp_files")

    def _flatten(self, data: dict, prefix: str = "") -> Dict[str, Any]:
        """
        Преобразует вложенный словарь в плоский.
        Например: {'a': {'b': 1}} -> {'a.b': 1}
        """
        result = {}
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                result.update(self._flatten(value, full_key))
            else:
                result[full_key] = value
        return result

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def authenticate(self) -> bool:
        """Аутентификация в GlonassSoft API."""
        self._enforce_rate_limit()
        url = f"{self.base_url}/auth/login"
        payload = {
            "login": self.metadata.get("login"),
            "password": self.metadata.get("password"),
        }
        logger.info(f"Авторизация: URL={url}")

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = orjson.loads(response.content)
            self.auth_token = data.get("AuthId")
            if not self.auth_token:
                logger.error("AuthId не найден в ответе.")
                return False
            logger.info("Авторизация успешна.")
            self._log_resources("authenticate")
            return True
        except requests.exceptions.HTTPError as e:
            logger.error(f"Ошибка HTTP при аутентификации: {e}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса при аутентификации: {e}")
            return False

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def get_vehicles(
        self,
        name: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Получает список автомобилей, их детали и сохраняет данные.
        """
        if not self.auth_token:
            logger.error("Токен отсутствует. Выполните аутентификацию.")
            return None

        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/find"
        params = {"name": name}
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Запрос автомобилей: {url}")

        try:
            response = requests.post(url, json=params, headers=headers)
            response.raise_for_status()
            data = orjson.loads(response.content)

            if not isinstance(data, list):
                logger.error(f"Ожидался список, получен: {type(data)}")
                return None

            logger.info(f"Получено {len(data)} автомобилей.")
            self._log_resources("get_vehicles")

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса при получении списка автомобилей: {e}")
            return None

        report_query = ReportQuery.objects.get(id=self.report_query_id)
        provider = report_query.provider_id

        existing_cars_qs = Car.objects.filter(data_providers=provider).values(
            "id_in_provider_system", "last_processed_date"
        )
        existing_cars_dict = {
            str(car["id_in_provider_system"]): car["last_processed_date"]
            for car in existing_cars_qs
        }

        is_initial_processing = not existing_cars_qs.exists()

        processed_vehicles = []

        for i in range(0, len(data), self.batch_size):
            batch = data[i : i + self.batch_size]
            logger.info(f"Батч {i + 1}-{i + len(batch)} из {len(data)}")

            for vehicle in batch:
                vehicle_id = vehicle.get("vehicleId")
                if not vehicle_id:
                    logger.warning(f"Пропущена машина без vehicleId")
                    continue

                unit = vehicle.get("unitName")
                if not unit:
                    logger.warning(f"Пропущена машина без unit")
                    continue

                if unit != "Гараж":
                    logger.warning("Пропущена машина без Unit Гараж")
                    continue

                if vehicle_id in self.processed_vehicle_ids:
                    logger.info(
                        f"Пропущена машина vehicleId={vehicle_id}: уже обработана в этом сеансе."
                    )
                    continue
                self.processed_vehicle_ids.add(vehicle_id)

                vehicle_details = self.get_vehicle_details(vehicle_id)
                if not vehicle_details:
                    logger.warning(
                        f"Не удалось получить детали для vehicleId={vehicle_id}"
                    )
                    continue

                try:
                    self.save_to_db(vehicle_details, provider)
                except Exception as e:
                    logger.error(
                        f"Ошибка сохранения в БД для vehicleId={vehicle_id}: {e}"
                    )
                    continue

                if not self._validate_vehicle_data(vehicle_details):
                    car = Car.objects.filter(id_in_provider_system=vehicle_id).first()
                    if car:
                        self._create_bad_data(
                            car, "Нетарированное ТС или отсутствуют данные"
                        )
                    logger.info(
                        f"Пропущена машина vehicleId={vehicle_id}: не прошла валидацию."
                    )
                    continue

                car_start_date = start_date
                created_at_str = vehicle_details.get("createdAt")

                try:
                    created_at = datetime.strptime(
                        created_at_str, "%Y-%m-%dT%H:%M:%S.%fZ"
                    ).replace(tzinfo=pytz.UTC)
                except (ValueError, TypeError):
                    logger.warning(f"Некорректный формат createdAt: {created_at_str}")
                    continue

                last_processed_date = existing_cars_dict.get(str(vehicle_id))

                if not car_start_date:
                    car_start_date = (
                        created_at
                        if is_initial_processing
                        else (last_processed_date or created_at)
                    )

                car_end_date = end_date or datetime.now(tz=pytz.UTC)

                data_period = (car_end_date - car_start_date).days
                if data_period < self.min_data_period_days:
                    car = Car.objects.filter(id_in_provider_system=vehicle_id).first()
                    if car:
                        self._create_bad_data(
                            car,
                            f"Мало данных, минимальный объём {self.min_data_period_days} дней",
                        )
                    logger.info(
                        f"Пропущена машина vehicleId={vehicle_id}: Период данных {data_period} дней < {self.min_data_period_days}"
                    )
                    continue

                try:
                    self.get_terminal_to_json(vehicle_id, car_start_date, car_end_date)
                    processed_vehicles.append(vehicle_details)
                except Exception as e:
                    logger.error(
                        f"Критическая ошибка обработки vehicleId={vehicle_id}: {e}",
                        exc_info=True,
                    )
                    continue

        if (
            processed_vehicles
            and os.path.exists(self.csv_file_path)
            and os.path.getsize(self.csv_file_path) > 0
        ):
            self._create_media_record(self.csv_file_path, "raw")

        if (
            os.path.exists(self.full_json_file_path)
            and os.path.getsize(self.full_json_file_path) > 0
        ):
            self._create_media_record(self.full_json_file_path, "raw_json")
        else:
            logger.warning(
                "Медиа-файл не создан: нет обработанных данных или CSV/JSON пустой."
            )

        self._cleanup_tmp_files()
        return processed_vehicles if processed_vehicles else None

    def _validate_vehicle_data(self, vehicle_details: Dict[str, Any]) -> bool:
        """
        Проверяет, подходит ли автомобиль для анализа.
        """
        input_value = vehicle_details.get("input")
        output_value = vehicle_details.get("output")
        is_active = vehicle_details.get("isActive", True)

        if not is_active:
            return False

        if (
            (input_value is None or output_value is None)
            or (input_value == output_value)
            or output_value == 0.0
        ):
            return False

        if (
            vehicle_details.get("sensorsMapping", {}).get("calc_sensors_fuel_level")
            is None
        ):
            return False

        return True

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def get_vehicle_details(self, vehicle_id: int) -> Optional[Dict[str, Any]]:
        """
        Получает детальную информацию об автомобиле, включая маппинг датчиков.
        """
        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/{vehicle_id}"
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Запрос данных машины: {vehicle_id}")

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = orjson.loads(response.content)

            input_value, output_value = None, None
            sensors_mapping = {}

            for sensor in data.get("sensors", []):
                sensor_type = sensor.get("type")
                sensor_name = sensor.get("name", "")
                parameter_name = sensor.get("parameterName")
                input_number = sensor.get("inputNumber")
                input_type = sensor.get("inputType")
                # TODO: нужен refactoring т.к. слишком много edge кейсов
                if "Скорость" in sensor_name or parameter_name == "can_speed":
                    sensors_mapping["speed"] = f"parameters.{parameter_name}"
                elif sensor_type == "FuelLvl":
                    if sensor.get("gradeType") == "GradeTable":
                        grades_tables = sensor.get("gradesTables", [{}])
                        if grades_tables and grades_tables[0]:
                            grades = grades_tables[0].get("grades", [{}])
                            if grades:
                                record = grades[-1]
                                input_value = record.get("input")
                                output_value = record.get("output")
                    if parameter_name:
                        key_part = parameter_name.split(";")[0]
                        # без числа именно эта колонка должна быть добавлена
                        if key_part.startswith("can_fuel_volume"):
                            sensors_mapping["calc_sensors_fuel_level"] = (
                                f"parameters.can_fuel_volume"
                            )
                        # can_fuel_level название датчика can{input_number}
                        elif key_part.startswith("can_") and input_number:
                            sensors_mapping["calc_sensors_fuel_level"] = (
                                f"parameters.can{input_number}"
                            )
                        else:
                            # остальные имеют parameterName как имя колонки (fuel1, fuel2, lss1)
                            sensors_mapping["calc_sensors_fuel_level"] = (
                                f"parameters.{key_part}"
                            )
                    elif input_number:
                        # пустой parameterName у датчиков типа Analog, колонка analog{input_number}
                        sensors_mapping["calc_sensors_fuel_level"] = (
                            f"parameters.analog{input_number}"
                        )

                elif sensor_type == "EngineRPM":
                    if parameter_name:
                        key_part = parameter_name.split(";")[0]
                        if key_part.startswith("can_") and input_number:
                            sensors_mapping["rpm"] = f"parameters.can{input_number}"
                        else:
                            sensors_mapping["rpm"] = f"parameters.{key_part}"
                # по имени датчика или по типо, + защита от опечаток для имени
                elif (
                    sensor_type == "MileageSensor"
                    or textdistance.damerau_levenshtein(sensor_name, "Пробег") <= 2
                ):
                    if parameter_name:
                        key_part = parameter_name.split(";")[0]
                        if key_part == "can_mileage":
                            sensors_mapping["mileage"] = f"parameters.can_mileage"
                        elif key_part.startswith("can_") and input_number:
                            sensors_mapping["mileage"] = f"parameters.can{input_number}"
                        else:
                            sensors_mapping["mileage"] = f"parameters.{key_part}"
                elif sensor_type == "Temperature":
                    if parameter_name:
                        key_part = parameter_name.split(";")[0]
                        if key_part.startswith("can_") and input_number:
                            sensors_mapping["engine_temp"] = (
                                f"parameters.can{input_number}"
                            )
                        else:
                            sensors_mapping["engine_temp"] = f"parameters.{key_part}"
                elif sensor_type == "EngineTemperature":
                    if parameter_name:
                        key_part = parameter_name.split(";")[0]
                        if key_part.startswith("can_") and input_number:
                            sensors_mapping["engine_temp"] = (
                                f"parameters.can{input_number}"
                            )
                        else:
                            sensors_mapping["engine_temp"] = f"parameters.{key_part}"
                elif sensor_type == "Ignition":
                    if parameter_name:
                        key_part = "iobits"
                        if input_type == "FMS":
                            key_part = "ign"
                        sensors_mapping["ign"] = f"parameters.{key_part}"
                elif (
                    sensor_type == "Motohours"
                    or textdistance.damerau_levenshtein(sensor_name, "моточасы") <= 2
                ):
                    if parameter_name:
                        key_part = parameter_name.split(";")[0]
                        if key_part == "can_engine_hours":
                            sensors_mapping["motohours"] = (
                                f"parameters.can_engine_hours"
                            )
                        elif key_part.startswith("can_") and input_number:
                            sensors_mapping["motohours"] = (
                                f"parameters.can{input_number}"
                            )
                        else:
                            sensors_mapping["motohours"] = f"parameters.{key_part}"
            data["input"] = input_value
            data["output"] = output_value
            data["sensorsMapping"] = sensors_mapping
            self._log_resources("get_vehicle_details")
            return data

        except requests.exceptions.RequestException as e:
            logger.error(
                f"Ошибка запроса при получении деталей для vehicleId={vehicle_id}: {e}"
            )
            return None

    def get_terminal_to_json(
        self, vehicle_id: int, start_date: datetime, end_date: datetime
    ) -> None:
        """
        Получает данные терминала за заданный период и сохраняет их в CSV и JSON.
        """
        try:
            car = Car.objects.get(id_in_provider_system=vehicle_id)
            start_date = start_date.replace(tzinfo=pytz.UTC)
            end_date = end_date.replace(tzinfo=pytz.UTC)

            if start_date >= end_date:
                logger.info(f"Пропущен vehicleId={vehicle_id}: start_date >= end_date")
                return

            logger.info(
                f"Запрос данных vehicleId={vehicle_id} с {start_date} по {end_date}"
            )
            current_start = start_date

            while current_start < end_date:
                period_days = self._get_adaptive_period(current_start, end_date)
                current_end = min(current_start + timedelta(days=period_days), end_date)

                try:
                    success = self._fetch_terminal_to_json_for_period(
                        vehicle_id, current_start, current_end
                    )
                    if not success and self.default_period_days > 1:
                        self.default_period_days = max(self.default_period_days // 2, 1)
                        logger.warning(
                            f"Уменьшен период до {self.default_period_days} дней из-за ошибки."
                        )
                        continue
                    elif not success:
                        logger.error(
                            f"Не удалось получить данные для vehicleId={vehicle_id} даже за 1 день."
                        )
                        break

                    current_start = current_end + timedelta(seconds=1)

                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 429:
                        self.default_period_days = max(self.default_period_days // 2, 1)
                        logger.warning(
                            f"Сработал Rate Limit (429). Уменьшаем период до {self.default_period_days} дней."
                        )
                        continue
                    else:
                        raise

            self._save_all_terminal_messages_to_csv(vehicle_id)
            self._save_full_terminal_messages_to_json(vehicle_id)

            if not car.last_processed_date or end_date > car.last_processed_date:
                car.last_processed_date = end_date
                car.save(update_fields=["last_processed_date"])
                logger.info(
                    f"Обновлена last_processed_date для vehicleId={vehicle_id} до {end_date}"
                )
            else:
                logger.info(
                    f"last_processed_date для vehicleId={vehicle_id} не обновлена: {car.last_processed_date}"
                )

            self._log_resources("get_terminal_to_json")

        except Car.DoesNotExist:
            logger.error(f"Автомобиль vehicleId={vehicle_id} не найден.")
        except Exception as e:
            logger.error(
                f"Критическая ошибка получения данных для vehicleId={vehicle_id}: {e}",
                exc_info=True,
            )

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def _fetch_terminal_to_json_for_period(
        self, vehicle_id: int, start_date: datetime, end_date: datetime
    ) -> bool:
        """
        Запрашивает данные терминала за указанный период.
        """
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
        logger.info(f"Запрос данных vehicleId={vehicle_id}, {start_date} - {end_date}")

        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = orjson.loads(response.content)
            messages = data.get("messages", [])

            if not isinstance(messages, list):
                logger.error(f"'Messages' не список: {type(messages)}")
                return False

            logger.info(
                f"Получено {len(messages)} сообщений для vehicleId={vehicle_id}."
            )

            if not messages:
                logger.info(f"Нет сообщений для vehicleId={vehicle_id}.")
                return True

            if (
                not hasattr(self, "all_terminal_messages")
                or self.all_terminal_messages is None
            ):
                self.all_terminal_messages = {}

            if vehicle_id not in self.all_terminal_messages:
                self.all_terminal_messages[vehicle_id] = []
            self.all_terminal_messages[vehicle_id].extend(messages)

            tmp_json_path = (
                self.tmp_dir
                / f"terminal_messages_{vehicle_id}_{start_date.strftime('%Y%m%d%H%M%S')}.json"
            )
            with open(tmp_json_path, "wb") as f:
                f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
            logger.debug(f"Буферные данные сохранены в {tmp_json_path}")

            return True

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP-ошибка при запросе данных: {e.response.status_code}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Ошибка запроса данных терминала для vehicleId={vehicle_id}: {e}"
            )
            return False

    def _save_full_terminal_messages_to_json(self, vehicle_id: int) -> None:
        """Сохраняет все собранные сообщения терминала в один JSON-файл."""
        messages = self.all_terminal_messages.get(vehicle_id, [])
        if not messages:
            logger.info(f"Нет данных terminalMessages для vehicleId={vehicle_id}")
            return

        with open(self.full_json_file_path, "wb") as f:
            f.write(orjson.dumps(messages, option=orjson.OPT_INDENT_2))

        file_size = os.path.getsize(self.full_json_file_path) / 1024**2
        logger.info(
            f"Полные данные JSON сохранены в {self.full_json_file_path} ({file_size:.2f} MB)"
        )

        self.all_terminal_messages.pop(vehicle_id, None)

    def _save_all_terminal_messages_to_csv(self, vehicle_id: int) -> None:
        """
        Сохраняет все собранные сообщения терминала в один CSV-файл.
        """
        messages = self.all_terminal_messages.get(vehicle_id, [])
        if not messages:
            logger.info(f"Нет данных terminalMessages для vehicleId={vehicle_id}")
            return

        try:
            car = Car.objects.get(id_in_provider_system=vehicle_id)
            sensors_qs = car.sensors.all()
            sensors_mapping = {s.label: s.value for s in sensors_qs}
            vehicle_guid = car.id
        except Car.DoesNotExist:
            logger.error(f"Автомобиль vehicleId={vehicle_id} не найден")
            return

        speed_key_path = sensors_mapping.get("speed", "speed").split(".")
        fuel_key_path = sensors_mapping.get("calc_sensors_fuel_level", "").split(".")
        rpm_key_path = sensors_mapping.get("rpm", "").split(".")
        ign_key_path = sensors_mapping.get("ign", "").split(".")
        engine_temp_key_path = sensors_mapping.get("engine_temp", "").split(".")
        mileage_key_path = sensors_mapping.get("mileage", "").split(".")
        motohours_key_path = sensors_mapping.get("motohours", "").split(".")

        logger.info(
            f"Маппинг для vehicleId={vehicle_id}: fuel_key_path={fuel_key_path}, rpm_key_path={rpm_key_path}"
        )

        headers = [
            "auto",
            "timestamp",
            "pos_s",
            "calc_sensors_fuel_level",
            "calc_sensors_voltage",
            "rpm",
            "amtr",
            "mileage",
            "engine_temp",
            "ign",
            "latitude",
            "longitude",
            "motohours",
            "satellites",
        ]

        if not self.csv_initialized:
            mode = "w"
            self.csv_initialized = True
            logger.info(f"Инициализация CSV-файла: {self.csv_file_path}")
        else:
            mode = "a"

        messages_iter = iter(messages)
        with open(self.csv_file_path, mode, encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers, lineterminator="\n")
            if mode == "w":
                writer.writeheader()

            while True:
                chunk = list(islice(messages_iter, self.chunk_size))
                if not chunk:
                    break

                rows_to_write = []
                for record in chunk:
                    # TODO: может это куда-то перенести?
                    def get_nested_value(d: dict, keys: list):
                        current = d
                        if not keys or keys == [""]:
                            return None

                        for key in keys:
                            if not isinstance(current, dict) or key not in current:
                                return None
                            current = current[key]
                        return current

                    speed = get_nested_value(record, speed_key_path)
                    fuel_level = get_nested_value(record, fuel_key_path)
                    rpm = get_nested_value(record, rpm_key_path)
                    ign = get_nested_value(record, ign_key_path)
                    if ign is None:
                        ign = 0
                    else:
                        try:
                            ign = int(ign)
                        except:
                            ign = 0
                        engine_temp = get_nested_value(record, engine_temp_key_path)
                    mileage = get_nested_value(record, mileage_key_path)
                    motohours = get_nested_value(record, motohours_key_path)

                    logger.debug(f"Найдено: fuel_level={fuel_level}, rpm={rpm}")

                    amtr_x = float(record.get("amtr_x", 0) or 0)
                    amtr_y = float(record.get("amtr_y", 0) or 0)
                    amtr_z = float(record.get("amtr_z", 0) or 0)
                    amtr = int(abs(amtr_x) + abs(amtr_y) + abs(amtr_z))

                    rows_to_write.append(
                        {
                            "auto": vehicle_guid,
                            "timestamp": record.get("deviceTime"),
                            "pos_s": speed,
                            "latitude": record.get("latitude"),
                            "longitude": record.get("longitude"),
                            "calc_sensors_fuel_level": fuel_level,
                            "calc_sensors_voltage": record.get("voltage"),
                            "rpm": rpm,
                            "amtr": amtr,
                            "ign": 1 if ign > 0 else 0,
                            "engine_temp": engine_temp,
                            "mileage": mileage,
                            "motohours": motohours,
                            "satellites": record.get("satellites"),
                        }
                    )

                writer.writerows(rows_to_write)
                logger.info(
                    f"Добавлено {len(rows_to_write)} записей для vehicle_guid={vehicle_guid}"
                )
                self._log_resources("_save_all_terminal_messages_to_csv chunk")

        file_size = os.path.getsize(self.csv_file_path) / 1024**2
        logger.info(f"Все данные сохранены в {self.csv_file_path} ({file_size:.2f} MB)")

        self.all_terminal_messages.pop(vehicle_id, None)
        self._log_resources("save_all_terminal_messages_to_csv final")

    def _create_media_record(self, file_path: Path, file_type: str) -> None:
        """
        Создаёт или обновляет запись в модели Media, связанную с ReportQuery.
        """
        try:
            report_query = ReportQuery.objects.get(id=self.report_query_id)
            if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                logger.warning(
                    f"Файл {file_path} не существует или пустой, пропускаем создание Media"
                )
                return

            existing_media = Media.objects.filter(
                report_query_id=report_query, type=file_type
            ).first()

            media_file_relative_path = str(file_path.relative_to(settings.MEDIA_ROOT))
            file_size = os.path.getsize(file_path)

            if existing_media:
                if existing_media.file and os.path.exists(existing_media.file.path):
                    os.remove(existing_media.file.path)
                    logger.info(f"Удален старый файл: {existing_media.file.path}")

                existing_media.file = media_file_relative_path
                existing_media.filename = file_path.name
                existing_media.size = file_size
                existing_media.save()
                logger.info(f"Обновлена запись Media: {existing_media.id}")
            else:
                media = Media.objects.create(
                    report_query_id=report_query,
                    type=file_type,
                    file=media_file_relative_path,
                    filename=file_path.name,
                    size=file_size,
                )
                logger.info(f"Создана запись Media: {media.id}")
        except Exception as e:
            logger.error(f"Ошибка создания Media: {e}")
            raise

    @transaction.atomic
    def save_to_db(self, vehicle_data: Dict[str, Any], provider: Any) -> None:
        """
        Сохраняет или обновляет данные об автомобиле и датчиках в БД.
        """
        custom_fields = {
            field["name"]: field["value"]
            for field in vehicle_data.get("customFields", [])
        }
        engine_type = 1.0 if custom_fields.get("Тип учета") == "Моточасы" else 0.0
        vehicle_guid = vehicle_data.get("vehicleGuid")
        vehicle_id = vehicle_data.get("vehicleId")

        if not vehicle_guid or not vehicle_id:
            logger.error(
                f"Отсутствуют vehicleGuid или vehicleId в данных: {vehicle_data}"
            )
            raise ValueError("Missing vehicleGuid or vehicleId")

        car, created = Car.objects.update_or_create(
            id=vehicle_guid,
            defaults={
                "id_in_provider_system": vehicle_id,
                "name": vehicle_data.get("name", ""),
                "description": f"{vehicle_data.get('parentName', '')}, {vehicle_data.get('modelName', '')}, {vehicle_data.get('unitName', '')}",
                "engine_type": engine_type,
                "input": vehicle_data.get("input"),
                "output": vehicle_data.get("output"),
                "is_tarrified": not (
                    (
                        vehicle_data.get("input") is None
                        or vehicle_data.get("input") == 1.0
                    )
                    and (
                        vehicle_data.get("output") is None
                        or vehicle_data.get("output") == 1.0
                    )
                ),
                "is_active": vehicle_data.get("isActive", True),
            },
        )

        provider.cars.add(car)

        for label, value in vehicle_data.get("sensorsMapping", {}).items():
            SensorsMapping.objects.update_or_create(
                car_id=car, label=label, defaults={"value": value}
            )

        logger.info(f"Сохранены данные для vehicleId={vehicle_id}.")

    def get_single_vehicle_data_in_memory(
        self, vehicle_id: int, start_date: datetime, end_date: datetime
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Получает данные для одного автомобиля за период, парсит timestamp и mileage "на лету" и возвращает результат в памяти.
        """
        if not self.auth_token:
            logger.error("Токен отсутствует. Выполните аутентификацию.")
            return None

        mileage_key_path = self._get_mileage_key_path(vehicle_id)
        if mileage_key_path is None:
            return []

        parsed_data = []
        current_start = start_date

        periods = self._calculate_periods(start_date, end_date)

        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = []
            for period_start, period_end in periods:
                future = executor.submit(
                    self._fetch_and_parse_mileage_messages_for_period,
                    vehicle_id,
                    period_start,
                    period_end,
                    mileage_key_path,
                )
                futures.append(future)

            for future in futures:
                result = future.result()
                if result is not None:
                    parsed_data.extend(result)

        logger.info(
            f"Всего спарсено {len(parsed_data)} записей timestamp + mileage для vehicleId={vehicle_id}"
        )
        return parsed_data

    @lru_cache(maxsize=100)
    def _get_mileage_key_path(self, vehicle_id: int) -> Optional[List[str]]:
        """Кешируем получение пути к mileage."""
        try:
            car = Car.objects.only("id").get(id_in_provider_system=vehicle_id)
            sensor = car.sensors.only("label", "value").filter(label="mileage").first()
            if not sensor or not sensor.value:
                logger.warning(f"Нет маппинга для mileage для vehicleId={vehicle_id}.")
                return None

            key_path = sensor.value.split(".")
            return key_path if key_path and key_path != [""] else None

        except Car.DoesNotExist:
            logger.error(f"Автомобиль vehicleId={vehicle_id} не найден.")
            return None

    def _calculate_periods(
        self, start_date: datetime, end_date: datetime
    ) -> List[Tuple[datetime, datetime]]:
        """Предварительно рассчитываем все периоды для параллельной обработки."""
        periods = []
        current_start = start_date

        while current_start < end_date:
            period_days = self._get_adaptive_period(current_start, end_date)
            current_end = min(current_start + timedelta(days=period_days), end_date)
            periods.append((current_start, current_end))
            current_start = current_end + timedelta(seconds=1)

        return periods

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def _fetch_and_parse_mileage_messages_for_period(
        self,
        vehicle_id: int,
        start_date: datetime,
        end_date: datetime,
        mileage_key_path: List[str],
    ) -> Optional[List[Dict[str, Any]]]:
        """Упрощенная версия без передачи parsed_data по ссылке."""
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

        try:
            start_time = time.time()
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()

            data = orjson.loads(response.content)
            messages = data.get("messages", [])

            if not isinstance(messages, list):
                logger.error(f"'Messages' не список: {type(messages)}")
                return []

            logger.info(
                f"Получено {len(messages)} сообщений за {time.time() - start_time:.2f} сек"
            )

            parsed_data = self._parse_messages_batch(messages, mileage_key_path)
            return parsed_data

        except requests.exceptions.HTTPError as e:
            logger.error(
                f"HTTP-ошибка {e.response.status_code} для периода {start_date} - {end_date}"
            )
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса для vehicleId={vehicle_id}: {e}")
            return None

    def _parse_messages_batch(
        self, messages: List[Dict], mileage_key_path: List[str]
    ) -> List[Dict[str, Any]]:
        """Батчевый парсинг сообщений."""
        parsed_data = []

        for record in messages:
            timestamp_str = record.get("deviceTime")
            if not timestamp_str:
                continue

            mileage = self._extract_mileage(record, mileage_key_path)
            if mileage is None:
                continue

            timestamp = self._parse_timestamp(timestamp_str)
            if timestamp:
                parsed_data.append({"timestamp": timestamp, "mileage": float(mileage)})

        return parsed_data

    def _extract_mileage(
        self, record: Dict, mileage_key_path: List[str]
    ) -> Optional[float]:
        """Быстрое извлечение mileage по пути."""
        try:
            current = record
            for key in mileage_key_path:
                current = current.get(key) if isinstance(current, dict) else None
                if current is None:
                    return None
            return float(current) if current is not None else None
        except (ValueError, TypeError, AttributeError):
            return None

    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """Быстрый парсинг timestamp."""
        try:
            if "." in timestamp_str:
                return datetime.strptime(
                    timestamp_str, "%Y-%m-%dT%H:%M:%S.%fZ"
                ).replace(tzinfo=pytz.UTC)
            else:
                return datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=pytz.UTC
                )
        except ValueError:
            return None


class ProviderType(Enum):
    GLONASSSOFT = GlonassSoftProvider

    @classmethod
    def get_class(cls, member: str):
        try:
            return cls[member.upper()].value
        except KeyError:
            return None


def provider_factory(report_query_id: str, metadata: Dict[str, Any]) -> Optional[Any]:
    """
    Создаёт экземпляр класса провайдера, используя ключ из метаданных и Enum.
    """
    try:
        provider_type_str = metadata.get("provider_type")
        if not provider_type_str:
            logger.error(
                f"В метаданных ReportQuery ID={report_query_id} отсутствует ключ 'provider_type'."
            )
            return None

        provider_class = ProviderType.get_class(provider_type_str)

        if not provider_class:
            logger.error(f"Неизвестный тип провайдера: '{provider_type_str}'.")
            return None

        return provider_class(metadata, report_query_id)

    except Exception as e:
        logger.error(
            f"Ошибка при создании экземпляра провайдера для ReportQuery ID={report_query_id}: {e}"
        )
        return None


def save_response_to_file(
    data: Dict[str, Any], filename: str = "response.json"
) -> None:
    """Сохраняет JSON-ответ в файл для отладки."""
    file_path = Path(settings.BASE_DIR) / filename
    try:
        with open(file_path, "wb") as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
        logger.info(f"Ответ сохранён: {file_path}")
    except Exception as e:
        logger.error(f"Ошибка сохранения {file_path}: {e}")
