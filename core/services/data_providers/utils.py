import requests
import logging
from typing import Dict, Any, Optional, List, Iterable
from django.conf import settings
import orjson
import time
from datetime import datetime, timedelta
from django.db import transaction
from core.models import Car, CarConsumption, Media, ReportQuery, DataProvider
import csv
import os
import glob
from pathlib import Path
import psutil
from itertools import islice
import pytz

logger = logging.getLogger(__name__)

class GlonassSoftProvider:
    def __init__(self, metadata: Dict[str, Any], report_query_id: str):
        self.metadata = metadata
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.auth_token: Optional[str] = None
        self.last_request_time: float = 0
        self.report_query_id = report_query_id
        self.batch_size = 10
        self.chunk_size = 25_000
        self.all_terminal_messages: Dict[int, List[Dict[str, Any]]] = {}
        timestamp = datetime.now(tz=pytz.UTC).strftime("%Y%m%d_%H%M%S")
        media_dir = Path(settings.MEDIA_ROOT) / "raw_data"
        tmp_dir = Path(settings.BASE_DIR) / "tmp"
        os.makedirs(media_dir, exist_ok=True)
        os.makedirs(tmp_dir, exist_ok=True)
        self.csv_file_path = media_dir / f"raw_data_{report_query_id}_{timestamp}.csv"
        self.tmp_dir = tmp_dir
        self.csv_initialized = False
        self.default_period_days = 90

    def _enforce_rate_limit(self) -> None:
        logger.info("Задержка ровно 1.00 сек для соблюдения лимита API.")
        time.sleep(1.0)
        self.last_request_time = time.time()

    def _log_resources(self, method: str) -> None:
        process = psutil.Process()
        ram_mb = process.memory_info().rss / 1024 ** 2
        cpu_percent = psutil.cpu_percent()
        logger.info(f"[{method}] RAM: {ram_mb:.2f} MB, CPU: {cpu_percent:.1f}%")

    def _get_adaptive_period(self, start_date: datetime, end_date: datetime) -> int:
        days = (end_date - start_date).days
        period_days = min(self.default_period_days, days)
        logger.info(f"Выбран период {period_days} дней.")
        return max(period_days, 1)

    def _cleanup_tmp_files(self) -> None:
        pattern = str(self.tmp_dir / "terminal_messages_*.json")
        for file_path in glob.glob(pattern):
            try:
                os.remove(file_path)
                logger.info(f"Удалён буферный файл: {file_path}")
            except Exception as e:
                logger.error(f"Ошибка удаления {file_path}: {e}")
        self._log_resources("cleanup_tmp_files")

    def authenticate(self) -> bool:
        self._enforce_rate_limit()
        url = f"{self.base_url}/auth/login"
        payload = {
            "login": self.metadata.get("login"),
            "password": self.metadata.get("password")
        }
        logger.info(f"Авторизация: URL={url}")
        max_retries = 3
        retry_delays = [5, 10, 20]
        attempt = 0

        while attempt < max_retries:
            try:
                response = requests.post(url, json=payload)
                response.raise_for_status()
                data = orjson.loads(response.content)
                self.auth_token = data.get("AuthId")
                logger.info(f"Авторизация успешна. AuthId: {self.auth_token}")
                self._log_resources("authenticate")
                return True
            except requests.HTTPError as e:
                if response.status_code == 429:
                    attempt += 1
                    if attempt < max_retries:
                        delay = retry_delays[attempt - 1]
                        logger.warning(f"Ошибка 429 при авторизации, попытка {attempt}/{max_retries}, жду {delay} сек.")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Исчерпаны попытки авторизации после ошибки 429.")
                        return False
                else:
                    logger.error(f"Ошибка HTTP при авторизации: {e}")
                    return False
            except Exception as e:
                logger.error(f"Ошибка авторизации: {e}")
                return False

    def get_vehicles(self, name: Optional[str] = None, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> Optional[List[Dict[str, Any]]]:
        if not self.auth_token:
            logger.error("Токен отсутствует.")
            return None

        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/find"
        params = {"name": name}
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Запрос автомобилей: {url}")
        max_retries = 3
        retry_delays = [5, 10, 20]
        attempt = 0

        while attempt < max_retries:
            try:
                response = requests.post(url, json=params, headers=headers)
                response.raise_for_status()
                data = orjson.loads(response.content)
                logger.info(f"Получено {len(data)} автомобилей.")
                self._log_resources("get_vehicles")

                if not isinstance(data, list):
                    logger.error(f"Ожидался список, получен: {type(data)}")
                    return None

                processed_vehicles = []
                for i in range(0, len(data), self.batch_size):
                    batch = data[i:i + self.batch_size]
                    logger.info(f"Батч {i + 1}-{i + len(batch)} из {len(data)}")
                    for vehicle in batch:
                        vehicle_id = vehicle.get("vehicleId")
                        if not vehicle_id:
                            logger.warning(f"Пропущена машина без vehicleId")
                            continue
                        vehicle_details = self.get_vehicle_details(vehicle_id)
                        if vehicle_details:
                            try:
                                self.save_to_db(vehicle_details)
                                processed_vehicles.append(vehicle_details)
                                try:
                                    car = Car.objects.get(id_in_provider_system=vehicle_id)
                                    car_start_date = start_date or car.last_processed_date
                                    created_at_str = vehicle_details.get("createdAt")
                                    try:
                                        created_at = datetime.strptime(
                                            created_at_str, "%Y-%m-%dT%H:%M:%S.%fZ"
                                        ).replace(tzinfo=pytz.UTC)
                                    except ValueError:
                                        # Обработка нестандартного формата с лишними цифрами
                                        created_at_str = created_at_str[:-2] + "Z" if created_at_str.endswith("Z") else created_at_str
                                        created_at = datetime.strptime(
                                            created_at_str[:26] + "Z", "%Y-%m-%dT%H:%M:%S.%fZ"
                                        ).replace(tzinfo=pytz.UTC)
                                    if not car_start_date:
                                        car_start_date = created_at
                                    car_end_date = end_date or datetime.now(tz=pytz.UTC)
                                    if car_start_date.tzinfo is None:
                                        car_start_date = car_start_date.replace(tzinfo=pytz.UTC)
                                    if car_end_date.tzinfo is None:
                                        car_end_date = car_end_date.replace(tzinfo=pytz.UTC)
                                except Car.DoesNotExist:
                                    logger.warning(f"Автомобиль vehicleId={vehicle_id} не найден, использую createdAt")
                                    created_at_str = vehicle_details.get("createdAt")
                                    try:
                                        created_at = datetime.strptime(
                                            created_at_str, "%Y-%m-%dT%H:%M:%S.%fZ"
                                        ).replace(tzinfo=pytz.UTC)
                                    except ValueError:
                                        created_at_str = created_at_str[:-2] + "Z" if created_at_str.endswith("Z") else created_at_str
                                        created_at = datetime.strptime(
                                            created_at_str[:26] + "Z", "%Y-%m-%dT%H:%M:%S.%fZ"
                                        ).replace(tzinfo=pytz.UTC)
                                    car_start_date = created_at
                                    car_end_date = end_date or datetime.now(tz=pytz.UTC)
                                    if car_end_date.tzinfo is None:
                                        car_end_date = car_end_date.replace(tzinfo=pytz.UTC)
                                self.get_terminal_to_json(vehicle_id, car_start_date, car_end_date)
                            except Exception as e:
                                logger.error(f"Ошибка обработки vehicleId={vehicle_id}: {e}")
                                continue
                    self._log_resources(f"get_vehicles_batch_{i}")

                self._create_media_record()
                self._cleanup_tmp_files()
                return processed_vehicles if processed_vehicles else None
            except requests.HTTPError as e:
                if response.status_code == 429:
                    attempt += 1
                    if attempt < max_retries:
                        delay = retry_delays[attempt - 1]
                        logger.warning(
                            f"Ошибка 429 при получении автомобилей, попытка {attempt}/{max_retries}, жду {delay} сек.")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Исчерпаны попытки получения автомобилей после ошибки 429.")
                        return None
                else:
                    logger.error(f"Ошибка HTTP при получении автомобилей: {e}")
                    return None
            except Exception as e:
                logger.error(f"Ошибка получения автомобилей: {e}")
                return None

    def get_vehicle_details(self, vehicle_id: int) -> Optional[Dict[str, Any]]:
        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/{vehicle_id}"
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Запрос данных машины: {vehicle_id}")
        max_retries = 3
        retry_delays = [5, 10, 20]
        attempt = 0

        while attempt < max_retries:
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                data = orjson.loads(response.content)
                logger.info(f"Получены данные машины: {vehicle_id}")

                input_value, output_value = None, None
                for sensor in data.get("sensors", []):
                    if "FuelLvl" in sensor.get("type", "") and sensor.get("gradeType") == "GradeTable":
                        record = sensor.get("gradesTables", [{}])[0].get("grades", [{}])[-1]
                        input_value = record.get("input")
                        output_value = record.get("output")
                        break

                data["input"] = input_value if input_value is not None else 1.0
                data["output"] = output_value if output_value is not None else 1.0
                self._log_resources("get_vehicle_details")
                return data
            except requests.HTTPError as e:
                if response.status_code == 429:
                    attempt += 1
                    if attempt < max_retries:
                        delay = retry_delays[attempt - 1]
                        logger.warning(
                            f"Ошибка 429 для vehicleId={vehicle_id}, попытка {attempt}/{max_retries}, жду {delay} сек.")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Исчерпаны попытки для vehicleId={vehicle_id} после ошибки 429.")
                        return None
                else:
                    logger.error(f"Ошибка HTTP для vehicleId={vehicle_id}: {e}")
                    return None
            except Exception as e:
                logger.error(f"Ошибка получения данных vehicleId={vehicle_id}: {e}")
                return None

    def get_terminal_to_json(self, vehicle_id: int, start_date: datetime, end_date: datetime) -> None:
        try:
            if start_date.tzinfo is None:
                start_date = start_date.replace(tzinfo=pytz.UTC)
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=pytz.UTC)

            if start_date >= end_date:
                logger.info(f"Пропущен vehicleId={vehicle_id}: start_date={start_date} >= end_date={end_date}")
                return

            logger.info(f"Запрос данных vehicleId={vehicle_id} с {start_date} по {end_date}")
            current_start = start_date
            while current_start < end_date:
                period_days = self._get_adaptive_period(current_start, end_date)
                current_end = min(current_start + timedelta(days=period_days), end_date)
                success = self._fetch_terminal_to_json_for_period(vehicle_id, current_start, current_end)
                if not success and self.default_period_days == 1:
                    logger.error(f"Не удалось получить данные для vehicleId={vehicle_id} даже за 1 день.")
                    break
                current_start = current_end + timedelta(seconds=1)
                if not success:
                    self.default_period_days = max(self.default_period_days // 2, 1)
                    logger.info(f"Уменьшен период до {self.default_period_days} дней из-за ошибки 429.")
                else:
                    self.default_period_days = 90
            self._save_all_terminal_messages_to_csv(vehicle_id)
            try:
                car = Car.objects.get(id_in_provider_system=vehicle_id)
                if not car.last_processed_date or end_date > car.last_processed_date:
                    car.last_processed_date = end_date
                    car.save()
                    logger.info(f"Обновлена last_processed_date для vehicleId={vehicle_id} до {end_date}")
                else:
                    logger.info(f"last_processed_date для vehicleId={vehicle_id} не обновлена: {car.last_processed_date}")
            except Car.DoesNotExist:
                logger.error(f"Автомобиль vehicleId={vehicle_id} не найден")
            self._log_resources("get_terminal_to_json")
        except Exception as e:
            logger.error(f"Ошибка получения данных vehicleId={vehicle_id}: {e}")

    def _fetch_terminal_to_json_for_period(self, vehicle_id: int, start_date: datetime, end_date: datetime) -> bool:
        self._enforce_rate_limit()
        url = f"{self.base_url}/terminalMessages"
        payload = {
            "vehicleId": vehicle_id,
            "from": start_date.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "to": end_date.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3]
        }
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Запрос данных vehicleId={vehicle_id}, {start_date} - {end_date}")
        max_retries = 3
        retry_delays = [5, 10, 20]
        attempt = 0

        while attempt < max_retries:
            try:
                response = requests.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = orjson.loads(response.content)
                messages = data.get("messages", [])
                logger.info(f"Получено {len(messages)} сообщений для vehicleId={vehicle_id}")

                if not isinstance(messages, list):
                    logger.error(f"'Messages' не список: {type(messages)}")
                    return False
                if not messages:
                    logger.info(f"Нет сообщений для vehicleId={vehicle_id}")
                    return True

                if vehicle_id not in self.all_terminal_messages:
                    self.all_terminal_messages[vehicle_id] = []
                self.all_terminal_messages[vehicle_id].extend(messages)

                timestamp = datetime.now(tz=pytz.UTC).strftime("%Y%m%d_%H%M%S")
                tmp_json_path = self.tmp_dir / f"terminal_messages_{vehicle_id}_{timestamp}.json"
                with open(tmp_json_path, "wb") as f:
                    f.write(orjson.dumps(data))
                file_size = os.path.getsize(tmp_json_path) / 1024 ** 2
                logger.info(f"Буферные данные сохранены в {tmp_json_path} ({file_size:.2f} MB)")

                messages_iter = iter(messages)
                while True:
                    chunk = list(islice(messages_iter, self.chunk_size))
                    if not chunk:
                        break
                    self._save_to_csv(vehicle_id, chunk)
                    self._log_resources(f"fetch_messages_{vehicle_id}")
                return True
            except requests.HTTPError as e:
                if response.status_code == 429:
                    attempt += 1
                    if attempt < max_retries:
                        delay = retry_delays[attempt - 1]
                        logger.warning(
                            f"Ошибка 429 для vehicleId={vehicle_id}, попытка {attempt}/{max_retries}, жду {delay} сек.")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Исчерпаны попытки для vehicleId={vehicle_id} после ошибки 429.")
                        return False
                else:
                    logger.error(f"Ошибка HTTP для vehicleId={vehicle_id}: {e}")
                    return False
            except Exception as e:
                logger.error(f"Ошибка получения данных vehicleId={vehicle_id}: {e}")
                return False

    def _save_all_terminal_messages_to_csv(self, vehicle_id: int) -> None:
        messages = self.all_terminal_messages.get(vehicle_id, [])
        if not messages:
            logger.info(f"Нет данных terminalMessages для vehicleId={vehicle_id}")
            return

        timestamp = datetime.now(tz=pytz.UTC).strftime("%Y%m%d_%H%M%S")
        csv_file_path = Path(
            settings.MEDIA_ROOT) / "full_terminal_messages" / f"full_terminal_messages_{vehicle_id}_{self.report_query_id}_{timestamp}.csv"
        os.makedirs(csv_file_path.parent, exist_ok=True)

        headers = set()
        for msg in messages:
            flat_msg = self._flatten_parameters(msg)
            headers.update(flat_msg.keys())
        headers = sorted(headers)

        with open(csv_file_path, "w", encoding="utf-8", newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers, lineterminator='\n')
            writer.writeheader()
            for msg in messages:
                flat_msg = self._flatten_parameters(msg)
                row = {key: flat_msg.get(key, '') for key in headers}
                writer.writerow(row)
        file_size = os.path.getsize(csv_file_path) / 1024 ** 2
        logger.info(
            f"Все terminalMessages сохранены в {csv_file_path} ({file_size:.2f} MB) для vehicleId={vehicle_id}")
        self.all_terminal_messages[vehicle_id] = []
        self._log_resources("save_all_terminal_messages_to_csv")

    def _flatten_parameters(self, message: Dict[str, Any]) -> Dict[str, Any]:
        flat = message.copy()
        parameters = flat.pop("parameters", {})
        if isinstance(parameters, dict):
            for key, value in parameters.items():
                flat[f"parameters.{key}"] = value
        return flat

    def _save_to_csv(self, vehicle_id: int, data: List[Dict[str, Any]]) -> None:
        if not data:
            logger.info(f"Нет данных для vehicleId={vehicle_id}")
            return

        try:
            car = Car.objects.get(id_in_provider_system=vehicle_id)
            vehicle_guid = car.id
        except Car.DoesNotExist:
            logger.error(f"Автомобиль vehicleId={vehicle_id} не найден")
            return

        column_mapping = {
            "vehicleId": "auto",
            "deviceTime": "timestamp",
            "speed": "pos_s",
            "voltage": "calc_sensors_voltage",
            "parameters.fuel1": "calc_sensors_fuel_level"
        }
        headers = list(column_mapping.values())
        logger.info(f"Запись CSV с заголовками: {headers}")

        if not self.csv_initialized:
            with open(self.csv_file_path, "w", encoding="utf-8", newline='') as f:
                writer = csv.DictWriter(f, fieldnames=headers, lineterminator='\n')
                writer.writeheader()
            self.csv_initialized = True
            logger.info(f"CSV инициализирован: {self.csv_file_path}")

        renamed_data = []
        for record in data:
            flat_record = self._flatten_parameters(record)
            filtered_record = {
                "vehicleId": vehicle_guid,
                "deviceTime": flat_record.get("deviceTime"),
                "speed": flat_record.get("speed"),
                "voltage": flat_record.get("voltage"),
                "parameters.fuel1": flat_record.get("parameters.fuel1")
            }
            renamed_record = {column_mapping[k]: v for k, v in filtered_record.items() if k in column_mapping}
            renamed_data.append(renamed_record)

        with open(self.csv_file_path, "a", encoding="utf-8", newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers, lineterminator='\n')
            writer.writerows(renamed_data)
        logger.info(f"Добавлено {len(renamed_data)} записей для vehicleGuid={vehicle_guid}")
        self._log_resources("save_to_csv")

    def _create_media_record(self) -> None:
        try:
            report_query = ReportQuery.objects.get(id=self.report_query_id)
            existing_media = Media.objects.filter(report_query_id=report_query).first()
            if existing_media:
                if existing_media.file and os.path.exists(existing_media.file.path):
                    os.remove(existing_media.file.path)
                    logger.info(f"Удалён старый файл: {existing_media.file.path}")
                existing_media.delete()

            media = Media.objects.create(
                report_query_id=report_query,
                type="raw",
                file=str(self.csv_file_path.relative_to(settings.MEDIA_ROOT)),
                filename=self.csv_file_path.name,
                size=os.path.getsize(self.csv_file_path)
            )
            logger.info(f"Создана запись Media: {media.id}")
        except Exception as e:
            logger.error(f"Ошибка создания Media: {e}")
            raise

    def save_to_db(self, vehicle_data: Dict[str, Any]) -> None:
        try:
            with transaction.atomic():
                report_query = ReportQuery.objects.get(id=self.report_query_id)
                provider = report_query.provider_id
                if not provider:
                    logger.error(f"Провайдер не найден для report_query_id={self.report_query_id}")
                    raise ValueError("Provider not found")

                custom_fields = {field["name"]: field["value"] for field in vehicle_data.get("customFields", [])}
                engine_type = 1.0 if custom_fields.get("Тип учета") == "Моточасы" else 0.0
                vehicle_guid = vehicle_data.get("vehicleGuid")
                vehicle_id = vehicle_data.get("vehicleId")

                if not vehicle_guid or not vehicle_id:
                    logger.error(f"Отсутствуют vehicleGuid или vehicleId")
                    raise ValueError("Missing vehicleGuid or vehicleId")

                car, created = Car.objects.update_or_create(
                    id=vehicle_guid,
                    defaults={
                        "id_in_provider_system": vehicle_id,
                        "name": vehicle_data.get("name", ""),
                        "description": f"{vehicle_data.get('parentName', '')}, {vehicle_data.get('modelName', '')}, {vehicle_data.get('unitName', '')}",
                        "engine_type": engine_type,
                        "input": vehicle_data.get("input", 0.0),
                        "output": vehicle_data.get("output", 0.0),
                    }
                )
                provider.cars.add(car)

                created_at_str = vehicle_data.get("createdAt")
                if created_at_str:
                    try:
                        created_at = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC)
                    except ValueError:
                        try:
                            # Обработка нестандартного формата с лишними цифрами в микросекундах
                            created_at_str = created_at_str[:-2] + "Z" if created_at_str.endswith("Z") else created_at_str
                            created_at = datetime.strptime(created_at_str[:26] + "Z", "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC)
                        except ValueError as e:
                            logger.warning(f"Невозможно разобрать createdAt='{created_at_str}' для vehicleId={vehicle_id}: {e}. Использую текущую дату.")
                            created_at = datetime.now(tz=pytz.UTC)
                else:
                    logger.warning(f"createdAt отсутствует для vehicleId={vehicle_id}. Использую текущую дату.")
                    created_at = datetime.now(tz=pytz.UTC)

                valid_period = (created_at + timedelta(days=365 * 10)).date()
                consumption = vehicle_data.get("consumptionPer100Km", 0.0)

                CarConsumption.objects.update_or_create(
                    car_id=car,
                    defaults={
                        "winter_volume": consumption,
                        "summer_volume": consumption,
                        "valid_period": valid_period
                    }
                )
                logger.info(f"Сохранены данные для vehicleId={vehicle_id}, created_at={created_at}")
                self._log_resources("save_to_db")
        except Exception as e:
            logger.error(f"Ошибка сохранения данных для vehicleId={vehicle_id}: {e}")
            raise

def provider_factory(provider_name: str, metadata: Dict[str, Any], report_query_id: str) -> Optional[Any]:
    providers = {
        "glonasssoft": GlonassSoftProvider
    }
    provider_class = providers.get(provider_name.lower())
    if not provider_class:
        logger.error(f"Провайдер {provider_name} не поддерживается.")
        return None
    return provider_class(metadata, report_query_id)

def save_response_to_file(data: Dict[str, Any], filename: str = "response.json") -> None:
    file_path = settings.BASE_DIR / filename
    try:
        with open(file_path, "wb") as f:
            f.write(orjson.dumps(data))
        logger.info(f"Ответ сохранён: {file_path}")
    except Exception as e:
        logger.error(f"Ошибка сохранения {file_path}: {e}")