import logging
import pathlib

from celery import chord, shared_task
from app.celery import app as celery_app
from django.conf import settings
import pandas as pd
import polars as pl

from core.models import ReportQuery, Media, Organization
from core.services.notifications.tg_bot import send_telegram_message
from core.services.preprocessing.utils import fuel_leak_calculate_standart, merge, preprocess

# Настройка логирования
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Размер батча
BATCH_SIZE = 500_000


## TODO: Сохранение raw_data в flux.
## TODO: Сохранение резульnатов о сливах и прочем в БД (в ТГ отправляй сообщение о конце обработке - считай количество и отправляй в телеграмм)
## TODO: Сценарий - если загружен только auto.csv

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=60,
    priority=10,
)
def thread_task(self, auto_df: pd.DataFrame, norma_df: pd.DataFrame, chunk_df: pl.DataFrame):
    try:
        logger.info(f"Processing batch with {chunk_df.shape[0]} rows")
        result_df = fuel_leak_calculate_standart(merge(auto_df, preprocess(chunk_df)), norma_rasx_df=norma_df)
        return result_df
    except Exception as e:
        logger.error(f"Error in thread_task: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=True,
    soft_time_limit=120,
    priority=5,
)
def merge_parts_task(self, results):
    try:
        logger.info("Merging results...")
        merge_result = pd.concat(results)
        logger.info(f"Merged DataFrame shape: {merge_result.shape}")
        merge_result = merge_result[['auto', 'timestamp', 'pos_s', 'spent_fuel', 'is_leak', 'leak']]
        return merge_result
    except Exception as e:
        logger.error(f"Error in merge_parts_task: {e}")
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=1,
    retry_backoff=True,
    soft_time_limit=300,
    priority=1,  # Низкий приоритет
)
def calculate_leak_task(self):
    try:
        logger.info("Loading datasets...")
        ## TODO : Брать не последнюю заявку в целом, а последнюю заявку компании, но смотреть все компании?
        ## TODO : Декомпозировать таск для процессинга отчёта на шейред и сделать таск для процесинга всех отчётов, изменить нейминги.
        ## TODO : Починить (сделать) мультипоток.
        report_query = ReportQuery.objects.exclude(status='completed').last()
        if not report_query:
            logger.info("No pending report queries found.")
            return

        organization = report_query.organization
        bot_token = organization.bot_token
        chat_id = organization.chat_id

        auto_df = Media.objects.get(report_query_id=report_query.id, type="auto")
        norma_df = Media.objects.get(report_query_id=report_query.id, type="norm")
        raw_df = Media.objects.get(report_query_id=report_query.id, type="raw")

        media_root = settings.MEDIA_ROOT
        auto_df_path = pathlib.Path.joinpath(media_root, str(auto_df.file))
        auto_df = pd.read_csv(f"{auto_df_path}")
        norm_df_path = pathlib.Path.joinpath(media_root, str(norma_df.file))
        raw_df_path = pathlib.Path.joinpath(media_root, str(raw_df.file))
        norma_df = pd.read_csv(f"{norm_df_path}")
        chunk_df = pd.read_csv(f"{raw_df_path}",
                               usecols=['timestamp', 'calc_sensors_fuel_level', 'pos_s', 'calc_sensors_voltage',
                                        'auto'],
                               dtype={'auto': str, 'calc_sensors_fuel_level': float, 'pos_s': float, 'timestamp': str,
                                      'calc_sensors_voltage': float}, chunksize=BATCH_SIZE)

        logger.info(f"Loaded batches for processing.")
        send_telegram_message(bot_token, chat_id, f"Loaded batches for processing.")

        report_query.status = 'pending'
        report_query.save()

        task_group = chord(
            (thread_task.s(auto_df, norma_df, batch) for batch in chunk_df),
            merge_parts_task.s()
        )

        logger.info("Dispatching tasks...")
        result = task_group.apply_async()
        logger.info(f"Task dispatched successfully. Task ID: {result.id}")
        send_telegram_message(bot_token, chat_id, f"Task dispatched successfully. Task ID: {result.id}")

        report_query.status = 'completed'
        report_query.save()

    except Exception as e:
        logger.error(f"Error in calculate_leak_task: {e}")
        report_query.status = 'error'
        report_query.save()
        send_telegram_message(bot_token, chat_id, f"Error in calculate_leak_task: {e}")
        raise self.retry(exc=e)
