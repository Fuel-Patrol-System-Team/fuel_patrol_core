import logging
import os
import pathlib
from datetime import datetime

from celery import chord, shared_task, chain
from django.db import close_old_connections
from influxdb_client import Point
import pandas as pd
import polars as pl

from app.celery import app as celery_app
from django.conf import settings
from core.models import ReportQuery, Media, Organization, Car, CarReport
from core.services.databases.influx_db import get_influx_write_client, INFLUXDB_BUCKET
from core.services.notifications.tg_bot import send_telegram_message
from core.services.preprocessing.utils import fuel_leak_calculate_standart, merge, preprocess

# Настройка логирования
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Размер батча
BATCH_SIZE = 500_000


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


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=5,
)
def process_report_query(self, report_query_id):
    try:
        logger.info(f"Processing report query {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

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

        logger.info(f"Loaded batches for processing for organization {organization.name}.")
        send_telegram_message(organization.bot_token, organization.chat_id,
                              f"Loaded batches for processing for organization {organization.name}.")

        report_query.status = 'pending'
        report_query.save()

        task_chain = chord(
            [thread_task.s(auto_df, norma_df, batch) for batch in chunk_df],
            merge_parts_task.s() | save_leak_results.s(report_query_id)
        ).on_error(save_leak_results.s(report_query_id))

        task_chain.apply_async()

    except Exception as e:
        logger.error(f"Error in process_report_query: {e}")
        report_query.status = 'error'
        report_query.save()
        send_telegram_message(organization.bot_token, organization.chat_id, f"Error in process_report_query: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=5,
)
def save_leak_results(self, result_df, report_query_id):
    try:
        logger.info(f"Saving leak results for report query {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        for _, row in result_df[result_df['is_leak']].iterrows():
            car, _ = Car.objects.get_or_create(name=row['auto'], organization=organization)
            CarReport.objects.create(
                car=car,
                datetime=row['timestamp'],
                volume=row['spent_fuel'],
                status=row['is_leak']
            )

        report_query.status = 'completed'
        report_query.save()
        logger.info(f"Leak results saved for report query {report_query_id}.")
        send_telegram_message(organization.bot_token, organization.chat_id,
                              f"Leak results saved for report query {report_query_id}.")

    except Exception as e:
        logger.error(f"Error in save_leak_results: {e}")
        report_query.status = 'error'
        report_query.save()
        send_telegram_message(organization.bot_token, organization.chat_id, f"Error in save_leak_results: {e}")
        raise self.retry(exc=e)

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=1,
    retry_backoff=True,
    soft_time_limit=300,
    priority=1,
)
def calculate_leak_task(self, report_query_id):
    try:
        logger.info(f"Starting leak calculation for report query {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        process_report_query.delay(report_query.id)

    except Exception as e:
        logger.error(f"Error in calculate_leak_task: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=5,
)
def process_raw_data_to_influx(self, report_query_id):
    try:
        logger.info(f"Starting processing raw data to InfluxDB for report query {report_query_id}...")
        report_query = ReportQuery.objects.get(id=report_query_id)
        organization = report_query.organization

        raw_media = Media.objects.filter(report_query=report_query, type="raw").first()
        if not raw_media:
            logger.info(f"No raw file found for report query {report_query.id}.")
            return

        file_path = raw_media.file.path
        logger.info(f"Processing raw file: {file_path}")
        chunk_size = 100_000
        chunks = pd.read_csv(file_path,
                             usecols=['timestamp', 'pos_s', 'calc_sensors_fuel_level', 'calc_sensors_voltage',
                                      'auto'],
                             chunksize=chunk_size)

        client, write_api = get_influx_write_client()
        logger.info(f"InfluxDB client initialized. Bucket: {INFLUXDB_BUCKET}")

        for chunk in chunks:
            points = []
            for _, row in chunk.iterrows():
                try:
                    timestamp = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
                except ValueError as e:
                    logger.error(f"Invalid timestamp format in row: {row}. Error: {e}")
                    continue
                pos_s = float(row['pos_s']) if pd.notna(row['pos_s']) else None
                point = Point(f"raw_data:{organization.id}") \
                    .tag("organization", organization.name) \
                    .tag("auto", row['auto']) \
                    .field("pos_s", pos_s) \
                    .field("calc_sensors_fuel_level", float(row['calc_sensors_fuel_level'])) \
                    .field("calc_sensors_voltage", float(row['calc_sensors_voltage'])) \
                    .time(timestamp)

                points.append(point)

            logger.info(f"Processed {len(points)} points for organization {organization.name}.")
            write_api.write(bucket=INFLUXDB_BUCKET, record=points)

        logger.info(f"Raw data for organization {organization.name} has been processed and saved to InfluxDB.")
        report_query.flux_parsed = True
        report_query.save()
        client.close()

    except Exception as e:
        logger.error(f"Error in process_raw_data_to_influx: {e}")
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    soft_time_limit=300,
    priority=0,
)
def check_and_process_reports(self):
    try:
        logger.info("Starting report processing pipeline...")

        organizations = Organization.objects.all()

        for organization in organizations:
            report_query = ReportQuery.objects.filter(organization=organization, flux_parsed=False).exclude(status='completed').last()
            if not report_query:
                logger.info(f"No pending report queries found for organization {organization.name}.")
                continue

            logger.info(f"Checking report query {report_query.id} for organization {organization.name}.")
            send_telegram_message(organization.bot_token, organization.chat_id,
                                  f"Checking report query {report_query.id} for organization {organization.name}.")

            chain(
                process_raw_data_to_influx.s(report_query.id),
                calculate_leak_task.si(report_query.id)
            ).apply_async()

    except Exception as e:
        logger.error(f"Error in check_and_process_reports: {e}")
        raise self.retry(exc=e)