import datetime

from numpy import shape
import pytest
import polars as pl

from core.services.providers.car_sensors_raw_parser import CarSensorsRawParser


@pytest.mark.django_db
def test_parser_charts_mileage_part():
    parser = CarSensorsRawParser("87c04b05-4571-4eab-9c81-b91b9d8eb413", datetime.datetime(2025, 7, 14, 0, 0, 0, 0), datetime.datetime(2025, 7, 16, 0, 0, 0))
    result = parser.parse_raw_data()
    result_df = pl.DataFrame(result, infer_schema_length=None)
    assert "mileage" in result_df.columns
    assert "timestamp" in result_df.columns
    assert result_df.shape[0] > 0

@pytest.mark.django_db
def test_parser_charts_motohours_part():
    parser = CarSensorsRawParser("87c04b05-4571-4eab-9c81-b91b9d8eb413", datetime.datetime(2025, 7, 14, 0, 0, 0, 0), datetime.datetime(2025, 7, 16, 0, 0, 0), "motohours")
    result = parser.parse_raw_data()
    result_df = pl.DataFrame(result, infer_schema_length=None)
    assert "motohours" in result_df.columns
    assert "timestamp" in result_df.columns
    assert result_df.shape[0] > 0

@pytest.mark.django_db
def test_parser_charts_fuel_part():
    parser = CarSensorsRawParser("87c04b05-4571-4eab-9c81-b91b9d8eb413", datetime.datetime(2025, 7, 14, 0, 0, 0, 0), datetime.datetime(2025, 7, 16, 0, 0, 0), "fuel")
    result = parser.parse_raw_data()
    result_df = pl.DataFrame(result, infer_schema_length=None)
    assert "calc_sensors_fuel_level" in result_df.columns
    assert "timestamp" in result_df.columns
    assert "calc_sensors_voltage" in result_df.columns
    assert result_df.shape[0] > 0