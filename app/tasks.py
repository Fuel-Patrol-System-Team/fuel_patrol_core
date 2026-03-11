import logging
from typing import Literal, Optional, List, cast
from zoneinfo import ZoneInfo

from charset_normalizer.utils import is_emoticon
from django.db.models import Q
import polars as pl
from datetime import datetime, timedelta, timezone
import pytz
from celery import chain, shared_task

from app.celery import app as celery_app

from core.admin import CarPrimary, SensorsValues
from core.models import (
    CarMileageReport,
    ReportQuery,
    Organization,
    Car,
    DataProvider,
)

import glob
import os
import psutil
import time

from core.services.providers import leaks_service
from core.services.providers.car_consumption_service import CarConsumptionService
from core.services.providers.car_data_service import CarDataService
from core.services.providers.filtering_service import FilteringService

from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.glonass.glonassoft_terminal_messages_parser import GlonassSoftTerminalMessagesParser

from core.services.providers.mileage_calculation_service import MileageAlgorithms, MileageCalculationService
from core.services.providers.norms_service import NormsService
from core.services.providers.provider_factory import ProviderFactory
from core.services.providers.report_service import ReportService
from core.services.providers.vehicle_sync_service import VehicleSyncService

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
    """
    Celery задача для асинхронной синхронизации транспортных средств с созданием отчета
    """
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
    """
    Обрабатывает данные для одной машины с полной интеграцией отчетов и сохранением результатов
    """
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
        # raw_df = data_provider.get_car_data(start_dt, end_dt)
        
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
            ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date)
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
                ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date)
                logger.error(error_msg)
                return {"success": False, "car_id": car_id, "error": error_msg}

        if norms_df is None or norms_df.is_empty():
            error_msg = f"Не удалось рассчитать нормы для машины {car_id}"
            logger.error(error_msg)

            ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date)
            norms_df = None
            return {"success": False, "car_id": car_id, "error": error_msg}

        logger.info("ЭТАП 4: Расчет утечек топлива...")
        leak_service = leaks_service.LeaksService()
        leaks_result, intermediate_df = None, None
        if norms_df is not None and not norms_df.is_empty():
            leaks_result, intermediate_df = leak_service.compute_leaks(
                auto_df=auto_df,
                data_df=raw_df,
                primary_df=primary_df,
                norma_df=norms_df,
                is_save_bad_data=is_save_bad_data,
                is_filter_bad_data=True,
            )

        if leaks_result is None or leaks_result.is_empty():
            error_msg = f"Не удалось рассчитать утечки для машины {car_id}"
            logger.error(error_msg)
            ReportService.create_bad_data_record(car, error_msg, report_query, start_date, end_date)
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
                "processed": timezone.now().isoformat(),
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
def calculate_primary_cron(self, provider_name: str, is_save_bad_data=False):
    
    datetime_now = datetime.now()
    provider = DataProvider.objects.get(name=provider_name)
    

    cars_primary = provider.cars.select_related("carprimary").filter(carprimary__isnull=True)
    
    if len(cars_primary) != 0:
        datetime_primary = datetime_now - timedelta(days=45)
        # primary computing
        parser = GlonassGeneralProvider(cars_primary, None, provider, datetime_primary, datetime_now)
        for car in cars_primary:
            try:
                is_sensor = len(SensorsValues.objects.filter(car_id__id=car.id).select_related("key").filter(key__key="calc_sensors_fuel_level"))
                if is_sensor == 0:
                    continue
                auto_data = CarDataService.prepare_auto_data(car)
                status, df = parser.parse_raw_data("fuel", True)
                    
                if status and isinstance(df, pl.DataFrame):
                    primary = CarDataService.calculate_primary_single(df, auto_data )
                    if primary is None or primary.is_empty():
                        error_msg = f"Не удалось вычислить первичные показатели для машины {car.id}"
                        logger.error(error_msg)
                        report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.PRIMARY,
                            is_save_bad_data=is_save_bad_data
                        )
                        # TODO: один отчет на все машины
                        ReportService.create_bad_data_record(car, error_msg, report_query, datetime_primary, datetime_now)
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
                            is_save_bad_data=is_save_bad_data
                        )
                error_msg = f"Внутренняя ошибка {err} для {car.id}"
                ReportService.create_bad_data_record(car, error_msg, report_query, datetime_primary, datetime_now)
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue

@shared_task(bind=True)
def calculate_norms_cron(self, provider_name: str, is_save_bad_data=False):
    
    datetime_now = datetime.now()
    provider = DataProvider.objects.get(name=provider_name)
    

    cars_norms = provider.cars.select_related("carprimary").filter(carprimary__isnull=False).prefetch_related("consumptions").filter(consumptions__isnull=True)
    
    if len(cars_norms) != 0:
        date_time_norms = datetime_now - timedelta(days=90)
        # primary computing
        parser = GlonassGeneralProvider(cars_norms, None, provider, date_time_norms, datetime_now)
        for car in cars_norms:
            try:
                auto_data = CarDataService.prepare_auto_data(car)
                status, df = parser.parse_raw_data("fuel", True)
                    
                if status and isinstance(df, pl.DataFrame):
                    primary = pl.DataFrame(car.carprimary.primary)
                    norms = NormsService.calculate_norms_single(df, primary, auto_data)
                    if norms is None or norms.is_empty():
                        error_msg = f"Не удалось вычислить первичные показатели для машины {car.id}"
                        logger.error(error_msg)
                        report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.PRIMARY,
                            is_save_bad_data=is_save_bad_data
                        )
                        # TODO: один отчет на все машины
                        ReportService.create_bad_data_record(car, error_msg, report_query, date_time_norms, datetime_now)
                        ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                        continue
                    # norms = norms.filter(pl.col("norma_rasx_summer") > 5)
                    if norms.is_empty():
                        error_msg = f"Недостаточно данных для построения норм {car.id}"
                        logger.error(error_msg)
                        ReportService.create_bad_data_record(car, error_msg, report_query, date_time_norms, datetime_now)
                        ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                        continue
                    logger.info("Сохранение первичных показателей в БД...")
                    norms_saved = CarConsumptionService.save_consumption_rates(norms, car)
                    if norms_saved:
                        logger.info(f"Первичные показатели сохранены для машины {car.id}")
                    else:
                        logger.warning(f"Не удалось сохранить первичные показатели для машины {car.id}")
            except Exception:
                report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.PRIMARY,
                            is_save_bad_data=is_save_bad_data
                        )
                error_msg = f"Внутренняя ошибка {car.id}"
                ReportService.create_bad_data_record(car, error_msg, report_query, date_time_norms, datetime_now)
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue

@shared_task(bind=True)
def calculate_leaks_cron(
    self,
    provider_name: str,
    is_save_bad_data: bool = False
):
    provider = DataProvider.objects.get(name=provider_name)
    
    tz = pytz.UTC
    now = datetime.now().astimezone(tz)
    day_start= datetime.combine(now, datetime.min.time()).astimezone(tz)

    cars_for_computing= provider.cars.select_related("carprimary").prefetch_related("consumptions").filter(consumptions__isnull=False, carprimary__isnull=False, is_active=True).filter(
        Q(last_processed_date__lt=day_start) | Q(last_processed_date__isnull=True)
    )
    
                
    if len(cars_for_computing) != 0:
        parser = GlonassGeneralProvider(cars_for_computing, None, provider, now, now)
        for car in cars_for_computing:
            try:
                last_date_for_processing =  car.last_processed_date if car.last_processed_date else (now.now() - timedelta(365))
                datetime_parsing = cast(datetime,  last_date_for_processing)
                datetime_parsing = datetime_parsing.astimezone(tz)

                report_query, report_details = ReportService.create_report(
                    str(provider.id),
                    ReportQuery.ReportType.LEAKS,
                    is_save_bad_data=is_save_bad_data
                )
                status, data_df = parser.parse_raw_data("fuel", True, car, datetime_parsing, now)
                if status and isinstance(data_df, pl.DataFrame):
                    primary_df = pl.DataFrame(car.carprimary.primary, schema_overrides={
                        "auto": pl.Categorical
                    })
                    norms_df = pl.DataFrame(car.consumptions.first().json_data)
                    auto_data = CarDataService.prepare_auto_data(car)
                    leak_service = leaks_service.LeaksService()
                    leaks_result, _ =  leak_service.compute_leaks(auto_data , data_df , primary_df, norms_df)
                if leaks_result is None or leaks_result.is_empty():
                    error_msg = f"Не однаружены сливы для машины {car.id}"
                    logger.error(error_msg)
                    ReportService.create_bad_data_record(car, error_msg, report_query, datetime_parsing, now)
                    continue
                filtering_service = FilteringService()
                filtering_result = filtering_service.apply_filters(leaks_result)
                report = ReportService.save_car_reports_batch(filtering_result)
                logger.info(f"Сохранено {filtering_result.shape[0]} сливов")
                Car.objects.filter(id=car.id).update(last_processed_date=now)
            except Exception as err:
                report_query, report_details = ReportService.create_report(
                            provider_id=str(provider.id),
                            report_type=ReportQuery.ReportType.PRIMARY,
                            is_save_bad_data=is_save_bad_data
                        )
                error_msg = f"Внутренняя ошибка {car.id}"
                ReportService.create_bad_data_record(car, error_msg, report_query, now, now) # нужно придумать как прокинуть реальную дату для каждой машины
                ReportService.complete_report_error(report_query, error_msg, cars_skipped=1)
                continue
    else:
        logger.info("Нет машин требующих расчета сливов")
        



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
        logger.error(f"Ошибка в задаче парсинга terminalMessages: {e}", exc_info=True)
        self.update_state(
            state='FAILURE',
            meta={'error': str(e)}
        )
        raise

@shared_task(bind=True)
def parse_cars_milleage_task(
        self,
        provider_name: str,
        aggregation: int,
        is_save_bad_data : bool = False
):
    try:

        tz = pytz.UTC
        end_date = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=1)

        provider = DataProvider.objects.filter(name=provider_name).first()
        cars = list(provider.cars.all())

        ##TODO: Тут ЮЛИК прошелся по циклу и вызвал для машины свой сервис
        parser = GlonassGeneralProvider(cars, None, provider, start_date, end_date)
        for car in cars:
            result, status  = MileageCalculationService.calculate_mileage(car.id, MileageAlgorithms.fraud, None, start_date, end_date, parser=parser, is_save_bad_data=is_save_bad_data)
            if status == 200:
                CarMileageReport.objects.update_or_create(car_id_id=car.id, datetime=start_date, mileage_start=result["result"]["first_mileage"], mileage_end=result["result"]["last_mileage"], travel=result["result"]["travel"], fraud=result["result"]["travel_fraud"] )
            else:
                logger.error("Статус репорта не успешный")
                    


        ##TODO: МБ 1 из админки не будет работать как bool, тогда стоит сделать строку и каставать к Bool из строки

        ## Важный момент - сохранение результата в модель CarMileageReport
        ## 2 варианта предлагаю
        ## Вариант 1 - в сигнатуру сервиса по милейджу - добавляем флаг save_result и сохраняем внутри процедуры
        ## Вариант 2 - результат возвращаемой функции тут получать и тут же код сохранения
    except Exception as e:
        logger.error(f"Ошибка в задаче парсинга mileage: {e}", exc_info=True)
        self.update_state(
            state='FAILURE',
            meta={'error': str(e)}
        )
        raise


@shared_task(bind=True)
def parse_cars_fuel_provider(self, provider_name: str, is_save_bad_data = False):
    agg = 1440
    try:
        chain(
            calculate_primary_cron.si(provider_name, is_save_bad_data),
            calculate_norms_cron.si(provider_name, is_save_bad_data),
            calculate_leaks_cron.si(provider_name, is_save_bad_data),
            parse_cars_milleage_task.si(provider_name, agg, is_save_bad_data )
        ).apply_async()
        pass
    except Exception as e:
        logger.error(f"Full exception: {e}", exc_info=True)
        raise e