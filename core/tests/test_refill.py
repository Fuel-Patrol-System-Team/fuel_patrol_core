import datetime
import polars
import pytest

from app.tasks import DataProvider, GlonassGeneralProvider


@pytest.mark.django_db
def test_parser_multiple_cars_mapped():
    provider = DataProvider.objects.last()
    cars = provider.cars.filter(id="baaa2f8c-4adc-4c68-b09f-68e6a4498b98").first()

    start_date = datetime.datetime(2025, 6, 1)
    end_date = datetime.datetime(2025, 8, 1)

    parser = GlonassGeneralProvider([cars], None, provider,start_date, end_date, "raw_mapped" )
    parser.authenticate()
    df = parser.parse_refill_data_full(cars, start_date, end_date)
    if df is not None:
        test_df = polars.read_csv("/data/datasets/fuel/spent_fuel_2026-04-02.csv", schema_overrides={
            "timestamp": polars.Datetime
        })
        total_data = FuelReportService.build_right_history(test_df, df)
        amount = FuelReportService.make_reports_from_df(total_data)
        assert amount > 0

