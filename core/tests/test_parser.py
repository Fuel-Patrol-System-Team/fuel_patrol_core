import datetime

import pytest
import polars as pl

from app.tasks import DataProvider
from core.models import Car
from core.services.providers.car_sensors_raw_parser import CarSensorsRawParser
from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.mileage_calculation_service import MileageAlgorithms, MileageCalculationService
from core.services.providers.motohours_calculation_service import MotohoursCalculationService
def wrapper_for_parser(car_id: str):
    car = Car.objects.select_related("sensors").get(id=car_id)
    provider = car.data_providers.first()
    return {"car": car, "provider": provider}

test_start_date =  datetime.datetime(2025, 1, 1)
test_end_date =  datetime.datetime(2025, 6, 1)

@pytest.mark.django_db
def test_parser_raw_simple():
    provider = DataProvider.objects.last()
    car = provider.cars.filter(input__gt=1).first()
    assert car is not None
    assert provider is not None
    parser = GlonassGeneralProvider(None, car, provider, test_start_date, test_end_date, "raw"  )
    status, result = parser.parse_raw_data("raw", True, car)
    status, result = parser.parse_raw_data("raw", True, car)
    status, result = parser.parse_raw_data("raw", True, car)
    assert status == True
    assert isinstance( result , pl.DataFrame)
    assert "auto" in result.columns
    
    
@pytest.mark.django_db
def test_parser_multiple_cars_mapped():
    provider = DataProvider.objects.first()
    cars = provider.cars.filter(input__gt=1)[:2]


    parser = GlonassGeneralProvider(cars, None, provider, datetime.datetime(2025, 1, 1), datetime.datetime(2025, 7, 1), "raw_mapped" )
    result = parser.parse_raw_data_all()
    assert result['status'] == 'completed'
    assert result['total_cars'] == 2
    assert result['processed_cars'] == 2
    
@pytest.mark.django_db
def test_parser_multiple_cars():
    provider = DataProvider.objects.first()
    cars = provider.cars.filter(input__gt=1)[:2]
    

    parser = GlonassGeneralProvider(cars, None, provider, datetime.datetime(2025, 1, 1), datetime.datetime(2025, 7, 1), "raw" )
    result = parser.parse_raw_data_all()
    assert result['status'] == 'completed'
    assert result['total_cars'] == 2
    assert result['processed_cars'] == 2

@pytest.mark.django_db
def test_parser_general_mileage():
    result, status_code = MileageCalculationService.calculate_mileage(
            car_id="973b4e73-a5e0-43c5-bfba-354f8046922c",
            agg=None,
            alg = MileageAlgorithms.fraud,
            start_date=datetime.datetime(2025, 1, 22,),
            end_date=datetime.datetime(2025, 1, 23,),
            is_save_bad_data=False
    )
    result = result['result']
    assert status_code == 200
    assert result['travel_fraud'] > 0

@pytest.mark.django_db
def test_parser_general_motohours():
    result, status = MotohoursCalculationService.calculate_motohours(
        car_id="6af9e785-936a-499d-bdf0-5e961a58acab",
        agg=None,
        start_date=datetime.datetime(2025, 10, 3),
        end_date=datetime.datetime(2025, 10, 4),
        
    )
    result = result["result"]
    assert status == 200
    assert result['motohours'] > 0

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