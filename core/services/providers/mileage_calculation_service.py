import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from core.helpers.mileage_test import MileageModes, make_empty_mileage_result, mileage_test
from core.models import Car, DataProvider, ReportQuery
from core.services.providers.glonass.glonassoft_mileage_provider import GlonassSoftMileageProvider
from core.services.providers.report_service import ReportService

logger = logging.getLogger(__name__)


class MileageCalculationService:
    """Сервис для расчета пробега с полной бизнес-логикой"""

    @staticmethod
    def calculate_mileage(
            car_id: str,
            agg: Optional[int] = None,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None,
            is_save_bad_data: bool = True
    ) -> Tuple[Dict[str, Any], int]:
        """
        Выполняет расчет пробега с созданием отчета
        Возвращает (результат, статус_код)
        """
        report_query = None

        try:
            car, provider_obj = MileageCalculationService._get_car_and_provider(car_id)
            if not car or not provider_obj:
                return {"error": "Автомобиль или провайдер не найдены"}, 400


            report_query, report_details = ReportService.create_report(
                provider_id=str(provider_obj.id),
                report_type=ReportQuery.ReportType.MILEAGE,
                is_save_bad_data=is_save_bad_data
            )


            validation_error = MileageCalculationService._validate_dates(start_date, end_date)
            if validation_error:
                ReportService.complete_report_error(report_query, validation_error)
                return {"error": validation_error}, 400


            provider = GlonassSoftMileageProvider(provider_obj.metadata, car_id)
            if not provider.authenticate():
                error_msg = "Не удалось авторизоваться у провайдера"
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 401


            df = provider.get_car_data(start_date, end_date)

            if df is None or df.is_empty():
                mode = MileageModes.standart if agg is None else MileageModes.agg
                result = make_empty_mileage_result(mode)


                ReportService.create_bad_data_record(
                    car,
                    "Нет данных mileage за указанный период",
                    report_query
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


            mode = MileageModes.standart if agg is None else MileageModes.agg
            agg_period = 1 if agg is None else agg
            result = mileage_test(df, agg_period, mode)


            report_data = {
                "result": result,
                "rows_processed": len(df),
                "calculation_mode": mode,
                "aggregation_period_minutes": agg_period
            }


            ReportService.complete_report_success(
                report_query,
                report_data,
                cars_proceed=1,
                cars_skipped=0
            )

            return {"result": result}, 200

        except Car.DoesNotExist:
            error_msg = "Автомобиль не найден"
            if report_query:
                ReportService.complete_report_error(report_query, error_msg)
            return {"error": error_msg}, 404

        except ValueError as e:
            error_msg = f"Неверный формат данных: {e}"
            if report_query:
                ReportService.complete_report_error(report_query, error_msg, e)
            return {"error": error_msg}, 400

        except Exception as e:
            error_msg = "Внутренняя ошибка сервера при расчете пробега"
            logger.error(f"Ошибка расчета пробега для car_id={car_id}: {e}", exc_info=True)

            if report_query:
                ReportService.complete_report_error(report_query, error_msg, e)

            return {"error": error_msg}, 500

    @staticmethod
    def _get_car_and_provider(car_id: str) -> Tuple[Optional[Car], Optional[DataProvider]]:
        """Получает автомобиль и провайдер"""
        try:
            car = Car.objects.prefetch_related('data_providers').get(id=car_id)
            provider_obj = car.data_providers.first()
            return car, provider_obj
        except Car.DoesNotExist:
            return None, None

    @staticmethod
    def _validate_dates(start_date: Optional[datetime], end_date: Optional[datetime]) -> Optional[str]:
        """Валидирует даты"""
        if start_date and end_date and start_date >= end_date:
            return "start_date должна быть раньше end_date."
        return None