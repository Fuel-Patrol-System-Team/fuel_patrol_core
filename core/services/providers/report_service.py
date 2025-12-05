import logging
import traceback
import json
from typing import Dict, Any, Optional, Tuple, List
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
import polars as pl

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
    """Сервис для управления отчетами и их деталями"""

    @staticmethod
    @transaction.atomic
    def create_report(
        provider_id: str, report_type: str, is_save_bad_data: bool = True
    ) -> Tuple[ReportQuery, ReportQueryDetails]:
        """
        Создает новую заявку на отчет с деталями
        """
        try:

            report_query = ReportQuery.objects.create(
                provider_id_id=provider_id,
                report_type=report_type,
                is_save_bad_data=is_save_bad_data,
                status="created",
            )

            report_details = ReportQueryDetails.objects.create(
                report_query=report_query, start_time=timezone.now()
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
        """
        Завершает отчет успешно
        """
        try:
            end_time = timezone.now()
            start_time = report_query.report_query_details.start_time

            if start_time:
                time_proceed = end_time - start_time
            else:
                time_proceed = timedelta(0)

            serialized_result = serialize_for_json(result_data)

            report_query.status = "completed"
            report_query.save()

            report_details = report_query.report_query_details
            report_details.result = serialized_result
            report_details.end_time = end_time
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
        """
        Завершает отчет с ошибкой
        """
        try:
            end_time = timezone.now()
            start_time = report_query.report_query_details.start_time

            if start_time:
                time_proceed = end_time - start_time
            else:
                time_proceed = timedelta(0)

            traceback_data = {
                "error": error_message,
                "timestamp": timezone.now().isoformat(),
            }

            if exception:
                traceback_data.update(
                    {
                        "exception_type": type(exception).__name__,
                        "exception_message": str(exception),
                        "traceback": traceback.format_exc(),
                    }
                )

            serialized_traceback = serialize_for_json(traceback_data)

            report_query.status = "error"
            report_query.save()

            report_details = report_query.report_query_details
            report_details.traceback = serialized_traceback
            report_details.end_time = end_time
            report_details.time_proceed = time_proceed
            report_details.cars_proceed = cars_proceed
            report_details.cars_skipped = cars_skipped
            report_details.save()

            logger.error(
                f"Отчет {report_query.id} завершен с ошибкой за {time_proceed}: {error_message}"
            )

        except Exception as e:
            logger.error(
                f"Ошибка при завершении отчета с ошибкой {report_query.id}: {e}"
            )
            raise

    @staticmethod
    @transaction.atomic
    def create_bad_data_record(
        car: Car, reason: str, report_query: Optional[ReportQuery] = None
    ) -> None:
        """
        Создает запись о некорректных данных
        """
        try:
            if not report_query or not report_query.is_save_bad_data:
                return

            CarBadData.objects.create(
                car_id=car, reason=reason, datetime=timezone.now()
            )

            logger.warning(f"Создана запись CarBadData для {car.name}: {reason}")

        except Exception as e:
            logger.error(f"Ошибка создания CarBadData для {car.name}: {e}")

    @staticmethod
    @transaction.atomic
    def save_car_reports_batch(leaks_df: pl.DataFrame) -> int:
        """
        Сохраняет результаты утечек в CarReport батчами
        """
        if leaks_df is None or leaks_df.is_empty():
            logger.warning("Нет данных для сохранения в CarReport")
            return 0

        try:

            leaks_records = (
                leaks_df.filter(pl.col("is_leak") == True)
                .to_dicts()
            )

            logger.info(
                f"Найдено {len(leaks_records)} записей с утечками для сохранения в CarReport"
            )

            if not leaks_records:
                logger.warning("Нет записей с is_leak=True для сохранения")
                return 0

            car_reports = []
            saved_count = 0

            for record in leaks_records:
                try:
                    car_id = record["auto"]
                    timestamp = record["timestamp"]
                    speed = record["pos_s"]
                    leak_volume = record["leak"]
                    is_leak = record["is_leak"]

                    logger.debug(
                        f"Обработка записи: car_id={car_id}, timestamp={timestamp}, leak={leak_volume}, is_leak={is_leak}"
                    )

                    car = Car.objects.get(id=car_id)

                    car_report = CarReport(
                        car_id=car,
                        datetime=timestamp,
                        volume=int(leak_volume),
                        speed=float(speed),
                        status=bool(is_leak),
                    )
                    car_reports.append(car_report)

                    if len(car_reports) >= 100:
                        CarReport.objects.bulk_create(car_reports)
                        saved_count += len(car_reports)
                        logger.info(
                            f"Сохранено {len(car_reports)} записей CarReport (батч)"
                        )
                        car_reports = []

                except Car.DoesNotExist:
                    logger.warning(f"Машина {record['auto']} не найдена для CarReport")
                    continue
                except Exception as e:
                    logger.error(f"Ошибка создания CarReport для {record['auto']}: {e}")
                    continue

            if car_reports:
                CarReport.objects.bulk_create(car_reports)
                saved_count += len(car_reports)
                logger.info(
                    f"Сохранено {len(car_reports)} записей CarReport (финальный батч)"
                )

            logger.info(f"Всего сохранено {saved_count} записей в CarReport")
            return saved_count

        except Exception as e:
            logger.error(f"Ошибка сохранения CarReport: {e}", exc_info=True)
            return 0

    @staticmethod
    @transaction.atomic
    def save_car_consumption_batch(norms_df: pl.DataFrame) -> int:
        """
        Сохраняет нормы расхода в CarConsumption
        """
        if norms_df is None or norms_df.is_empty():
            logger.warning("Нет данных для сохранения в CarConsumption")
            return 0

        try:
            consumption_records = []
            saved_count = 0

            for row in norms_df.to_dicts():
                try:
                    car = Car.objects.get(id=row["sl_avto"])

                    consumption = CarConsumption(
                        car_id=car,
                        winter_volume=row.get("norma_rasx_winter"),
                        summer_volume=row.get("norma_rasx_summer"),
                        speed_etalon=row.get("speed_etalon", 60.0),
                        max_fuel=row.get("max_fuel", 2000.0),
                        valid_period=row.get("period"),
                    )
                    consumption_records.append(consumption)

                    if len(consumption_records) >= 50:
                        CarConsumption.objects.bulk_create(consumption_records)
                        saved_count += len(consumption_records)
                        consumption_records = []
                        logger.debug(f"Сохранено {saved_count} записей CarConsumption")

                except Car.DoesNotExist:
                    logger.warning(
                        f"Машина {row['sl_avto']} не найдена для CarConsumption"
                    )
                    continue
                except Exception as e:
                    logger.error(
                        f"Ошибка создания CarConsumption для {row['sl_avto']}: {e}"
                    )
                    continue

            if consumption_records:
                CarConsumption.objects.bulk_create(consumption_records)
                saved_count += len(consumption_records)

            logger.info(f"Сохранено {saved_count} записей в CarConsumption")
            return saved_count

        except Exception as e:
            logger.error(f"Ошибка сохранения CarConsumption: {e}")
            return 0
