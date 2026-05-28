from enum import Enum
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional, List
from uuid import UUID
import zipfile

from attr import dataclass
from django.conf import settings
import orjson
import polars as pl
import pytz
from pathlib import Path
import requests
from django.utils import timezone

from core.models import Car, DataProvider, SensorsValues
from core.services.providers.glonass.constants import GL_ACTION_KEYS, GL_PARAM_KEYS as GP, GLOBAL_GLONASS_ACTIONS, GLOBAL_GLONASS_PARAMS, GlonassAfterParsingProtocol
from core.services.providers.rate_limiter import global_rate_limiter

logger = logging.getLogger(__name__)

class GlonassGeneralProvider:
    
    failed_cars = 0
    processed_cars = 0
    total_cars = 0
    mode: str
    old_car_id: UUID | None
    i = 0
    last_auth: datetime 

    def __init__(self,cars: List[Car] | None, car: Car | None, provider: DataProvider, start_date: datetime, end_date: datetime, mode: str = "mileage", default_period_days=90):
        if cars is None and car is not None:
            self.cars = list([car])
            self.total_cars = 1
        if cars is not None:
            self.cars =(list(cars))
            self.total_cars = len(cars)
        self.start_date = start_date.replace(tzinfo=pytz.UTC)
        self.i = 0
        self.car = self.cars[self.i]
        self.end_date = end_date.replace(tzinfo=pytz.UTC)
        self.provider = provider
        self.old_car_id = None
        self.metadata = self.provider.metadata or {}

        self.base_url = "https://hosting.glonasssoft.ru/api/v3"
        self.auth_token = None
        self.default_period_days = default_period_days
        self.base_path = Path(settings.MEDIA_ROOT) / "raw_data" / f"raw_data_{self.start_date.strftime("%Y%m%d")}_{self.end_date.strftime("%Y%m%d")}"
        if not self.base_path.exists():
            self.base_path.mkdir(parents=True)
        self.mode = mode
        self.sensors_mapping_cache = {}
        self.last_auth = datetime(1999, 1, 1, 0, 0, 0, 0)

        self.total_messages = 0
        self.processed_messages = 0
    

    def _enforce_rate_limit(self) -> None:
        """Соблюдение rate limit (1 запрос в секунду)"""
        global_rate_limiter.wait_for_rate_limit()

    def _get_sensors_mapping(self, car: Car) -> Dict[str, list[str]]:
        """Получает маппинг сенсоров для машины"""
        if self.old_car_id is None or self.old_car_id != car.id:
            self.old_car_id = car.id
            self.sensors_mapping_cache = None
        
        if not self.sensors_mapping_cache:
            right_car = Car.objects.only("id").get(id_in_provider_system=car.id_in_provider_system)
            sensors = SensorsValues.objects.filter(car_id=right_car.id)
            try:
                sensors_mapping = {sv.key.key: [] for sv in sensors}
                for sv in sensors:
                    if sv.key.key in sensors_mapping:
                        sensors_mapping[sv.key.key].append(sv.value)

                self.sensors_mapping_cache = sensors_mapping
                logger.debug(f"Загружен маппинг для {car.name}: {len(sensors_mapping)} сенсоров")
            except Exception as e:
                logger.error(f"Ошибка загрузки маппинга для {car.name}: {e}")
                self.sensors_mapping_cache = {}

        return self.sensors_mapping_cache

    def authenticate(self) -> bool:
        """Аутентификация в GlonassSoft API"""
        now = datetime.now()
        if (now - self.last_auth).total_seconds() > 60 * 15:
            self._enforce_rate_limit()

            url = f"{self.base_url}/auth/login"
            payload = {
                "login": self.metadata.get("login"),
                "password": self.metadata.get("password"),
            }

            try:
                response = requests.post(url, json=payload, timeout=(10, 120))
                response.raise_for_status()
                data = orjson.loads(response.content)
                self.auth_token = data.get("AuthId")

                if not self.auth_token:
                    logger.error("AuthId не найден в ответе")
                    return False
                self.last_auth = datetime.now()

                logger.info("Аутентификация успешна")
                return True

            except requests.exceptions.RequestException as e:
                logger.error(f"Ошибка аутентификации: {e}")
                return False
        return True
        
    def parse_raw_data_all(self, return_df=False ):
        if self.cars is None:
            return {
                "status": "completed",
                "message": f"Нет машин для обработки",
                "mode": "parse_all",
                "total_cars": 0,
                "processed_cars": 0,
                "failed_cars": 0,
                "parse_all": False,
                "car_ids_count": 0
            }
        start_time = datetime.now()
        if not self.authenticate():
            return {
                "status": "completed",
                "message": f"Нет  для обработки",
                "mode": "parse_all",
                "total_cars": self.total_cars,
                "processed_cars": 0,
                "failed_cars": 0,
                "parse_all": False,
                "car_ids_count": self.total_cars
            }
        if self.cars is not None:
            for car in self.cars:
                try:
                    self._enforce_rate_limit()
                    status, result = self.parse_raw_data(self.mode, return_df=return_df, car=car)
                    if status:
                        self.processed_cars += 1
                    else:
                        self.failed_cars += 1
                except Exception as err:
                    logger.warning(f"Ошибка связанная с машиной {car.id_in_provider_system} {err}")
                    continue

        end_time = datetime.now()
        return {
            "status": "completed",
            "message": f"Обработано {self.processed_cars} из {self.total_cars} машин",
            "mode": self.mode,
            "total_cars": self.total_cars,
            "processed_cars": self.processed_cars,
            "failed_cars": self.failed_cars,
            "start_time": start_time,
            "end_time": end_time,
            "data_mode": self.mode,
            "parse_all": False,
            "car_ids_count": self.total_cars,
            "execution_time": str(end_time - start_time)
        }


    def _pick_car(self):
        self.car = self.cars[self.i]
        self.i += 1
        return self.car 

    def _skip_car(self, car: Car):
        self.car = self.cars[self.i]
        self.i += 1
        return True
    
    def parse_refill_data_full(self, car: Car, start_date: datetime ,end_date: datetime):
        result = self._parse_refill_data(car, start_date, end_date)
        if result is not None:
           refill_data = self._preprocess_refill_data(result, car)
           return refill_data
        return None

    
    def _parse_refill_data(self, car: Car, start_date: datetime, end_date: datetime):
        """Запрашивает сообщения за конкретный период"""
        self._enforce_rate_limit()

        url = f"{self.base_url}/vehicles/fuelInOut"
        payload = {
            "vehicleIds":[ car.id_in_provider_system],
            "from": start_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "to": end_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "timezone": 0
        }
        headers = {"X-Auth": self.auth_token}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=(10, 180), stream=True)
            response.raise_for_status()

            data = orjson.loads(response.content)
            
            if not isinstance(data, list):
                logger.error(f"Некорректный формат ответа для {car.id_in_provider_system}")
                return None
            if len(data) == 0:
                logger.warning(f"Нет данных по заправкам для {car.id_in_provider_system}")
                return None
            fuels = data[0]

            logger.info(
                f"Получено {len(fuels["fuels"])} заправок для {car.id_in_provider_system} за период {start_date.date()} - {end_date.date()}")
            return fuels

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса данных для {car.id_in_provider_system} {car.name}: {e}")
            return None
    
    def _preprocess_refill_data(self, data: Dict[str, Any], car: Car):
        day_refill = {}

        for item in data["fuels"]:
            if item["event"] == "FuelIn":
                refill_amount = item["valueFuel"]
                refill_date = datetime.fromisoformat(item["startDate"]).date()
                refill_date = datetime(refill_date.year, refill_date.month, refill_date.day)
                if refill_date in day_refill:
                    day_refill[refill_date] += refill_amount 
                else:
                    day_refill[refill_date] = refill_amount 
        return pl.DataFrame(
            {
                "timestamp": day_refill.keys(),
                "refill": day_refill.values(),
            }
        )



    def parse_raw_data(self, mode: str, return_df=False, car : Car | None = None, start_date: datetime | None = None, end_date: datetime | None = None) -> tuple[ bool, pl.DataFrame | List[Dict[str, Any]] | None]:
        """Основной метод парсинга данных"""
        car_to_use = car if car is not None else self._pick_car()
        sensors_mapping = self._get_sensors_mapping(car_to_use)
        if not self.authenticate():
            return (False, [{"error": "Auth Error"}])
        if mode not in ["mileage", "fuel", "fuel_charts", "motohours", "raw", "raw_mapped"]:
            return (False, [{"error": f"Недопустимый режим: {mode}. Допустимые: mileage, fuel, motohours, raw, raw_mapped"}])
        

        


        all_messages = self._get_all_messages_for_period(start_date, end_date, car_to_use)

        if not all_messages:
            logger.warning(f"Нет данных для машины {car_to_use.id_in_provider_system}")
            return True, pl.DataFrame() if return_df else []

        if mode == "mileage":
            result = self._process_general(car_to_use,all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.mileage, GP.satellites, GP.fuel_consumpt, GP.rpm, GP.ignition, GP.msg_number],[GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.auto_column)],  return_df=return_df)
        elif mode == "fuel":
            result = self._process_general(car_to_use, all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.fuel_level, GP.satellites, GP.ignition, GP.msg_number , GP.voltage, GP.amtr_x, GP.amtr_y, GP.amtr_z], [GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.auto_column), GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.amtr_merge)], return_df=return_df)
        elif mode == "fuel_charts":
            result = self._process_general(car_to_use, all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.fuel_level, GP.satellites, GP.ignition, GP.msg_number, GP.voltage, GP.amtr_x, GP.amtr_y, GP.amtr_z], [GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.auto_column), GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.amtr_merge),  GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.chart_preprocess)], return_df=return_df)
        elif mode == "motohours":
            result = self._process_general(car_to_use, all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.motohours, GP.satellites, GP.rpm, GP.ignition], [GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.auto_column)], return_df=return_df)
        elif mode == "raw":
            result = self._process_unmapped(car_to_use, all_messages)
            if isinstance(result, pl.DataFrame):
                _, path = self._save_to_csv(result, car_to_use)
                self._archive_csv_file(path)
        elif mode == "raw_mapped":
            result = self._process_general(car_to_use, all_messages, sensors_mapping, [GP.timestamp, GP.speed, GP.motohours, GP.mileage, GP.satellites, GP.msg_number, GP.voltage, GP.fuel_level, GP.rpm, GP.ignition, GP.amtr_x, GP.amtr_y, GP.amtr_z, GP.longitude, GP.latitude, GP.fuel_consumpt], [GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.auto_column), GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.amtr_merge)], return_df=True)
            if isinstance(result, pl.DataFrame):
                _, path = self._save_to_csv(result, car_to_use)
                self._archive_csv_file(path)

        logger.info(f"Обработано {self.processed_messages} сообщений из {self.total_messages} для режима {mode}")
        return (True, result)
    def _get_all_messages_for_period(self, start_time: datetime | None, end_time: datetime | None, car: Car, default_days=90) -> List[Dict[str, Any]]:
        """Получает все сообщения за период с адаптивными запросами"""
        all_messages = []
        current_start = start_time if start_time else self.start_date
        end_time = end_time if end_time else self.end_date
        vehicle_id = car.id_in_provider_system
        period_days = self.default_period_days 

        while current_start < end_time:
            period_days = min(period_days, (end_time - current_start).days)
            if period_days < 1:
                period_days = 1

            current_end = min(current_start + timezone.timedelta(days=period_days), self.end_date)

            try:
                messages = self._fetch_messages_for_period(vehicle_id, current_start, current_end)

                if messages is not None:
                    all_messages.extend(messages)
                    current_start = current_end + timezone.timedelta(seconds=1)
                else:

                    if period_days > 1:
                        period_days = max(period_days // 2, 1)
                        logger.warning(f"Уменьшаем период до {period_days} дней")
                        continue
                    else:
                        logger.error(f"Не удалось получить данные для {vehicle_id} даже за 1 день")
                        break

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:

                    if period_days > 1:
                        period_days = max(period_days // 2, 1)
                    logger.warning(f"Rate limit (429). Уменьшаем период до {period_days} дней")
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
            response = requests.post(url, json=payload, headers=headers, timeout=(10, 180), stream=True)
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

    def _build_use_cols(self, required_columns, sensors_mapping: Dict[str, list[str]] ):
        usecols = ["auto"]
        for col in required_columns:
            param = GLOBAL_GLONASS_PARAMS.get(col)
            if param:
                path_to_param = ""
                if param.is_dynamic:
                    path_to_param = sensors_mapping.get(col.value, param.default_key)
                if path_to_param == "":
                    continue
                if isinstance(path_to_param, list):
                    usecols.extend(path_to_param)
                    continue
                usecols.append(path_to_param)
        return usecols

    def _save_to_csv(self, df: pl.DataFrame, car: Car) -> tuple[bool, Path]:
        path = self.base_path / f"auto{car.id_in_provider_system}_{self.mode}.csv"
        df.write_csv(path)
        return (True, path)

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
            
            # cleanup
            
            csv_file_path.unlink(True)

        except Exception as e:
            logger.error(f"Ошибка при архивации файла {csv_file_path}: {e}")

    def filter_parameters(self, messages: List[Dict[str, Any]], allowed_keys):
        allowed = set(allowed_keys)
        if allowed:
            allowed = list(map(lambda x: x.replace("parameters.", ""), allowed ))
        
        for msg in messages:
            params = msg.get("parameters")

            if isinstance(params, dict):
                msg["parameters"] = {
                    k: params.get(k, None)
                    for k in params.keys() & allowed
                }
            else:
                msg["parameters"] = None

        return messages
    def _process_unmapped(self, car: Car, messages: List[Dict[str, Any]], parameters: list[str] | None = None) -> pl.DataFrame:
        if parameters is not None:
            messages = self.filter_parameters(messages, parameters)
        result = pl.DataFrame(messages, infer_schema_length=30_000)
        fields = list(map(lambda x: f"parameters.{x}" , result["parameters"].struct.fields))
        result = result.with_columns(pl.col("parameters").struct.rename_fields(fields)).unnest("parameters") # разбить на части
        result = result.with_columns(pl.lit(str(car.id)).alias("auto"))
        return result
    
    def _build_required_columns(self, required_columns: List[GP], multi_sensor_info: Dict[str, int]):
        result = []
        for col in required_columns:
            el = GLOBAL_GLONASS_PARAMS.get(col).label
            target = multi_sensor_info[el]
            if target > 1:
                result.extend(list(map(lambda x: f"{el}_{x}", range(0, target ) ) ))
            else:
                result.append(el)
        return result


    def _process_general(self, car: Car, messages: List[Dict[str, Any]], sensors_mapping: Dict[str, list[str]], required_columns: List[GP], required_actions: List[GlonassAfterParsingProtocol | None] | None = None, return_df=False) -> pl.DataFrame | list[dict[str, Any]]:
        use_cols = self._build_use_cols(required_columns, sensors_mapping )
        result = self._process_unmapped(car, messages, use_cols)
        

        parameters_count = {}
        for col in required_columns:
            param = GLOBAL_GLONASS_PARAMS.get(col)

            if param:
                "test"
                path_to_params = [""]
                msensor = False
                if param.is_dynamic:
                    path_to_params = sensors_mapping.get(col.value, param.default_key)
                    path_to_params = [path_to_params] if isinstance(path_to_params, str) else path_to_params
                else:
                    if param.default_key in result.columns:
                        path_to_params = [param.default_key]
                
                msensor = len(path_to_params) > 1
                # должен быть один, иначе идем по каждому
                for index, path_to_param in enumerate(path_to_params):
                    true_label = param.label + f"_{index}" if msensor else param.label 
                    if path_to_param == "" or path_to_param not in result.columns:
                        # заменить спец значением весь столбец
                        
                        result = result.with_columns(pl.lit(param.default_on_absence).alias(true_label))
                    else:
                        # переименовываем столбец
                        
                        result = result.rename({path_to_param: true_label})
                        if param.cast: # если есть каст, кастуем
                            result = param.cast(result)
                        if param.default_value is not None:
                            result = result.with_columns(pl.col(true_label).fill_null(param.default_value))
                        if param.filter_on_absence:
                            result = result.filter(pl.col(true_label).is_not_null())
                    parameters_count[param.label] = len(path_to_params)
        # mapped_required_columns = list(map(lambda x: GLOBAL_GLONASS_PARAMS.get(x).label, required_columns))
        mapped_required_columns = self._build_required_columns(required_columns, parameters_count)
    
        
        if required_actions is not None:
            for action in required_actions:
                if action is not None:
                    result = action(result, car, mapped_required_columns, sensors_mapping)
        result = result.select(mapped_required_columns)
        self.processed_messages += result.shape[0]
        return result if return_df else result.to_dicts()