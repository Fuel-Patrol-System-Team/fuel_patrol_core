import os
import sys
import argparse

import polars as pl
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pl.enable_string_cache()


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

import django
django.setup()

import app.tasks  # noqa: E402,F401 — must be imported before the providers below
                  # to break a circular import (car_data_service -> app.tasks -> fuelreport_service -> car_data_service)

from core.models import Car


_CSV_SCHEMA_OVERRIDES = {
    "date": pl.Date,
    "car": pl.Utf8,
    "refuel": pl.Float32,
}

def main():
    parser = argparse.ArgumentParser(
        "pick_cars",
        "split refuel bills csv into per-car waybill csvs (auto_waybill_{id_in_provider_system}.csv)",
    )
    parser.add_argument("file", help="path to the refuel bills csv (columns: date,car,refuel)")
    parser.add_argument("output_dir", help="folder where per-car waybill csvs are written")
    args = parser.parse_args()

    data = pl.read_csv(args.file, schema_overrides=_CSV_SCHEMA_OVERRIDES)
    output_dir = Path(args.output_dir)

    result = []
    for data_piece in data.iter_rows(named=True):
        car = Car.objects.filter(name__istartswith=data_piece["car"]).first()
        if car is None:
            print(f"Car '{data_piece}' is not found")
            continue
        result.append({
            "id": str(car.id),
            "name": car.name, 
            "records":  data_piece["len"]
        })
    result = pl.DataFrame(result)
    print(result)
    result.write_csv(args.output_dir)

if __name__ == "__main__":
    main()
