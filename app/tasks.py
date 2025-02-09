import logging
from celery import chord, shared_task
from app.celery import app as celery_app
import pandas as pd
import polars as pl
from core.services.preprocessing.utils import fuel_leak_calculate_standart, merge, preprocess

# Настройка логирования
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Размер батча
BATCH_SIZE = 500_000


## Важное пояснение, т.к. виндоус тупой - я не могу запустить мультипроцессинг у себя просто так, я тестировал это всё
## с командой, указаной в readme.md - если захочешь у себя попробовать сделать на разных воркерах в мультипотоке - напиши команду ниже при запуске
## celery -A app worker --loglevel=info --concurrency=4 --queues=high_priority,medium_priority,low_priority


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=60,  # Ограничение по времени выполнения (либо убери, либо поиграйся)
    priority=10,  # ПРИОРИТЕТ ОТ 0 до 10, чем выше, тем лучше
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
def calcualate_leak_task(self):
    try:
        logger.info("Loading datasets...")
        auto_df = pd.read_csv("./app/datasets/auto_info.csv")
        norma_df = pd.read_csv("./app/datasets/mart_norm_rasx_topl_202401261739.csv")

        # Загружаем батчи
        chunk_df = pd.read_csv("./app/datasets/auto.csv", usecols=['timestamp', 'calc_sensors_fuel_level', 'pos_s', 'calc_sensors_voltage', 'auto'], dtype={'auto': str, 'calc_sensors_fuel_level': float, 'pos_s': float, 'timestamp': str,  'calc_sensors_voltage': float}, chunksize=BATCH_SIZE)
        logger.info(f"Loaded batches for processing.")

        # Генерируем задачи для каждого батча
        task_group = chord(
            (thread_task.s(auto_df, norma_df, batch) for batch in chunk_df),
            merge_parts_task.s()
        )

        logger.info("Dispatching tasks...")
        result = task_group.apply_async()
        logger.info(f"Task dispatched successfully. Task ID: {result.id}")

    except Exception as e:
        logger.error(f"Error in calcualate_leak_task: {e}")
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    soft_time_limit=10,
    priority=2,
)
def test_task(self):
    try:
        logger.info("Executing test task...")
        with open("test.asdf", "w") as file:
            file.write("result")
        logger.info("Test task completed successfully.")
    except Exception as e:
        logger.error(f"Error in test_task: {e}")
        raise self.retry(exc=e)
