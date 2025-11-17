import logging
import pathlib
from datetime import datetime
from typing import Dict, Optional, Any

from celery import chord, shared_task
from celery.exceptions import SoftTimeLimitExceeded
import pandas as pd
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from app.celery import app as celery_app
from app.constants import BATCH_SIZE
from app.settings import BASE_DIR
from core.models import ReportQuery, Media, Organization, Car, CarReport, CarConsumption, DataProvider, CarBadData, \
    ReportQueryDetails
from core.services.csv_parsing.utils import PARSE_NORMS_OUTPUT_COLUMNS, parse_cars, parse_merged_util, parse_norms
from core.services.data_providers.utils import save_response_to_file, provider_factory
from core.services.databases.influx_db import write_to_influxdb
from core.services.norms_computing.utils import calculate_norms
from core.services.notifications.tg_bot import send_telegram_message
from core.services.preprocessing.utils import fuel_leak_calculate_standart, preprocess, merge
from pathlib import Path

import glob
import os
import psutil
import gzip
import pickle
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

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
    soft_time_limit=300,
    priority=5,
    rate_limit="1/s"
)
def fetch_data_from_provider(self, report_query_id: str,
                             start_date: Optional[datetime] = None, end_date: Optional[datetime] = None):
    def _fetch():
        report_query = None
        try:
            logger.info(
                f"Получение данных для {report_query_id}, start_date={start_date}, end_date={end_date}")
            report_query = ReportQuery.objects.get(id=report_query_id)

            provider = provider_factory(report_query_id, report_query.provider_id.metadata)
            if not provider:
                report_query.status = "error"
                report_query.save()
                return

            if not provider.authenticate():
                logger.error("Не удалось авторизоваться.")
                report_query.status = "error"
                report_query.save()
                return

            vehicles_data = provider.get_vehicles(name=None, start_date=start_date, end_date=end_date)
            if vehicles_data is None:
                logger.error("Не удалось получить данные автомобилей.")
                report_query.status = "error"
                report_query.save()
                return

            if not vehicles_data:
                logger.warning(
                    f"Нет обработанных данных для {report_query_id}. Пропускаем запуск calculate_norms_task.")
                report_query.status = "completed"
                report_query.save()
                return

            report_query.status = "completed"
            report_query.save()

            try:
                logger.info(f"Запуск calculate_norms_task для {report_query_id}")
                calculate_norms_task.delay(report_query_id)
            except ReportQuery.DoesNotExist:
                logger.error(f"ReportQuery {report_query_id} не найден.")
                return

            logger.info(f"Задача fetch_data_from_provider завершена для {report_query_id}.")
            _log_resources("fetch_data_from_provider")
        except ReportQuery.DoesNotExist:
            logger.error(f"Заявка {report_query_id} не найдена.")
            if report_query:
                report_query.status = "error"
                report_query.save()
            raise
        except Exception as e:
            logger.error(f"Ошибка в fetch_data_from_provider: {e}")
            if report_query:
                report_query.status = "error"
                report_query.save()
                try:
                    report_details = ReportQueryDetails.objects.get(report_query_id=report_query_id)
                    import traceback
                    report_details.traceback = {
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": timezone.now().isoformat()
                    }
                    report_details.end_time = timezone.now()
                    report_details.save()
                except Exception as detail_error:
                    logger.error(f"Ошибка сохранения traceback: {detail_error}")
            raise

    return _timeit("fetch_data_from_provider", _fetch)

@shared_task(
    bind=True,
    soft_time_limit=600,
    priority=5,
    rate_limit="1/s"
)
def calculate_norms_task(self, report_query_id: str):
    def _calculate():
        report_query = None
        try:
            logger.info(f"Запуск calculate_norms_task для {report_query_id}")
            report_query = ReportQuery.objects.get(id=report_query_id)

            media = Media.objects.filter(report_query_id=report_query, type="raw").first()
            if not media or not media.file:
                logger.error(f"Файл Media не найден для report_query_id={report_query_id}")
                report_query.status = "error"
                report_query.save()
                return

            csv_path = Path(settings.MEDIA_ROOT) / str(media.file)
            if not csv_path.exists():
                logger.error(f"CSV-файл {csv_path} не существует")
                report_query.status = "error"
                report_query.save()
                return

            raw_df = pd.read_csv(csv_path)
            logger.info(f"Загружен raw_df из {csv_path}, строк: {len(raw_df)}")

            tariffied_cars = Car.objects.filter(is_tarrified=True).values('id', 'input', 'output')
            tariffied_df = pd.DataFrame(list(tariffied_cars)).rename(columns={'id': 'guid'})
            tariffied_df['guid'] = tariffied_df['guid'].astype(str)
            logger.info(f"Сформирован tariffied_df, строк: {len(tariffied_df)}")

            if tariffied_df.empty:
                logger.warning(f"Нет тарированных машин для report_query_id={report_query_id}")
                report_query.status = "completed"
                report_query.save()
                return

            norms = calculate_norms(raw_df, tariffied_df)
            logger.info(f"Вычислены нормы, строк: {len(norms)}")

            for _, row in norms.iterrows():
                try:
                    car = Car.objects.get(id=row['sl_avto'])
                    CarConsumption.objects.update_or_create(
                        car_id=car,
                        defaults={
                            'summer_volume': row['norma_rasx_summer'],
                            'winter_volume': row['norma_rasx_winter'],
                            'speed_etalon': row['speed_etalon'],
                            'max_fuel': row['max_fuel'],
                            'valid_period': row['period']
                        }
                    )
                    logger.info(f"Сохранены нормы для car_id={row['sl_avto']}")
                except Car.DoesNotExist:
                    logger.error(f"Автомобиль с id={row['sl_avto']} не найден")
                    continue
                except Exception as e:
                    logger.error(f"Ошибка сохранения норм для car_id={row['sl_avto']}: {e}")
                    continue

            try:
                ReportQuery.objects.get(id=report_query_id)
                logger.info(f"Запуск process_raw_data_task для {report_query_id}")
                process_raw_data_task.delay(report_query_id)
            except ReportQuery.DoesNotExist:
                logger.error(f"ReportQuery {report_query_id} не найден")
                report_query.status = "error"
                report_query.save()
                return

            report_query.status = "completed"
            report_query.save()
            logger.info(f"Задача calculate_norms_task завершена для {report_query_id}")
            _log_resources("calculate_norms_task")

        except ReportQuery.DoesNotExist:
            logger.error(f"Заявка {report_query_id} не найдена")
            if report_query:
                report_query.status = "error"
                report_query.save()
            raise
        except Exception as e:
            logger.error(f"Ошибка в calculate_norms_task: {e}")
            if report_query:
                report_query.status = "error"
                report_query.save()
            raise

    return _timeit("calculate_norms_task", _calculate)


@shared_task(
    bind=True,
    soft_time_limit=1800,
    priority=2,
    rate_limit="2/m"
)
def process_raw_data_task(self, report_query_id: str):
    def _process():
        report_query = None
        organization = None
        try:
            logger.info(f"Обработка сырых данных для отчёта {report_query_id}...")
            report_query = ReportQuery.objects.get(id=report_query_id)
            organization = report_query.provider_id.org_id

            provider = report_query.provider_id
            if not provider:
                logger.error(f"Поставщик не указан для отчёта {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Поставщик не указан для отчёта {report_query_id}")
                return

            excel_extensions = ['csv', 'xls', 'xlsx']
            is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)
            is_glonasssoft_provider = provider.name.lower() == "glonasssoft"

            logger.info(f"Провайдер: {provider.name}, csv: {is_csv_provider}, glonasssoft: {is_glonasssoft_provider}")

            if is_csv_provider or is_glonasssoft_provider:
                if is_csv_provider and not ReportQuery.objects.filter(provider_id__org_id=organization,
                                                                      status="completed").exists():
                    logger.error(f"Данные автомобилей для {organization.name} не завершены")
                    report_query.status = 'error'
                    report_query.save()
                    send_telegram_message(organization.bot_token, organization.chat_id,
                                          f"Обработайте файл 'auto' для отчёта {report_query_id}")
                    return

                raw_media = Media.objects.filter(report_query_id=report_query, type="raw").first()
                if not raw_media:
                    logger.error(f"Сырой файл не найден для отчёта {report_query_id}")
                    report_query.status = 'error'
                    report_query.save()
                    send_telegram_message(organization.bot_token, organization.chat_id,
                                          f"Сырой файл не найден для отчёта {report_query_id}")
                    return

                file_size = os.path.getsize(raw_media.file.path) / 1024 ** 3
                logger.info(f"Размер файла: {file_size:.2f} ГБ")
                if file_size > 10:
                    logger.warning(f"Файл слишком большой ({file_size:.2f} ГБ). Возможны проблемы с памятью.")

                required_columns = ['timestamp', 'calc_sensors_fuel_level', 'pos_s', 'calc_sensors_voltage', 'auto']
                open_func = gzip.open if raw_media.file.path.endswith('.gz') else open

                if is_glonasssoft_provider:
                    cars = Car.objects.filter(id__in=provider.cars.values_list('id', flat=True)).select_related()
                else:
                    cars = Car.objects.all().select_related()

                if not cars.exists():
                    logger.error(f"Автомобили не найдены для {report_query_id}")
                    report_query.status = 'error'
                    report_query.save()
                    send_telegram_message(organization.bot_token, organization.chat_id,
                                          f"Автомобили не найдены для {report_query_id}")
                    return

                car_data = pd.DataFrame(
                    list(cars.values('id', 'name', 'engine_type', 'input', 'output', 'is_tarrified')))
                car_data['id'] = car_data['id'].astype(str).str.strip().str.lower()
                car_data = car_data.rename(columns={'engine_type': 'sl_tip_dvigat'})
                logger.info(f"Car data: {car_data.shape}, тип: {type(car_data)}, колонки: {car_data.columns.tolist()}")
                logger.info(f"Уникальные id: {car_data['id'].unique()[:5]}")

                if is_glonasssoft_provider:
                    consumptions = CarConsumption.objects.filter(
                        car_id__in=cars.values_list('id', flat=True)).select_related('car_id')
                else:
                    consumptions = CarConsumption.objects.all().select_related('car_id')

                if not consumptions.exists():
                    logger.error(f"Нормы не найдены для {report_query_id}")
                    report_query.status = 'error'
                    report_query.save()
                    send_telegram_message(organization.bot_token, organization.chat_id,
                                          f"Нормы не найдены для отчёта {report_query_id}")
                    return

                norma_df = pd.DataFrame(list(consumptions.values(
                    'car_id__id', 'winter_volume', 'summer_volume', 'valid_period', 'speed_etalon', "max_fuel"
                )))
                norma_df['car_id__id'] = norma_df['car_id__id'].astype(str).str.strip().str.lower()
                norma_df = norma_df.rename(columns={
                    'car_id__id': 'sl_avto',
                    'winter_volume': 'norma_rasx_winter',
                    'summer_volume': 'norma_rasx_summer',
                    'valid_period': 'period'
                })
                logger.info(
                    f"Norma data: {norma_df.shape}, тип: {type(norma_df)}, колонки: {norma_df.columns.tolist()}")
                logger.info(f"Уникальные sl_avto: {norma_df['sl_avto'].unique()[:5]}")

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                chunk_tasks = []
                try:
                    with open_func(raw_media.file.path, "rt", encoding="utf-8") as f:
                        raw_df = pd.read_csv(
                            f,
                            usecols=required_columns,
                            parse_dates=['timestamp'],
                            chunksize=BATCH_SIZE,
                            dtype={'auto': str}
                        )
                        logger.info(f"Тип raw_df: {type(raw_df)}")
                        for chunk_idx, chunk_df in enumerate(raw_df):
                            logger.info(
                                f"Чанк {chunk_idx + 1}: {chunk_df.shape}, тип: {type(chunk_df)}, колонки: {chunk_df.columns.tolist()}")
                            chunk_df['auto'] = chunk_df['auto'].astype(str).str.strip().str.lower()
                            logger.info(f"Уникальные auto: {chunk_df['auto'].unique()[:5]}")

                            common_autos = set(chunk_df['auto']).intersection(set(car_data['id']))
                            logger.info(f"Общие auto с car_data: {len(common_autos)}")
                            if not common_autos:
                                logger.warning(f"Нет общих auto в чанке {chunk_idx + 1}")

                            chunks = [group for _, group in chunk_df.groupby('auto')]
                            logger.info(f"Разделено на {len(chunks)} чанков по auto")

                            chunk_tasks.extend([
                                process_chunk.s(
                                    pickle.dumps(chunk),
                                    pickle.dumps(car_data),
                                    pickle.dumps(norma_df),
                                    report_query_id,
                                    organization.id
                                )
                                for chunk in chunks
                            ])
                except Exception as e:
                    logger.error(f"Ошибка чтения CSV {raw_media.file.path}: {e}")
                    report_query.status = 'error'
                    report_query.save()
                    send_telegram_message(organization.bot_token, organization.chat_id,
                                          f"Ошибка чтения CSV для отчёта {report_query_id}: {e}")
                    return

                if chunk_tasks:
                    chord(chunk_tasks)(save_leak_results.s(report_query_id))
                else:
                    logger.error(f"Нет чанков для обработки в {report_query_id}")
                    report_query.status = 'error'
                    report_query.save()
                    send_telegram_message(organization.bot_token, organization.chat_id,
                                          f"Нет данных для обработки в {report_query_id}")

                _cleanup_temp_files(BASE_DIR, report_query_id, timestamp)
                report_query.status = 'pending'
                report_query.save()
                logger.info(f"Обработка завершена для {report_query_id}")
                _log_resources("process_raw_data_task")
            else:
                logger.info(f"Тестовый вызов провайдера {provider.name} для {report_query_id}")
        except SoftTimeLimitExceeded as e:
            logger.error(f"Превышено время в process_raw_data_task для {report_query_id}: {e}")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            if organization:
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Превышено время в process_raw_data_task для {report_query_id}")
            raise
        except ObjectDoesNotExist as e:
            logger.error(f"ReportQuery или Media не найдены для {report_query_id}: {e}")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            if organization:
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"ReportQuery или Media не найдены для {report_query_id}")
            raise self.retry(exc=e)
        except Exception as e:
            logger.error(f"Ошибка в process_raw_data_task для {report_query_id}: {e}")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            if organization:
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Ошибка в process_raw_data_task для {report_query_id}: {e}")
            raise self.retry(exc=e)

    return _timeit("process_raw_data_task", _process)


@shared_task(
    bind=True,
    soft_time_limit=1000,
    priority=3
)
def process_chunk(self, chunk_pickle: bytes, car_data_pickle: bytes, norma_data_pickle: bytes, report_query_id: str,
                  org_id: str):
    def _process():
        try:
            logger.info(f"Обработка чанка для {report_query_id}")
            logger.debug(f"Тип chunk_pickle: {type(chunk_pickle)}, размер: {len(chunk_pickle)} байт")
            logger.debug(f"Тип car_data_pickle: {type(car_data_pickle)}, размер: {len(car_data_pickle)} байт")
            logger.debug(f"Тип norma_data_pickle: {type(norma_data_pickle)}, размер: {len(norma_data_pickle)} байт")

            try:
                chunk_df = pickle.loads(chunk_pickle)
                car_data_df = pickle.loads(car_data_pickle)
                norma_data_df = pickle.loads(norma_data_pickle)
            except pickle.UnpicklingError as e:
                logger.error(f"Ошибка десериализации данных для {report_query_id}: {e}")
                raise self.retry(exc=e)

            logger.debug(f"Тип chunk_df после десериализации: {type(chunk_df)}, размер: {chunk_df.shape}")
            logger.debug(f"Тип car_data_df после десериализации: {type(car_data_df)}, размер: {car_data_df.shape}")
            logger.debug(
                f"Тип norma_data_df после десериализации: {type(norma_data_df)}, размер: {norma_data_df.shape}")

            if not isinstance(chunk_df, pd.DataFrame):
                logger.error(f"Ожидался pandas.DataFrame для chunk_df, получен {type(chunk_df)}")
                raise TypeError(f"Ожидался pandas.DataFrame для chunk_df, получен {type(chunk_df)}")
            if not isinstance(car_data_df, pd.DataFrame):
                logger.error(f"Ожидался pandas.DataFrame для car_data_df, получен {type(car_data_df)}")
                raise TypeError(f"Ожидался pandas.DataFrame для car_data_df, получен {type(car_data_df)}")
            if not isinstance(norma_data_df, pd.DataFrame):
                logger.error(f"Ожидался pandas.DataFrame для norma_data_df, получен {type(norma_data_df)}")
                raise TypeError(f"Ожидался pandas.DataFrame для norma_data_df, получен {type(norma_data_df)}")

            chunk_df['auto'] = chunk_df['auto'].apply(lambda x: str(x).replace("UUID:'", "").replace("'", ""))
            logger.info(f"Чанк: {chunk_df.shape}, тип: {type(chunk_df)}, колонки: {chunk_df.columns.tolist()}")
            logger.info(f"Уникальные auto: {chunk_df['auto'].unique()[:5]}")

            preprocessed_df = preprocess(chunk_df)
            logger.info(
                f"Преобразовано: {preprocessed_df.shape}, тип: {type(preprocessed_df)}, колонки: {preprocessed_df.columns.tolist()}")
            if not isinstance(preprocessed_df, pd.DataFrame):
                logger.error(f"preprocess вернул {type(preprocessed_df)} вместо pandas.DataFrame")
                raise TypeError(f"preprocess вернул {type(preprocessed_df)} вместо pandas.DataFrame")

            car_data_df['id'] = car_data_df['id'].apply(lambda x: str(x).replace("UUID:'", "").replace("'", ""))
            logger.info(
                f"Car data: {car_data_df.shape}, тип: {type(car_data_df)}, колонки: {car_data_df.columns.tolist()}")
            logger.info(f"Уникальные id: {car_data_df['id'].unique()[:5]}")

            norma_data_df['sl_avto'] = norma_data_df['sl_avto'].apply(
                lambda x: str(x).replace("UUID:'", "").replace("'", ""))
            logger.info(
                f"Norma data: {norma_data_df.shape}, тип: {type(norma_data_df)}, колонки: {norma_data_df.columns.tolist()}")
            logger.info(f"Уникальные sl_avto: {norma_data_df['sl_avto'].unique()[:5]}")

            preprocessed_df['auto'] = preprocessed_df['auto'].apply(
                lambda x: str(x).replace("UUID:'", "").replace("'", ""))

            merged_df = merge(car_data_df, preprocessed_df)
            logger.info(f"Объединено: {merged_df.shape}, тип: {type(merged_df)}, колонки: {merged_df.columns.tolist()}")
            if not isinstance(merged_df, pd.DataFrame):
                logger.error(f"merge вернул {type(merged_df)} вместо pandas.DataFrame")
                raise TypeError(f"merge вернул {type(merged_df)} вместо pandas.DataFrame")
            if merged_df.empty:
                logger.warning(f"merged_df пустой для {report_query_id}. Проверьте ключи id и auto.")

            try:
                report_query = ReportQuery.objects.get(id=report_query_id)
                is_save_bad_data = report_query.is_save_bad_data
                logger.info(f"is_save_bad_data из ReportQuery: {is_save_bad_data}")
            except ReportQuery.DoesNotExist:
                logger.error(f"ReportQuery {report_query_id} не найден")
                is_save_bad_data = False
            result_df, bad_data_df = fuel_leak_calculate_standart(merged_df, norma_data_df,
                                                                  is_save_bad_data=is_save_bad_data)
            logger.info(
                f"Результат утечек: {result_df.shape}, тип: {type(result_df)}, колонки: {result_df.columns.tolist()}")
            if not isinstance(result_df, pd.DataFrame):
                logger.error(f"fuel_leak_calculate_standart вернул {type(result_df)} вместо pandas.DataFrame")
                raise TypeError(f"fuel_leak_calculate_standart вернул {type(result_df)} вместо pandas.DataFrame")
            leak_count = result_df['is_leak'].sum() if 'is_leak' in result_df.columns else 0
            logger.info(f"Количество утечек в чанке: {leak_count}")

            if is_save_bad_data and bad_data_df is not None and not bad_data_df.empty:
                logger.info(f"Сохранение bad_data для {report_query_id}, строк: {len(bad_data_df)}")
                cars = {str(car.id).replace("UUID:'", "").replace("'", ""): car for car in Car.objects.all().only('id')}
                with transaction.atomic():
                    for _, row in bad_data_df.iterrows():
                        try:
                            car_id = str(row['auto']).strip().lower()
                            car = cars.get(car_id)
                            if not car:
                                logger.warning(f"Автомобиль с id={car_id} не найден для bad_data")
                                continue
                            CarBadData.objects.create(
                                car_id=car,
                                reason=str(row['reason']).strip(),
                                datetime=pd.to_datetime(row.get('timestamp', datetime.now()), errors='coerce')
                            )
                            logger.debug(f"Сохранена запись CarBadData для car_id={car_id}")
                        except Exception as e:
                            logger.error(f"Ошибка сохранения CarBadData для car_id={car_id}: {e}")
                            continue
            ##TODO: Если нужна запись в флюкс
            ##write_to_influxdb(preprocessed_df, org_id, report_query_id)

            _log_resources("process_chunk")
            return result_df
        except Exception as e:
            logger.error(f"Ошибка в process_chunk для {report_query_id}: {e}")
            raise self.retry(exc=e)

    return _timeit("process_chunk", _process)

##TODO: НЕ ИСПОЛЬЗУЕТСЯ
@shared_task(
    bind=True,
    soft_time_limit=300,
    priority=5
)
def parse_cars_task(self, report_query_id):
    report_query = None
    organization = None
    try:
        logger.info(f"Парсинг автомобилей для отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.provider_id.org_id

        provider = report_query.provider_id
        if not provider:
            logger.error(f"Поставщик не указан для отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Поставщик не указан для отчёта {report_query_id}")
            return

        excel_extensions = ['csv', 'xls', 'xlsx']
        is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)

        if is_csv_provider:
            auto_media = Media.objects.filter(report_query_id=report_query, type="auto").first()
            if not auto_media:
                logger.error(f"Медиафайл автомобилей не найден для отчёта {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Медиафайл автомобилей не найден для отчёта {report_query_id}")
                return

            auto_file_path = auto_media.file.path
            logger.info(f"Обработка файла: {auto_file_path}")
            path = pathlib.Path(auto_file_path)

            try:
                parsed_cars = parse_cars(path)
            except Exception as e:
                logger.error(f"Ошибка чтения CSV {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Ошибка чтения файла автомобилей для отчёта {report_query_id}: {e}")
                return

            if parsed_cars.empty:
                logger.error(f"Пустой CSV в {path}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Нет данных в файле для отчёта {report_query_id}")
                return

            required_columns = ['guid', 'name', 'description']
            missing_columns = [col for col in required_columns if col not in parsed_cars.columns]
            if missing_columns:
                logger.error(f"Отсутствуют колонки: {missing_columns}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Отсутствуют колонки для отчёта {report_query_id}: {missing_columns}")
                return

            with transaction.atomic():
                for _, row in parsed_cars.iterrows():
                    try:
                        if not all(pd.notna(row[col]) for col in required_columns):
                            logger.warning(f"NaN в строке: {row}")
                            continue

                        car, created = Car.objects.get_or_create(
                            id=str(row['guid']).strip().lower(),
                            defaults={
                                'name': str(row['name']).strip(),
                                'description': str(row['description']).strip() if pd.notna(row['description']) else "",
                                'engine_type': str(row.get('sl_tip_dvigat', 0.0)).strip(),
                                'is_tarrified': True
                            }
                        )
                        if not created:
                            car.name = str(row['name']).strip()
                            car.description = str(row['description']).strip() if pd.notna(row['description']) else ""
                            car.is_tarrified = True
                            car.save()

                        provider.cars.add(car)
                        logger.debug(f"Автомобиль {row['guid']} сохранён")
                    except Exception as e:
                        logger.error(f"Ошибка сохранения {row['guid']}: {e}")
                        continue

            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Автомобили распарсены для отчёта {report_query_id}")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Автомобили распарсены для отчёта {report_query_id}")
            _log_resources("parse_cars_task")

        else:
            logger.info(f"Тестовый вызов провайдера {provider.name} для {report_query_id}")

    except SoftTimeLimitExceeded as e:
        logger.error(f"Превышено время в parse_cars_task для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено время в parse_cars_task для {report_query_id}")
        raise

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery или Media не найдены для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery или Media не найдены для {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в parse_cars_task для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в parse_cars_task для {report_query_id}: {e}")
        raise self.retry(exc=e)

##TODO: Не используется
@shared_task(
    bind=True,
    soft_time_limit=300,
    priority=5
)
def parse_norms_task(self, report_query_id):
    report_query = None
    organization = None
    try:
        logger.info(f"Парсинг норм для отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.provider_id.org_id

        provider = report_query.provider_id
        if not provider:
            logger.error(f"Поставщик не указан для отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Поставщик не указан для отчёта {report_query_id}")
            return

        excel_extensions = ['csv', 'xls', 'xlsx']
        is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)

        if is_csv_provider:
            norm_media = Media.objects.filter(report_query_id=report_query, type="norm").first()
            if not norm_media:
                logger.error(f"Медиафайл норм не найден для отчёта {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Медиафайл норм не найден для отчёта {report_query_id}")
                return

            if not ReportQuery.objects.filter(provider_id__org_id=organization, status="completed").exists():
                logger.error(f"Данные автомобилей для {organization.name} не завершены")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Сначала обработайте файл 'auto' для отчёта {report_query_id}")
                return

            norm_file_path = norm_media.file.path
            logger.info(f"Обработка норм: {norm_file_path}")
            path = pathlib.Path(norm_file_path)

            try:
                parsed_norms = parse_norms(path)
            except FileNotFoundError as e:
                logger.error(f"Файл не найден: {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Файл норм не найден для отчёта {report_query_id}")
                return
            except KeyError as e:
                logger.error(f"Ошибка ключей в {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Ошибка структуры для отчёта {report_query_id}: {e}")
                return

            with transaction.atomic():
                for _, row in parsed_norms.iterrows():
                    try:
                        if not all(pd.notna(row[col]) for col in PARSE_NORMS_OUTPUT_COLUMNS):
                            logger.warning(f"NaN в строке: {row}")
                            continue
                        car = Car.objects.get(id=str(row['auto']).strip().lower())
                        CarConsumption.objects.create(
                            car_id=car,
                            winter_volume=float(row['winter_norm']) if pd.notna(row['winter_norm']) else -1,
                            summer_volume=float(row['summer_norm']) if pd.notna(row['summer_norm']) else -1,
                            valid_period=pd.to_datetime(row['due'], errors='coerce').date() if pd.notna(
                                row['due']) else None
                        )
                    except Car.DoesNotExist:
                        logger.warning(f"Автомобиль {row['auto']} не найден")
                        continue
                    except (ValueError, TypeError) as e:
                        logger.error(f"Неверный тип в {row}: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Ошибка обработки {row}: {e}")
                        continue

            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Нормы распарсены для отчёта {report_query_id}")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Нормы распарсены для отчёта {report_query_id}")
            _log_resources("parse_norms_task")

        else:
            logger.info(f"Тестовый вызов провайдера {provider.name} для {report_query_id}")

    except SoftTimeLimitExceeded as e:
        logger.error(f"Превышено время в parse_norms_task для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено время в parse_norms_task для {report_query_id}")
        raise

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery или Media не найдены для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery или Media не найдены для {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в parse_norms_task для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в parse_norms_task для {report_query_id}: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    soft_time_limit=1000,
    priority=4
)
def save_leak_results(self, results, report_query_id):
    report_query = None
    organization = None
    try:
        logger.info(f"Сохранение утечек для {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.provider_id.org_id

        if results is None or not results:
            logger.error(f"Нет результатов для {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Нет результатов для отчёта {report_query_id}")
            return

        result_df = pd.concat([r for r in results if isinstance(r, pd.DataFrame) and not r.empty])
        if result_df.empty:
            logger.error(f"Объединённый result_df пустой для {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Результаты пусты для {report_query_id}")
            return

        logger.info(f"Объединённый DataFrame: {result_df.shape}, колонки: {result_df.columns.tolist()}")
        raw_csv_path = BASE_DIR / f"res_df_{report_query_id}.csv.gz"
        with gzip.open(raw_csv_path, "wt", encoding="utf-8") as f:
            result_df.to_csv(f, index=False)
        logger.info(f"Сохранён result_df в {raw_csv_path}")

        cars = {str(car.id).strip().lower(): car for car in Car.objects.all().only('id')}
        provider = report_query.provider_id

        with transaction.atomic():
            for _, row in result_df[result_df['is_leak']].iterrows():
                try:
                    car_id = str(row['auto']).strip().lower()
                    car = cars.get(car_id)
                    if not car:
                        car, created = Car.objects.get_or_create(
                            id=car_id,
                            defaults={
                                'name': str(row.get('name', 'Unknown')).strip(),
                                'description': " ",
                                'engine_type': str(row.get('sl_tip_dvigat', 0.0)).strip(),
                                'is_tarrified': True,
                            }
                        )
                        provider.cars.add(car)

                    CarReport.objects.create(
                        car_id=car,
                        datetime=pd.to_datetime(row['timestamp'], errors='coerce'),
                        volume=float(row['leak']) if pd.notna(row['leak']) else 0,
                        status=bool(row['is_leak'])
                    )
                    logger.debug(f"Сохранён отчёт для {car_id}")
                except Exception as e:
                    logger.error(f"Ошибка сохранения для {car_id}: {e}")
                    continue

        report_query.status = 'completed'
        report_query.save()
        logger.info(f"Утечки сохранены для {report_query_id}")
        send_telegram_message(organization.bot_token, organization.chat_id,
                              f"Утечки сохранены для {report_query_id}")
        _log_resources("save_leak_results")

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery не найден для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery не найден для {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в save_leak_results для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в save_leak_results для {report_query_id}: {e}")
        raise self.retry(exc=e)

##TODO: Не используется
@shared_task(
    bind=True,
    soft_time_limit=1000,
    priority=0
)
def check_and_process_raw_reports(self):
    report_query = None
    organization = None
    try:
        logger.info("Проверка незавершённых отчётов...")
        organizations = Organization.objects.all()

        for organization in organizations:
            report_query = ReportQuery.objects.filter(
                provider_id__org_id=organization,
                status="created"
            ).exclude(
                Q(status="completed") | Q(status="error")
            ).first()

            if not report_query:
                logger.info(f"Нет незавершённых ReportQuery для {organization.name}")
                continue

            provider = report_query.provider_id
            if not provider:
                logger.error(f"Поставщик не указан для {report_query.id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Поставщик не указан для {report_query.id}")
                continue

            excel_extensions = ['csv', 'xls', 'xlsx']
            is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)
            is_glonasssoft_provider = provider.name.lower() == "glonasssoft"

            if is_csv_provider or is_glonasssoft_provider:
                raw_media = Media.objects.filter(report_query_id=report_query, type="raw").first()
                if not raw_media:
                    logger.info(f"Сырой файл не найден для {report_query.id}")
                    continue

                if is_csv_provider:
                    if not ReportQuery.objects.filter(provider_id__org_id=organization, status="completed").exists():
                        logger.info(f"Данные автомобилей для {organization.name} не завершены")
                        continue
                    if not ReportQuery.objects.filter(provider_id__org_id=organization, status="completed").exists():
                        logger.info(f"Нормы для {organization.name} не завершены")
                        continue

                try:
                    ReportQuery.objects.get(id=report_query.id)
                    logger.info(f"Обработка для {report_query.id}")
                    process_raw_data_task.delay(report_query.id)
                except ReportQuery.DoesNotExist:
                    logger.error(f"ReportQuery {report_query.id} не найден")
                    continue
            else:
                logger.info(f"Тестовый вызов {provider.name} для {report_query.id}")

        _log_resources("check_and_process_raw_reports")
    except Exception as e:
        logger.error(f"Ошибка в check_and_process_raw_reports: {e}")
        raise self.retry(exc=e)

##TODO: Не используется
@shared_task(
    bind=True,
    soft_time_limit=300,
    priority=5
)
def parse_merged_data(self, report_query_id):
    report_query = None
    organization = None
    try:
        logger.info(f"Парсинг объединённых данных для {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.provider_id.org_id

        provider = report_query.provider_id
        if not provider:
            logger.error(f"Поставщик не указан для {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Поставщик не указан для {report_query_id}")
            return

        logger.debug(f"Провайдер: {provider.id}, имя: {provider.name}, cars: {hasattr(provider, 'cars')}")
        if not hasattr(provider, 'cars'):
            logger.error(f"Поле 'cars' отсутствует у {provider.id}")
            report_query.status = 'error'
            report_query.save()
            return

        excel_extensions = ['csv', 'xls', 'xlsx']
        is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)

        if is_csv_provider:
            media = Media.objects.filter(report_query_id=report_query, type="auto").first()
            if not media:
                logger.error(f"Медиафайл не найден для {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Медиафайл не найден для {report_query_id}")
                return

            norm_file_path = media.file.path
            logger.info(f"Обработка файла: {norm_file_path}")
            path = pathlib.Path(norm_file_path)

            try:
                cars, norms = parse_merged_util(path)
            except FileNotFoundError as e:
                logger.error(f"Файл не найден: {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Файл не найден для {report_query_id}")
                return

            except KeyError as e:
                logger.error(f"Ошибка ключей в {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Ошибка структуры для {report_query_id}: {e}")
                return

            with transaction.atomic():
                for obj in cars:
                    try:
                        car, created = Car.objects.get_or_create(
                            id=str(obj.id).strip().lower(),
                            defaults={
                                'name': str(obj.name).strip(),
                                'description': str(obj.description).strip() if pd.notna(obj.description) else "",
                                'engine_type': str(obj.engine_type).strip() if pd.notna(obj.engine_type) else 0.0,
                                'is_tarrified': True
                            }
                        )
                        if not created:
                            car.name = str(obj.name).strip()
                            car.description = str(obj.description).strip() if pd.notna(obj.description) else ""
                            car.is_tarrified = True
                            car.save()

                        logger.debug(f"Добавляем {car.id} к {provider.id}")
                        provider.cars.add(car)
                        logger.debug(f"Автомобиль {car.id} сохранён")
                    except Exception as e:
                        logger.error(f"Ошибка сохранения {obj.id}: {e}")
                        continue

                for norm in norms:
                    try:
                        car = Car.objects.get(id=str(norm.auto).strip().lower())
                        CarConsumption.objects.create(
                            car_id=car,
                            winter_volume=float(norm.winter_norm) if pd.notna(norm.winter_norm) else -1,
                            summer_volume=float(norm.summer_norm) if pd.notna(norm.summer_norm) else -1,
                            valid_period=pd.to_datetime(norm.due, errors='coerce').date() if pd.notna(
                                norm.due) else None
                        )
                    except Car.DoesNotExist:
                        logger.warning(f"Автомобиль {norm.auto} не найден")
                        continue
                    except (ValueError, TypeError) as e:
                        logger.error(f"Неверный тип в {norm}: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Ошибка обработки {norm}: {e}")
                        continue

            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Объединённые данные распарсены для {report_query_id}")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Объединённые данные распарсены для {report_query_id}")
            _log_resources("parse_merged_data")

        else:
            logger.info(f"Тестовый вызов провайдера {provider.name} для {report_query_id}")

    except SoftTimeLimitExceeded as e:
        logger.error(f"Превышено время в parse_merged_data для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено время в parse_merged_data для {report_query_id}")
        raise

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery или Media не найдены для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery или Media не найдены для {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в parse_merged_data для {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в parse_merged_data для {report_query_id}: {e}")
        raise self.retry(exc=e)
