import os
import sys
import argparse

import polars as pl

from core.helpers.mileage import mileage_test_fraud_new
from core.services.providers.mileage_calculation_service import (
    MileageCalculationService,
    MileageModes,
)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django

django.setup()
from core.services.providers.filtering_service import FilteringService
from core.services.providers.report_service import ReportService


from cli.helpers.csv import write_csv_compute

pl.enable_string_cache()


import app.tasks  # noqa: E402,F401 — must be imported before the providers below

# to break a circular import (car_data_service -> app.tasks -> fuelreport_service -> car_data_service)

from core.models import Car
from core.services.providers.car_data_service import CarDataService
from core.services.providers.leaks_service import LeaksService

_CSV_SCHEMA_OVERRIDES = {
    "auto": pl.Categorical,
    "timestamp": pl.Datetime,
    "timestamp_server": pl.Datetime,
    "rpm": pl.Float32,
    "mileage": pl.Float32,
    "speed_can": pl.Float32,
    "motohours": pl.Float32,
    "longitude": pl.Float32,
    "latitude": pl.Float32,
    "calc_sensors_fuel_level": pl.Float32,
}


def _run_mileage_from_file(data_slice: pl.DataFrame, car_id: str):
    car, provider_obj = MileageCalculationService._get_car_and_provider(car_id)
    if not car or not provider_obj:
        return {"error": "Автомобиль или провайдер не найдены"}, 400

    auto = CarDataService.prepare_auto_data(car)
    auto_record = auto.filter(pl.col("auto") == str(car_id)).to_dicts()[0]
    mode = MileageModes.standart
    agg = None
    force_chart = False
    sensor_speed_chart = "can"
    return_true_sensor_data = False
    result = mileage_test_fraud_new(
        car_id,
        data_slice,
        sensors,
        auto_record,
        agg,
        mode,
        sensor_chart=sensor_speed_chart,
        force_chart=force_chart,
        return_true_sensor_data=return_true_sensor_data,
    )
    return result


def main():
    parser = argparse.ArgumentParser("mileage", "compute mileage fraudfrom file")
    parser.add_argument("file")
    parser.add_argument("output_path")
    args = parser.parse_args()

    data = pl.read_csv(args.file, schema_overrides=_CSV_SCHEMA_OVERRIDES)
    car_id = data["auto"].first()
    result = _run_mileage_from_file(data, car_id)
    if result is not None:
        write_csv_compute(result, args.output_path)
        return
    print("result for computations is zero")


if __name__ == "__main__":
    main()
