import datetime
import logging
import os
import warnings
from typing import List, Dict, Any

import pandas as pd
from django.conf import settings
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.warnings import MissingPivotFunction
from influxdb_client.client.write_api import WriteOptions

INFLUXDB_URL = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN',
                           'JNlfb4sd67Ahd5BgBRtjcvOSdCR5K8Scq9P705hn1A_fljE7n_1FdcNodVhT-giLZTLFfZZjjB3OJeNgF66-Ug==')
INFLUXDB_ORG = os.getenv('INFLUXDB_ORG', 'fuel_patrol')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET', 'test_fuel')

warnings.simplefilter("ignore", MissingPivotFunction)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def write_to_influxdb(preprocessed_df, org_id, report_query_id):
    """
    Записывает данные в InfluxDB.

    Args:
        preprocessed_df: DataFrame с предобработанными данными.
        org_id: ID организации.
        report_query_id: ID запроса отчёта.
    """
    try:
        # Проверяем, что URL — это строка
        url = settings.INFLUXDB_CONFIG['URL']
        if not isinstance(url, str):
            raise ValueError(f"InfluxDB URL must be a string, got {type(url)}: {url}")
        logger.debug(f"InfluxDB URL: {url}")

        # Создаём клиент с использованием контекстного менеджера
        with InfluxDBClient(url=url,
                            token=settings.INFLUXDB_CONFIG['TOKEN'],
                            org=settings.INFLUXDB_CONFIG['ORG'],
                            timeout=settings.INFLUXDB_CONFIG['TIMEOUT']) as client:
            write_api = client.write_api(
                write_options=WriteOptions(
                    batch_size=settings.INFLUXDB_CONFIG['WRITE_OPTIONS']['batch_size'],
                    flush_interval=settings.INFLUXDB_CONFIG['WRITE_OPTIONS']['flush_interval']
                )
            )
            points = []
            for _, row in preprocessed_df.iterrows():
                point = Point(f"preprocessed_data:{org_id}") \
                    .tag("organization", str(org_id)) \
                    .tag("auto", str(row['auto'])) \
                    .field("pos_s", float(row['pos_s']) if pd.notna(row['pos_s']) else 0.0) \
                    .field("calc_sensors_fuel_level", 0.0) \
                    .field("spent_fuel", 0) \
                    .time(row['timestamp'])
                points.append(point)

            with write_api:
                write_api.write(bucket=INFLUXDB_BUCKET, record=points)
            logger.info(
                f"Сохранено {len(points)} предобработанных точек в InfluxDB для запроса отчёта {report_query_id}")
    except Exception as e:
        logger.error(f"Ошибка при записи в InfluxDB для запроса отчёта {report_query_id}: {e}")
        raise

def query_influxdb(org_id: str, car_id: str, metric: str, period_from: datetime = None,
                   period_due: datetime = None, agg_window: str = None, agg_func: str = None) -> List[Dict[str, Any]]:
    """
    Выполняет запрос к InfluxDB для получения метрик автомобиля.

    Args:
        org_id (str): ID организации.
        car_id (str): ID автомобиля.
        metric (str): Метрика для фильтрации ('fuel_level', 'speed' или None для всех).
        period_from (datetime, optional): Начало периода.
        period_due (datetime, optional): Конец периода.
        agg_window (str, optional): Окно агрегации (например, '1h').
        agg_func (str, optional): Функция агрегации (например, 'mean').

    Returns:
        List[Dict[str, Any]]: Список словарей с метриками в формате {'x': timestamp, 'y': value, 'metric': metric_name}.

    Raises:
        Exception: Если запрос к InfluxDB завершился с ошибкой.
    """
    try:
        # Проверяем, что URL — это строка
        url = settings.INFLUXDB_CONFIG['URL']
        if not isinstance(url, str):
            raise ValueError(f"InfluxDB URL must be a string, got {type(url)}: {url}")
        logger.debug(f"InfluxDB URL: {url}")

        # Формируем range_clause
        range_clause = "range(start: -10y)"
        if period_from and period_due:
            range_clause = f'range(start: {period_from.strftime("%Y-%m-%dT00:00:00Z")}, stop: {period_due.strftime("%Y-%m-%dT23:59:59Z")})'
        elif period_from:
            range_clause = f'range(start: {period_from.strftime("%Y-%m-%dT00:00:00Z")})'
        elif period_due:
            range_clause = f'range(start: -10y, stop: {period_due.strftime("%Y-%m-%dT23:59:59Z")})'

        # Формируем фильтр по метрике
        metric_filter = 'r["_field"] == "calc_sensors_fuel_level" or r["_field"] == "pos_s"'
        if metric == 'fuel_level':
            metric_filter = 'r["_field"] == "calc_sensors_fuel_level"'
        elif metric == 'speed':
            metric_filter = 'r["_field"] == "pos_s"'

        # Формируем Flux-запрос
        base_query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
            |> {range_clause}
            |> filter(fn: (r) => r["_measurement"] == "preprocessed_data:{org_id}")
            |> filter(fn: (r) => r["auto"] == "{car_id}")
            |> filter(fn: (r) => {metric_filter})
        '''

        if agg_window and agg_func:
            base_query += f'|> aggregateWindow(every: {agg_window}, fn: {agg_func}, createEmpty: false)'

        logger.info(f"Flux query: {base_query}")

        # Выполняем запрос с использованием контекстного менеджера
        with InfluxDBClient(url=settings.INFLUXDB_CONFIG['URL'],
                            token=settings.INFLUXDB_CONFIG['TOKEN'],
                            org=settings.INFLUXDB_CONFIG['ORG'],
                            timeout=settings.INFLUXDB_CONFIG['TIMEOUT']) as client:
            query_api = client.query_api()
            tables = query_api.query(base_query)
            logger.info(f"Выполнен запрос к InfluxDB для автомобиля {car_id}")

            # Формируем результат
            result = [
                {
                    "x": record.get_time().strftime('%Y-%m-%d %H:%M:%S'),
                    "y": float(record.get_value()),
                    "metric": "fuel_level" if record["_field"] == "calc_sensors_fuel_level" else "speed"
                }
                for table in tables
                for record in table.records
            ]

        return result

    except Exception as e:
        logger.error(f"Ошибка при запросе данных из InfluxDB для автомобиля {car_id}: {e}")
        raise