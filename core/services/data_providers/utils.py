
import requests
import logging
from typing import Dict, Any, Optional, List
from django.conf import settings
import json
import time
from datetime import datetime, timedelta
from django.db import transaction
from core.models import Car, CarConsumption, Media, ReportQuery, DataProvider
import csv
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

class GlonassSoftProvider:
    def __init__(self, metadata: Dict[str, Any], report_query_id: str):
        self.metadata = metadata
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.auth_token: Optional[str] = None
        self.last_request_time: float = 0
        self.csv_file_path = settings.BASE_DIR / "terminal_messages.csv"
        self.csv_initialized = False
        self.res_file_path = settings.BASE_DIR / "res.json"
        self.report_query_id = report_query_id

    def _enforce_rate_limit(self) -> None:
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        if time_since_last_request < 1.0:
            sleep_time = 1.0 - time_since_last_request
            logger.info(f"Задержка запроса на {sleep_time:.2f} секунд для соблюдения ограничения частоты.")
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def authenticate(self) -> bool:
        self._enforce_rate_limit()
        url = f"{self.base_url}/auth/login"
        payload = {
            "login": self.metadata.get("login"),
            "password": self.metadata.get("password")
        }
        logger.info(f"Отправка запроса на авторизацию: URL={url}, Payload={json.dumps(payload, ensure_ascii=False)}")
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            self.auth_token = data.get("AuthId")
            logger.info(f"Успешная авторизация для провайдера GlonassSoft. AuthId: {self.auth_token}")
            logger.debug(f"Полный ответ авторизации: {json.dumps(data, ensure_ascii=False)}")
            return True
        except requests.RequestException as e:
            logger.error(f"Ошибка авторизации для провайдера GlonassSoft: {e}")
            logger.debug(f"Код ответа: {e.response.status_code if e.response else 'N/A'}, Текст ответа: {e.response.text if e.response else 'N/A'}")
            return False

    def get_vehicles(self, name: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        if not self.auth_token:
            logger.error("Токен авторизации отсутствует. Сначала выполните авторизацию.")
            return None

        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/find"
        params = {"name": name}
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Отправка запроса на получение списка автомобилей: URL={url}, Method=POST, Params={json.dumps(params, ensure_ascii=False)}, Headers={json.dumps(headers, ensure_ascii=False)}")
        try:
            response = requests.post(url, json=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            logger.info("Успешно получен список автомобилей от GlonassSoft.")
            logger.debug(f"Полный ответ: {json.dumps(data, ensure_ascii=False)}")

            if not isinstance(data, list):
                logger.error(f"Ожидался список автомобилей, но получен: {type(data)}")
                return None

            for vehicle in data:
                vehicle_id = vehicle.get("vehicleId")
                if not vehicle_id:
                    logger.warning(f"Пропущена машина без vehicleId: {json.dumps(vehicle, ensure_ascii=False)}")
                    continue
                vehicle_details = self.get_vehicle_details(vehicle_id)
                if vehicle_details:
                    self.save_to_db(vehicle_details)
                    self.get_terminal_messages(vehicle_id, vehicle_details["createdAt"])


            if self.csv_initialized and os.path.exists(self.csv_file_path):
                report_query = ReportQuery.objects.get(id=self.report_query_id)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                media_dir = Path(settings.MEDIA_ROOT) / "raw_data"
                os.makedirs(media_dir, exist_ok=True)
                new_file_name = f"raw_data_{self.report_query_id}_{timestamp}.csv"
                new_file_path = media_dir / new_file_name


                shutil.move(self.csv_file_path, new_file_path)
                logger.info(f"Файл перемещён в {new_file_path}")


                media = Media.objects.create(
                    report_query_id=report_query,
                    type="raw",
                    file=f"raw_data/{new_file_name}"
                )
                logger.info(f"Создана запись в Media: {media.id}, путь: {media.file.path}")

            return data
        except requests.RequestException as e:
            logger.error(f"Ошибка при получении списка автомобилей от GlonassSoft: {e}")
            logger.debug(f"Код ответа: {e.response.status_code if e.response else 'N/A'}, Текст ответа: {e.response.text if e.response else 'N/A'}")
            return None

    def get_vehicle_details(self, vehicle_id: int) -> Optional[Dict[str, Any]]:
        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/{vehicle_id}"
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Отправка запроса на получение данных машины: URL={url}, Method=GET, Headers={json.dumps(headers, ensure_ascii=False)}")
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Успешно получены данные машины с vehicleId={vehicle_id} от GlonassSoft.")
            logger.debug(f"Полный ответ: {json.dumps(data, ensure_ascii=False)}")
            return data
        except requests.RequestException as e:
            logger.error(f"Ошибка при получении данных машины с vehicleId={vehicle_id} от GlonassSoft: {e}")
            logger.debug(f"Код ответа: {e.response.status_code if e.response else 'N/A'}, Текст ответа: {e.response.text if e.response else 'N/A'}")
            return None

    def get_terminal_messages(self, vehicle_id: int, created_at: str) -> None:
        try:
            start_date = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ")
            end_date = datetime(2025, 4, 12)
            current_start = start_date
            while current_start < end_date:
                current_end = min(current_start + timedelta(days=90), end_date)
                self._fetch_terminal_messages_for_period(vehicle_id, current_start, current_end)
                current_start = current_end
        except Exception as e:
            logger.error(f"Ошибка при получении исторических данных для машины vehicleId={vehicle_id}: {e}")
            raise

    def _fetch_terminal_messages_for_period(self, vehicle_id: int, start_date: datetime, end_date: datetime) -> None:
        self._enforce_rate_limit()
        url = f"{self.base_url}/terminalMessages"
        payload = {
            "vehicleId": vehicle_id,
            "from": start_date.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "to": end_date.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        }
        headers = {"X-Auth": self.auth_token}
        logger.info(f"Отправка запроса на получение исторических данных: URL={url}, Payload={json.dumps(payload, ensure_ascii=False)}, Headers={json.dumps(headers, ensure_ascii=False)}")
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Успешно получены исторические данные для vehicleId={vehicle_id} за период {start_date} - {end_date}.")

            with open(self.res_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"Ответ сохранён в файл: {self.res_file_path}")

            messages = data.get("messages", [])
            if not isinstance(messages, list):
                logger.error(f"Поле 'messages' не является списком: {type(messages)}")
                return
            if not messages:
                logger.info(f"Нет сообщений для vehicleId={vehicle_id} за период {start_date} - {end_date}.")
                return

            self._save_to_csv(vehicle_id, messages)

        except requests.RequestException as e:
            logger.error(f"Ошибка при получении исторических данных для vehicleId={vehicle_id}: {e}")
            logger.debug(f"Код ответа: {e.response.status_code if e.response else 'N/A'}, Текст ответа: {e.response.text if e.response else 'N/A'}")
            return

    def _flatten_parameters(self, message: Dict[str, Any]) -> Dict[str, Any]:
        flat_message = message.copy()
        parameters = flat_message.pop("parameters", {})
        if isinstance(parameters, dict):
            if "fuel1" in parameters:
                flat_message["parameters.fuel1"] = parameters["fuel1"]
        return flat_message

    def _save_to_csv(self, vehicle_id: int, data: List[Dict[str, Any]]) -> None:
        if not data:
            logger.info(f"Нет данных для сохранения в CSV для vehicleId={vehicle_id}.")
            return

        if not isinstance(data[0], dict):
            logger.error(f"Ожидалась запись в формате словаря, но получен: {type(data[0])}")
            return

        flat_data = [self._flatten_parameters(record) for record in data]

        for record in flat_data:
            record["vehicleId"] = vehicle_id

        filtered_data = []
        for record in flat_data:
            filtered_record = {
                "vehicleId": record.get("vehicleId"),
                "deviceTime": record.get("deviceTime"),
                "speed": record.get("speed"),
                "voltage": record.get("voltage"),
                "parameters.fuel1": record.get("parameters.fuel1")
            }
            filtered_data.append(filtered_record)

        column_mapping = {
            "vehicleId": "auto",
            "deviceTime": "timestamp",
            "speed": "pos_s",
            "voltage": "calc_sensors_voltage",
            "parameters.fuel1": "calc_sensors_fuel_level"
        }

        headers = list(column_mapping.values())

        if not self.csv_initialized:
            with open(self.csv_file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
            self.csv_initialized = True
            logger.info(f"CSV-файл инициализирован с заголовками: {headers}")

        renamed_data = []
        for record in filtered_data:
            renamed_record = {column_mapping[old_key]: value for old_key, value in record.items()}
            renamed_data.append(renamed_record)

        with open(self.csv_file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writerows(renamed_data)
            logger.info(f"Добавлено {len(renamed_data)} записей в CSV для vehicleId={vehicle_id}.")

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
                    logger.error(f"Отсутствуют vehicleGuid или vehicleId в данных машины: {vehicle_data}")
                    raise ValueError("Missing vehicleGuid or vehicleId")


                car, created = Car.objects.update_or_create(
                    id=vehicle_guid,
                    defaults={
                        "id_in_provider_system": vehicle_id,
                        "name": vehicle_data.get("name", ""),
                        "description": f"{vehicle_data.get('parentName', '')}, {vehicle_data.get('modelName', '')}, {vehicle_data.get('unitName', '')}",
                        "engine_type": engine_type,
                    }
                )
                logger.info(f"Машина {'создана' if created else 'обновлена'}: {car.id} ({car.name})")


                provider.cars.add(car)
                logger.info(f"Машина {car.id} добавлена к провайдеру {provider.name}")


                created_at = datetime.strptime(vehicle_data["createdAt"], "%Y-%m-%dT%H:%M:%S.%fZ")
                valid_period = (created_at + timedelta(days=365 * 10)).date()
                consumption = vehicle_data.get("consumptionPer100Km", 0.0)


                consumption_record, consumption_created = CarConsumption.objects.update_or_create(
                    car_id=car,
                    defaults={
                        "winter_volume": consumption,
                        "summer_volume": consumption,
                        "valid_period": valid_period
                    }
                )
                if consumption_created:
                    logger.info(f"Созданы нормы для машины {car.id}: winter_volume={consumption}, summer_volume={consumption}, valid_period={valid_period}")
                else:
                    logger.info(f"Обновлены нормы для машины {car.id}: winter_volume={consumption}, summer_volume={consumption}, valid_period={valid_period}")

        except Exception as e:
            logger.error(f"Ошибка при сохранении данных машины в БД: {e}")
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
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info(f"Ответ сохранён в файл: {file_path}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении ответа в файл {file_path}: {e}")