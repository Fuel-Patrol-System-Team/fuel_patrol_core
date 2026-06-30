import logging
import traceback
from typing import Dict, Any, List, Optional, Tuple, Union
from django.db import transaction
from datetime import timedelta, datetime, timezone
import polars as pl
import pytz

from core.models import (
    ReportQuery,
    ReportQueryDetails,
    CarBadData,
    Car,
    CarReport,
    CarConsumption,
)
from core.services.providers.json_serializer import serialize_for_json

logger = logging.getLogger(__name__)


class ReportService:
    @staticmethod
    @transaction.atomic
    def create_report(
            provider_id: str,
            report_type: str,
            is_save_bad_data: bool = True,
    ) -> Tuple[ReportQuery, ReportQueryDetails]:
        try:
            report_query = ReportQuery.objects.create(
                provider_id_id=provider_id,
                report_type=report_type,
                is_save_bad_data=is_save_bad_data,
                status="created",
            )
            report_details = ReportQueryDetails.objects.create(
                report_query=report_query,
                start_time=datetime.now().astimezone(pytz.utc),
            )
            logger.info(f"Создан отчет {report_query.id} типа {report_type}")
            return report_query, report_details
        except Exception as e:
            logger.error(f"Ошибка создания отчета: {e}")
            raise

    @staticmethod
    @transaction.atomic
    def complete_report_success(
            report_query: ReportQuery,
            result_data: Dict[str, Any],
            cars_proceed: int = 1,
            cars_skipped: int = 0,
    ) -> None:
        try:
            end_time     = datetime.now().astimezone()
            start_time   = report_query.report_query_details.start_time
            time_proceed = (end_time - start_time) if start_time else timedelta(0)

            report_query.status = "completed"
            report_query.save()

            report_details             = report_query.report_query_details
            report_details.result      = serialize_for_json(result_data)
            report_details.end_time    = end_time
            report_details.time_proceed = time_proceed
            report_details.cars_proceed = cars_proceed
            report_details.cars_skipped = cars_skipped
            report_details.save()

            logger.info(f"Отчет {report_query.id} завершен успешно за {time_proceed}")
        except Exception as e:
            logger.error(f"Ошибка завершения отчета {report_query.id}: {e}")
            raise

    @staticmethod
    @transaction.atomic
    def complete_report_error(
            report_query: ReportQuery,
            error_message: str,
            exception: Optional[Exception] = None,
            cars_proceed: int = 0,
            cars_skipped: int = 1,
    ) -> None:
        try:
            end_time     = datetime.now(pytz.utc)
            start_time   = report_query.report_query_details.start_time
            time_proceed = (end_time - start_time) if start_time else timedelta(0)

            traceback_data: Dict[str, Any] = {
                "error":     error_message,
                "timestamp": datetime.now(pytz.utc).isoformat(),
            }
            if exception:
                traceback_data.update({
                    "exception_type":    type(exception).__name__,
                    "exception_message": str(exception),
                    "traceback":         traceback.format_exc(),
                })

            report_query.status = "error"
            report_query.save()

            report_details           = report_query.report_query_details
            report_details.traceback = serialize_for_json(traceback_data)
            report_details.end_time  = end_time
            report_details.time_proceed = time_proceed
            report_details.cars_proceed = cars_proceed
            report_details.cars_skipped = cars_skipped
            report_details.save()

            logger.error(
                f"Отчет {report_query.id} завершен с ошибкой за {time_proceed}: {error_message}"
            )
        except Exception as e:
            logger.error(f"Ошибка при завершении отчета с ошибкой {report_query.id}: {e}")
            raise
        
    @staticmethod
    @transaction.atomic
    def create_bad_data_record_from_list(
            car: Car,
            reports: List[Any],
            report_query: Optional[ReportQuery] = None,
            
    ) -> int | None:
        if not report_query or not report_query.is_save_bad_data:
            return None

        period_info   = ""
        bad_reports = []
        for report in reports:

            tags = report["tags"]
            if tags is None:
                tags = [CarBadData.Tag.SERVER]

            valid_tags = ReportService._validate_tags(tags)

            bad_data = CarBadData.objects.create(
                car_id=car,
                reason=f"{report["message"]}",
                datetime=datetime.now().astimezone(),
                severity=report["severity"],
                category=report["category"],
                tags=valid_tags,
                report_query=report_query,
            )

            logger.error(
                f"[{report["severity"].upper()}][{report["category"]}] tags={valid_tags} "
                f"CarBadData для {car.name}: {report["message"]}{period_info}"
            )
        if len(bad_reports) > 0:
            CarBadData.objects.bulk_create(
                bad_reports
            )
        return len(bad_reports)

    @staticmethod
    @transaction.atomic
    def create_bad_data_record(
            car: Car,
            reason: str,
            report_query: Optional[ReportQuery] = None,
            start_date: Optional[Union[datetime, str]] = None,
            end_date: Optional[Union[datetime, str]] = None,
            severity: str = CarBadData.Severity.ERROR,
            category: str = CarBadData.Category.UNKNOWN,
            tags: Optional[list[str]] = None,
    ) -> Optional[CarBadData]:
        if not report_query or not report_query.is_save_bad_data:
            return None

        period_info   = ""
        start_date_dt = ReportService._parse_date(start_date, "start_date")
        end_date_dt   = ReportService._parse_date(end_date, "end_date")

        if start_date_dt and end_date_dt:
            period_info = f" за период с {start_date_dt:%d.%m.%Y} по {end_date_dt:%d.%m.%Y}"
        elif start_date_dt:
            period_info = f" за период с {start_date_dt:%d.%m.%Y}"
        elif end_date_dt:
            period_info = f" за период до {end_date_dt:%d.%m.%Y}"
        elif start_date or end_date:
            period_info = (
                f" за период {start_date or ''} {f'- {end_date}' if end_date else ''}".strip()
            )

        if tags is None:
            tags = [CarBadData.Tag.SERVER]

        valid_tags = ReportService._validate_tags(tags)

        bad_data = CarBadData.objects.create(
            car_id=car,
            reason=f"{reason}{period_info}",
            datetime=datetime.now().astimezone(),
            severity=severity,
            category=category,
            tags=valid_tags,
            report_query=report_query,
        )

        logger.error(
            f"[{severity.upper()}][{category}] tags={valid_tags} "
            f"CarBadData для {car.name}: {reason}{period_info}"
        )

        return bad_data


    @staticmethod
    def _validate_tags(tags: Optional[list[str]]) -> list[str]:
        if not tags:
            return []

        valid_values = {t.value for t in CarBadData.Tag}
        result, invalid = [], []

        for t in tags:
            if t in valid_values:
                result.append(t)
            else:
                invalid.append(t)

        if invalid:
            logger.warning(f"Переданы неизвестные теги CarBadData, проигнорированы: {invalid}")

        return result

    @staticmethod
    def _parse_date(
            value: Optional[Union[datetime, str]],
            field_name: str,
    ) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                from dateutil import parser
                return parser.parse(value)
            except (ValueError, TypeError):
                logger.warning(f"Не удалось распарсить {field_name}: {value!r}")
                return None
        logger.warning(f"Неизвестный тип {field_name}: {type(value)}")
        return None

        

    @staticmethod
    @transaction.atomic
    def save_car_reports_batch(leaks_df: pl.DataFrame) -> int:
        if leaks_df is None or leaks_df.is_empty():
            logger.warning("Нет данных для сохранения в CarReport")
            return 0

        try:
            leaks_records = leaks_df.filter(pl.col("is_leak") == True).to_dicts()

            if not leaks_records:
                logger.warning("Нет записей с is_leak=True для сохранения")
                return 0

            car_reports = []
            saved_count = 0

            for record in leaks_records:
                try:
                    car = Car.objects.get(id=record["auto"])
                    car_reports.append(CarReport(
                        car_id=car,
                        datetime=record["timestamp"].replace(tzinfo=timezone.utc),
                        volume=int(record["leak"]),
                        speed=float(record["pos_s"]),
                        status=bool(record["is_leak"]),
                        picked_by=record["picked_by"]
                    ))
                    if len(car_reports) >= 100:
                        CarReport.objects.bulk_create(car_reports)
                        saved_count += len(car_reports)
                        car_reports  = []
                except Car.DoesNotExist:
                    logger.warning(f"Машина {record['auto']} не найдена для CarReport")
                except Exception as e:
                    logger.error(f"Ошибка создания CarReport для {record['auto']}: {e}")

            if car_reports:
                CarReport.objects.bulk_create(car_reports)
                saved_count += len(car_reports)

            logger.info(f"Всего сохранено {saved_count} записей в CarReport")
            return saved_count

        except Exception as e:
            logger.error(f"Ошибка сохранения CarReport: {e}", exc_info=True)
            return 0

    @staticmethod
    @transaction.atomic
    def save_car_consumption_batch(norms_df: pl.DataFrame) -> int:
        if norms_df is None or norms_df.is_empty():
            logger.warning("Нет данных для сохранения в CarConsumption")
            return 0

        try:
            consumption_records = []
            saved_count = 0

            for row in norms_df.to_dicts():
                try:
                    car = Car.objects.get(id=row["sl_avto"])
                    consumption_records.append(CarConsumption(
                        car_id=car,
                        winter_volume=row.get("norma_rasx_winter"),
                        summer_volume=row.get("norma_rasx_summer"),
                        speed_etalon=row.get("speed_etalon", 60.0),
                        max_fuel=row.get("max_fuel", 2000.0),
                        valid_period=row.get("period"),
                    ))
                    if len(consumption_records) >= 50:
                        CarConsumption.objects.bulk_create(consumption_records)
                        saved_count         += len(consumption_records)
                        consumption_records  = []
                except Car.DoesNotExist:
                    logger.warning(f"Машина {row['sl_avto']} не найдена для CarConsumption")
                except Exception as e:
                    logger.error(f"Ошибка создания CarConsumption для {row['sl_avto']}: {e}")

            if consumption_records:
                CarConsumption.objects.bulk_create(consumption_records)
                saved_count += len(consumption_records)

            logger.info(f"Сохранено {saved_count} записей в CarConsumption")
            return saved_count

        except Exception as e:
            logger.error(f"Ошибка сохранения CarConsumption: {e}")
            return 0