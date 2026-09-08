import argparse
import polars as pl

from app.tasks import GlonassGeneralProvider
from core.models import Car


def main(car_id: str):
    car = Car.objects.filter(
        id=car_id
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser("collect_no_expression")
    parser.add_argument("car_id")
    parser.add_argument("start_date")
    parser.add_argument("end_date")
    args = parser.parse_args()