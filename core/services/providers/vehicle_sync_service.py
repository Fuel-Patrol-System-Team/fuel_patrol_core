import logging
from typing import Dict, Any, final

from core.models import Car, DataProvider, Organization

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

            stats = self._process_vehicles(vehicles, self.provider, report_query)

            current_cars = self.provider.cars.values_list("id_in_provider_system", flat=True)
            cars_to_inactivate =  set(current_cars) - set(stats["vehicles_ids"]) if len(current_cars) > 0 else set()
            self._deactivate_missing_cars(cars_to_inactivate)
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
    
    def _deactivate_missing_cars(self, cars_to_inactivate: set):
        """Деактивирует автомобили, которые отсутствуют в новом списке от провайдера"""
        for car_id in cars_to_inactivate:
            try:
                car = self.provider.cars.filter(id_in_provider_system=car_id).first()
                car.is_active = False
                car.save()
                logger.info(f"Деактивирован автомобиль {car.name} (ID: {car.id}) - отсутствует у провайдера")
            except Car.DoesNotExist:
                logger.warning(f"Не найден автомобиль с id_in_provider_system={car_id} для деактивации")

    def _process_vehicles(self, vehicles: list,provider: DataProvider, report_query=None ) -> Dict[str, Any]:
        """Обрабатывает список транспортных средств с созданием CarBadData записей"""
        stats = {
            "created": 0,
            "updated": 0,
            "failed": 0,
            "inactivated": 0,
            "vehicles_ids": [],
        }

        for i, vehicle in enumerate(vehicles, 1):
            try:
                vehicle_id = vehicle.get("vehicleId")
                if not vehicle_id:
                    stats["failed"] += 1
                    logger.warning("Пропущена машина без vehicleId")
                    continue

                logger.info(f"Обработка машины {i}/{len(vehicles)}: vehicleId={vehicle_id}")

                vehicle_details = self.provider_instance.get_vehicle_details(vehicle_id, provider)
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

            finally:
                stats["vehicles_ids"].append(vehicle_id)

        return stats
