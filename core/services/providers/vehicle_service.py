import logging
from typing import Dict, Any, Tuple, List
from django.db import transaction
from core.models import Car, DataProvider, SensorsValues, SensorsKey, CarUnit

logger = logging.getLogger(__name__)


class VehicleService:
    """Сервис для работы с транспортными средствами"""

    @staticmethod
    @transaction.atomic
    def save_vehicle_with_validation(
            vehicle_data: Dict[str, Any],
            provider: DataProvider
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
                    logger.debug(f"Найден CarUnit по ID: {car_unit_id} ({car_unit.name})")
                except CarUnit.DoesNotExist:
                    logger.warning(f"CarUnit с ID {car_unit_id} не найден, связь не установлена")

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
                    "input": vehicle_data.get("input"),
                    "grades": vehicle_data.get("grades"),
                    "output": vehicle_data.get("output"),
                    "is_tarrified": VehicleService._calculate_is_tarrified(vehicle_data),
                    "is_active": len(critical_errors) == 0,
                },
            )

            provider.cars.add(car)

            sensors_mapping = vehicle_data.get("sensorsMapping", {})
            VehicleService._save_sensors_mapping(car, sensors_mapping)

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

        sensor_paths = list(sensors_mapping.values())
        if len(sensor_paths) != len(set(sensor_paths)):
            seen_paths = set()
            for path in sensor_paths:
                if path in seen_paths and path:
                    errors.append("Один датчик ремапится в два поля")
                    break
                seen_paths.add(path)

        is_active = vehicle_data.get("isActive", True)
        if not is_active:
            errors.append("ТС неактивно в системе провайдера")

        return errors

    @staticmethod
    def _calculate_is_tarrified(vehicle_data: Dict[str, Any]) -> bool:
        """Определяет, является ли ТС тарированным"""
        input_val = vehicle_data.get("input")
        output_val = vehicle_data.get("output")

        return not (
                (input_val is None or input_val == 1.0) and
                (output_val is None or output_val == 1.0)
        )

    @staticmethod
    def _save_sensors_mapping(car: Car, sensors_mapping: Dict[str, str]) -> None:
        """Сохраняет маппинг сенсоров в БД"""
        for label, value in sensors_mapping.items():
            try:
                sensor_key, _ = SensorsKey.objects.get_or_create(key=label)
                SensorsValues.objects.update_or_create(
                    car_id=car,
                    key=sensor_key,
                    defaults={"value": str(value)}
                )
            except Exception as e:
                logger.error(f"Ошибка при сохранении сенсора {label}: {e}")
                continue

    @staticmethod
    @transaction.atomic
    def save_vehicle_to_db(vehicle_data: Dict[str, Any], provider: DataProvider) -> Tuple[Car, bool]:
        """
        Старый метод для обратной совместимости
        """
        car, created, _ = VehicleService.save_vehicle_with_validation(vehicle_data, provider)
        return car, created
