import polars as pl

def mileage_test(df: pl.DataFrame, AGG_PERIOD_MINUTES = 1):
    col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")
    df = df.with_columns(
        [
            pl.col("mileage").diff().over("auto", col_dtime_2hour).fill_null(0).cast(pl.Float32).alias("travel"),
        ]
    )

    df = (
        df.group_by_dynamic(index_column="timestamp", every=f"{AGG_PERIOD_MINUTES}m", by="auto").agg([
            pl.col("travel").sum(),
            pl.col("mileage").max().alias("last_mileage"),
        ])
    )
    df = df.with_columns(
        pl.col("last_mileage").fill_null(strategy="backward").over("auto")
    )

    return df
