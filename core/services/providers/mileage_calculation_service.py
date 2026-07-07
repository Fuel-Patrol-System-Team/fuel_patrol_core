from enum import Enum
import logging
from typing import Dict, Any, Literal, Optional, Tuple
from datetime import datetime

import polars as pl
from pandas import DataFrame
import polars

from app.tasks import CarDataService
from core.admin import CarBadData
from core.helpers.mileage import MileageModes, make_empty_mileage_result, mileage_test_compute, mileage_test_fraud_new
from core.models import Car, DataProvider, ReportQuery
from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.glonass.glonassoft_mileage_provider import GlonassSoftMileageProvider
from core.services.providers.report_service import ReportService

logger = logging.getLogger(__name__)

class MileageAlgorithms(Enum):
    compute = "compute"
    fraud = "fraud"

class MileageCalculationService:
    """Сервис для расчета пробега с полной бизнес-логикой"""

    @staticmethod
    def calculate_mileage(
            car_id: str,
            alg: MileageAlgorithms,
            agg: Optional[int] = None,
            start_date: datetime = datetime.now(),
            end_date: datetime = datetime.now(),
            is_save_bad_data: bool = True,
            parser: Optional[GlonassGeneralProvider] = None,
            sensor_speed_chart: Literal["can"] | Literal["mileage"] = "mileage",
            force_chart = False
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


            provider = parser if parser is not None else GlonassGeneralProvider(None, car, provider_obj, start_date, end_date, "mileage")
            if not provider.authenticate():
                error_msg = "Не удалось авторизоваться у провайдера"
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 401


            status, df, sensors = provider.parse_raw_data("mileage", True, car)
            if df is None or df.is_empty() or not status:

                mode = MileageModes.standart if agg is None else MileageModes.agg
                result = make_empty_mileage_result(mode)


                ReportService.create_bad_data_record(
                    car,
                    "Нет данных mileage за указанный период",
                    report_query,
                    start_date, end_date
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
            if isinstance(df, polars.DataFrame):
                auto = CarDataService.prepare_auto_data(car)
                auto_record = auto.filter(pl.col("auto") == str(car_id)).to_dicts()[0]
                mode = MileageModes.standart if agg is None else MileageModes.agg
                if alg == MileageAlgorithms.compute:
                    result = mileage_test_compute(df, auto_record, agg, mode)
                else:
                    result = mileage_test_fraud_new(car_id, df, sensors, auto_record, agg, mode, sensor_chart=sensor_speed_chart, force_chart=force_chart )

                if result["msg_skip_big"] == 1:
                    try:
                        ReportService.create_bad_data_record(Car.objects.get(id=car_id), "Обнаружены пропущенные сообщения для пробега", report_query, start_date, end_date, CarBadData.Severity.INFO, CarBadData.Category.PROVIDER_ERROR, [CarBadData.Tag.MOTOHOURS, CarBadData.Tag.PROVIDER])
                    
                    except BaseException as err:
                        logger.error(f"Невозможно создать baddata для mileage отчета для {car_id} (пропуск данных) из-за {err}")
                report_data = {
                    "result": result,
                    "rows_processed": len(df),
                    "calculation_mode": mode,
                    "alg": alg.name,
                    "aggregation_period_minutes": agg
                }
                if result["reports"] and is_save_bad_data:
                    ReportService.create_bad_data_record_from_list(car, result["reports"], report_query)


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