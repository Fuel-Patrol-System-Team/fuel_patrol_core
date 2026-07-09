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
            .alias("unefficient_cases")
        )
        df = df.with_columns(
            pl.col("unefficient_cases").mul(pl.col("dtime").truediv(3600)).alias("dtime_unefficient"),
            
        )
        cases = df.group_by(["auto", col_dtime_for_case]).agg(
            pl.col("unefficient_cases").max().alias("has_case")
        ).group_by("auto").agg(
            pl.col("has_case").sum().alias("unefficient_cases_total")
        )["unefficient_cases_total"].sum()
        df = df.with_columns(
            pl.lit(cases).alias("unefficient_cases_total")
        )
        
        return df
    else:
        return df.with_columns(
            pl.lit(0).alias("unefficient_cases"),
            pl.lit(0).alias("dtime_unefficient"),
            pl.lit(0).alias("unefficient_cases_total")
        )

def rpm_almost_same_rpm(
    df: pl.DataFrame, time_period_m = 15, RPM_IDLE=800
):
    check_period = time_period_m - 5
    is_rpm_present = df["rpm"].is_not_null().any()
    col_dtime_for_case = pl.col("timestamp").dt.truncate(f"{time_period_m}m")

    if is_rpm_present:
        df = df.with_columns(
            pl.col("rpm").mean().over(["auto", col_dtime_for_case]).alias("rpm_mean")
        )
        df = df.with_columns(
            pl.col("rpm").diff().abs().mean().over(["auto", col_dtime_for_case]).alias("rpm_diff")
        )
        df = df.with_columns(
            (pl.col("rpm_diff").lt(1) & pl.col("rpm").is_not_null() & pl.col("rpm_mean").gt(RPM_IDLE)).cast(pl.Int8).alias("rpm_same_cases")
        )
        df = df.with_columns(
            pl.col("rpm_same_cases").mul(pl.col("dtime")).alias("rpm_same_cases_time")
        )
        same_cases = df.group_by_dynamic(index_column="timestamp", every=f"{time_period_m}m", group_by="auto").agg(
            pl.col("rpm_same_cases_time").sum().gt(check_period * 60).cast(pl.Int32).alias("same_cases")
        )
        df = df.with_columns(pl.lit(same_cases["same_cases"].sum()).alias("rpm_same_cases_total"))
        df = df.with_columns(pl.col("rpm_same_cases_time").truediv(3600))
        # df = df.with_columns(
        #     pl.col("rpm_same_cases_time").sum().gt(check_period * 60).over(["auto", col_dtime_for_case]).sum().alias("rpm_same_cases_total")
        # )
    else:
        df = df.with_columns(
            pl.lit(0).alias("rpm_same_cases"),
            pl.lit(0).alias("rpm_same_cases_time"),
            pl.lit(0).alias("rpm_same_cases_total")
        )
    
    return df
