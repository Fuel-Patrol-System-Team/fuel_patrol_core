import os
import sys
import argparse

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
import django
django.setup()

from core.models import Car
from core.services.providers.norms_service import NormsService
pl.enable_string_cache()
import app.tasks  # noqa:

from cli.run_norms_from_file import _run_norms_from_file, _CSV_SCHEMA_OVERRIDES


def _read_csv(path: str) -> pl.DataFrame:
    return pl.read_csv(path, schema_overrides=_CSV_SCHEMA_OVERRIDES)

    
if __name__ == "__main__":
    df = _read_csv("/data/datasets/fuel/auto750212_raw_mapped.csv")
    car = Car.objects.filter(id="f1295d9a-42c4-412a-9a80-7b8339ffbed3").first()
    if car:
        NormsService.calculte_new_average_line(car, "speed")
        print("Success")