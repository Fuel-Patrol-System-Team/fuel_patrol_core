import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

import polars

from app.tasks import ParsingCarStats
from core.helpers.motohours import compute_theoretical_rpm
from core.models import Car, CarBadData, DataProvider, ReportQuery, SensorsValues
from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.report_service import ReportService

logger = logging.getLogger(__name__)


class RpmAutoCalculationService:
    """Сервис для расчета моточасов с полной бизнес-логикой"""

    @staticmethod
    def try_calculate_rpms(
            car_id: str,
            start_date: datetime = datetime.now(),
            end_date: datetime = datetime.now(),
            is_save_bad_data: bool = True
    ) -> Tuple[bool, Dict[str, Any] | None]:
        """
        Выполняет расчет моточасов с созданием отчета
        Возвращает (результат, статус_код)
        """
        try:

            car, provider = RpmAutoCalculationService._get_car_and_provider(car_id)
            if car is not None:
                contains_rpm = SensorsValues.objects.filter(car_id__id=car.id,key__key="rpm" ).count() > 0
                if not contains_rpm:
                    logger.warning(f"Для машины {car.name} нет rpm для расчетов")
                    return False, None

                if car.parsingcar_stats.rpm_idle is None:
                    return True, RpmAutoCalculationService.calculate_rpm_automatic(car_id, start_date, end_date, is_save_bad_data)
                else:
                    logger.warning(f"Для машины {car.name} rpm присутствует, пропускаем")
                    return False, None
        except Exception as e:
            error_msg = "Ошибка при расчете автоматического rpm"
            logger.error(f"Ошибка при расчете автоматического rpm для car_id={car_id}: {e}", exc_info=True)
            return {"error": error_msg}, 400
    @staticmethod
    def calculate_rpm_automatic(
            car_id: str,
            start_date: datetime = datetime.now(),
            end_date: datetime = datetime.now(),
            is_save_bad_data: bool = True
    ) -> Tuple[Dict[str, Any], int]:
        """
        Выполняет расчет моточасов с созданием отчета
        Возвращает (результат, статус_код)
        """
        report_query = None

        try:
            car, provider_obj = RpmAutoCalculationService._get_car_and_provider(car_id)
            if not car or not provider_obj:
                return {"error": "Автомобиль или провайдер не найдены"}, 400

            report_query, report_details = ReportService.create_report(
                provider_id=str(provider_obj.id),
                report_type=ReportQuery.ReportType.MOTOHOURS,
                is_save_bad_data=is_save_bad_data
            )

            validation_error = RpmAutoCalculationService._validate_dates(start_date, end_date)
            if validation_error:
                ReportService.complete_report_error(report_query, validation_error)
                return {"error": validation_error}, 400

            provider = GlonassGeneralProvider(None, car, provider_obj, start_date, end_date, "motohours")

            try:
                if not provider.authenticate():
                    error_msg = "Не удалось авторизоваться у провайдера"
                    ReportService.complete_report_error(report_query, error_msg)
                    return {"error": error_msg}, 401
            except Exception as auth_error:
                error_msg = f"Ошибка при аутентификации у провайдера: {str(auth_error)}"
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 401

            try:
                status, df = provider.parse_raw_data("motohours", return_df=True)
            except Exception as data_error:
                error_msg = f"Не удалось получить данные от провайдера: {str(data_error)}"
                logger.error(f"Ошибка получения данных от провайдера для car_id={car_id}: {data_error}", exc_info=True)
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 400

            if df is None:
                error_msg = "Провайдер не вернул данные"
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 400

            if df.is_empty():
                result = {
                    "rpm_idle": 0
                }

                ReportService.create_bad_data_record(
                    car,
                    "Нет возможности посчитать автоматический rpm за указанный период",
                    report_query,
                    start_date, end_date,
                    CarBadData.Severity.INFO,
                    CarBadData.Category.PROVIDER_ERROR,
                    [CarBadData.Tag.MOTOHOURS, CarBadData.Tag.PROVIDER]
                    
                )

                report_data = {
                    "result": result,
                    "empty_data": True,
                    "rows_processed": 0
                }

                ReportService.complete_report_success(
                    report_query,
                    report_data,
                    cars_proceed=0,
                    cars_skipped=1
                )

                return {"result": result}, 200

            try:
                if isinstance(df, polars.DataFrame):
                    statsManager = ParsingCarStats.objects.filter(car_id=car.id)
                    stats = statsManager.first()
                    if stats is None:
                        raise BaseException("No parsing stats for this car")
                    wall_value, wall_std, trigger, rpm_present = compute_theoretical_rpm(df)
                    if not rpm_present:
                        pass
                    if not trigger: # значит с расчетами все ок
                        statsManager.update(
                            rpm_idle=wall_value,
                        )
            except Exception as calc_error:
                error_msg = f"Ошибка при расчете rpm: {str(calc_error)}"
                logger.error(f"Ошибка расчета rpm для car_id={car_id}: {calc_error}", exc_info=True)
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 400


            report_data = {
                "result": {"rpm_idle": wall_value},
                "rows_processed": len(df),
            }

            ReportService.complete_report_success(
                report_query,
                report_data,
                cars_proceed=1,
                cars_skipped=0
            )

            return {"result": {"rpm_idle": wall_value}}, 200

        except Car.DoesNotExist:
            error_msg = "Автомобиль не найден"
            if report_query:
                ReportService.complete_report_error(report_query, error_msg)
            return {"error": error_msg}, 400

        except ValueError as e:
            error_msg = f"Неверный формат данных: {e}"
            if report_query:
                ReportService.complete_report_error(report_query, error_msg, e)
            return {"error": error_msg}, 400

        except Exception as e:
            error_msg = "Внутренняя ошибка при обработке запроса"
            logger.error(f"Неожиданная ошибка расчета моточасов для car_id={car_id}: {e}", exc_info=True)

            if report_query:
                ReportService.complete_report_error(report_query, error_msg, e)

            return {"error": error_msg}, 400

    @staticmethod
    def _get_car_and_provider(car_id: str) -> Tuple[Optional[Car], Optional[DataProvider]]:
        """Получает автомобиль и провайдер"""
        try:
            car = Car.objects.prefetch_related('data_providers').select_related('parsingcar_stats').get(id=car_id)
            provider_obj = car.data_providers.first()
            return car, provider_obj
        except Car.DoesNotExist:
            return None, None
        except Exception as e:
            logger.error(f"Ошибка при получении автомобиля car_id={car_id}: {e}")
            return None, None

    @staticmethod
    def _validate_dates(start_date: Optional[datetime], end_date: Optional[datetime]) -> Optional[str]:
        """Валидирует даты"""
        try:
            if start_date and end_date and start_date >= end_date:
                return "start_date должна быть раньше end_date."
            return None
        except Exception as e:
            return f"Ошибка валидации дат: {str(e)}"