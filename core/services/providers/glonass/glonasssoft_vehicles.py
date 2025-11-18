import logging
import time
import requests
import orjson
import pytz
import textdistance
from typing import Dict, Any, Optional, List
from core.services.providers.vehicle_base import BaseProvider
from core.services.providers.rate_limiter import GlobalRateLimiter
from core.helpers.decorators import retry_on_status

logger = logging.getLogger(__name__)


class GlonassSoftVehiclesProvider(BaseProvider):
    """Провайдер для работы с транспортными средствами GlonassSoft"""

    def __init__(self, metadata: Dict[str, Any], report_query_id: str = None):
        super().__init__(metadata, report_query_id)
        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.rate_limiter = GlobalRateLimiter()

    def _enforce_rate_limit(self) -> None:
        """Использует глобальный rate limiter для всех запросов"""
        self.rate_limiter.wait_for_rate_limit()

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

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def get_vehicles(self) -> Optional[List[Dict[str, Any]]]:
        if not self.auth_token:
            logger.error("Токен отсутствует. Выполните аутентификацию.")
            return None

        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/find"
        headers = {"X-Auth": self.auth_token}

        try:
            response = requests.post(url, json={}, headers=headers)

            if response.status_code == 429:
                logger.warning("Rate limit достигнут при получении списка машин, повтор через 5 секунд")
                time.sleep(5)
                self._enforce_rate_limit()
                response = requests.post(url, json={}, headers=headers)

            response.raise_for_status()
            data = orjson.loads(response.content)

            if not isinstance(data, list):
                logger.error(f"Ожидался список, получен: {type(data)}")
                return None

            logger.info(f"Получено {len(data)} автомобилей.")
            return data

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.error("Rate limit превышен после повторной попытки получения списка машин")
            logger.error(f"Ошибка при получении списка автомобилей: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении списка автомобилей: {e}")
            return None

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def get_vehicle_details(self, vehicle_id: int) -> Optional[Dict[str, Any]]:
        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/{vehicle_id}"
        headers = {"X-Auth": self.auth_token}

        try:
            response = requests.get(url, headers=headers)

            if response.status_code == 429:
                logger.warning(
                    f"Rate limit достигнут при получении деталей vehicle_id={vehicle_id}, повтор через 5 секунд")
                time.sleep(5)
                self._enforce_rate_limit()
                response = requests.get(url, headers=headers)

            response.raise_for_status()
            vehicle_data = orjson.loads(response.content)

            enriched_data = self._enrich_with_sensors_mapping(vehicle_data)
            return enriched_data

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.error(f"Rate limit превышен после повторной попытки для vehicle_id={vehicle_id}")
            logger.error(f"Ошибка при получении деталей vehicleId={vehicle_id}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении деталей vehicleId={vehicle_id}: {e}")
            return None

    def _enrich_with_sensors_mapping(self, vehicle_data: Dict[str, Any]) -> Dict[str, Any]:
        input_value, output_value = None, None
        sensors_mapping = {}

        for sensor in vehicle_data.get("sensors", []):
            sensor_type = sensor.get("type")
            sensor_name = sensor.get("name", "")
            parameter_name = sensor.get("parameterName")
            input_number = sensor.get("inputNumber")
            input_type = sensor.get("inputType")

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

                    if key_part.startswith("can_fuel_volume"):
                        sensors_mapping["calc_sensors_fuel_level"] = (
                            f"parameters.can_fuel_volume"
                        )

                    elif key_part.startswith("can_") and input_number:
                        sensors_mapping["calc_sensors_fuel_level"] = (
                            f"parameters.can{input_number}"
                        )
                    else:

                        sensors_mapping["calc_sensors_fuel_level"] = (
                            f"parameters.{key_part}"
                        )
                elif input_number:

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

        vehicle_data["input"] = input_value
        vehicle_data["output"] = output_value
        vehicle_data["sensorsMapping"] = sensors_mapping

        return vehicle_data
