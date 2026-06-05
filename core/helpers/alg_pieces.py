import polars as pl


def rpm_unefficient_cases(
    df: pl.DataFrame, idle_column="is_idle", time_period_m=15
):
    is_rpm_present = df["rpm"].is_not_null().any()
    col_dtime_for_case = pl.col("timestamp").dt.truncate(f"{time_period_m}m")
    if is_rpm_present:
        df = df.with_columns(
            pl.col(idle_column)
            .mul(pl.col("dtime"))
            .truediv(3600)
            .sum()
            .over(["auto", col_dtime_for_case])
            .truediv(time_period_m / 60)
            .gt(0.9)
            .cast(pl.Int8)
            .alias("unefficient_case")
        )
        df = df.with_columns(
            pl.col("unefficient_case").mul(pl.col("dtime").truediv(3600)).alias("dtime_unefficient")
        )
        return df
    else:
        return df.with_columns(
            pl.lit(0).alias("unefficient_case"),
            pl.lit(0).alias("dtime_unefficient")
        )

def rpm_almost_same_rpm(
    df: pl.DataFrame, time_period_m = 10
):
    is_rpm_present = df["rpm"].is_not_null().any()
    col_dtime_for_case = pl.col("timestamp").dt.truncate(f"{time_period_m}m")

    if is_rpm_present:
        df = df.with_columns(
            pl.col("rpm").diff().abs().mean().over(["auto", col_dtime_for_case]).alias("rpm_diff")
        )
        df = df.with_columns(
            pl.col("rpm_diff").lt(25).cast(pl.Int8).alias("rpm_same_case")
        )
        df = df.with_columns(
            pl.col("rpm_same_case").mul(pl.col("dtime")).alias("rpm_same_case_time")
        )
    else:
        df = df.with_columns(
            pl.lit(0).alias("rpm_same_case"),
            pl.lit(0).alias("rpm_same_case_time")
        )


    return df
