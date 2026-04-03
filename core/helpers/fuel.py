import polars as pl

def fuel_spent_calculate(result: pl.DataFrame):
    spent_report = result.group_by_dynamic(
        index_column="timestamp", group_by="auto", every=f"24h"
    ).agg(
        pl.first("fuel_first").alias("fuel_start"),
        pl.last("fuel_last").alias("fuel_end"),
    )
    return spent_report