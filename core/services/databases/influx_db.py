import warnings

from influxdb_client.client import influxdb_client
from influxdb_client.client.warnings import MissingPivotFunction

warnings.simplefilter("ignore", MissingPivotFunction)

def get_influx_query_client():
    if hasattr(get_influx_query_client, 'api') and hasattr(get_influx_query_client, 'client'):
        return get_influx_query_client.client, get_influx_query_client.api

    client = influxdb_client.InfluxDBClient.from_env_properties()
    get_influx_query_client.client = client
    get_influx_query_client.api = client.query_api()
    return get_influx_query_client.client, get_influx_query_client.api