from datetime import datetime, timedelta
import logging
from typing import Literal, Optional, Tuple, Dict, Any
import polars as pl

from core.admin import CarFuelReport
from core.helpers.fuel import make_fuel_spent, make_primary_fast, preprocess_basic_one
from core.models import Car, DataProvider, ReportQuery
from core.services.providers.car_data_service import CarDataService
from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.report_service import ReportService


logger = logging.getLogger(__name__)
class FuelReportService:
    
    @staticmethod
    def build_right_history(df: pl.DataFrame, fillings: pl.DataFrame):
        df = df.join( fillings, on="timestamp", how="left")
        return df
    @staticmethod
    def build_right_history_no_refuel(df: pl.DataFrame):
        df = df.with_columns(pl.lit(0).alias("refill"))
        return df
    @staticmethod
    def make_reports_from_df(df: pl.DataFrame):
        slice = df.select(["timestamp", "fuel_start", "fuel_end", "auto", "refill"])
        slice = slice.rename({"timestamp": "start_moment", "auto": "car_id_id", "refill": "fuel_filled"})
        slice = slice.with_columns((pl.col("start_moment") + timedelta(1)).alias("end_moment"))
        slice_dict = slice.to_dicts()
        reports = []
        for item in slice_dict:
            t_report = CarFuelReport(**item)
            reports.append(t_report)
        CarFuelReport.objects.bulk_create(
            reports
        )
        return len(reports)
    
    # TODO: написать для fuel_consumpt с dtime, убранными сообщениями и прочим
    @staticmethod
    def fuel_spent_calculate_by_consumpt(result: pl.DataFrame):
        return result 
    
    @staticmethod
    def _fuel_spent_agg(df: pl.DataFrame, refuel: float):
        return df.group_by("auto").agg([
                pl.first("fuel_first").alias("fuel_start"),
                pl.last("fuel_last").alias("fuel_end"),
                pl.col("fuel_spent").clip(upper_bound=0).abs().sum().alias("fuel_spent"),
                pl.lit(refuel).alias("refuel")
            ])
    
    @staticmethod
    def fuel_spent_calculate_instant(result: pl.DataFrame, fillings: pl.DataFrame | None, cars: dict[str, Any], agg: int | None):
        is_primary, primary = make_primary_fast(result)
        result, reports = preprocess_basic_one(result, cars, primary, is_fuel_processing=True)
        if not is_primary:
            result = result.with_columns(
                pl.lit(0).alias("calc_sensors_fuel_level"),
                pl.lit(0).alias("spent_fuel")
            )
        # elif result["fuel_consumpt"].is_not_null().any():
        #    result_consumpt = result.with_columns(pl.lit(0).alias("calc_sensors_fuel_level"), pl.col("fuel_consumpt").diff().mul(-1).alias("spent_fuel"))
        refuel = 0
        return_fillings = []
        if fillings is not None and fillings.shape[0] > 0:
            refuel = fillings["refill"].sum()
            return_fillings = fillings.with_columns(pl.col("timestamp").dt.to_string("iso:strict")).to_dicts()
        spent_report = [] 
        if agg is not None:
            spent_report = result.group_by_dynamic(
                index_column="timestamp", group_by="auto", every=f"{agg}m"
            ).agg(
                pl.col("calc_sensors_fuel_level").first().alias("fuel_start"),
                pl.col("calc_sensors_fuel_level").last().alias("fuel_last"),
                pl.col("spent_fuel").sum().clip(upper_bound=0).abs().alias("fuel_spent"),
                pl.col("fuel_consumpt_spent").sum(),
            ).with_columns(pl.col("timestamp").dt.replace_time_zone("UTC")).to_dicts()
        result = result.group_by_dynamic(
            index_column="timestamp", group_by="auto", every="1h"
        ).agg(
            pl.col("calc_sensors_fuel_level").first().alias("fuel_first"),
            pl.col("calc_sensors_fuel_level").last().alias("fuel_last"),
            pl.col("fuel_consumpt_spent").sum().alias("fuel_consumpt_spent"),
            pl.col("fuel_consumpt_first").first(),
            pl.col("fuel_consumpt_last").last(),
            pl.col("spent_fuel_t").first().alias("fuel_spent")
        )
        result_agg = result.group_by("auto").agg([
            pl.first("fuel_first").alias("fuel_start"),
            pl.last("fuel_last").alias("fuel_end"),
            pl.col("fuel_spent").sub(refuel).clip(upper_bound=0).abs().first().alias("fuel_spent"),
            pl.col("fuel_consumpt_spent").sum().alias("fuel_consumpt_spent"),
            pl.col("fuel_consumpt_first").first(),
            pl.col("fuel_consumpt_last").last(),
            pl.lit(refuel).alias("refuel")
        ]).to_dicts()
        if len(result_agg) == 0:
            spent_result_calc = {
                "fuel_start": 0,
                "fuel_end": 0,
                "fuel_spent": 0,
                "fuel_consumpt_spent": 0,
                "fuel_consumpt_start": 0,
                "fuel_consumpt_end": 0,
                "refuel": refuel,
                "agg": [],
                "fillings": [],
                "count": 0,
            }
        else:
            spent_result_calc = result_agg[0]
        if spent_result_calc["fuel_consumpt_spent"] > 0:
            spent_result_calc["fuel_spent_final"] = spent_result_calc["fuel_consumpt_spent"]
            spent_result_calc["fuel_spent_final_sensor"] = "fuel_consumpt"
        else:
            spent_result_calc["fuel_spent_final"] = spent_result_calc["fuel_spent"]
            spent_result_calc["fuel_spent_final_sensor"] = "calc_sensors_fuel_level"

        
        return {
            **spent_result_calc,
            "agg": spent_report,
            "fillings": return_fillings,
            "count": 1,
        }, []
    @staticmethod
    def calculate_fuelspent(
            car_id: str,
            agg: Optional[int] = None,
            start_date: datetime = datetime.now(),
            end_date: datetime = datetime.now(),
            is_save_bad_data: bool = True,
            parser: Optional[GlonassGeneralProvider] = None,
            force_chart = False
    ) -> Tuple[Dict[str, Any], int]:
        """
        Выполняет расчет пробега с созданием отчета
        Возвращает (результат, статус_код)
        """
        report_query = None

        try:
            car, provider_obj = FuelReportService._get_car_and_provider(car_id)
            if not car or not provider_obj:
                return {"error": "Автомобиль или провайдер не найдены"}, 400

            report_query, report_details = ReportService.create_report(
                provider_id=str(provider_obj.id),
                report_type=ReportQuery.ReportType.FUEL,
                is_save_bad_data=is_save_bad_data
            )


            validation_error = FuelReportService._validate_dates(start_date, end_date)
            if validation_error:
                ReportService.complete_report_error(report_query, validation_error)
                return {"error": validation_error}, 400


            provider = parser if parser is not None else GlonassGeneralProvider(None, car, provider_obj, start_date, end_date, "fuel")
            if not provider.authenticate():
                error_msg = "Не удалось авторизоваться у провайдера"
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 401


            status, df = provider.parse_raw_data("fuel", True, car)
            fillings = provider.parse_refill_data_full(car, start_date, end_date)
            if df is None or df.is_empty() or not status:

                result = make_fuel_spent(0, 0, 0, [], [])


                ReportService.create_bad_data_record(
                    car,
                    "Нет данных за указанный период",
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
            if isinstance(df, pl.DataFrame):
                auto = CarDataService.prepare_auto_data(car)
                auto_record = auto.filter(pl.col("auto") == str(car_id)).to_dicts()[0]
                result, reports = FuelReportService.fuel_spent_calculate_instant(df, fillings, auto_record, agg)
                report_data = {
                    "result": result,
                    "rows_processed": len(df),
                    "aggregation_period_minutes": agg
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

