from dataclasses import dataclass
import logging
import re
import time
import requests
import orjson
import pytz
import textdistance
from typing import Dict, Any, Optional, List

from core.models import CarUnit, DataProvider
from core.services.providers.rate_limited_provider import (
    RateLimitedProvider,
    VehicleRateLimitedProvider,
)

from core.helpers.decorators import retry_on_status

logger = logging.getLogger(__name__)

@dataclass
class SensorType:
    parameter: str
    metadata: dict[str, Any] | None
    priority: int # датчик с наивысшим приоритетом по группе выбирается
    is_active: bool # является ли активным
    is_picked: bool = False

class GlonassSoftVehiclesProvider(VehicleRateLimitedProvider):
    """Провайдер для работы с транспортными средствами GlonassSoft"""

    def __init__(self, metadata: Dict[str, Any], report_query_id: str = None):
        super().__init__(metadata, report_query_id)
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
                logger.warning(
                    "Rate limit достигнут при получении списка машин, повтор через 5 секунд"
                )
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
                logger.error(
                    "Rate limit превышен после повторной попытки получения списка машин"
                )
            logger.error(f"Ошибка при получении списка автомобилей: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении списка автомобилей: {e}")
            return None

    @retry_on_status(retry_delays=[5, 10, 15], status_codes=[400, 429])
    def get_vehicle_details(self, vehicle_id: int, provider: DataProvider | None = None) -> Optional[Dict[str, Any]]:
        self._enforce_rate_limit()
        url = f"{self.base_url}/vehicles/{vehicle_id}"
        headers = {"X-Auth": self.auth_token}

        try:
            response = requests.get(url, headers=headers)

            if response.status_code == 429:
                logger.warning(
                    f"Rate limit достигнут при получении деталей vehicle_id={vehicle_id}, повтор через 5 секунд"
                )
                time.sleep(5)
                self._enforce_rate_limit()
                response = requests.get(url, headers=headers)

            response.raise_for_status()
            vehicle_data = orjson.loads(response.content)

            enriched_data = self._enrich_with_sensors_mapping(vehicle_data, provider)
            return enriched_data

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.error(
                    f"Rate limit превышен после повторной попытки для vehicle_id={vehicle_id}"
                )
            logger.error(f"Ошибка при получении деталей vehicleId={vehicle_id}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении деталей vehicleId={vehicle_id}: {e}")
            return None

    def _get_right_grade(self, grade: Any):
        grade_for_choice = {
            "input": 0,
            "output": 0,
        }
        for grade_item in grade:
            if grade_item["output"] != 0:
                grade_for_choice = grade_item
        return grade_for_choice
                
        
    def _enrich_with_sensors_mapping(
            self, vehicle_data: Dict[str, Any], provider: DataProvider | None = None
    ) -> Dict[str, Any]:
        input_value, output_value = None, None
        sensors_mapping: dict[str, List[SensorType]] = {
            "mileage": [],
            "motohours": [],
            "speed": [],
            "rpm": [],
            "calc_sensors_fuel_level": [],
            "engine_temp": [],
            "ign": [],
            "fuel_consumpt": [],
        }

        unit_name = vehicle_data.get("unitName")
        car_unit = None

        if unit_name:
            car_unit, created = CarUnit.objects.get_or_create(
                name=unit_name
            )
            if created:
                logger.info(f"Создан новый CarUnit: {unit_name}")
            else:
                logger.debug(f"Найден существующий CarUnit: {unit_name}")

        if car_unit:
            vehicle_data["car_unit_id"] = str(car_unit.id)
        vehicle_data["unit_name"] = unit_name

        for sensor in vehicle_data.get("sensors", []):
            sensor_type = sensor.get("type")
            sensor_name = sensor.get("name", "")
            sensor_kind = sensor.get("kind", "")
            parameter_name = sensor.get("parameterName")
            input_number = sensor.get("inputNumber")
            input_type = sensor.get("inputType")
            expr = sensor.get("expr", "")
            is_disabled = sensor.get("disabled", False)
            

            if "Скорость" in sensor_name or parameter_name == "can_speed":
                sensors_mapping["speed"].append(SensorType(f"parameters.{parameter_name}", None, 3, is_disabled))
                continue
            elif sensor_type == "FuelLvl":
                sensor_grades = None
                if sensor.get("gradeType") == "GradeTable":

                    grades_tables = sensor.get("gradesTables", [{}])
                    if grades_tables and grades_tables[-1]:
                        grades = grades_tables[-1].get("grades", [{}])
                        sensor_grades = grades
                            
                    
                if parameter_name:
                    key_part = parameter_name.split(";")[0]
                    

                    if key_part.startswith("can_fuel_volume"):
                        sensors_mapping["calc_sensors_fuel_level"].append(
                            SensorType(f"parameters.can_fuel_volume", {"grades": sensor_grades}, 3, is_disabled)
                        )
                        continue
                    if key_part.startswith("can_fuel_level"):
                        sensors_mapping["calc_sensors_fuel_level"].append(
                            SensorType(f"parameters.can_fuel_level",{"grades": sensor_grades}, 3, is_disabled)
                        )
                        continue

                    if key_part.startswith("can_") and input_number:
                        sensors_mapping.get("calc_sensors_fuel_level", []).append(
                            SensorType(f"parameters.can{input_number}",{"grades": sensor_grades}, 3, is_disabled)
                        )
                        continue
                    if  input_type == "Analog":
                        match = re.search(r"\bflex_adc(\d+)\b", expr)
                        analog_match = re.search(r"\banalog(\d+)\b", expr)
                        if match is not None:
                            sensors_mapping.get("calc_sensors_fuel_level", []).append(
                                SensorType(f"parameters.{match.group(0)}", {"grades": sensor_grades}, 3, is_disabled)
                            )
                        if analog_match is not None:
                            sensors_mapping.get("calc_sensors_fuel_level", []).append(
                                
                                SensorType(f"paramaters.{analog_match.group(0)}", {"grades": sensor_grades}, 3, is_disabled)
                            )
                    else:

                        sensors_mapping.get("calc_sensors_fuel_level", []).append (
                            SensorType(f"parameters.{key_part}", {"grades": sensor_grades}, 3, is_disabled)
                        )
                elif input_number:
                    if input_type == "Analog":
                        match = re.search(r"\bflex_adc(\d+)\b", expr)
                        analog_match = re.search(r"\banalog(\d+)\b", expr)
                        if match is not None:
                            sensors_mapping.get("calc_sensors_fuel_level", []).append (
                                
                            SensorType(f"parameters.{match.group(0)}", {"grades": sensor_grades}, 3, is_disabled)
                            )
                    else:
                        sensors_mapping.get("calc_sensors_fuel_level", []).append (
                            SensorType(f"parameters.analog{input_number}", {"grades": sensor_grades}, 3, is_disabled)
                    )

            elif sensor_type == "EngineRPM":
                if parameter_name:
                    key_part = parameter_name.split(";")[0]
                    if key_part.startswith("can_") and input_number:
                        sensors_mapping.get("rpm", []).append(SensorType( f"parameters.can{input_number}", None, 3, is_disabled))
                    else:
                        sensors_mapping.get("rpm", []).append(SensorType( f"parameters.{key_part}", None, 3, is_disabled))
            elif (
                    sensor_type == "MileageSensor" or sensor_name.startswith("Пробег")
                    or textdistance.damerau_levenshtein(sensor_name, "Пробег") <= 2 or sensor_name == "Датчик пробега"
            ):
                mileage_priority = 3 if sensor_type == "MileageSensor" else 1
                mileage_grading = None

                if parameter_name:
                    key_part = parameter_name.split(";")[0]

                    if sensor.get("gradeType") == "GradeTable":
                        grades_tables = sensor.get("gradesTables")
                        mileage_grading = grades_tables[-1].get("grades",) if grades_tables else None
                    else:
                        mileage_grading = None
                    if mileage_grading is not None:
                        mileage_grading = {"grades": mileage_grading}

                    if key_part == "can_mileage":
                        if provider and provider.metadata.get("mileage_source") == "can":
                            sensors_mapping.get("mileage", []).append(SensorType(f"parameters.can_mileage",mileage_grading, mileage_priority, is_disabled))
                        else:
                            sensors_mapping.get("mileage", []).append(SensorType(f"parameters.mileage" ,mileage_grading, mileage_priority, is_disabled))# пока так
                    elif key_part.startswith("can_") and input_number:
                        sensors_mapping.get("mileage", []).append(SensorType(f"parameters.can{input_number}",mileage_grading, mileage_priority, is_disabled))
                    else:
                        sensors_mapping.get("mileage", []).append(SensorType(f"parameters.{key_part}",mileage_grading, mileage_priority, is_disabled))
                    
            elif sensor_type == "Temperature":
                if parameter_name:
                    key_part = parameter_name.split(";")[0]
                    if key_part.startswith("can_") and input_number:
                        sensors_mapping.get("engine_temp", []).append(SensorType(f"parameters.can{input_number}", None, 3, is_disabled))
                    else:
                        sensors_mapping.get("engine_temp", []).append(SensorType(f"parameters.{key_part}", None, 3, is_disabled))
            elif sensor_type == "EngineTemperature":
                if parameter_name:
                    key_part = parameter_name.split(";")[0]
                    if key_part.startswith("can_") and input_number:
                        sensors_mapping.get("engine_temp", []).append(SensorType(f"parameters.can{input_number}", None, 3, is_disabled))
                    else:
                        sensors_mapping.get("engine_temp", []).append(SensorType(f"parameters.{key_part}", None, 3, is_disabled))
            elif sensor_type == "Ignition":
                if parameter_name:
                    key_part = parameter_name.split(";")[0]
                    if input_type == "FMS":
                        key_part = "ign"
                    if key_part.startswith("iobits"):
                        iobit = 0
                        iobit_byte_reg = r"iobits(\d*)"
                        match = re.match(iobit_byte_reg, key_part)
                        if match is not None:
                            iobit = int(match.group(1))
                        ign_metadata = None if iobit == 0 else {"iobit": iobit}
                        sensors_mapping.get("ign", []).append(SensorType(f"parameters.iobits", ign_metadata, 3, is_disabled))
                    else:
                        sensors_mapping.get("ign", []).append(SensorType(f"parameters.{key_part}", None, 3, is_disabled))
            elif sensor_type == "Consumption":
                if "can_fuel_consumpt" in parameter_name:
                    sensors_mapping.get("fuel_consumpt", []).append(SensorType(f"parameters.{parameter_name}", None, 3, is_disabled))
            elif (
                    sensor_type == "Motohours"
                    or textdistance.damerau_levenshtein(sensor_name, "моточасы") <= 2
            ):
                if parameter_name:
                    key_part = parameter_name.split(";")[0]
                    if parameter_name == "can_engine_hours":
                        sensors_mapping.get("motohours", []).append(SensorType(f"parameters.can_engine_hours", None, 3, is_disabled))
                    elif key_part.startswith("can_") and input_number:
                        sensors_mapping.get("motohours", []).append(SensorType(f"parameters.can{input_number}", None, 3, is_disabled))
                    else:
                        sensors_mapping.get("motohours", []).append(SensorType(f"parameters.{key_part}", None, 3, is_disabled))

        if "speed" not in sensors_mapping:
            sensors_mapping.get("speed", []).append(SensorType("speed", None, 3, is_disabled))
        
        for label, group in sensors_mapping.items():
            group.sort(key=lambda x: x.priority, reverse=True)
            if len(group) > 0:
                group[0].is_picked = True
        vehicle_data["input"] = input_value
        vehicle_data["output"] = output_value
        vehicle_data["sensorsMapping"] = sensors_mapping
        return vehicle_data
