import os
import warnings

from influxdb_client.client import influxdb_client
from influxdb_client.client.warnings import MissingPivotFunction
from influxdb_client.client.write_api import WriteOptions

INFLUXDB_URL = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN',
                           'JNlfb4sd67Ahd5BgBRtjcvOSdCR5K8Scq9P705hn1A_fljE7n_1FdcNodVhT-giLZTLFfZZjjB3OJeNgF66-Ug==')
INFLUXDB_ORG = os.getenv('INFLUXDB_ORG', 'fuel_patrol')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET', 'test_fuel')

warnings.simplefilter("ignore", MissingPivotFunction)


def get_influx_write_client():
    if hasattr(get_influx_write_client, 'api') and hasattr(get_influx_write_client, 'client'):
        return get_influx_write_client.client, get_influx_write_client.api

    client = influxdb_client.InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    write_api = client.write_api(write_options=WriteOptions(batch_size=50_000, flush_interval=10_000))
    get_influx_write_client.client = client
    get_influx_write_client.api = write_api
    return client, write_api
