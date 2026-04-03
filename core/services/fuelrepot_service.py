import datetime
import polars

from core.admin import CarFuelReport


class FuelReportService:
    
    @staticmethod
    def build_right_history(df: polars.DataFrame, fillings: polars.DataFrame):
        df = df.join( fillings, on="timestamp", how="left")
        return df
    @staticmethod
    def make_reports_from_df(df: polars.DataFrame):
        slice = df.select(["timestamp", "fuel_start", "fuel_end", "auto", "refill"])
        slice = slice.rename({"timestamp": "start_moment", "auto": "car_id_id", "refill": "fuel_filled"})
        slice = slice.with_columns((polars.col("start_moment") + datetime.timedelta(1)).alias("end_moment"))
        slice_dict = slice.to_dicts()
        reports = []
        for item in slice_dict:
            t_report = CarFuelReport(**item)
            reports.append(t_report)
        CarFuelReport.objects.bulk_create(
            reports
        )
        return len(reports)

