import polars as pl
from core.models import ComputedData
class ComputedDataService:
    
    def __init__(self) -> None:
        pass

    
    def save_preprocessed_data(self, df: pl.DataFrame):
        select = ComputedData.get_required_columns()
        tmp = df.select(select)
        tmp = tmp.with_columns(pl.col("timestamp").dt.replace_time_zone("Etc/Universal"))
        tmp = tmp.rename({"auto": "auto_id"}).to_dicts()
        tmp = list(map(lambda x: ComputedData(
            **x
        ), tmp ))
        result = ComputedData.objects.bulk_create(tmp)
        return len(result), result