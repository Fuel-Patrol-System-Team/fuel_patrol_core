import logging
from typing import Dict, Any

from core.models import DataProvider, Organization

from core.services.providers.provider_factory import ProviderFactory
from core.services.providers.report_service import ReportService
from core.services.providers.vehicle_service import VehicleService

logger = logging.getLogger(__name__)


class VehicleSyncService:
    """Сервис синхронизации транспортных средств с провайдером"""

    def __init__(self, provider: DataProvider, organization: Organization):
        self.provider = provider
        self.organization = organization
        self.provider_instance = None

    def sync_vehicles(self, report_query=None) -> Dict[str, Any]:
        """Основной метод синхронизации с созданием отчета"""
        try:

            self.provider_instance = ProviderFactory.create_provider(
                self.provider.metadata,
                provider_type="vehicles"
            )

            if not self.provider_instance:
                error_msg = "Не удалось создать экземпляр провайдера"
                if report_query:
                    ReportService.complete_report_error(report_query, error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }

            if not self.provider_instance.authenticate():
                error_msg = "Ошибка аутентификации у провайдера"
                if report_query:
                    ReportService.complete_report_error(report_query, error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }

            vehicles = self.provider_instance.get_vehicles()
            if vehicles is None:
                error_msg = "Не удалось получить список транспортных средств"
                if report_query:
                    ReportService.complete_report_error(report_query, error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }

            logger.info(f"Получено {len(vehicles)} транспортных средств для обработки")

            stats = self._process_vehicles(vehicles, report_query)

            result = {
                "success": True,
                "total_vehicles": len(vehicles),
                "created": stats["created"],
                "updated": stats["updated"],
                "failed": stats["failed"],
                "inactivated": stats["inactivated"]
            }

            if report_query:
                ReportService.complete_report_success(
                    report_query,
                    result,
                    cars_proceed=stats["created"] + stats["updated"],
                    cars_skipped=stats["failed"]
                )

            return result

        except Exception as e:
            error_msg = f"Внутренняя ошибка сервера: {str(e)}"
            logger.error(f"Ошибка синхронизации транспортных средств: {e}")

            if report_query:
                ReportService.complete_report_error(report_query, error_msg, e)

            return {
                "success": False,
                "error": error_msg
            }

    def _process_vehicles(self, vehicles: list, report_query=None) -> Dict[str, int]:
        """Обрабатывает список транспортных средств с созданием CarBadData записей"""
        stats = {
            "created": 0,
            "updated": 0,
            "failed": 0,
            "inactivated": 0
        }

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

                car, created, critical_errors = VehicleService.save_vehicle_with_validation(
                    vehicle_details,
                    self.provider
                )

                if critical_errors:

                    for error in critical_errors:
                        ReportService.create_bad_data_record(car, error, report_query)

                    car.is_active = False
                    car.save()
                    stats["inactivated"] += 1

                    logger.warning(f"Машина {car.name} деактивирована из-за ошибок: {critical_errors}")

                    if created:
                        stats["created"] += 1
                    else:
                        stats["updated"] += 1

                else:

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
