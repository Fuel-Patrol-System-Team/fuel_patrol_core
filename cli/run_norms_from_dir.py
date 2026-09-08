import os
import sys
import argparse

import polars as pl
pl.enable_string_cache()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

import django
django.setup()

import app.tasks  # noqa: E402,F401 — must be imported before the providers below
                  # to break a circular import (car_data_service -> app.tasks -> fuelreport_service -> car_data_service)

from cli.run_norms_from_file import _run_norms_from_file, _CSV_SCHEMA_OVERRIDES


def _read_csv(path: str) -> pl.DataFrame:
    return pl.read_csv(path, schema_overrides=_CSV_SCHEMA_OVERRIDES)


def _run_norms_from_dir(dir_path: str):
    if not os.path.isdir(dir_path):
        print(f"Directory {dir_path} is not found")
        return None

    csv_files = sorted(
        os.path.join(dir_path, name)
        for name in os.listdir(dir_path)
        if name.lower().endswith(".csv") and os.path.isfile(os.path.join(dir_path, name))
    )
    if not csv_files:
        print(f"No CSV files found in {dir_path}")
        return None

    for path in csv_files:
        print(f"Processing {path} ...")
        data = _read_csv(path)
        car_id = data["auto"].first()
        _run_norms_from_file(data, car_id)


def main():
    parser = argparse.ArgumentParser("norms", "compute norms from a directory of files")
    parser.add_argument("directory")
    args = parser.parse_args()

    _run_norms_from_dir(args.directory)


if __name__ == "__main__":
    main()
