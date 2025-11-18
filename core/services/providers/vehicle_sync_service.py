import logging
from typing import Dict, Any
from core.models import DataProvider, Organization
from core.services.providers.provider_factory import ProviderFactory
from core.services.providers.vehicle_service import VehicleService

logger = logging.getLogger(__name__)


class VehicleSyncService:
    """Сервис синхронизации транспортных средств с провайдером"""

    def __init__(self, provider: DataProvider, organization: Organization):
        self.provider = provider
        self.organization = organization
        self.provider_instance = None

    def sync_vehicles(self) -> Dict[str, Any]:
        """Основной метод синхронизации"""
        try:

            self.provider_instance = ProviderFactory.create_provider(
                self.provider.metadata,
                provider_type="vehicles"
            )

            if not self.provider_instance:
                return {
                    "success": False,
                    "error": "Не удалось создать экземпляр провайдера"
                }

            if not self.provider_instance.authenticate():
                return {
                    "success": False,
                    "error": "Ошибка аутентификации у провайдера"
                }

            vehicles = self.provider_instance.get_vehicles()
            if vehicles is None:
                return {
                    "success": False,
                    "error": "Не удалось получить список транспортных средств"
                }

            stats = self._process_vehicles(vehicles)

            return {
                "success": True,
                "total_vehicles": len(vehicles),
                "created": stats["created"],
                "updated": stats["updated"],
                "failed": stats["failed"]
            }

        except Exception as e:
            logger.error(f"Ошибка синхронизации транспортных средств: {e}")
            return {
                "success": False,
                "error": f"Внутренняя ошибка сервера: {str(e)}"
            }

    def _process_vehicles(self, vehicles: list) -> Dict[str, int]:
        """Обрабатывает список транспортных средств"""
        stats = {"created": 0, "updated": 0, "failed": 0}

        for i, vehicle in enumerate(vehicles, 1):
            try:
                vehicle_id = vehicle.get("vehicleId")
                if not vehicle_id:
                    stats["failed"] += 1
                    logger.warning("Пропущена машина без vehicleId")
                    continue

                logger.info(f"Обработка машины {i}/{len(vehicles)}: vehicleId={vehicle_id}")

                vehicle_details = self.provider_instance.get_vehicle_details(vehicle_id)
                if not vehicle_details:
                    stats["failed"] += 1
                    logger.warning(f"Не удалось получить детали для vehicleId={vehicle_id}")
                    continue

                car, created = VehicleService.save_vehicle_to_db(vehicle_details, self.provider)

                if created:
                    stats["created"] += 1
                    logger.info(f"Создан автомобиль: {car.name} (ID: {car.id})")
                else:
                    stats["updated"] += 1
                    logger.info(f"Обновлен автомобиль: {car.name} (ID: {car.id})")

            except Exception as e:
                stats["failed"] += 1
                logger.error(f"Ошибка обработки vehicleId={vehicle_id}: {e}")
                continue

        return stats
