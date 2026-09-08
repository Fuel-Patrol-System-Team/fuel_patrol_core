import os
import sys
import argparse

import polars as pl

from cli.helpers.csv import write_csv_compute
pl.enable_string_cache()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

import django
django.setup()

import app.tasks  # noqa: E402,F401 — must be imported before the providers below
                  # to break a circular import (car_data_service -> app.tasks -> fuelreport_service -> car_data_service)

from core.models import Car
from core.services.providers.car_data_service import CarDataService
from core.services.providers.leaks_service import LeaksService


_CSV_SCHEMA_OVERRIDES = {
    "auto": pl.Categorical,
    "timestamp": pl.Datetime,
    "timestamp_server": pl.Datetime,
    "rpm": pl.Int32,
    "mileage": pl.Float32,
    "motohours": pl.Float32,
    "longitude": pl.Float32,
    "latitude": pl.Float32,
}


def _run_compute_from_file(data_slice: pl.DataFrame, car_id: str):
    car = None
    try:
        car = (
            
            Car.objects
            .select_related("carprimary")
            .prefetch_related("consumptions")
            .filter(id=car_id)
            .first()
        )
    except BaseException:
        print(f"Car {car_id} no primary or consumptions")
        return None
    if car is None:
        print(f"Car {car_id} is not found at all")
        return None
    primary_df = pl.DataFrame(car.carprimary.primary, schema_overrides={"auto": pl.Categorical})
    if car.consumptions.first() is None:
        print(f"Car has no carconsumption, skipping")
        return None
    norms_df = pl.DataFrame(
        car.consumptions.first().json_data, schema_overrides={"sl_avto": pl.Categorical}
    )
    auto_data = CarDataService.prepare_auto_data(car)
    leaks_service = LeaksService()
    sensors = CarDataService.get_car_sensors(car)
    result, intermediate, reports = leaks_service.compute_leaks(
        auto_data, data_slice, sensors, primary_df, norms_df
    )
    if result is None:
        print("result for computations in None check the underlying dataframe")
    return result


def main():
    parser = argparse.ArgumentParser("compute", "compute leaks from file")
    parser.add_argument("file")
    parser.add_argument("output_path")
    args = parser.parse_args()

    data = pl.read_csv(args.file, schema_overrides=_CSV_SCHEMA_OVERRIDES)
    car_id = data["auto"].first()
    result = _run_compute_from_file(data, car_id)
    if result is not None:
        write_csv_compute(result, args.output_path)
        return
    print("result for computations is zero")
    
        


if __name__ == "__main__":
    main()