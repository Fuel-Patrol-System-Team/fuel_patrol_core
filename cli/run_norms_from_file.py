import os
import sys
import argparse

import polars as pl

from core.services.providers.car_consumption_service import CarConsumptionService
from core.tests.test_parser_calc import NormsService
pl.enable_string_cache()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

import django
django.setup()

import app.tasks  # noqa: E402,F401 — must be imported before the providers below
                  # to break a circular import (car_data_service -> app.tasks -> fuelreport_service -> car_data_service)

from core.models import Car, CarPrimary
from core.services.providers.car_data_service import CarDataService
from core.services.providers.leaks_service import LeaksService


_CSV_SCHEMA_OVERRIDES = {
    "auto": pl.Categorical,
    "timestamp": pl.Datetime,
    "timestamp_server": pl.Datetime,
    "rpm": pl.Int32,
    "mileage": pl.Float32,
    "motohours": pl.Float32,
}


def _run_norms_from_file(data_slice: pl.DataFrame, car_id: str):
    car = (
        Car.objects
        .filter(id=car_id)
        .first()
    )
    if car is None:
        print(f"Car {car_id} is not found at all")
        return None
    
    auto_data = CarDataService.prepare_auto_data(car)
    primary = CarDataService.calculate_primary_single(data_slice, auto_data)
    if primary is not None:
        status = CarDataService.save_primary_to_db(car, primary)
        norms = NormsService.calculate_norms_single(data_slice, primary, auto_data)
        if norms is not None:
            CarConsumptionService.save_consumption_rates(norms, car)

def main():
    parser = argparse.ArgumentParser("compute", "compute leaks from file")
    parser.add_argument("file")
    args = parser.parse_args()

    data = pl.read_csv(args.file, schema_overrides=_CSV_SCHEMA_OVERRIDES)
    car_id = data["auto"].first()
    result = _run_norms_from_file(data, car_id)
    if result is not None:
        print(result)


if __name__ == "__main__":
    main()
