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


def _write_waybill_for_car(car_rows: pl.DataFrame, car: Car, output_dir: Path) -> Path:
    """Write the refuel rows for a single matched Car to its own waybill csv."""
    out_df = car_rows.select([
        pl.col("date"),
        pl.lit(str(car.id)).alias("car_id"),
        pl.col("refuel"),
    ])
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"auto_waybill_{car.id_in_provider_system}.csv"
    out_df.write_csv(out_file)
    return out_file


def main():
    parser = argparse.ArgumentParser(
        "correct_waybill",
        "split refuel bills csv into per-car waybill csvs (auto_waybill_{id_in_provider_system}.csv)",
    )
    parser.add_argument("file", help="path to the refuel bills csv (columns: date,car,refuel)")
    parser.add_argument("output_dir", help="folder where per-car waybill csvs are written")
    args = parser.parse_args()

    data = pl.read_csv(args.file, schema_overrides=_CSV_SCHEMA_OVERRIDES)
    output_dir = Path(args.output_dir)

    car_names = data["car"].unique().to_list()
    if not car_names:
        print("no cars found in the input file")
        return

    for car_name in car_names:
        group = data.filter(pl.col("car") == car_name)
        car = Car.objects.filter(name__istartswith=car_name).first()
        if car is None:
            print(f"Car '{car_name}' is not found")
            continue
        out_file = _write_waybill_for_car(group, car, output_dir)
        print(f"Wrote {out_file} ({group.height} rows) for car '{car_name}' -> {car.id_in_provider_system}")


if __name__ == "__main__":
    main()
