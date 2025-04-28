import logging
import pathlib
from datetime import datetime

from celery import chord, shared_task
from celery.exceptions import SoftTimeLimitExceeded
import pandas as pd
import polars as pl
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist
from app.celery import app as celery_app
from app.settings import BASE_DIR
from core.models import ReportQuery, Media, Organization, Car, CarReport, CarConsumption, DataProvider
from core.services.csv_parsing.utils import PARSE_NORMS_OUTPUT_COLUMNS, parse_cars, \
    parse_merged_util, \
    parse_norms
from core.services.data_providers.utils import save_response_to_file, provider_factory
from core.services.databases.influx_db import INFLUXDB_BUCKET, write_to_influxdb
from core.services.notifications.tg_bot import send_telegram_message
from core.services.preprocessing.utils import fuel_leak_calculate_standart, preprocess, merge

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BATCH_SIZE = 500_00

celery_app.conf.task_concurrency = 4

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    soft_time_limit=300,
    priority=5
)
def parse_cars_task(self, report_query_id):
    report_query = None
    organization = None

    try:
        logger.info(f"Начат парсинг автомобилей для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization_id

        provider = report_query.provider_id
        if not provider:
            logger.error(f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            return

        excel_extensions = ['csv', 'xls', 'xlsx']
        is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)

        if is_csv_provider:
            auto_media = Media.objects.filter(report_query_id=report_query, type="auto").first()
            if not auto_media:
                logger.error(f"Медиафайл автомобилей не найден или неверный тип для запроса отчёта {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Медиафайл автомобилей не найден для запроса отчёта {report_query_id}")
                return

            auto_file_path = auto_media.file.path
            logger.info(f"Обработка файла автомобилей от CSV-провайдера: {auto_file_path}")
            path = pathlib.Path(auto_file_path)

            try:
                parsed_cars = parse_cars(path)
            except Exception as e:
                logger.error(f"Ошибка чтения CSV файла {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Ошибка чтения файла автомобилей для запроса отчёта {report_query_id}: {e}")
                return

            if parsed_cars.empty:
                logger.error(f"Пустой CSV файл или нет валидных данных в {path}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Нет валидных данных об автомобилях в файле для запроса отчёта {report_query_id}")
                return

            required_columns = ['guid', 'name', 'description']
            missing_columns = [col for col in required_columns if col not in parsed_cars.columns]
            if missing_columns:
                logger.error(f"Отсутствуют обязательные колонки в parsed_cars: {missing_columns}")
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
                                'engine_type': str(row['sl_tip_dvigat']).strip() if pd.notna(
                                    row['sl_tip_dvigat']) else 0.0
                            }
                        )
                        if not created:
                            car.name = str(row['name']).strip()
                            car.description = str(row['description']).strip() if pd.notna(row['description']) else ""
                            car.save()


                        provider.cars.add(car)
                        logger.debug(f"Автомобиль {row['guid']} сохранён или обновлён: {car.name}")
                    except Exception as e:
                        logger.error(f"Ошибка при сохранении автомобиля {row['guid']}: {e}")
                        continue

            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Данные автомобилей успешно распарсены и сохранены для запроса отчёта {report_query_id}")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Данные автомобилей успешно распарсены и сохранены для запроса отчёта {report_query_id}")

        else:
            logger.info(f"Тестовый вызов к внешнему сервису провайдера {provider.name} для запроса {report_query_id}")
            pass

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
    soft_time_limit=300,
    priority=5
)
def parse_norms_task(self, report_query_id):
    report_query = None
    organization = None

    try:
        logger.info(f"Начат парсинг норм для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization_id

        provider = report_query.provider_id
        if not provider:
            logger.error(f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            return

        excel_extensions = ['csv', 'xls', 'xlsx']
        is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)

        if is_csv_provider:
            norm_media = Media.objects.filter(report_query_id=report_query, type="norm").first()
            if not norm_media:
                logger.error(f"Медиафайл норм не найден или неверный тип для запроса отчёта {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Медиафайл норм не найден для запроса отчёта {report_query_id}")
                return

            if not ReportQuery.objects.filter(organization_id=organization, status="completed").exists():
                logger.error(f"Данные об автомобилях для организации {organization.name} не завершены")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Сначала загрузите и обработайте файл 'auto' для запроса отчёта {report_query_id}")
                return

            norm_file_path = norm_media.file.path
            logger.info(f"Обработка файла норм: {norm_file_path}")
            path = pathlib.Path(norm_file_path)

            try:
                parsed_norms = parse_norms(path)
            except FileNotFoundError as e:
                logger.error(f"Файл по пути не найден: {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Файл норм не найден для запроса отчёта {report_query_id}")
                return
            except KeyError as e:
                logger.error(f"Ошибка ключей в файле норм {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Ошибка в структуре файла норм для запроса отчёта {report_query_id}: {e}")
                return

            with transaction.atomic():
                for _, row in parsed_norms.iterrows():
                    try:
                        if not all(pd.notna(row[col]) for col in PARSE_NORMS_OUTPUT_COLUMNS):
                            logger.warning(f"Отсутствуют или содержат NaN значения в строке: {row}")
                            continue
                        car = Car.objects.get(id=str(row['auto']))
                        CarConsumption.objects.create(
                            car_id=car,
                            winter_volume=float(row['winter_norm']) if pd.notna(row['winter_norm']) else -1,
                            summer_volume=float(row['summer_norm']) if pd.notna(row['summer_norm']) else -1,
                            valid_period=pd.to_datetime(row['due'], errors='coerce').date() if pd.notna(
                                row['due']) else None
                        )
                    except Car.DoesNotExist:
                        logger.warning(f"Автомобиль с id {row['auto']} не найден")
                        continue
                    except (ValueError, TypeError) as e:
                        logger.error(f"Неверный тип данных в строке {row}: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Неожиданная ошибка при обработке строки {row}: {e}")
                        continue

            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Данные норм успешно распаршены и сохранены для запроса отчёта {report_query_id}")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Данные норм успешно распарсены и сохранены для запроса отчёта {report_query_id}")

        else:
            logger.info(f"Тестовый вызов к внешнему сервису провайдера {provider.name} для запроса {report_query_id}")
            pass

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
        organization = report_query.organization_id

        provider = report_query.provider_id
        if not provider:
            logger.error(f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            return

        excel_extensions = ['csv', 'xls', 'xlsx']
        is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)
        is_glonasssoft_provider = provider.name.lower() == "glonasssoft"

        logger.info(
            f"Провайдер: {provider.name}, is_csv_provider: {is_csv_provider}, is_glonasssoft_provider: {is_glonasssoft_provider}")

        if is_csv_provider or is_glonasssoft_provider:
            if is_csv_provider and not ReportQuery.objects.filter(organization_id=organization,
                                                                  status="completed").exists():
                logger.error(f"Данные об автомобилей для организации {organization.name} не завершены")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Сначала загрузите и обработайте файл 'auto' для запроса отчёта {report_query_id}")
                return

            raw_media = Media.objects.filter(report_query_id=report_query, type="raw").first()
            if not raw_media:
                logger.error(f"Сырой медиафайл не найден для запроса отчёта {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Сырой медиафайл не найден для запроса отчёта {report_query_id}")
                return

            raw_df = pl.read_csv(
                raw_media.file.path,
                columns=['timestamp', 'calc_sensors_fuel_level', 'pos_s', 'calc_sensors_voltage', 'auto'],
                try_parse_dates=True,
                batch_size=BATCH_SIZE,
                dtypes={'auto': pl.Utf8}
            )
            logger.info(f"Загружено {raw_df.height} строк сырых данных для обработки. Колонки: {raw_df.columns}")


            if is_glonasssoft_provider:
                cars = Car.objects.filter(id__in=provider.cars.values_list('id', flat=True)).select_related()
            else:
                cars = Car.objects.all().select_related()

            if not cars.exists():
                logger.error(f"Не найдено автомобилей для обработки в запросе {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Не найдено автомобилей для обработки в запросе {report_query_id}")
                return

            car_data = pl.from_pandas(pd.DataFrame(list(cars.values('id', 'name', 'engine_type', 'in', 'out'))).astype({'id': str}))
            car_data = car_data.rename({'engine_type': 'sl_tip_dvigat'})
            logger.info(
                f"Данные автомобилей: {car_data.shape}, колонки: {car_data.columns}, пример ID: {car_data['id'].head().to_list()}")

            if is_glonasssoft_provider:
                consumptions = CarConsumption.objects.filter(
                    car_id__in=provider.cars.values_list('id', flat=True)).select_related('car_id')
            else:
                consumptions = CarConsumption.objects.all().select_related('car_id')

            if not consumptions.exists():
                logger.error(f"Не найдено норм для обработки в запросе {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Не найдено норм для обработки в запросе {report_query_id}")
                return

            norma_df = pl.from_pandas(pd.DataFrame(list(consumptions.values(
                'car_id__id', 'winter_volume', 'summer_volume', 'valid_period'
            ))).astype({'car_id__id': str})).rename({
                'car_id__id': 'sl_avto',
                'winter_volume': 'norma_rasx_winter',
                'summer_volume': 'norma_rasx_summer',
                'valid_period': 'period'
            })
            logger.info(
                f"Данные норм: {norma_df.shape}, колонки: {norma_df.columns}, пример sl_avto: {norma_df['sl_avto'].head().to_list()}")

            base_dir = pathlib.Path(settings.BASE_DIR)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_id = report_query_id

            # TODO: УДАЛИТЬ ПРИ РЕЛИЗЕ И ДОБАВИТЬ НЕДОСТАЮЩИЙ КОД РАСЧЁТОВ
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

            chord(chunk_tasks)(save_leak_results.s(report_query_id))

            report_query.save()
            logger.info(f"Завершена обработка сырых данных для запроса отчёта {report_query_id}")

        else:
            logger.info(f"Тестовый вызов к внешнему сервису провайдера {provider.name} для запроса {report_query_id}")
            pass

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
    soft_time_limit=1000,
    priority=3
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
        preprocessed_df['auto'] = preprocessed_df['auto'].astype(str)
        norma_data_pandas['sl_avto'] = norma_data_pandas['sl_avto'].astype(str)

        merged_df = merge(car_data_pandas, preprocessed_df)
        logger.info(
            f"Объединённые данные (merge): {merged_df.shape}, колонки: {merged_df.columns.tolist()}")

        result_df = fuel_leak_calculate_standart(merged_df, norma_data_pandas)
        logger.info(
            f"Результат расчёта утечек: {result_df.shape}, колонки: {result_df.columns.tolist()}")

        write_to_influxdb(preprocessed_df, org_id, report_query_id)

        return result_df

    except Exception as e:
        logger.error(f"Ошибка в process_chunk для запроса отчёта {report_query_id}: {e}")
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
        logger.info(f"Сохранение результатов утечек для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization_id

        if results is None or not results:
            logger.error(f"Нет результатов для сохранения для запроса отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            return

        result_df = pd.concat(results)
        logger.info(f"Объединённый DataFrame результатов: {result_df.shape}, колонки: {result_df.columns.tolist()}")
        raw_csv_path = BASE_DIR / f"res_df.csv"
        result_df.to_csv(raw_csv_path, index=False)

        cars = {car.id: car for car in Car.objects.all().only('id')}
        provider = report_query.provider_id

        with transaction.atomic():
            for _, row in result_df[result_df['is_leak']].iterrows():
                try:
                    car_id = str(row['auto'])
                    car = cars.get(car_id)
                    if not car:
                        car, created = Car.objects.get_or_create(
                            id=car_id,
                            defaults={
                                'name': str(row['name']).strip(),
                                'description': " ",
                                'engine_type': str(row['sl_tip_dvigat']).strip() if pd.notna(
                                    row['sl_tip_dvigat']) else 0.0
                            }
                        )

                        provider.cars.add(car)

                    CarReport.objects.create(
                        car_id=car,
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
        logger.info(f"Результаты утечек сохранены для запроса отчёта {report_query_id}")
        send_telegram_message(organization.bot_token, organization.chat_id,
                              f"Результаты утечек сохранены для запроса отчёта {report_query_id}")

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
    soft_time_limit=1000,
    priority=0
)
def check_and_process_raw_reports(self):
    report_query = None
    organization = None
    try:
        logger.info("Проверка незавершённых отчётов сырых данных...")
        organizations = Organization.objects.all()

        for organization in organizations:
            report_query = ReportQuery.objects.filter(
                organization_id=organization,
                status="created"
            ).exclude(
                Q(status="completed") | Q(status="error")
            ).first()

            if not report_query:
                logger.info(f"Нет незавершённых ReportQuery для организации {organization.name}")
                continue

            provider = report_query.provider_id
            if not provider:
                logger.error(f"Поставщик данных не указан для запроса отчёта {report_query.id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Поставщик данных не указан для запроса отчёта {report_query.id}")
                continue

            excel_extensions = ['csv', 'xls', 'xlsx']
            is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)
            is_glonasssoft_provider = provider.name.lower() == "glonasssoft"

            if is_csv_provider or is_glonasssoft_provider:
                raw_media = Media.objects.filter(report_query_id=report_query, type="raw").first()
                if not raw_media:
                    logger.info(f"Сырой медиафайл не найден для запроса отчёта {report_query.id}")
                    continue


                if is_csv_provider:
                    if not ReportQuery.objects.filter(organization_id=organization, status="completed").exists():
                        logger.info(f"Данные об автомобилях для организации {organization.name} не завершены")
                        continue
                    if not ReportQuery.objects.filter(organization_id=organization, status="completed").exists():
                        logger.info(f"Данные норм для организации {organization.name} не завершены")
                        continue


                try:
                    ReportQuery.objects.get(id=report_query.id)
                    logger.info(f"Обработка сырых данных для запроса отчёта {report_query.id}")
                    process_raw_data_task.delay(report_query.id)
                except ReportQuery.DoesNotExist:
                    logger.error(f"ReportQuery с ID {report_query.id} не найден перед вызовом process_raw_data_task.")
                    continue
            else:
                logger.info(
                    f"Тестовый вызов к внешнему сервису провайдера {provider.name} для запроса {report_query.id}")
                pass

    except Exception as e:
        logger.error(f"Ошибка в check_and_process_raw_reports: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    soft_time_limit=300,
    priority=5
)
def parse_merged_data(self, report_query_id):
    report_query = None
    organization = None

    try:
        logger.info(f"Начат парсинг объединённых данных для запроса отчёта {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization_id

        provider = report_query.provider_id
        if not provider:
            logger.error(f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            report_query.status = 'error'
            report_query.save()
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Поставщик данных не указан для запроса отчёта {report_query_id}")
            return

        logger.debug(f"Провайдер: {provider.id}, имя: {provider.name}, есть поле cars: {hasattr(provider, 'cars')}")
        if not hasattr(provider, 'cars'):
            logger.error(f"Поле 'cars' отсутствует у провайдера {provider.id}")
            report_query.status = 'error'
            report_query.save()
            return

        excel_extensions = ['csv', 'xls', 'xlsx']
        is_csv_provider = any(ext in provider.name.lower() for ext in excel_extensions)

        if is_csv_provider:
            media = Media.objects.filter(report_query_id=report_query, type="auto").first()
            if not media:
                logger.error(
                    f"Медиафайл объединённых данных не найден или неверный тип для запроса отчёта {report_query_id}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Медиафайл объединённых данных не найден для запроса отчёта {report_query_id}")
                return

            norm_file_path = media.file.path
            logger.info(f"Обработка файла машин и норм: {norm_file_path}")
            path = pathlib.Path(norm_file_path)

            try:
                cars, norms = parse_merged_util(path)
            except FileNotFoundError as e:
                logger.error(f"Файл по пути не найден: {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Файл объединённых данных не найден для запроса отчёта {report_query_id}")
                return
            except KeyError as e:
                logger.error(f"Ошибка ключей в файле {path}: {e}")
                report_query.status = 'error'
                report_query.save()
                send_telegram_message(organization.bot_token, organization.chat_id,
                                      f"Ошибка в структуре файла для запроса отчёта {report_query_id}: {e}")
                return

            with transaction.atomic():
                for obj in cars:
                    try:
                        car, created = Car.objects.get_or_create(
                            id=obj.id,
                            defaults={
                                'name': str(obj.name).strip(),
                                'description': str(obj.description).strip() if pd.notna(obj.description) else "",
                                'engine_type': str(obj.engine_type).strip() if pd.notna(obj.engine_type) else 0.0
                            }
                        )
                        if not created:
                            car.name = str(obj.name).strip()
                            car.description = str(obj.description).strip() if pd.notna(obj.description) else ""
                            car.save()


                        logger.debug(f"Добавляем машину {car.id} к провайдеру {provider.id}")
                        provider.cars.add(car)
                        logger.debug(f"Автомобиль {car.id} сохранён или обновлён: {car.name}")
                    except Exception as e:
                        logger.error(f"Ошибка при сохранении автомобиля {obj.id}: {e}")
                        continue

                for norm in norms:
                    try:
                        car = Car.objects.get(id=str(norm.car))
                        CarConsumption.objects.create(
                            car_id=car,
                            winter_volume=float(norm.winter_norm) if pd.notna(norm.winter_norm) else -1,
                            summer_volume=float(norm.summer_norm) if pd.notna(norm.summer_norm) else -1,
                            valid_period=pd.to_datetime(norm.due).date() if pd.notna(norm.due) else None
                        )
                    except Car.DoesNotExist:
                        logger.warning(f"Автомобиль с id {norm.car} не найден")
                        continue
                    except (ValueError, TypeError) as e:
                        logger.error(f"Неверный тип данных в норме {norm}: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Неожиданная ошибка при обработке нормы {norm}: {e}")
                        continue

            report_query.status = 'completed'
            report_query.save()
            logger.info(f"Данные норм и машин успешно распарсены и сохранены для запроса отчёта {report_query_id}")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Данные норм и машин успешно распарсены и сохранены для запроса отчёта {report_query_id}")

        else:
            logger.info(f"Тестовый вызов к внешнему сервису провайдера {provider.name} для запроса {report_query_id}")
            pass

    except SoftTimeLimitExceeded as e:
        logger.error(f"Превышено временное ограничение в parse_merged_data для запроса отчёта {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Превышено временное ограничение в parse_merged_data для запроса отчёта {report_query_id}")
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
        logger.error(f"Ошибка в parse_merged_data для report_query_id {report_query_id}: {e}")
        if report_query:
            report_query.status = 'error'
            report_query.save()
        if organization:
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Ошибка в parse_merged_data для запроса отчёта {report_query_id}: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    soft_time_limit=300,
    priority=5
)
def fetch_data_from_provider(self, provider_name: str, metadata: dict, report_query_id: str):
    """Задача для получения данных от провайдера."""
    report_query = None
    try:
        logger.info(f"Запуск задачи для получения данных от провайдера {provider_name}...")

        report_query = ReportQuery.objects.get(id=report_query_id)

        provider = provider_factory(provider_name, metadata, report_query_id)
        if not provider:
            logger.error(f"Не удалось создать провайдера {provider_name}.")
            report_query.status = "error"
            report_query.save()
            return

        if not provider.authenticate():
            logger.error("Не удалось авторизоваться у провайдера.")
            report_query.status = "error"
            report_query.save()
            return

        vehicles_data = provider.get_vehicles(name=None)
        if vehicles_data is None:
            logger.error("Не удалось получить данные автомобилей.")
            report_query.status = "error"
            report_query.save()
            return

        save_response_to_file(vehicles_data, "response.json")
        report_query.status = "completed"
        report_query.save()


        try:
            ReportQuery.objects.get(id=report_query_id)
            logger.info(f"Запуск задачи process_raw_data_task для report_query_id={report_query_id}")
            process_raw_data_task.delay(report_query_id)
        except ReportQuery.DoesNotExist:
            logger.error(f"ReportQuery с ID {report_query_id} не найден перед вызовом process_raw_data_task.")
            return

        logger.info("Задача fetch_data_from_provider успешно завершена.")

    except ReportQuery.DoesNotExist:
        logger.error(f"Заявка с ID {report_query_id} не найдена.")
        if report_query:
            report_query.status = "error"
            report_query.save()
        raise
    except Exception as e:
        logger.error(f"Ошибка в задаче fetch_data_from_provider: {e}")
        if report_query:
            report_query.status = "error"
            report_query.save()
        raise
