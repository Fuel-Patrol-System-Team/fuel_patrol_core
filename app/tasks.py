import logging
import pathlib
from datetime import datetime

import numpy as np
from celery import chord, shared_task, chain
from celery.exceptions import SoftTimeLimitExceeded
from influxdb_client import Point
import pandas as pd
import polars as pl
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist
from app.celery import app as celery_app
from app.settings import BASE_DIR
from core.models import ReportQuery, Media, Organization, Car, CarReport, CarConsumption
from core.services.csv_parsing.utils import PARSE_NORMS_OUTPUT_COLUMNS, PARSE_NORMS_REQUIRED_COLUMNS, parse_cars, parse_merged_util, \
    parse_norms
from core.services.databases.influx_db import get_influx_write_client, INFLUXDB_BUCKET
from core.services.notifications.tg_bot import send_telegram_message
from core.services.preprocessing.utils import fuel_leak_calculate_standart, preprocess, merge

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BATCH_SIZE = 500_00

celery_app.conf.task_concurrency = 4

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=5,
    rate_limit="10/m"
)
def parse_cars_task(self, report_query_id):
    report_query = None
    organization = None

    try:
        logger.info(f"Начат парсинг автомобилей для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        auto_media = Media.objects.filter(report_query=report_query, type="auto").first()
        if not auto_media:
            logger.error(f"Медиафайл автомобилей не найден для запроса отчёта {report_query_id}.")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Медиафайл автомобилей не найден для запроса отчёта {report_query_id}.")
            return

        auto_file_path = auto_media.file.path
        logger.info(f"Обработка файла автомобилей: {auto_file_path}")
        path = pathlib.Path(auto_file_path)
        logger.info(f"Путь к файлу: {path}")

        logger.info("Начало парсинга CSV файла автомобилей...")
        try:
            parsed_cars = parse_cars(path)

        except Exception as e:
            logger.error(f"Ошибка чтения CSV файла {path}: {e}")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка чтения файла автомобилей для запроса отчёта {report_query_id}: {e}")
            return

        if parsed_cars.empty:
            logger.error(f"Пустой CSV файл или нет валидных данных в {path}")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Нет валидных данных об автомобилях в файле для запроса отчёта {report_query_id}")
            return

        logger.info(
            f"Итоговый DataFrame автомобилей: {parsed_cars.shape}, колонки: {parsed_cars.columns.tolist()}, пример: {parsed_cars.head().to_dict()}")

        required_columns = ['guid', 'name', 'description']
        missing_columns = [col for col in required_columns if col not in parsed_cars.columns]
        if missing_columns:
            logger.error(f"Отсутствуют обязательные колонки в parsed_cars: {missing_columns}")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Отсутствуют колонки в данных автомобилей для запроса отчёта {report_query_id}: {missing_columns}")
            return

        with transaction.atomic():
            for _, row in parsed_cars.iterrows():
                try:
                    if not all(pd.notna(row[col]) for col in required_columns):
                        logger.warning(f"Отсутствуют или содержат NaN значения в строке: {row}")
                        continue

                    car, created = Car.objects.get_or_create(
                        id=str(row['guid']),
                        defaults={
                            'name': str(row['name']).strip(),
                            'description': str(row['description']).strip() if pd.notna(row['description']) else "",
                            'organization': organization,
                            'engine_type': str(row['sl_tip_dvigat']).strip() if pd.notna(row['sl_tip_dvigat']) else ""
                        }
                    )
                    if not created:
                        car.name = str(row['name']).strip()
                        car.description = str(row['description']).strip() if pd.notna(row['description']) else ""
                        car.save()
                    logger.debug(f"Автомобиль {row['guid']} сохранён или обновлён: {car.name}")
                except Exception as e:
                    logger.error(f"Ошибка при сохранении автомобиля {row['guid']}: {e}")
                    continue

        if report_query:
            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Данные автомобилей успешно распарсены и сохранены для запроса отчёта {report_query_id}.")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Данные автомобилей успешно распарсены и сохранены для запроса отчёта {report_query_id}.")

    except SoftTimeLimitExceeded as e:
        logger.error(f"Превышено временное ограничение в parse_cars_task для запроса отчёта {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено временное ограничение в parse_cars_task для запроса отчёта {report_query_id}")
        raise

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery или Media не найдены для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery или Media не найдены для запроса отчёта {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в parse_cars_task для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в parse_cars_task для запроса отчёта {report_query_id}: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=5,
    rate_limit="10/m"
)
def parse_norms_task(self, report_query_id):
    report_query = None
    organization = None

    try:
        logger.info(f"Начат парсинг норм для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        norm_media = Media.objects.filter(report_query=report_query, type="norm").first()
        if not norm_media:
            logger.error(f"Медиафайл норм не найден для запроса отчёта {report_query_id}.")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Медиафайл норм не найден для запроса отчёта {report_query_id}.")
            return

        if not ReportQuery.objects.filter(organization=organization, media__type="auto", status="completed").exists():
            logger.error(f"Данные об автомобилях для организации {organization.name} не завершены.")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Сначала загрузите и обработайте файл 'auto' для запроса отчёта {report_query_id}")
            return

        norm_file_path = norm_media.file.path
        logger.info(f"Обработка файла норм: {norm_file_path}")
        path = pathlib.Path(norm_file_path)
        logger.info(f"Путь к файлу: {path}")

        error = None
        try:
            parsed_norms = parse_norms(path)
        except FileNotFoundError:
            error = f"Файл по пути не найден: {path}"
        except KeyError as error:
            error = f"{error}"

        if error != None:
            logger.error(error)
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"{error}")

        with transaction.atomic():
            for _, row in parsed_norms.iterrows():
                try:
                    if not all(pd.notna(row[col]) for col in PARSE_NORMS_OUTPUT_COLUMNS):
                        logger.warning(f"Отсутствуют или содержат NaN значения в строке: {row}")
                        continue
                    car = Car.objects.get(id=str(row['auto']), organization=organization)
                    CarConsumption.objects.create(
                        car=car,
                        winter_volume=float(row['winter_norm']) if pd.notna(row['winter_norm']) else -1,
                        summer_volume=float(row['summer_norm']) if pd.notna(row['summer_norm']) else -1,
                        valid_period=pd.to_datetime(row['due'], errors='coerce').date() if pd.notna(
                            row['due']) else None
                    )
                except Car.DoesNotExist:
                    logger.warning(f"Автомобиль с id {row['auto']} не найден для организации {organization.name}.")
                    continue
                except (ValueError, TypeError) as e:
                    logger.error(f"Неверный тип данных в строке {row}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Неожиданная ошибка при обработке строки {row}: {e}")
                    continue

        if report_query:
            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Данные норм успешно распаршены и сохранены для запроса отчёта {report_query_id}.")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Данные норм успешно распарсены и сохранены для запроса отчёта {report_query_id}.")

    except SoftTimeLimitExceeded as e:
        logger.error(f"Превышено временное ограничение в parse_norms_task для запроса отчёта {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено временное ограничение в parse_norms_task для запроса отчёта {report_query_id}")
        raise

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery или Media не найдены для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery или Media не найдены для запроса отчёта {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в parse_norms_task для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в parse_norms_task для запроса отчёта {report_query_id}: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=1800,
    priority=2,
    rate_limit="2/m"
)
def process_raw_data_task(self, report_query_id):
    report_query = None
    organization = None
    try:
        logger.info(f"Начат обработка сырых данных для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        if not ReportQuery.objects.filter(organization=organization, media__type="auto", status="completed").exists():
            logger.error(f"Данные об автомобилях для организации {organization.name} не завершены")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Сначала загрузите и обработайте файл 'auto' для запроса отчёта {report_query_id}")
            return

        raw_media = Media.objects.filter(report_query=report_query, type="raw").first()
        if not raw_media:
            logger.error(f"Сырой медиафайл не найден для запроса отчёта {report_query_id}.")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Сырой медиафайл не найден для запроса отчёта {report_query_id}.")
            return
        # тут проблема
        raw_df = pl.read_csv(raw_media.file.path, 
                             columns=['timestamp', 'calc_sensors_fuel_level', 'pos_s', 'calc_sensors_voltage', 'auto'],
                             try_parse_dates=True,
                             batch_size=BATCH_SIZE,
                             dtypes={'auto': pl.Utf8}
                             )
        logger.info(f"Загружено {raw_df.height} строк сырых данных для обработки. Колонки: {raw_df.columns}")

        cars = Car.objects.filter(organization=organization).select_related('organization')
        car_data = pl.from_pandas(pd.DataFrame(list(cars.values('id', 'name', 'engine_type'))).astype({'id': str}))
        car_data = car_data.rename({'engine_type': 'sl_tip_dvigat'})
        logger.info(
            f"Данные автомобилей: {car_data.shape}, колонки: {car_data.columns}, пример ID: {car_data['id'].head().to_list()}")

        consumptions = CarConsumption.objects.filter(car__organization=organization).select_related('car')
        norma_df = pl.from_pandas(pd.DataFrame(list(consumptions.values(
            'car__id', 'winter_volume', 'summer_volume', 'valid_period'
        ))).astype({'car__id': str})).rename({
            'car__id': 'sl_avto',
            'winter_volume': 'norma_rasx_winter',
            'summer_volume': 'norma_rasx_summer',
            'valid_period': 'period'
        })
        print(norma_df.columns)
        logger.info(
            f"Данные норм: {norma_df.shape}, колонки: {norma_df.columns}, пример sl_avto: {norma_df['sl_avto'].head().to_list()}")

        base_dir = pathlib.Path(settings.BASE_DIR)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_id = report_query_id

        # TODO: УДАЛИТЬ ПРИ РЕЛИЗЕ
        raw_df_pandas = raw_df.to_pandas()
        raw_csv_path = base_dir / f"raw_df_{report_id}_{timestamp}.csv"
        raw_df_pandas.to_csv(raw_csv_path, index=False)
        logger.info(f"Сохранён raw_df в {raw_csv_path} как CSV")

        car_data_pandas = car_data.to_pandas()
        car_csv_path = base_dir / f"car_data_{report_id}_{timestamp}.csv"
        car_data_pandas.to_csv(car_csv_path, index=False)
        logger.info(f"Сохранён car_data в {car_csv_path} как CSV")

        norma_df_pandas = norma_df.to_pandas()
        norma_csv_path = base_dir / f"norma_df_{report_id}_{timestamp}.csv"
        norma_df_pandas.to_csv(norma_csv_path, index=False)
        logger.info(f"Сохранён norma_df в {norma_csv_path} как CSV")

        report_query.status = 'pending'
        report_query.save()

        chunks = raw_df.partition_by(by=["auto"], maintain_order=False, as_dict=False)
        logger.info(f"Разделено на {len(chunks)} чанков по машинам")

        chunk_tasks = [
            process_chunk.s(chunk.to_pandas(), car_data.to_pandas(), norma_df.to_pandas(), report_query_id,
                            organization.id)
            for chunk in chunks
        ]

        chord(chunk_tasks)(save_leak_results.s(report_query_id)).on_error(
            lambda *args, **kwargs: save_leak_results.apply_async(args=[None, report_query_id])
        )

        report_query.flux_parsed = True
        report_query.save()
        logger.info(f"Завершена обработка сырых данных для запроса отчёта {report_query_id}.")

    except SoftTimeLimitExceeded as e:
        logger.error(
            f"Превышено временное ограничение в process_raw_data_task для запроса отчёта {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено временное ограничение в process_raw_data_task для запроса отчёта {report_query_id}")
        raise

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery или Media не найдены для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery или Media не найдены для запроса отчёта {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в process_raw_data_task для запроса отчёта {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в process_raw_data_task для запроса отчёта {report_query_id}: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=3,
)
def process_chunk(self, chunk_df, car_data, norma_data, report_query_id, org_id):
    try:
        logger.info(f"Обработка чанка с {len(chunk_df)} строками для запроса отчёта {report_query_id}")

        chunk_pl = pl.from_pandas(chunk_df)
        chunk_pl = chunk_pl.with_columns(pl.col('auto').cast(pl.Utf8))
        chunk_pandas = chunk_pl.to_pandas()

        preprocessed_df = preprocess(chunk_pandas)
        logger.info(
            f"Преобразованные данные (preprocess): {preprocessed_df.shape}, колонки: {preprocessed_df.columns.tolist()}")

        if isinstance(car_data, pl.DataFrame):
            car_data_pandas = car_data.to_pandas()
        else:
            car_data_pandas = car_data
        logger.info(f"Car data в формате pandas: {car_data_pandas.shape}, колонки: {car_data_pandas.columns.tolist()}")

        if isinstance(norma_data, pl.DataFrame):
            norma_data_pandas = norma_data.to_pandas()
        else:
            norma_data_pandas = norma_data
        logger.info(
            f"Norma data в формате pandas: {norma_data_pandas.shape}, колонки: {norma_data_pandas.columns.tolist()}")

        car_data_pandas['id'] = car_data_pandas['id'].astype(str)
        norma_data_pandas['sl_avto'] = norma_data_pandas['sl_avto'].astype(str)

        merged_df = merge(car_data_pandas, preprocessed_df)
        logger.info(
            f"Объединённые данные (merge): {merged_df.shape}, колонки: {merged_df.columns.tolist()}")

        result_df = fuel_leak_calculate_standart(merged_df, norma_data_pandas)
        logger.info(
            f"Результат расчёта утечек: {result_df.shape}, колонки: {result_df.columns.tolist()}")

        client, write_api = get_influx_write_client()
        points = []
        for _, row in preprocessed_df.iterrows():
            point = Point(f"preprocessed_data:{org_id}") \
                .tag("organization", str(org_id)) \
                .tag("auto", str(row['auto'])) \
                .field("pos_s", float(row['pos_s']) if pd.notna(row['pos_s']) else 0.0) \
                .field("calc_sensors_fuel_level", float(row['max_fuel']) if pd.notna(row['max_fuel']) else 0.0) \
                .field("spent_fuel", float(row['spent_fuel']) if pd.notna(row['spent_fuel']) else 0.0) \
                .time(row['timestamp'])
            points.append(point)

        write_api.write(bucket=INFLUXDB_BUCKET, record=points)
        logger.info(f"Сохранено {len(points)} предобработанных точек в InfluxDB для запроса отчёта {report_query_id}")
        client.close()

        return result_df

    except Exception as e:
        logger.error(f"Ошибка в process_chunk для запроса отчёта {report_query_id}: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=600,
    priority=4,
)
def save_leak_results(self, results, report_query_id):
    report_query = None
    organization = None
    try:
        logger.info(f"Сохранение результатов утечек для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        if results is None or not results:
            logger.error(f"Нет результатов для сохранения для запроса отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            return

        result_df = pd.concat(results)
        logger.info(f"Объединённый DataFrame результатов: {result_df.shape}, колонки: {result_df.columns.tolist()}")
        raw_csv_path = BASE_DIR / f"res_df.csv"
        result_df.to_csv(raw_csv_path, index=False)
        cars = {car.id: car for car in Car.objects.filter(organization=organization).only('id')}
        logger.info(f"Загружено {len(cars)} автомобилей для организации {organization.name}")

        with transaction.atomic():
            for _, row in result_df[result_df['is_leak']].iterrows():
                try:
                    car_id = str(row['auto'])
                    car, created = Car.objects.get_or_create(id=car_id, defaults={
                        'name': str(row['name']).strip(),
                        'description': " ",
                        'organization': organization,
                        'engine_type': str(row['sl_tip_dvigat']).strip() if pd.notna(row['sl_tip_dvigat']) else ""
                    })

                    CarReport.objects.create(
                        car=car,
                        datetime=pd.to_datetime(row['timestamp'], errors='coerce'),
                        volume=float(row['leak']) if pd.notna(row['leak']) else 0,
                        status=bool(row['is_leak'])
                    )
                    logger.debug(f"Сохранён отчёт об утечке для автомобиля {car_id}")
                except Exception as e:
                    logger.error(f"Ошибка при сохранении отчёта для автомобиля {car_id}: {e}")
                    continue

        report_query.status = 'completed'
        report_query.save()
        logger.info(f"Результаты утечек сохранены для запроса отчёта {report_query_id}.")
        send_telegram_message(organization.bot_token, organization.chat_id,
                              f"Результаты утечек сохранены для запроса отчёта {report_query_id}.")

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery не найден для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery не найден для запроса отчёта {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в save_leak_results для запроса отчёта {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в save_leak_results для запроса отчёта {report_query_id}: {e}")
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=0,
)
def check_and_process_raw_reports(self):
    report_query = None
    organization = None
    try:
        logger.info("Проверка незавершённых отчётов сырых данных...")
        organizations = Organization.objects.all()

        for organization in organizations:
            report_query = ReportQuery.objects.filter(
                organization=organization,
                media__type="raw",
                status="created"
            ).exclude(
                Q(status="completed") | Q(status="error")
            ).first()

            if not report_query:
                continue

            if not ReportQuery.objects.filter(organization=organization, media__type="auto",
                                              status="completed").exists():
                logger.info(f"Данные об автомобилях для организации {organization.name} не завершены")
                continue
            if not ReportQuery.objects.filter(organization=organization, media__type="norm",
                                              status="completed").exists():
                logger.info(f"Данные норм для организации {organization.name} не завершены")
                continue

            logger.info(f"Обработка сырых данных для запроса отчёта {report_query.id}")
            process_raw_data_task.delay(report_query.id)

    except Exception as e:
        logger.error(f"Ошибка в check_and_process_raw_reports: {e}")
        raise self.retry(exc=e)

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3, # бесполезно из-за условия фильтра
    retry_backoff=True,
    soft_time_limit=300,
    priority=5,
    rate_limit="10/m"
)
def parse_merged_data(self, report_query_id):
    report_query = None
    organization = None

    try:
        logger.info(f"Начат парсинг норм для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        media = Media.objects.filter(report_query=report_query, type="auto").first()
        if not media:
            logger.error(f"Медиафайл норм не найден для запроса отчёта {report_query_id}.")
            if report_query:
                report_query.status = 'error'
                report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Медиафайл норм не найден для запроса отчёта {report_query_id}.")
            return

        norm_file_path = media.file.path
        logger.info(f"Обработка файла машин и норм: {norm_file_path}")
        path = pathlib.Path(norm_file_path)
        logger.info(f"Путь к файлу: {path}")

        error = None
        try:

            cars, norms = parse_merged_util(path)
        except FileNotFoundError:
            error = f"Файл по пути не найден: {path}"
        except KeyError as error:
            error = f"{error}"

        if error != None:
            logger.error(error)
            if report_query:
                report_query.status = 'error'
                report_query.save()
            # send_telegram_message(organization.bot_token, organization.chat_id,
            #                       f"{error}")

        with transaction.atomic():
            for obj in cars:
                try:
                    # TODO: обработаь иначе если надо
                    # if not all(pd.notna(row[col]) for col in required_columns):
                        # logger.warning(f"Отсутствуют или содержат NaN значения в строке: {index}")
                        # continue
                    car, created = Car.objects.get_or_create(
                        id=obj.id,
                        defaults={
                            'name': str(obj.name).strip(),
                            'description': str(obj.description).strip() if pd.notna(obj.description) else "",
                            'organization': organization,
                            'engine_type': str(obj.engine_type).strip() if pd.notna(obj.engine_type) else ""
                        }
                    )
                    if not created:
                        car.name = str(obj.name).strip()
                        car.description = str(obj.description).strip() if pd.notna(obj.description) else ""
                        car.save()
                    logger.debug(f"Автомобиль {car.id} сохранён или обновлён: {car.name}")
                except Exception as e:
                    logger.error(f"Ошибка при сохранении автомобиля {car.id}: {e}")
                    continue
            for index, norm in enumerate( norms):
                try:
                    car = Car.objects.get(id=str(norm.car), organization=organization)
                    CarConsumption.objects.create(
                        car=car,
                        winter_volume=float(norm.winter_norm) if pd.notna(norm.winter_norm) else -1,
                        summer_volume=float(norm.summer_norm) if pd.notna(norm.summer_norm) else -1,
                        valid_period=pd.to_datetime(norm.due).date() if pd.notna(
                            norm.due) else None
                    )
                except Car.DoesNotExist:
                    logger.warning(f"Автомобиль с id {norm.car} не найден для организации {organization.name}.")
                    continue
                except (ValueError, TypeError) as e:
                    logger.error(f"Неверный тип данных в строке {index}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Неожиданная ошибка при обработке строки {index}: {e}")
                continue

                    
        if report_query:
            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Данные норм и машин успешно распаршены и сохранены для запроса отчёта {report_query_id}.")
            # send_telegram_message(organization.bot_token, organization.chat_id,
            #                     f"Данные норм и машин успешно распарсены и сохранены для запроса отчёта {report_query_id}.")

    except SoftTimeLimitExceeded as e:
        logger.error(f"Превышено временное ограничение в parse_norms_task для запроса отчёта {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено временное ограничение в parse_norms_task для запроса отчёта {report_query_id}")
        raise

    except ObjectDoesNotExist as e:
        logger.error(f"ReportQuery или Media не найдены для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"ReportQuery или Media не найдены для запроса отчёта {report_query_id}")
        raise self.retry(exc=e)

    except Exception as e:
        logger.error(f"Ошибка в parse_norms_task для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в parse_norms_task для запроса отчёта {report_query_id}: {e}")
        raise self.retry(exc=e)
