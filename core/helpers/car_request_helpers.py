import logging
from typing import Dict, List, Tuple, Optional, Set
from app.tasks import process_single_car_data_task
from django.db.models import QuerySet

from core.models import DataProvider, Car, CarUnit, ReportQuery
from core.services.providers.report_service import ReportService

logger = logging.getLogger(__name__)


class CarRequestHelper:
    """Хелпер для обработки запросов данных по автомобилям."""

    @staticmethod
    def validate_input_data(
            provider_name: str,
            parse_all: bool,
            car_ids: List[str],
            unit_ids: List[str],
            start_date: str,
            end_date: str
    ) -> Tuple[bool, str]:
        """Валидация входных данных."""
        if not provider_name:
            return False, "Provider name is required"

        if not start_date or not end_date:
            return False, "Start date and end date are required"

        selected_modes = sum([bool(parse_all), bool(car_ids), bool(unit_ids)])

        if selected_modes == 0:
            return False, (
                "One of the following must be specified: "
                "parse_all=True, car_ids list, or unit_ids list"
            )

        if selected_modes > 1:
            return False, "Only one mode can be selected: parse_all, car_ids, or unit_ids"

        return True, ""

    @staticmethod
    def get_provider(provider_name: str) -> Tuple[Optional[DataProvider], Optional[str]]:
        """Получение провайдера по имени."""
        try:
            provider = DataProvider.objects.get(name=provider_name)
            return provider, None
        except DataProvider.DoesNotExist:
            return None, f"Provider {provider_name} not found"

    @staticmethod
    def get_cars_by_mode(
            provider: DataProvider,
            parse_all: bool,
            car_ids: List[str],
            unit_ids: List[str]
    ) -> Tuple[Optional[QuerySet], List[str], str, Optional[str]]:
        """Получение автомобилей в зависимости от режима."""
        cars = None
        mode_info = ""

        try:
            if parse_all:
                return CarRequestHelper._get_all_cars(provider)
            elif unit_ids:
                return CarRequestHelper._get_cars_by_units(provider, unit_ids)
            else:
                return CarRequestHelper._get_cars_by_ids(provider, car_ids)
        except Exception as e:
            logger.error(f"Error getting cars by mode: {e}", exc_info=True)
            return None, [], "", f"Error validating input IDs: {str(e)}"

    @staticmethod
    def _get_all_cars(provider: DataProvider) -> Tuple[QuerySet, List[str], str, Optional[str]]:
        """Получение всех автомобилей провайдера."""
        logger.info(f"parse_all flag is True, fetching all cars for provider {provider.name}")
        cars = Car.objects.filter(data_providers=provider)
        car_ids = [str(car.id) for car in cars]
        mode_info = f"parse_all mode - {len(cars)} cars"

        if not cars.exists():
            logger.warning(f"No cars found for provider {provider.name}")
            return None, [], "", f"No cars found for provider {provider.name}"

        logger.info(f"Found {len(cars)} cars for provider {provider.name}")
        return cars, car_ids, mode_info, None

    @staticmethod
    def _get_cars_by_units(provider: DataProvider, unit_ids: List[str]) -> Tuple[
        QuerySet, List[str], str, Optional[str]]:
        """Получение автомобилей по ID юнитов."""
        logger.info(f"Processing cars by unit_ids: {unit_ids}")

        try:
            car_units = CarUnit.objects.filter(id__in=unit_ids)
            found_unit_ids = {str(unit.id) for unit in car_units}
            missing_unit_ids = set(unit_ids) - found_unit_ids

            if missing_unit_ids:
                logger.warning(f"Missing unit IDs: {missing_unit_ids}")
                return None, [], "", f"Some units not found: {list(missing_unit_ids)}"
        except Exception as e:
            logger.error(f"Error fetching car units: {e}")
            return None, [], "", f"Error validating unit IDs: {str(e)}"

        cars = Car.objects.filter(
            data_providers=provider,
            car_unit__in=car_units
        ).distinct()

        car_ids = [str(car.id) for car in cars]
        mode_info = f"unit_ids mode - {len(car_units)} units, {len(cars)} cars"

        if not cars.exists():
            logger.warning(f"No cars found for units {unit_ids} in provider {provider.name}")
            return None, [], "", f"No cars found for specified units in provider {provider.name}"

        logger.info(f"Found {len(cars)} cars for {len(car_units)} units")
        return cars, car_ids, mode_info, None

    @staticmethod
    def _get_cars_by_ids(provider: DataProvider, car_ids: List[str]) -> Tuple[QuerySet, List[str], str, Optional[str]]:
        """Получение автомобилей по ID."""
        logger.info(f"Processing specific cars: {car_ids}")

        cars = Car.objects.filter(
            id__in=car_ids,
            data_providers=provider
        ).distinct()

        found_car_ids = {str(car.id) for car in cars}
        missing_car_ids = set(car_ids) - found_car_ids
        mode_info = f"car_ids mode - {len(found_car_ids)} cars"

        if missing_car_ids:
            logger.warning(f"Missing car IDs: {missing_car_ids}")
            return None, [], "", (
                f"Some cars not found or not associated with provider {provider.name}: "
                f"{list(missing_car_ids)}"
            )

        return cars, list(found_car_ids), mode_info, None

    @staticmethod
    def collect_unit_info(cars: QuerySet) -> Dict:
        """Сбор информации о юнитах для ответа."""
        unit_info = {}

        for car in cars:
            if car.car_unit:
                unit_id = str(car.car_unit.id)
                unit_name = car.car_unit.name
                if unit_id not in unit_info:
                    unit_info[unit_id] = {
                        'name': unit_name,
                        'car_count': 0
                    }
                unit_info[unit_id]['car_count'] += 1

        return unit_info

    @staticmethod
    def create_processing_tasks(
            cars: QuerySet,
            provider: DataProvider,
            start_date: str,
            end_date: str,
            is_save_bad_data: bool
    ) -> Tuple[List, List[str], Dict, Optional[str]]:
        """Создание задач обработки для автомобилей."""
        task_group = []
        report_query_ids = []
        car_names = {}

        try:
            for car in cars:
                # Создаем отчет
                report_query, report_details = ReportService.create_report(
                    provider_id=str(provider.id),
                    report_type=ReportQuery.ReportType.LEAKS,
                    is_save_bad_data=is_save_bad_data
                )

                task = process_single_car_data_task.s(
                    car_id=str(car.id),
                    provider_id=str(provider.id),
                    report_query_id=str(report_query.id),
                    start_date=start_date,
                    end_date=end_date
                )

                task_group.append(task)
                report_query_ids.append(str(report_query.id))
                car_names[str(car.id)] = car.name

            return task_group, report_query_ids, car_names, None

        except Exception as e:
            logger.error(f"Error creating processing tasks: {e}", exc_info=True)
            return [], [], {}, str(e)

    @staticmethod
    def create_response_data(
            task_group_id: str,
            report_query_ids: List[str],
            car_ids: List[str],
            car_names: Dict,
            provider_name: str,
            mode_info: str,
            parse_all: bool,
            unit_ids: List[str],
            unit_info: Dict = None
    ) -> Dict:
        """Создание структурированного ответа."""
        mode = mode_info.split(' - ')[0]

        response_data = {
            "task_group_id": task_group_id,
            "report_query_ids": report_query_ids,
            "total_cars": len(car_ids),
            "car_names": car_names,
            "message": f"Обработка данных для {len(car_ids)} машин запущена",
            "provider_name": provider_name,
            "mode": mode,
            "status_endpoint": f"/api/tasks/{task_group_id}/status/",
            "individual_status_endpoint": "/api/tasks/{task_id}/status/"
        }

        if parse_all:
            response_data["parse_all_mode"] = True
            response_data["message"] = (
                f"Обработка данных для всех ({len(car_ids)}) "
                f"машин провайдера {provider_name} запущена"
            )
        elif unit_ids:
            response_data["unit_ids"] = unit_ids
            response_data["units_info"] = unit_info or {}
            response_data["message"] = (
                f"Обработка данных для {len(car_ids)} машин "
                f"из {len(unit_info) if unit_info else 0} юнитов запущена"
            )
        else:
            response_data["car_ids"] = car_ids

        return response_data

    @staticmethod
    def handle_failed_tasks(report_query_ids: List[str], error_message: str):
        """Обработка неудачных задач."""
        for report_query_id in report_query_ids:
            try:
                report_query = ReportQuery.objects.get(id=report_query_id)
                ReportService.complete_report_error(
                    report_query, f"Failed to start processing task: {error_message}"
                )
            except Exception as e:
                logger.error(f"Failed to complete report error for {report_query_id}: {e}")