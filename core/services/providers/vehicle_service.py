from collections import defaultdict
import datetime
from itertools import chain
import logging
from typing import Dict, Any, Tuple, List, cast
from venv import create
from django.db import transaction
from pytz import utc
from core.models import Car, DataProvider, SensorsValues, SensorsKey, CarUnit
from core.services.providers.glonass.glonasssoft_vehicles_provider import SensorType

logger = logging.getLogger(__name__)


class VehicleService:
    """Сервис для работы с транспортными средствами"""

    @staticmethod
    @transaction.atomic
    def save_vehicle_with_validation(
        vehicle_data: Dict[str, Any], provider: DataProvider
    ) -> Tuple[Car, bool, List[str]]:
        """
        Сохраняет или обновляет данные об автомобиле с валидацией
        Возвращает (car, created, critical_errors)
        """
        critical_errors = []

        try:
            car_unit_id = vehicle_data.get("car_unit_id")
            car_unit = None

            if car_unit_id:
                try:
                    car_unit = CarUnit.objects.get(id=car_unit_id)
                    logger.debug(
                        f"Найден CarUnit по ID: {car_unit_id} ({car_unit.name})"
                    )
                except CarUnit.DoesNotExist:
                    logger.warning(
                        f"CarUnit с ID {car_unit_id} не найден, связь не установлена"
                    )

            custom_fields = {
                field["name"]: field["value"]
                for field in vehicle_data.get("customFields", [])
            }
            engine_type = 1.0 if custom_fields.get("Тип учета") == "Моточасы" else 0.0
            vehicle_guid = vehicle_data.get("vehicleGuid")
            vehicle_id = vehicle_data.get("vehicleId")

            if not vehicle_guid or not vehicle_id:
                raise ValueError("Missing vehicleGuid or vehicleId")

            validation_errors = VehicleService._validate_vehicle_data(vehicle_data)
            critical_errors.extend(validation_errors)

            car, created = Car.objects.update_or_create(
                id=vehicle_guid,
                defaults={
                    "id_in_provider_system": vehicle_id,
                    "car_unit": car_unit,
                    "name": vehicle_data.get("name", ""),
                    "description": f"{vehicle_data.get('parentName', '')}, {vehicle_data.get('modelName', '')}, {vehicle_data.get('unitName', '')}",
                    "engine_type": engine_type,
                    "input": 0,
                    "grades": vehicle_data.get("gradeMapping", {}).get(
                        "calc_sensors_fuel_level", None
                    ),
                    "output": 0,
                    "is_tarrified": VehicleService._calculate_is_tarrified(
                        vehicle_data
                    ),
                    "is_active": len(critical_errors) == 0,
                },
            )

            provider.cars.add(car)
            sensors_mapping = vehicle_data.get("sensorsMapping", {})
            VehicleService._save_sensors_mapping(car, sensors_mapping, created)

            logger.info(
                f"Сохранены данные для vehicleId={vehicle_id}. "
                f"Сенсоров: {len(sensors_mapping)}. "
                f"CarUnit: {car_unit.name if car_unit else 'не указан'}"
            )

            return car, created, critical_errors

        except Exception as e:
            logger.error(f"Ошибка сохранения vehicle данных: {e}")
            raise

    @staticmethod
    def _validate_vehicle_data(vehicle_data: Dict[str, Any]) -> List[str]:
        """Проверяет данные автомобиля на критические ошибки"""
        errors = []

        grades = vehicle_data.get("grades", None)

        is_tarrified = grades is not None and len(grades) > 2

        sensors_mapping = vehicle_data.get("sensorsMapping", {})

        items = list(
            chain(
                *[
                    [(label, value) for value in arr]
                    for label, arr in sensors_mapping.items()
                ]
            )
        )
        duplicate_preparation = defaultdict(list)
        for label, value in items:
            duplicate_preparation[value.parameter].append(label)

        duplicates = {
            v: labels for v, labels in duplicate_preparation.items() if len(labels) > 1
        }
        for raw_param, params in duplicates.items():
            params = set(params)
            if len(params) > 1:
                errors.append(
                    f"Для датчиков ({' '.join(params)})  используется одинаковый параметр {raw_param}"
                )

        is_active = vehicle_data.get("isActive", True)
        if not is_active:
            errors.append("ТС неактивно в системе провайдера")

        return errors

    @staticmethod
    def _calculate_is_tarrified(vehicle_data: Dict[str, Any]) -> bool:
        """Определяет, является ли ТС тарированным"""
        sensors = cast(Dict[str, List[SensorType]], vehicle_data.get("sensorsMapping"))
        if sensors is None:
            return False
        fuel_sensors = sensors.get("calc_sensors_fuel_level")
        if fuel_sensors is None or len(fuel_sensors) == 0:
            return True
        target_sensor = list(filter(lambda x: x.is_picked, fuel_sensors))
        if len(target_sensor) == 0:
            logger.warning(
                f"Машина не имеет тарировки т.к. нет сенсора, который подходит системе"
            )
            return False
        target_sensor = target_sensor[0]
        return (
            target_sensor.metadata is not None
            and target_sensor.metadata.get("grades") is not None
        )

    # TODO: придумать как избавить от is_first_parsing он не позволяет при появлении нового сенсора заменить текущий выбранный пользователем
    @staticmethod
    def _save_sensors_mapping(
        car: Car, sensors_mapping: Dict[str, List[SensorType]], is_first_parsing=False
    ) -> None:
        """Сохраняет маппинг сенсоров в БД"""
        is_first_parsing_logical = is_first_parsing
        amount = SensorsValues.objects.filter(car_id=car).count()
        if amount == 0:
            is_first_parsing_logical = True

        for category, mappings in sensors_mapping.items():
            try:
                sensor_key, _ = SensorsKey.objects.get_or_create(key=category)
                for mapping in mappings:
                    grades = (
                        None
                        if mapping.metadata is None
                        else mapping.metadata.get("grades")
                    )

                    new_sensor, is_created = SensorsValues.objects.update_or_create(
                        car_id=car,
                        key=sensor_key,
                        value=str(mapping.parameter),
                        defaults={
                            "grades": grades,
                            "metadata": mapping.metadata,
                            "created_at": datetime.datetime.now().astimezone(tz=utc),
                            "is_system_pick": mapping.is_picked,
                            "multi": mapping.is_multi,
                            "multi_type": mapping.multi_type
                        },
                    )
                    if is_created and is_first_parsing_logical:
                        new_sensor.is_active = new_sensor.is_system_pick
                        new_sensor.save()
                        pass
            except Exception as e:
                logger.error(f"Ошибка при сохранении сенсора {category}: {e}")
                continue

    @staticmethod
    @transaction.atomic
    def save_vehicle_to_db(
        vehicle_data: Dict[str, Any], provider: DataProvider
    ) -> Tuple[Car, bool]:
        """
        Старый метод для обратной совместимости
        """
        car, created, _ = VehicleService.save_vehicle_with_validation(
            vehicle_data, provider
        )
        return car, created
