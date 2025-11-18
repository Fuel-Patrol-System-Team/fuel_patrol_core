import logging
from typing import Dict, Any, List, Tuple
from django.db import transaction
from core.models import Car, DataProvider, SensorsValues, SensorsKey
from core.services.providers.vehicle_base import BaseProvider

logger = logging.getLogger(__name__)

class VehicleService:
    @staticmethod
    @transaction.atomic
    def save_vehicle_to_db(vehicle_data: Dict[str, Any], provider: DataProvider) -> Tuple[Car, bool]:
        custom_fields = {
            field["name"]: field["value"]
            for field in vehicle_data.get("customFields", [])
        }
        engine_type = 1.0 if custom_fields.get("Тип учета") == "Моточасы" else 0.0
        vehicle_guid = vehicle_data.get("vehicleGuid")
        vehicle_id = vehicle_data.get("vehicleId")

        if not vehicle_guid or not vehicle_id:
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
                "is_tarrified": VehicleService._calculate_is_tarrified(vehicle_data),
                "is_active": vehicle_data.get("isActive", True),
            },
        )

        provider.cars.add(car)

        sensors_mapping = vehicle_data.get("sensorsMapping", {})
        VehicleService._save_sensors_mapping(car, sensors_mapping)

        return car, created

    @staticmethod
    def _calculate_is_tarrified(vehicle_data: Dict[str, Any]) -> bool:
        input_val = vehicle_data.get("input")
        output_val = vehicle_data.get("output")

        return not (
                (input_val is None or input_val == 1.0) and
                (output_val is None or output_val == 1.0)
        )

    @staticmethod
    def _save_sensors_mapping(car: Car, sensors_mapping: Dict[str, str]) -> None:
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