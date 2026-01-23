import csv
import json
import logging
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, cast
from concurrent.futures import ThreadPoolExecutor, as_completed

import orjson
import pytz
import requests
from django.conf import settings
from django.utils import timezone

from core.helpers.decorators import retry_on_status
from core.models import Car, DataProvider, SensorsValues
from core.services.providers.rate_limiter import global_rate_limiter

logger = logging.getLogger(__name__)


class GlonassSoftTerminalMessagesParser:
    """
    Парсер для сохранения terminalMessages в CSV файлы (1 машина = 1 файл)
    Поддерживает два режима: raw (без маппинга) и mapped (с маппингом сенсоров)
    """

    def __init__(
            self,
            provider_name: str,
            start_date: datetime,
            end_date: datetime,
            is_raw_data: bool = True
    ):
        self.provider_name = provider_name
        self.start_date = start_date.replace(tzinfo=pytz.UTC)
        self.end_date = end_date.replace(tzinfo=pytz.UTC)
        self.is_raw_data = is_raw_data

        if self.start_date >= self.end_date:
            raise ValueError("start_date должен быть раньше end_date")

        try:
            self.provider = DataProvider.objects.get(name=provider_name)
        except DataProvider.DoesNotExist:
            raise ValueError(f"Провайдер '{provider_name}' не найден в БД")

        self.metadata = self.provider.metadata or {}
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.auth_token = None
        self.default_period_days = 90

        date_from_str = self.start_date.strftime("%Y%m%d")
        date_to_str = self.end_date.strftime("%Y%m%d")
        mode_suffix = "raw" if self.is_raw_data else "mapped"

        self.base_dir = Path(settings.MEDIA_ROOT) / "raw_data" / f"raw_data_{date_from_str}-{date_to_str}_{mode_suffix}"
        os.makedirs(self.base_dir, exist_ok=True)

        self.total_cars = 0
        self.processed_cars = 0
        self.failed_cars = 0
        self.start_time = None
        self.end_time = None

        self.sensors_mapping_cache = {}

    def _enforce_rate_limit(self) -> None:
        """Соблюдение rate limit (1 запрос в секунду)"""
        global_rate_limiter.wait_for_rate_limit()

    @retry_on_status(retry_delays=[10, 20, 30], status_codes=[400, 429])
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

    # TODO: убрать передавать в таск/класс
    def get_all_vehicles_from_db(self) -> List[Car]:
        """Получает все машины из БД для данного провайдера"""
        cars = Car.objects.filter(data_providers=self.provider).all()
        return list(cars)

    # TODO: убрать передавать в таск/класс
    def _get_sensors_mapping(self, car: Car) -> Dict[str, str]:
        """Получает маппинг сенсоров для машины"""
        if car.id not in self.sensors_mapping_cache:
            try:
                sensors_mapping = {
                    sv.key.key: sv.value
                    for sv in SensorsValues.objects.filter(car_id=car).select_related("key")
                }
                self.sensors_mapping_cache[car.id] = sensors_mapping
                logger.debug(f"Загружен маппинг для {car.name}: {len(sensors_mapping)} сенсоров")
            except Exception as e:
                logger.error(f"Ошибка загрузки маппинга для {car.name}: {e}")
                self.sensors_mapping_cache[car.id] = {}

        return self.sensors_mapping_cache[car.id]
    # TODO: убрать сделать через новый препроцессор парсер
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

    def parse_all_cars(self, max_workers: int = 3, parse_all: bool = False, car_ids: Optional[List[str]] = None) -> \
    Dict[str, Any]:
        """Парсит данные для машин"""
        self.start_time = timezone.now()

        if parse_all:
            cars = Car.objects.filter(is_active=True,data_providers=self.provider).all()
        elif car_ids:
            cars = Car.objects.filter(id__in=car_ids,data_providers=self.provider).all()
        else:
            cars = Car.objects.filter(data_providers=self.provider).all()

        self.total_cars = len(cars)

        if not cars:
            mode_description = "активных машин" if parse_all else "указанных машин" if car_ids else f"машин для провайдера '{self.provider_name}'"
            logger.warning(f"Нет {mode_description}")
            return {
                "status": "completed",
                "message": f"Нет {mode_description} для обработки",
                "mode": "parse_all" if parse_all else ("specific_cars" if car_ids else "all_cars"),
                "total_cars": 0,
                "processed_cars": 0,
                "failed_cars": 0,
                "parse_all": parse_all,
                "car_ids_count": len(car_ids) if car_ids else None
            }

        mode = "parse_all" if parse_all else ("specific_cars" if car_ids else "all_cars")
        logger.info(
            f"Начинаем парсинг {self.total_cars} машин в режиме {mode} ({'raw' if self.is_raw_data else 'mapped'})")

        if not self.authenticate():
            return {
                "status": "failed",
                "message": "Ошибка аутентификации",
                "mode": mode,
                "total_cars": self.total_cars,
                "processed_cars": 0,
                "failed_cars": 0,
                "parse_all": parse_all,
                "car_ids_count": len(car_ids) if car_ids else None
            }

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.parse_single_car, car): car
                for car in cars
            }

            for future in as_completed(futures):
                car = futures[future]
                try:
                    success = future.result()
                    if success:
                        self.processed_cars += 1
                        logger.info(f"Успешно обработана машина: {car.name} (ID: {car.id_in_provider_system})")
                    else:
                        self.failed_cars += 1
                        logger.error(f"Ошибка обработки машины: {car.name} (ID: {car.id_in_provider_system})")
                except Exception as e:
                    self.failed_cars += 1
                    logger.error(f"Исключение при обработке машины {car.id_in_provider_system}: {e}")

        self.end_time = timezone.now()

        return {
            "status": "completed",
            "message": f"Обработано {self.processed_cars} из {self.total_cars} машин",
            "mode": mode,
            "total_cars": self.total_cars,
            "processed_cars": self.processed_cars,
            "failed_cars": self.failed_cars,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "data_mode": "raw" if self.is_raw_data else "mapped",
            "parse_all": parse_all,
            "car_ids_count": len(car_ids) if car_ids else None,
            "execution_time": str(self.end_time - self.start_time)
        }

    def parse_single_car(self, car: Car) -> bool:
        """Парсит данные для одной машины"""
        vehicle_id = car.id_in_provider_system

        try:
            all_messages = self._get_all_messages_for_period(vehicle_id)

            if not all_messages:
                logger.warning(f"Нет данных для машины {vehicle_id}")
                return False

            if self.is_raw_data:
                success = self._save_to_csv_raw(car, all_messages)
            else:
                success = self._save_to_csv_mapped(car, all_messages)

            return success

        except Exception as e:
            logger.error(f"Ошибка парсинга машины {vehicle_id}: {e}")
            return False

    def _get_all_messages_for_period(self, vehicle_id: int) -> List[Dict[str, Any]]:
        """Получает все сообщения за период с адаптивными запросами"""
        all_messages = []
        current_start = self.start_date

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

        return all_messages

    @retry_on_status(retry_delays=[10, 20, 30], status_codes=[400, 429])
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

    def _save_to_csv_raw(self, car: Car, messages: List[Dict[str, Any]]) -> bool:
        """Сохраняет сырые сообщения в CSV файл (без маппинга)"""
        if not messages:
            return False

        filename = f"auto{car.id_in_provider_system}_raw.csv"
        file_path = self.base_dir / filename

        try:
            all_keys = set()
            for message in messages:
                all_keys.update(message.keys())

            fieldnames = sorted(all_keys)

            with open(file_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for message in messages:
                    row = {}
                    for key in fieldnames:
                        value = message.get(key)

                        if value is None:
                            row[key] = ''
                        elif isinstance(value, (dict, list)):
                            row[key] = json.dumps(value, ensure_ascii=False)
                        else:
                            row[key] = str(value)

                    writer.writerow(row)

            file_size = os.path.getsize(file_path) / 1024 ** 2

            logger.info(f"Сохранен RAW файл: {file_path} ({len(messages)} записей, {file_size:.2f} MB)")

            self._archive_csv_file(file_path)

            return True

        except Exception as e:
            logger.error(f"Ошибка сохранения RAW CSV для {car.id_in_provider_system} в {file_path}: {e}")
            return False

    def _save_to_csv_mapped(self, car: Car, messages: List[Dict[str, Any]]) -> bool:
        """Сохраняет данные с маппингом сенсоров в CSV файл"""
        if not messages:
            return False

        sensors_mapping = self._get_sensors_mapping(car)

        fieldnames = [
            "auto",
            "timestamp",
            "latitude",
            "longitude",
            "satellites",
            "calc_sensors_voltage",
            "calc_sensors_fuel_level",
            "pos_s",
            "rpm",
            "ign",
            "mileage",
            "motohours",
            "engine_temp",
            "amtr",
        ]

        filename = f"auto{car.id_in_provider_system}_mapped.csv"
        file_path = self.base_dir / filename

        try:
            with open(file_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                processed_count = 0
                skipped_count = 0

                for message in messages:
                    row = self._extract_mapped_data(message, sensors_mapping, str(car.id))
                    if row:
                        writer.writerow(row)
                        processed_count += 1
                    else:
                        skipped_count += 1

            file_size = os.path.getsize(file_path) / 1024 ** 2

            logger.info(
                f"Сохранен MAPPED файл: {file_path} ({processed_count} записей, "
                f"пропущено {skipped_count}, {file_size:.2f} MB)"
            )

            self._archive_csv_file(file_path)

            if skipped_count > 0:
                logger.info(f"Пропущено {skipped_count} сообщений из {len(messages)} для {car.name}")

            return True

        except Exception as e:
            logger.error(f"Ошибка сохранения MAPPED CSV для {car.id_in_provider_system} в {file_path}: {e}")
            return False

    def _extract_mapped_data(
            self,
            message: Dict[str, Any],
            sensors_mapping: Dict[str, str],
            car_guid: str
    ) -> Optional[Dict[str, Any]]:
        """
        Извлекает данные из сообщения по маппингу сенсоров
        Аналогично методу _extract_message_data из референса
        """
        try:
            row = {
                "auto": str(car_guid),
                "timestamp": str(message.get("deviceTime", "")),
                "latitude": float(message.get("latitude", 0) or 0),
                "longitude": float(message.get("longitude", 0) or 0),
                "satellites": int(message.get("satellites", 0) or 0),
                "calc_sensors_voltage": float(message.get("voltage", 0) or 0),
            }

            fuel_path = sensors_mapping.get("calc_sensors_fuel_level", "")
            row["calc_sensors_fuel_level"] = self._get_nested_value(message, fuel_path, None)

            row["pos_s"] = self._get_nested_value(message, sensors_mapping.get("speed", "speed"), 0.0)
            row["rpm"] = self._get_nested_value(message, sensors_mapping.get("rpm", ""), 0.0)
            row["mileage"] = self._get_nested_value(message, sensors_mapping.get("mileage", ""), 0.0)
            row["motohours"] = self._get_nested_value(message, sensors_mapping.get("motohours", ""), 0.0)
            row["engine_temp"] = self._get_nested_value(message, sensors_mapping.get("engine_temp", ""), 0.0)

            ign_value = cast(float, self._get_nested_value(message, sensors_mapping.get("ign", ""), 0.0))
            row["ign"] = 1 if ign_value > 0 else 0

            try:
                amtr_x = float(message.get("amtr_x", 0) or 0)
                amtr_y = float(message.get("amtr_y", 0) or 0)
                amtr_z = float(message.get("amtr_z", 0) or 0)
                row["amtr"] = int(abs(amtr_x) + abs(amtr_y) + abs(amtr_z))
            except (ValueError, TypeError):
                row["amtr"] = 0

            if (
                    row["calc_sensors_fuel_level"] is None
                    and row["pos_s"] == 0
                    and row["rpm"] == 0
            ):
                return None

            return row

        except Exception as e:
            logger.debug(f"Ошибка обработки сообщения: {e}")
            return None

    def _archive_csv_file(self, csv_file_path: Path) -> None:
        """
        Архивирует CSV файл в ZIP архив и удаляет исходный файл

        Args:
            csv_file_path: Путь к CSV файлу для архивации
        """
        try:
            zip_file_path = csv_file_path.with_suffix('.csv.zip')

            with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
                zipf.write(csv_file_path, arcname=csv_file_path.name)

            csv_size = os.path.getsize(csv_file_path) / 1024 ** 2
            zip_size = os.path.getsize(zip_file_path) / 1024 ** 2


            os.remove(csv_file_path)

            compression_ratio = ((csv_size - zip_size) / csv_size) * 100 if csv_size > 0 else 0
            logger.info(
                f"Файл {csv_file_path.name} заархивирован в {zip_file_path.name}. "
                f"Размер: {csv_size:.2f} MB -> {zip_size:.2f} MB "
                f"(сжатие: {compression_ratio:.1f}%)"
            )

        except Exception as e:
            logger.error(f"Ошибка при архивации файла {csv_file_path}: {e}")