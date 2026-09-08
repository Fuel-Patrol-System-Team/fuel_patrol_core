import polars as pl
import argparse

from app.tasks import GlonassGeneralProvider
from cli.helpers.csv import write_csv_compute
from core.models import Car, DataProvider
from datetime import datetime

# Can be used to check if behaviour of general_provider matches the data from api


def main(car_id: str, provider_name: str, start_date: datetime, end_date: datetime):
    car = Car.objects.filter(id=car_id).first()
    if car:
        provider = DataProvider.objects.filter(name=provider_name).first()
        if provider is not None:
            parser = GlonassGeneralProvider([car], None, provider, start_date, end_date)
            status, result, mapping = parser.parse_raw_data("fuel", True, car)
            if not status:
                print("Crash. Status is false")
                return
            print(result.columns)
            write_csv_compute(result, "/tmp/tmp.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        "run_general_provider",
        description="used to run general provider. Saves the result in /tmp/tmp.csv if result is successfull",
    )
    parser.add_argument("car")
    parser.add_argument("dataprovider")
    parser.add_argument("start_date")
    parser.add_argument("end_date")
    args = parser.parse_args()
    start_date = datetime.fromisoformat(parser.start_date)
    end_date= datetime.fromisoformat(parser.end_date)
    main(args.car, args.dataprovider, start_date, end_date)
