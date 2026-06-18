import logging
from typing import Literal, Optional, List, cast

import requests
from django.db.models import Q
import polars as pl
from datetime import datetime, timedelta, timezone
import pytz
from celery import chain, shared_task

from app.celery import app as celery_app

from core.admin import CarPrimary, SensorsValues
from core.helpers.alert import get_tg_user_for_org_user, get_unsent_alerts_by_org, build_digest_message, \
    save_bad_data_alerts_bulk
from core.helpers.fuel import fuel_spent_calculate
from core.models import (
    Alert,
    AlertSubscription,
    CarBadData,
    CarMileageReport,
    ComputedData,
    ParsingCarStats,
    ReportQuery,
    Organization,
    Car,
    DataProvider,
    TelegramUser,
)

import glob
import os
import psutil
import time

from core.services.providers.fuelreport_service import FuelReportService
from core.services.notifications.tg_notifier import send_telegram_message
from core.services.providers import leaks_service
from core.services.providers.car_consumption_service import CarConsumptionService
from core.services.providers.car_data_service import CarDataService
from core.services.providers.computed_data_service import ComputedDataService
from core.services.providers.filtering_service import FilteringService

from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.glonass.glonassoft_terminal_messages_parser import GlonassSoftTerminalMessagesParser

from core.services.providers.mileage_calculation_service import MileageAlgorithms, MileageCalculationService
from core.services.providers.norms_service import NormsService
from core.services.providers.provider_factory import ProviderFactory
from core.services.providers.report_service import ReportService
from core.services.providers.rpm_auto_calculation_service import RpmAutoCalculationService
from core.services.providers.vehicle_sync_service import VehicleSyncService
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration

from core.helpers.alert import (
    save_system_alert,
    save_leak_alerts_from_rows,
    save_fraud_alert,
)

sentry_sdk.init(
    dsn="https://effe72e389d74952bc590b92f3bb5359@api.sherlog.noodev.ru/26",
    integrations=[CeleryIntegration()],
    send_default_pii=True
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

celery_app.conf.task_concurrency = 4
BATCH_SIZE = 500_000


def _log_resources(method: str) -> None:
    process = psutil.Process()
    ram_mb = process.memory_info().rss / 1024 ** 2
    cpu_percent = psutil.cpu_percent()
    logger.info(f"[{method}] RAM: {ram_mb:.2f} MB, CPU: {cpu_percent:.1f}%")


def _timeit(method: str, func):
    start_time = time.time()
    result = func()
    elapsed = time.time() - start_time
    logger.info(f"[{method}] Выполнено за {elapsed:.2f} сек")
    return result


def _cleanup_temp_files(base_dir, report_id, timestamp):
    pattern = str(base_dir / f"*_{report_id}_{timestamp}_*.csv*")
    for file_path in glob.glob(pattern):
        try:
            os.remove(file_path)
            logger.info(f"Удалён временный файл: {file_path}")
        except Exception as e:
            logger.error(f"Ошибка удаления {file_path}: {e}")


@shared_task(
    bind=True,
    name="sync_vehicles_task",
    soft_time_limit=600,
    time_limit=650,
    priority=5,
    acks_late=True,
)
def sync_vehicles_task(self, provider_id: str, organization_id: str):
    report_query = None

    try:
        logger.info(f"Запуск синхронизации машин для провайдера {provider_id}")

        provider = DataProvider.objects.get(id=provider_id)
        organization = Organization.objects.get(id=organization_id)

        report_query, report_details = ReportService.create_report(
            provider_id=provider_id, report_type="vehicles", is_save_bad_data=True
        )

        sync_service = VehicleSyncService(provider, organization)
        result = sync_service.sync_vehicles(report_query)

        if result["success"]:
            logger.info(
                f"Синхронизация завершена: "
                f"всего {result['total_vehicles']}, "
                f"создано {result['created']}, "
                f"обновлено {result['updated']}, "
                f"ошибок {result['failed']}, "
                f"деактивировано {result['inactivated']}"
            )
            save_system_alert(
                organization=provider.org_id,
                message=(
                    f"Синхронизация машин завершена. "
                    f"Ошибок: {result['failed']}, "
                    f"обновлено: {result['updated']}, "
                    f"создано: {result['created']}, "
                    f"деактивировано: {result['inactivated']}"
                ),
                event_datetime=datetime.utcnow(),
            )
        else:
            logger.error(f"Ошибка синхронизации: {result['error']}")

        return result

    except DataProvider.DoesNotExist:
        error_msg = f"Провайдер {provider_id} не найден"
        logger.error(error_msg)
        if report_query:
            ReportService.complete_report_error(report_query, error_msg)
        return {"success": False, "error": error_msg}

    except Organization.DoesNotExist:
        error_msg = f"Организация {organization_id} не найдена"
        logger.error(error_msg)
        if report_query:
            ReportService.complete_report_error(report_query, error_msg)
        return {"success": False, "error": error_msg}

    except Exception as e:
        error_msg = f"Неожиданная ошибка при синхронизации: {str(e)}"
        logger.error(error_msg, exc_info=True)
        if report_query:
            ReportService.complete_report_error(report_query, error_msg, e)
        return {"success": False, "error": error_msg}


@shared_task(
    bind=True,
    name="process_single_car_data_task",
    soft_time_limit=1800,
    time_limit=1850,
    priority=5,
    rate_limit="1/s",
)
def process_single_car_data_task(
        self,
        car_id: str,
        provider_id: str,
        report_query_id: str,
        start_date: str = None,
        end_date: str = None,
):
    report_query = None
    try:
        logger.info(f"=== НАЧАЛО ОБРАБОТКИ С ОТЧЕТОМ ДЛЯ МАШИНЫ {car_id} ===")

        car = Car.objects.get(id=car_id)
        provider = DataProvider.objects.get(id=provider_id)
        report_query = ReportQuery.objects.get(id=report_query_id)

        is_save_bad_data = report_query.is_save_bad_data

        data_provider = ProviderFactory.create_provider(
            provider.metadata, provider_type="data", car_id=car_id
        )

        start_dt = (
            datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            if start_date
            else None
        )
        end_dt = (
            datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if end_date
            else None
        )

        logger.info(f"ЭТАП 1: Получение сырых данных для машины {car_id}...")
        parser = GlonassGeneralProvider(None, car, provider, start_dt, end_dt, "fuel")
        status, raw_df = parser.parse_raw_data("fuel", return_df=True)

        if not status:
            error_msg = f"Не удалось создать провайдер данных для машины {car_id}"
            logger.error(error_msg)
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
            return {"success": status, "car_id": car_id, "error": raw_df[0]}

        if raw_df is None or raw_df.is_empty():
            error_msg = f"Нет сырых данных для машины {car_id}"
            logger.warning(error_msg)
            ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date)
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
            return {"success": False, "car_id": car_id, "error": error_msg}

        logger.info(f"Получено {len(raw_df)} строк сырых данных")

        auto_df = CarDataService.prepare_auto_data(car)

        logger.info("ЭТАП 2: Вычисление первичных показателей...")
        primary_df = CarDataService.calculate_primary_single(raw_df, auto_df)

        if primary_df is None or primary_df.is_empty():
            error_msg = f"Не удалось вычислить первичные показатели для машины {car_id}"
            logger.error(error_msg)
            ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date, CarBadData.Severity.INFO, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
            return {"success": False, "car_id": car_id, "error": error_msg}

        logger.info("ЭТАП 2.1: Сохранение первичных показателей в БД...")
        primary_saved = CarDataService.save_primary_to_db(car, primary_df, start_dt, end_dt)
        if primary_saved:
            logger.info(f"Первичные показатели сохранены для машины {car_id}")
        else:
            logger.warning(f"Не удалось сохранить первичные показатели для машины {car_id}")

        logger.info("ЭТАП 3: Расчет норм расхода топлива...")
        norms_df = NormsService.calculate_norms_single(raw_df, primary_df, auto_df)
        if norms_df is not None:
            init_number = norms_df.shape[0]
            norms_df = norms_df.filter(pl.col("norma_rasx_summer") > 5)

            end_number = norms_df.shape[0]
            if end_number < init_number:
                error_msg = f"Машина {car_id} была отброшена - недостаточно данных для расчета норм"
                ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date, CarBadData.Severity.WARNING, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS, CarBadData.Tag.PROVIDER])
                logger.error(error_msg)
                return {"success": False, "car_id": car_id, "error": error_msg}

        if norms_df is None or norms_df.is_empty():
            error_msg = f"Не удалось рассчитать нормы для машины {car_id}"
            logger.error(error_msg)
            ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
            norms_df = None
            return {"success": False, "car_id": car_id, "error": error_msg}

        logger.info("ЭТАП 4: Расчет утечек топлива...")
        leak_service = leaks_service.LeaksService()
        leaks_result, intermediate_df = None, None
        if norms_df is not None and not norms_df.is_empty():
            leaks_result, intermediate_df, reports = leak_service.compute_leaks(
                auto_df=auto_df,
                data_df=raw_df,
                primary_df=primary_df,
                norma_df=norms_df,
                is_save_bad_data=is_save_bad_data,
                is_filter_bad_data=True,
            )
            if is_save_bad_data:
                ReportService.create_bad_data_record_from_list(car, reports, report_query)

        if leaks_result is None or leaks_result.is_empty():
            error_msg = f"Не удалось рассчитать утечки для машины {car_id}"
            logger.error(error_msg)
            ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
            leaks_result = None

        logger.info("ЭТАП 5: Фильтрация результатов утечек...")
        if leaks_result is not None:
            filtering_service = FilteringService()
            filtered_leaks = filtering_service.apply_filters(leaks_result)
        else:
            filtered_leaks = None

        logger.info("ЭТАП 6: Сохранение результатов в БД...")
        saved_reports = 0
        saved_consumptions = 0
        saved_primary = 0

        if norms_df is not None and not norms_df.is_empty():
            saved_consumptions = ReportService.save_car_consumption_batch(norms_df)
            logger.info(f"Сохранено {saved_consumptions} записей норм расхода")

        if filtered_leaks is not None and not filtered_leaks.is_empty():
            saved_reports = ReportService.save_car_reports_batch(filtered_leaks)
            logger.info(f"Сохранено {saved_reports} записей утечек")

        logger.info("ЭТАП 7: Формирование отчета...")

        primary_rows = (
            len(primary_df)
            if primary_df is not None and not primary_df.is_empty()
            else 0
        )
        norms_rows = (
            len(norms_df) if norms_df is not None and not norms_df.is_empty() else 0
        )
        leaks_rows = (
            len(filtered_leaks)
            if filtered_leaks is not None and not filtered_leaks.is_empty()
            else 0
        )

        result_data = {
            "car_id": car_id,
            "car_name": car.name,
            "processing_stats": {
                "raw_rows": (
                    len(raw_df) if raw_df is not None and not raw_df.is_empty() else 0
                ),
                "primary_rows": primary_rows,
                "primary_saved": primary_saved,
                "norms_rows": norms_rows,
                "leaks_rows": leaks_rows,
                "saved_reports": saved_reports,
                "saved_consumptions": saved_consumptions,
            },
            "timestamps": {
                "start": start_dt.isoformat() if start_dt else None,
                "end": end_dt.isoformat() if end_dt else None,
                "processed": datetime.now().astimezone().isoformat(),
            },
        }

    except Car.DoesNotExist:
        error_msg = f"Машина {car_id} не найдена"
        logger.error(error_msg)
        if report_query:
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
        return {"success": False, "car_id": car_id, "error": error_msg}

    except DataProvider.DoesNotExist:
        error_msg = f"Провайдер {provider_id} не найден"
        logger.error(error_msg)
        if report_query:
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
        return {"success": False, "car_id": car_id, "error": error_msg}

    except ReportQuery.DoesNotExist:
        error_msg = f"Отчет {report_query_id} не найден"
        logger.error(error_msg)
        return {"success": False, "car_id": car_id, "error": error_msg}

    except Exception as e:
        error_msg = f"Неожиданная ошибка при обработке машины {car_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        if report_query:
            ReportService.complete_report_error(
                report_query, error_msg, exception=e, cars_skipped=1
            )
        return {"success": False, "car_id": car_id, "error": error_msg}

@shared_task(bind=True)
def calculate_auto_rpm(self, provider_id: str, start_time: str | None, is_save_bad_data = False):
    provider = DataProvider.objects.filter(id=provider_id).first()
    cars = provider.cars.select_related("parsingcar_stats")
    mapping = [[str(car.id), start_time,is_save_bad_data ] for car in cars]
    try:
        tasks = chain(
            *(calculate_auto_rpm_single.si(m[0], m[1], m[2]) for m in mapping)
            
        ).apply_async()
    except BaseException as err:
        logger.error(f"Error {err}")


    
@shared_task(bind=True)
def calculate_auto_rpm_single(self, car_id: str, start_time: str | None, is_save_bad_data = False):
    time_start = datetime.now()
    if start_time is None:
        time_start = datetime.now()
    else:
        time_start = datetime.fromisoformat(start_time)
    
    try:
        service = RpmAutoCalculationService()
        status, result = service.try_calculate_rpms(car_id, time_start, is_save_bad_data=is_save_bad_data)
        if status:
            pass

    except BaseException as err:
        logger.error(f"Ошибка при расчетах: {err}")
        raise BaseException(err)
    

    

@shared_task(bind=True)
def calculate_primary_cron(self, provider_name: str, is_save_bad_data=False):
    datetime_now = datetime.now()
    provider = DataProvider.objects.get(name=provider_name)

    cars_primary = provider.cars.select_related("carprimary").filter(carprimary__isnull=True)

    if len(cars_primary) != 0:
        datetime_primary = datetime_now - timedelta(days=45)
        parser = GlonassGeneralProvider(cars_primary, None, provider, datetime_primary, datetime_now)
        for car in cars_primary:
            try:
                is_sensor = len(SensorsValues.objects.filter(car_id__id=car.id, is_active=True).select_related("key").filter(key__key="calc_sensors_fuel_level"))
                if is_sensor == 0:
                    continue
                auto_data = CarDataService.prepare_auto_data(car)
                status, df = parser.parse_raw_data("fuel", True, car)

                if status and isinstance(df, pl.DataFrame):
                    primary = CarDataService.calculate_primary_single(df, auto_data)
                    if primary is None or primary.is_empty():
                        error_msg = f"Не удалось вычислить первичные показатели для машины {car.id}"
                        logger.error(error_msg)
                        report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.PRIMARY,
                            is_save_bad_data=is_save_bad_data
                        )
                        ReportService.create_bad_data_record(car, error_msg, report_query, datetime_primary, datetime_now, CarBadData.Severity.INFO, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
                        ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                        continue
                    logger.info("ЭТАП 2.1: Сохранение первичных показателей в БД...")
                    primary_saved = CarDataService.save_primary_to_db(car, primary, datetime_now, datetime_primary)
                    if primary_saved:
                        logger.info(f"Первичные показатели сохранены для машины {car.id}")
                    else:
                        logger.warning(f"Не удалось сохранить первичные показатели для машины {car.id}")
            except Exception as err:
                report_query, report_details = ReportService.create_report(
                    provider_id=str(provider.id),
                    report_type=ReportQuery.ReportType.PRIMARY,
                    is_save_bad_data=is_save_bad_data,
                )
                error_msg = f"Внутренняя ошибка {err} для {car.id}"
                ReportService.create_bad_data_record(car, error_msg, report_query, datetime_primary, datetime_now, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue


@shared_task(bind=True)
def calculate_stats_fuel_cron_one(self, provider_name: str, car_id: str, force=False, is_save_bad_data=False):
    
    datetime_now = datetime.now()
    provider = DataProvider.objects.get(name=provider_name)

    car = Car.objects.filter(id=car_id).first()
    if car is None:
        logger.warning(f"Машина не найдена {car_id}")
        return
    if car is not None:
        datetime_for_stats = datetime_now - timedelta(days=180)
        # primary computing
        parser = GlonassGeneralProvider([car], None, provider, datetime_for_stats, datetime_now, default_period_days=30)
        try:
            is_sensor = len(SensorsValues.objects.filter(car_id__id=car.id, is_active=True).select_related("key").filter(key__key="calc_sensors_fuel_level"))
            if is_sensor == 0:
                return
            auto_data = CarDataService.prepare_auto_data(car)
            status, df = parser.parse_raw_data("fuel", True, car)

            if status and isinstance(df, pl.DataFrame):
                primary = CarDataService.calculate_primary_single(df, auto_data)
                if primary is None or primary.is_empty():
                    error_msg = f"Не удалось вычислить первичные показатели для машины {car.id}"
                    logger.error(error_msg)
                    report_query, report_details = ReportService.create_report(
                        provider_id=str(provider.id),
                        report_type=ReportQuery.ReportType.PRIMARY,
                        is_save_bad_data=is_save_bad_data
                    )
                    ReportService.create_bad_data_record(car, error_msg, report_query, datetime_for_stats, datetime_now)
                    ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                    return
                logger.info("ЭТАП 2.1: Сохранение первичных показателей в БД...")
                primary_saved = CarDataService.save_primary_to_db(car, primary, datetime_now, datetime_for_stats)
                if primary_saved:
                    logger.info(f"Первичные показатели сохранены для машины {car.id}")
                else:
                    logger.warning(f"Не удалось сохранить первичные показатели для машины {car.id}")
                logger.info("ЭТАП 3.0: Вычисление норм в БД...")
                norms = NormsService.calculate_norms_single(df, primary, auto_data)
                if norms is None or norms.is_empty():
                    error_msg = f"Не удалось вычислить нормы показатели для машины (нет записей) {car.id}"
                    logger.error(error_msg)
                    report_query, report_details = ReportService.create_report(
                        provider_id=str(provider.id),
                        report_type=ReportQuery.ReportType.NORMS,
                        is_save_bad_data=is_save_bad_data
                    )
                    ReportService.create_bad_data_record(car, error_msg, report_query, datetime_for_stats, datetime_now, CarBadData.Severity.WARNING, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
                    ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                    return
                logger.info("ЭТАП 3.1: Сохранение норм в БД...")
                norms_saved = CarConsumptionService.save_consumption_rates(norms, car)
                if norms_saved:
                    logger.info(f"Показатели норм сохранены для машины {car.id}")
                else:
                    logger.warning(f"Не удалось сохранить первичные показатели для машины {car.id}")

        except Exception as err:
            report_query, report_details = ReportService.create_report(
                provider_id=str(provider.id),
                report_type=ReportQuery.ReportType.PRIMARY,
                is_save_bad_data=is_save_bad_data
            )
            error_msg = f"Внутренняя ошибка {err} для {car.id}"
            ReportService.create_bad_data_record(car, error_msg, report_query, datetime_for_stats, datetime_now, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)


@shared_task(bind=True)
def calculate_stats_fuel_cron(self, provider_name: str, is_save_bad_data=False):
    datetime_now = datetime.now()
    provider = DataProvider.objects.get(name=provider_name)

    cars_primary = provider.cars.select_related("carprimary").filter(carprimary__isnull=True)

    if len(cars_primary) != 0:
        datetime_for_stats = datetime_now - timedelta(days=365)
        parser = GlonassGeneralProvider(cars_primary, None, provider, datetime_for_stats, datetime_now)
        for car in cars_primary:
            try:
                is_sensor = len(SensorsValues.objects.filter(car_id__id=car.id, is_active=True).select_related("key").filter(key__key="calc_sensors_fuel_level"))
                if is_sensor == 0:
                    continue
                auto_data = CarDataService.prepare_auto_data(car)
                status, df = parser.parse_raw_data("fuel", True, car)

                if status and isinstance(df, pl.DataFrame):
                    primary = CarDataService.calculate_primary_single(df, auto_data)
                    if primary is None or primary.is_empty():
                        error_msg = f"Не удалось вычислить первичные показатели для машины {car.id}"
                        logger.error(error_msg)
                        report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.PRIMARY,
                            is_save_bad_data=is_save_bad_data
                        )
                        ReportService.create_bad_data_record(car, error_msg, report_query, datetime_for_stats, datetime_now)
                        ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                        continue
                    logger.info("ЭТАП 2.1: Сохранение первичных показателей в БД...")
                    primary_saved = CarDataService.save_primary_to_db(car, primary, datetime_now, datetime_for_stats)
                    if primary_saved:
                        logger.info(f"Первичные показатели сохранены для машины {car.id}")
                    else:
                        logger.warning(f"Не удалось сохранить первичные показатели для машины {car.id}")
                    logger.info("ЭТАП 3.0: Вычисление норм в БД...")
                    norms = NormsService.calculate_norms_single(df, primary, auto_data)
                    if norms is None or norms.is_empty():
                        error_msg = f"Не удалось вычислить нормы показатели для машины {car.id}"
                        logger.error(error_msg)
                        report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.NORMS,
                            is_save_bad_data=is_save_bad_data
                        )
                        ReportService.create_bad_data_record(car, error_msg, report_query, datetime_for_stats, datetime_now)
                        ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                        continue
                    logger.info("ЭТАП 3.1: Сохранение норм в БД...")
                    norms_saved = CarConsumptionService.save_consumption_rates(norms, car)
                    if norms_saved:
                        logger.info(f"Показатели норм сохранены для машины {car.id}")
                    else:
                        logger.warning(f"Не удалось сохранить первичные показатели для машины {car.id}")

            except Exception as err:
                report_query, report_details = ReportService.create_report(
                    provider_id=str(provider.id),
                    report_type=ReportQuery.ReportType.PRIMARY,
                    is_save_bad_data=is_save_bad_data
                )
                error_msg = f"Внутренняя ошибка {err} для {car.id}"
                ReportService.create_bad_data_record(car, error_msg, report_query, datetime_for_stats, datetime_now, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue


@shared_task(bind=True)
def calculate_norms_cron(self, provider_name: str, is_save_bad_data=False):
    datetime_now = datetime.now()
    provider = DataProvider.objects.get(name=provider_name)

    cars_norms = provider.cars.select_related("carprimary").filter(carprimary__isnull=False).prefetch_related("consumptions").filter(consumptions__isnull=True)

    if len(cars_norms) != 0:
        date_time_norms = datetime_now - timedelta(days=360)
        parser = GlonassGeneralProvider(cars_norms, None, provider, date_time_norms, datetime_now)
        for car in cars_norms:
            try:
                auto_data = CarDataService.prepare_auto_data(car)
                status, df = parser.parse_raw_data("fuel", True, car)

                if status and isinstance(df, pl.DataFrame):
                    primary = pl.DataFrame(car.carprimary.primary)
                    norms = NormsService.calculate_norms_single(df, primary, auto_data)
                    if norms is None or norms.is_empty():
                        error_msg = f"Не удалось вычислить первичные показатели для машины {car.id}"
                        logger.error(error_msg)
                        report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.NORMS,
                            is_save_bad_data=is_save_bad_data
                        )
                        ReportService.create_bad_data_record(car, error_msg, report_query, date_time_norms, datetime_now)
                        ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                        continue
                    if norms.is_empty():
                        error_msg = f"Недостаточно данных для построения норм {car.id}"
                        logger.error(error_msg)
                        ReportService.create_bad_data_record(car, error_msg, report_query, date_time_norms, datetime_now)
                        ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                        continue
                    logger.info("Сохранение первичных показателей в БД...")
                    norms_saved = CarConsumptionService.save_consumption_rates(norms, car)
                    if norms_saved:
                        logger.info(f"Показатели норм сохранены для машины {car.id}")
                    else:
                        logger.warning(f"Не удалось сохранить первичные показатели для машины {car.id}")
            except Exception:
                report_query, report_details = ReportService.create_report(
                    provider_id=str(provider.id),
                    report_type=ReportQuery.ReportType.NORMS,
                    is_save_bad_data=is_save_bad_data
                )
                error_msg = f"Внутренняя ошибка {car.id}"
                ReportService.create_bad_data_record(car, error_msg, report_query, date_time_norms, datetime_now, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue

#TODO: перевести этот код на leaks_last_processed
@shared_task(bind=True)
def calculate_leaks_cron(
    self,
    provider_name: str,
    is_save_bad_data: bool = False
):
    provider = DataProvider.objects.get(name=provider_name)

    tz = pytz.UTC
    now = datetime.now().astimezone(tz)
    day_start = datetime.combine(now, datetime.min.time()).astimezone(tz)

    cars_for_computing = provider.cars.select_related("carprimary").prefetch_related("consumptions").filter(
        consumptions__isnull=False, carprimary__isnull=False, is_active=True
    ).filter(
        Q(last_processed_date__lt=day_start) | Q(last_processed_date__isnull=True)
    )

    if len(cars_for_computing) != 0:
        parser = GlonassGeneralProvider(cars_for_computing, None, provider, now, now)

        car_map = {car.name: car for car in cars_for_computing}

        for car in cars_for_computing:
            try:
                last_date_for_processing = car.last_processed_date if car.last_processed_date else (now - timedelta(365))
                datetime_parsing = cast(datetime, last_date_for_processing)
                datetime_parsing = datetime_parsing.astimezone(tz)

                report_query, report_details = ReportService.create_report(
                    str(provider.id),
                    ReportQuery.ReportType.LEAKS,
                    is_save_bad_data=is_save_bad_data
                )
                status, data_df = parser.parse_raw_data("fuel", True, car, datetime_parsing, now)
                if status and isinstance(data_df, pl.DataFrame):
                    if data_df.is_empty():
                        result_msg = f"Нет данных за период {datetime_parsing} {last_date_for_processing} для {car.id}"
                        report_data = {
                            "result": {},
                            "empty_data": True,
                            "rows_processed": 0
                        }
                        ReportService.complete_report_success(report_query, report_data)
                        logger.warning(result_msg)
                        continue
                    primary_df = pl.DataFrame(car.carprimary.primary, schema_overrides={"auto": pl.Categorical})
                    norms_df = pl.DataFrame(car.consumptions.first().json_data, schema_overrides={"sl_avto": pl.Categorical})
                    auto_data = CarDataService.prepare_auto_data(car)
                    leak_service = leaks_service.LeaksService()
                    leaks_result, _, reports = leak_service.compute_leaks(auto_data, data_df, primary_df, norms_df)
                    if is_save_bad_data:
                        ReportService.create_bad_data_record_from_list(car, reports, report_query)
                if leaks_result is None or leaks_result.is_empty():
                    error_msg = f"Не обнаружены сливы для машины {car.id}"
                    logger.error(error_msg)
                    ReportService.create_bad_data_record(car, error_msg, report_query, datetime_parsing, now)
                    Car.objects.filter(id=car.id).update(last_processed_date=now)
                    ParsingCarStats.objects.filter(car_id=car.id).update(leaks_last_processed=now)
                    continue
                filtering_service = FilteringService()
                filtering_result = filtering_service.apply_filters(leaks_result)
                ReportService.save_car_reports_batch(filtering_result)

                if not filtering_result.is_empty():
                    save_leak_alerts_from_rows(
                        filtering_result.rows(named=True),
                        provider.org_id,
                        car_map,
                    )

                logger.info(f"Сохранено {filtering_result.shape[0]} сливов")
                Car.objects.filter(id=car.id).update(last_processed_date=now)
            except Exception as err:
                report_query, report_details = ReportService.create_report(
                    provider_id=str(provider.id),
                    report_type=ReportQuery.ReportType.LEAKS,
                    is_save_bad_data=is_save_bad_data
                )
                error_msg = f"Внутренняя ошибка при расчете сливов для машины {car.id}: {err}"
                ReportService.create_bad_data_record(car, error_msg, report_query, now, now, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue
    else:
        logger.info("Нет машин для обработки сливов")


@shared_task(bind=True)
def calculate_leaks_cron_one(
    self,
    provider_name: str,
    car_id: str,
    is_save_bad_data: bool = False,
    ignore_last_processed: bool = False
):
    provider = DataProvider.objects.get(name=provider_name)

    tz = pytz.UTC
    now = datetime.now().astimezone(tz)

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    car = Car.objects.filter(id=car_id)\
        .select_related("carprimary")\
        .prefetch_related("consumptions")\
        .first()
        
    if car is not None:

        # ✅ Skip if already processed today (same as cron)
        if (
            not ignore_last_processed and
            car.last_processed_date is not None and
            car.last_processed_date.astimezone(tz) >= day_start
        ):
            logger.info(f"Car {car.id} already processed today, skipping")
            return

        parser = GlonassGeneralProvider([car], None, provider, now, now)

        try:
            # ✅ Same logic as cron
            if car.last_processed_date and not ignore_last_processed:
                datetime_parsing = car.last_processed_date.astimezone(tz)
            else:
                datetime_parsing = now - timedelta(365)

            report_query, report_details = ReportService.create_report(
                str(provider.id),
                ReportQuery.ReportType.LEAKS,
                is_save_bad_data=is_save_bad_data
            )

            status, data_df = parser.parse_raw_data(
                "fuel", True, car, datetime_parsing, now
            )
            is_fuel_report_made = False
            if status and isinstance(data_df, pl.DataFrame):
                primary_df = pl.DataFrame(car.carprimary.primary, schema_overrides={"auto": pl.Categorical})
                norms_df = pl.DataFrame(car.consumptions.first().json_data, schema_overrides={"sl_avto": pl.Categorical})
                auto_data = CarDataService.prepare_auto_data(car)
                leak_service = leaks_service.LeaksService()
                leaks_result, _, reports = leak_service.compute_leaks(auto_data, data_df, primary_df, norms_df)
                if is_save_bad_data:
                    ReportService.create_bad_data_record_from_list(car, reports, report_query)
                if isinstance(leaks_result, pl.DataFrame):
                    spent_report = fuel_spent_calculate(leaks_result)
                    try:
                        refill = parser.parse_refill_data_full(car, datetime_parsing, now)
                        if refill is not None and refill.shape[0] > 0:
                            df_to_report = FuelReportService.build_right_history(spent_report, refill)
                        else:
                            df_to_report = FuelReportService.build_right_history_no_refuel(spent_report)
                        FuelReportService.make_reports_from_df(df_to_report)
                        is_fuel_report_made = True
                    except BaseException as err:
                        logger.error(f"Can't make fuel reports for car {car.id}")
                if leaks_result is None or leaks_result.is_empty():
                    error_msg = f"Нет данных для машины после фильтров {car.id}"
                    logger.error(error_msg)
                    ReportService.create_bad_data_record(car, error_msg, report_query, datetime_parsing, now)
                    ParsingCarStats.objects.filter()
                computed_service = ComputedDataService()
                saved_records_amount, _ = computed_service.save_preprocessed_data(leaks_result)
                logger.info(f"Сохранено {saved_records_amount} записей для графиков")
                filtering_service = FilteringService()
                filtering_result = filtering_service.apply_filters(leaks_result)
                ReportService.save_car_reports_batch(filtering_result)

                if not filtering_result.is_empty():
                    save_leak_alerts_from_rows(
                        filtering_result.rows(named=True),
                        provider.org_id,
                        {car.name: car},
                    )

                logger.info(f"Сохранено {filtering_result.shape[0]} сливов")
            Car.objects.filter(id=car.id).update(last_processed_date=now)
            ParsingCarStats.objects.filter(car_id=car.id).update(computed_last_processed=now, leaks_last_processed=now)
            if is_fuel_report_made:
                ParsingCarStats.objects.filter(car_id=car.id).update(fuel_last_processed=now)
        except Exception as err:
            report_query, report_details = ReportService.create_report(
                provider_id=str(provider.id),
                report_type=ReportQuery.ReportType.LEAKS,
                is_save_bad_data=is_save_bad_data
            )
            error_msg = f"Внутренняя ошибка {car.id}"
            ReportService.create_bad_data_record(car, error_msg, report_query, now, now, CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.LEAKS])
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)

@shared_task(bind=True)
def calculate_leaks_cron_new(self, provider_name: str, is_save_bad_data = False):
    provider = DataProvider.objects.filter(name=provider_name).first()
    
    if provider is None:
        logger.error("No such provider for leaks calculation")
        raise BaseException("No such provider for leaks calculation")
    cars_for_computing = provider.cars.select_related("carprimary").prefetch_related("consumptions").filter(
        consumptions__isnull=False, carprimary__isnull=False, is_active=True,is_tarrified=True
    )
    tasks = [
        calculate_leaks_cron_one.si(provider_name, str(car.id), is_save_bad_data) for car in cars_for_computing 
    ]
    chain(*tasks).apply_async()
        
    



@shared_task(bind=True)
def parse_terminal_messages_task(
        self,
        provider_name: str,
        start_date_str: Optional[str] = None,
        end_date_str: Optional[str] = None,
        mode: Literal["raw", "raw_mapped"] = "raw",
        car_ids: Optional[List[str]] = None,
        by_cron: bool = False
):
    try:
        if by_cron:
            tz = pytz.UTC
            end_date = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            start_date = end_date - timedelta(days=1)
            start_date_str = start_date.strftime("%Y-%m-%d")
            end_date_str = end_date.strftime("%Y-%m-%d")
        else:
            if not start_date_str or not end_date_str:
                raise ValueError("Для ручного запуска требуются start_date_str и end_date_str")
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=pytz.UTC)
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=pytz.UTC)

        provider = DataProvider.objects.filter(name=provider_name).first()
        if not provider:
            raise ValueError(f"Провайдер с именем '{provider_name}' не найден")

        cars = None
        if car_ids:
            cars = list(Car.objects.filter(id__in=car_ids, data_providers=provider))
        elif by_cron:
            cars = list(provider.cars.all())

        logger.info(
            f"Запуск парсинга: провайдер={provider_name}, "
            f"даты={start_date_str} - {end_date_str}, "
            f"режим={mode}, машин={len(cars) if cars else 'все'}, "
            f"источник={'крон' if by_cron else 'ручной'}"
        )

        parser = GlonassGeneralProvider(cars, None, provider, start_date, end_date, mode)
        result = parser.parse_raw_data_all()

        self.update_state(
            state='SUCCESS',
            meta={
                'result': result,
                'provider_name': provider_name,
                'start_date': start_date_str,
                'end_date': end_date_str,
                'mode': mode,
                'by_cron': by_cron
            }
        )

        save_system_alert(
            organization=provider.org_id,
            message=f"Выполнена обработка данных для провайдера {provider.name}",
            event_datetime=datetime.utcnow(),
        )

        return {
            'status': 'success',
            'result': result,
            'provider_name': provider_name,
            'start_date': start_date_str,
            'end_date': end_date_str,
            'mode': mode,
            'by_cron': by_cron
        }

    except Exception as e:
        logger.error(f"Ошибка в задаче парсинга terminalMessages: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise


@shared_task(bind=True)
def parse_cars_milleage_task(
        self,
        provider_name: str,
        is_save_bad_data: bool = False,
        is_parse_mileage=False,
        start_date_manual=None,
        force=False
):
    try:
        if is_parse_mileage == False:
            return
        tz = pytz.UTC
        start_date = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0) if start_date_manual is None else datetime.fromisoformat(start_date_manual).astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        provider = DataProvider.objects.filter(name=provider_name).first()
        cars = provider.cars.select_related("parsingcar_stats").filter(
            Q(is_active=True) | Q(parsingcar_stats__is_parse_mileage=True)
        )
        if not force:
            cars = cars.filter(
                Q(parsingcar_stats__mileage_last_processed__isnull=True) | Q(parsingcar_stats__mileage_last_processed__lt=start_date)
            )
        cars = list(cars)

        if len(cars) == 0:
            logger.info("Нет машин для обработки пробега")
            self.update_state(
                state='SUCCESS',
                meta={
                    'message': 'Нет машин для обработки пробега',
                    'provider_name': provider_name,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                }
            )
            return

        parser = GlonassGeneralProvider(cars, None, provider, start_date, end_date)
        anomalies = 0

        for car in cars:
            try:
                logger.info(f"Обработка пробега для машины {car.id}")
                is_sensor = len(SensorsValues.objects.filter(car_id__id=car.id, is_active=True).select_related("key").filter(key__key="mileage"))
                is_already_computed = len(CarMileageReport.objects.filter(datetime=start_date, car_id__id=car.id))
                if is_already_computed > 0:
                    logger.info(f"Пробег для машины {car.id} {car.name} за {start_date.date().isoformat()} уже обработан, пропускаем")
                    parser._skip_car(car)
                    continue
                last_datetime = ParsingCarStats.objects.filter(car_id__id=car.id).first().mileage_last_processed
                if last_datetime is None or force:
                    last_datetime = start_date
                if is_sensor == 0:
                    parser._skip_car(car)
                    continue
                is_no_need_to_parse = len(CarMileageReport.objects.filter(datetime=start_date, car_id__id=car.id))
                if is_no_need_to_parse > 0:
                    parser._skip_car(car)
                    continue
                for i in range((end_date - last_datetime).days):
                    current_date = last_datetime + timedelta(days=i)
                    tmp_end_date = min(current_date + timedelta(days=1), end_date)
                    result, status = MileageCalculationService.calculate_mileage(
                        car.id, MileageAlgorithms.fraud, None, current_date, tmp_end_date,
                        parser=parser, is_save_bad_data=is_save_bad_data
                    )
                    data = result["result"]
                    if status == 200:
                        fraud_value = data.get("travel_fraud")
                        if fraud_value is not None and abs(fraud_value) > 0:
                            anomalies += 1
                        mileage_report, _ = CarMileageReport.objects.update_or_create(
                            car_id_id=car.id,
                            datetime=start_date,
                            defaults={
                                "mileage_start": data["first_mileage"],
                                "mileage_end": data["last_mileage"],
                                "travel": data["travel"],
                                "fraud": fraud_value,
                            }
                        )
                        logger.info(
                            f"Пробег для машины {car.id} за {current_date.date().isoformat()} сохранен. "
                            f"Пройдено км: {data['travel']}, подозрительный пробег: {fraud_value}"
                        )

                        if fraud_value is not None and abs(fraud_value) > 0:
                            save_fraud_alert(
                                car=car,
                                organization=provider.org_id,
                                fraud_km=fraud_value,
                                event_datetime=current_date,
                                source_mileage_report=mileage_report,
                            )
                    else:
                        logger.error("Статус репорта не успешный")
                ParsingCarStats.objects.update_or_create(car_id=car.id, defaults={"mileage_last_processed": start_date})
            except Exception as err:
                logger.error(f"Ошибка при обработке пробега для машины {car.id}: {err}")
                ReportService.create_bad_data_record(
                    car, f"Ошибка при обработке пробега: {err}", None,
                    start_date, end_date,
                    CarBadData.Severity.ERROR, CarBadData.Category.CALCULATION, [CarBadData.Tag.MILEAGE]
                )
                continue

        save_system_alert(
            organization=provider.org_id,
            message=f"Отчёт по пробегу за {start_date.date().isoformat()} завершён. Накруток: {anomalies}",
            event_datetime=datetime.utcnow(),
        )

    except Exception as e:
        logger.error(f"Ошибка в задаче парсинга mileage: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise


@shared_task(bind=True)
def parse_cars_computed_data_task_one(self, provider_id: str, car_id: str, is_save_bad_data: bool, ignore_last_processed=False):
    provider = DataProvider.objects.get(id=provider_id)

    tz = pytz.UTC
    now = datetime.now().astimezone(tz)

    car = Car.objects.filter(id=car_id).select_related("carprimary").prefetch_related("consumptions").first()

    if car is not None:
        parser = GlonassGeneralProvider([car], None, provider, now, now)
        try:
            p_stats = ParsingCarStats.objects.filter(car_id__id=car.id).first()
            last_date_for_processing = p_stats.computed_last_processed if p_stats.computed_last_processed is not None and not ignore_last_processed else (now - timedelta(365))
            datetime_parsing = cast(datetime, last_date_for_processing)
            datetime_parsing = datetime_parsing.astimezone(tz)

            report_query, report_details = ReportService.create_report(
                str(provider.id),
                ReportQuery.ReportType.LEAKS,
                is_save_bad_data=is_save_bad_data
            )
            status, data_df = parser.parse_raw_data("fuel", True, car, datetime_parsing, now)
            if status and isinstance(data_df, pl.DataFrame):
                primary_df = pl.DataFrame(car.carprimary.primary, schema_overrides={"auto": pl.Categorical})
                norms_df = pl.DataFrame(car.consumptions.first().json_data, schema_overrides={"sl_avto": pl.Categorical})
                auto_data = CarDataService.prepare_auto_data(car)
                leak_service = leaks_service.LeaksService()
                leaks_result, _, reports = leak_service.compute_leaks(auto_data, data_df, primary_df, norms_df)
                if is_save_bad_data:
                    ReportService.create_bad_data_record_from_list(car, reports, report_query)
                if isinstance(leaks_result, pl.DataFrame):
                    leaks_result = leaks_result.with_columns(pl.col("grades").cast(pl.String))
                    spent_report = fuel_spent_calculate(leaks_result)
                    try:
                        refill = parser.parse_refill_data_full(car, datetime_parsing, now)
                        if refill is not None:
                            df_to_report = FuelReportService.build_right_history(spent_report, refill)
                            FuelReportService.make_reports_from_df(df_to_report)
                    except BaseException as err:
                        logger.error(f"Can't make fuel reports for car {car.id}")
                if leaks_result is None or leaks_result.is_empty():
                    error_msg = f"Нет данных для машины {car.id}"
                    logger.error(error_msg)
                    ReportService.create_bad_data_record(car, error_msg, report_query, datetime_parsing, now)
                    return
                computed_service = ComputedDataService()
                saved_records_amount, _ = computed_service.save_preprocessed_data(leaks_result)
                logger.info(f"Сохранено {saved_records_amount} записей для графиков")
            ParsingCarStats.objects.filter(car_id=car.id).update(computed_last_processed=now)
        except Exception as err:
            report_query, report_details = ReportService.create_report(
                provider_id=str(provider.id),
                report_type=ReportQuery.ReportType.LEAKS,
                is_save_bad_data=is_save_bad_data
            )
            error_msg = f"Внутренняя ошибка {car.id}"
            ReportService.create_bad_data_record(car, error_msg, report_query, now, now)
            ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)


@shared_task(bind=True)
def parse_cars_computed_data_task(
    self,
    provider_id: str,
    is_save_bad_data: bool,
):
    provider = DataProvider.objects.filter(id=provider_id).first()
    cars_for_computing = provider.cars.select_related("carprimary", "parsingcar_stats").prefetch_related("consumptions").filter(
        consumptions__isnull=False, carprimary__isnull=False, is_active=True
    )

    now = datetime.now().astimezone(timezone.utc)

    if len(cars_for_computing) != 0:
        for car in cars_for_computing:
            parser = GlonassGeneralProvider(cars_for_computing, None, provider, now, now)
            try:
                last_date_for_processing = car.parsingcar_stats.computed_last_processed if car.parsingcar_stats.computed_last_processed else (now - timedelta(365))
                datetime_parsing = cast(datetime, last_date_for_processing)
                datetime_parsing = datetime_parsing.astimezone(pytz.utc)

                report_query, report_details = ReportService.create_report(
                    str(provider_id),
                    ReportQuery.ReportType.COMPUTED_DATA
                )
                status, data_df = parser.parse_raw_data("fuel", True, car, datetime_parsing, now)
                if status and isinstance(data_df, pl.DataFrame):
                    if data_df.is_empty():
                        result_msg = f"Нет данных за период {datetime_parsing} {last_date_for_processing}"
                        result_data = {"result": {}, "empty_data": True, "rows_processed": 0}
                        ReportService.complete_report_success(report_query, result_data)
                        logger.warning(result_msg)
                        continue
                    primary_df = pl.DataFrame(car.carprimary.primary, schema_overrides={"auto": pl.Categorical})
                    norms_df = pl.DataFrame(car.consumptions.first().json_data, schema_overrides={"sl_avto": pl.Categorical})
                    auto_data = CarDataService.prepare_auto_data(car)
                    leak_service = leaks_service.LeaksService()
                    leaks_result, _, reports = leak_service.compute_leaks(auto_data, data_df, primary_df, norms_df)
                    if is_save_bad_data:
                        ReportService.create_bad_data_record_from_list(car, reports, report_query)
                    if leaks_result is None or leaks_result.is_empty():
                        error_msg = f"Не обнаружены данные для машины {car.id}"
                        logger.error(error_msg)
                        ReportService.create_bad_data_record(car, error_msg, report_query, datetime_parsing, now)
                    computed_service = ComputedDataService()
                    saved_records_amount, _ = computed_service.save_preprocessed_data(leaks_result)
                    logger.info(f"Сохранено {saved_records_amount} записей для графиков")

            except Exception as err:
                report_query, report_details = ReportService.create_report(
                    provider_id=str(provider.id),
                    report_type=ReportQuery.ReportType.LEAKS,
                    is_save_bad_data=is_save_bad_data
                )
                error_msg = f"Внутренняя ошибка {car.id}"
                ReportService.create_bad_data_record(car, error_msg, report_query, now, now)
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue


@shared_task(bind=True)
def internal_migrate_to_new_processing_system(self):
    providers = DataProvider.objects.all()
    for provider in providers:
        cars = list(provider.cars.all())
        for car in cars:
            if ParsingCarStats.objects.filter(car_id__id=car.id).exists():
                continue
            ParsingCarStats.objects.create(
                car_id=car.id,
                norms_last_processed=car.last_processed_date,
                fuel_last_processed=None,
                computed_last_processed=None,
                leaks_last_processed=car.last_processed_date,
                primary_last_processed=car.last_processed_date,
                mileage_last_processed=None,
                preffered_period_days=90
            )


@shared_task(bind=True)
def parse_cars_fuel_provider(self, provider_name: str, is_save_bad_data=False, is_parse_mileage=False):
    agg = 1440
    try:
        chain(
            calculate_leaks_cron_new.si(provider_name, is_save_bad_data),
            parse_cars_milleage_task.si(provider_name, agg, is_save_bad_data, is_parse_mileage),
        ).apply_async()
    except Exception as e:
        logger.error(f"Full exception: {e}", exc_info=True)
        raise e


# ===========================================================================
# ALERT TASKS
# ===========================================================================

@shared_task(
    bind=True,
    name="sync_bad_data_to_alerts",
    max_retries=3,
    default_retry_delay=60,
)
def sync_bad_data_to_alerts(self):
    tz = pytz.UTC
    now = datetime.now(tz)
    hour_ago = now - timedelta(hours=1)

    try:
        already_transferred = (
            Alert.objects
            .filter(source_bad_data__isnull=False)
            .values_list("source_bad_data_id", flat=True)
        )

        new_bad_data = (
            CarBadData.objects
            .filter(datetime__gte=hour_ago, datetime__lte=now)
            .select_related("car_id")
            .exclude(id__in=already_transferred)
        )

        if not new_bad_data.exists():
            logger.info("[sync_bad_data_to_alerts] Новых CarBadData за последний час нет")
            return

        org_to_records: dict = {}
        for record in new_bad_data:
            car = record.car_id
            provider = car.data_providers.select_related("org_id").first()
            if provider is None or provider.org_id is None:
                logger.warning(
                    f"[sync_bad_data_to_alerts] Не удалось определить организацию "
                    f"для машины {car.id}, пропускаем"
                )
                continue
            org = provider.org_id
            org_to_records.setdefault(org, []).append(record)

        total_saved = 0
        for org, records in org_to_records.items():
            saved = save_bad_data_alerts_bulk(records, org)
            total_saved += saved
            logger.info(
                f"[sync_bad_data_to_alerts] org={org.name}: "
                f"перенесено {saved} из {len(records)}"
            )

        logger.info(f"[sync_bad_data_to_alerts] Итого: {total_saved} алертов")

    except Exception as e:
        logger.error(f"[sync_bad_data_to_alerts] Ошибка: {e}", exc_info=True)
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    name="send_alert_digests",
    max_retries=3,
    default_retry_delay=60,
)
def send_alert_digests(self):
    tz = pytz.UTC
    now = datetime.now(tz)
    current_hour = now.hour

    try:
        subscriptions = (
            AlertSubscription.objects
            .filter(is_active=True, notify_hour=current_hour)
            .select_related("user", "user__org")
        )

        if not subscriptions.exists():
            logger.info(f"[send_alert_digests] Нет подписок для часа {current_hour} UTC")
            return

        org_alerts = get_unsent_alerts_by_org()
        if not org_alerts:
            logger.info("[send_alert_digests] Нет несентованных алертов")
            return

        successfully_sent_ids: set[str] = set()

        for subscription in subscriptions:
            user = subscription.user
            org = user.org

            if org is None:
                logger.warning(
                    f"[send_alert_digests] У пользователя {user.username} "
                    f"нет организации, пропускаем"
                )
                continue

            alerts_for_org = org_alerts.get(str(org.id), [])
            if not alerts_for_org:
                logger.info(
                    f"[send_alert_digests] Нет несентованных алертов "
                    f"для org={org.name}"
                )
                continue

            tg_user = get_tg_user_for_org_user(user)

            if tg_user is None:
                logger.warning(
                    f"[send_alert_digests] TelegramUser не найден или неактивен: "
                    f"user={user.username}"
                )
                continue

            message = build_digest_message(subscription, alerts_for_org)

            if message is None:
                logger.info(
                    f"[send_alert_digests] После фильтрации алертов нет: "
                    f"user={user.username}"
                )
                continue

            success = send_telegram_message(tg_user.chat_id, message)

            if success:
                logger.info(
                    f"[send_alert_digests] Дайджест отправлен: "
                    f"user={user.username}, org={org.name}"
                )
                for alert in alerts_for_org:
                    successfully_sent_ids.add(str(alert.id))
            else:
                logger.error(
                    f"[send_alert_digests] Ошибка отправки: "
                    f"user={user.username}, chat_id={tg_user.chat_id}"
                )

        if successfully_sent_ids:
            sent_count = Alert.objects.filter(
                id__in=successfully_sent_ids
            ).update(is_sent=True, sent_at=now)
            logger.info(
                f"[send_alert_digests] Помечено is_sent=True: {sent_count} алертов"
            )

    except Exception as e:
        logger.error(f"[send_alert_digests] Ошибка: {e}", exc_info=True)
        raise self.retry(exc=e)

@shared_task(
    bind=True,
    name="push_uptime_kuma",
    max_retries=3,
    default_retry_delay=10,
)
def push_uptime_kuma(self):
    import os
    url = os.getenv("UPTIME_KUMA_PUSH_URL")

    if not url:
        logger.error("[push_uptime_kuma] UPTIME_KUMA_PUSH_URL not set in environment")
        return

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        logger.info(f"[push_uptime_kuma] ✅ Heartbeat sent — {resp.status_code}")
        return {"status": "ok", "code": resp.status_code}
    except Exception as e:
        logger.error(f"[push_uptime_kuma] ❌ Failed: {e}")
        raise self.retry(exc=e)