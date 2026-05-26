from datetime import timedelta
import polars as pl


def compute_motohours_total(df: pl.DataFrame, AGG_TIME: int | None):
    result_total = None
    for auto, data in df.partition_by("auto", as_dict=True, include_key=True).items():
        auto = auto[0]
        method, units = _criterion_detection(df)
        result = _compute_motohours(data, method, units, AGG_TIME)
        if result_total is None:
            result_total = result["data"]
        else:
            print("merging two")
            result_total = pl.concat([result_total, result["data"]])
    return result_total


def _criterion_detection(df: pl.DataFrame):
    units = "hours"
    compute_method = "ign"
    if df["motohours"].is_null().all():
        return compute_method, units 
    max_value = df["motohours"].max()
    if max_value is not None:
        compute_method = "motohours"
        if max_value > 10000:
            units = "seconds"
    return compute_method, units


def compute_motohours(df: pl.DataFrame, AGG_TIME: int | None):
    method, units = _criterion_detection(df)
    result = _compute_motohours(df, method, units, AGG_TIME)
    return result


def _compute_motohours(df: pl.DataFrame, method: str, units: str, AGG_TIME: int | None):
    if df.shape[0] == 0:
        return df
    result = None
    if method == "ign":
        result = _compute_motohours_by_ign(df, AGG_TIME)
    else:
        result = _compute_motohours_by_motohours(df, units, AGG_TIME)

    return result


def _compute_motohours_by_motohours(
    df: pl.DataFrame, units: str, AGG_PERIOD: int | None
):
    col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")

    sensor_check = "motohours"
    # if units == "seconds":
    # df = df.with_columns(pl.col("motohours") / 3600)
    if "rpm" in df.columns:
        sensor_check = "rpm"
        df = df.with_columns(pl.col("rpm").gt(0).cast(pl.Int8).alias("compare_active"))
    elif "ign" in df.columns:
        sensor_check = "ign"
        df = df.with_columns(pl.col("ign").gt(0).cast(pl.Int8).alias("compare_active"))
    else:
        df = df.with_columns(pl.col("motohours").diff().abs().gt(0).cast(pl.Int8).alias("compare_active"))


    df = df.filter(pl.col("motohours").is_not_null())
    df = df.with_columns(
        pl.col("motohours")
        .diff()
        .over(["auto", col_dtime_2hour])
        .alias("motohours_diff_raw"),
        pl.col("timestamp")
        .diff()
        .dt.total_seconds()
        .cast(pl.Int64)
        .abs()
        .fill_null(0)
        .alias("dtime"),
    )
    t = df.filter(
        pl.col("motohours_diff_raw").abs().gt(0)
    )
    multiplier = 3600 if (t["motohours_diff_raw"].mean() / t["dtime"].mean()) > 0.08 else 1 

    df = df.with_columns(
        pl.col("motohours_diff_raw").truediv(multiplier).alias("motohours_diff"),
        pl.col("motohours").truediv(multiplier).alias("motohours"),
    )

    

    df = df.with_columns(
        pl.when(pl.col("motohours_diff").ne(0)).then(pl.col("dtime")).otherwise(0)
    )

    df = df.with_columns(
        pl.col("dtime").mul(pl.col("compare_active")).truediv(3600).alias("motohours_by_sensor")
    )
    df = df.with_columns(
        pl.when(pl.col("motohours_diff") < 0)
        .then(pl.col("motohours_diff"))
        .otherwise(0)
        .alias("motohours_fraud")
    )

    df = df.with_columns(
        pl.col("motohours_by_sensor").sub(pl.col("motohours")).clip(lower_bound=0).alias("motohours_fraud_by_sensor")
    )

    
    


    data = None
    
    


    if AGG_PERIOD is not None:
        data = df.group_by_dynamic(
            index_column="timestamp", every=f"{AGG_PERIOD}m", group_by="auto"
        ).agg(
            [
                pl.col("motohours_diff").sum().alias("motohours"),
                pl.col("motohours_fraud").sum(),
                pl.lit("motohours").alias("criterion"),
                pl.col("motohours").first().alias("motohours_first"),
                pl.col("motohours").last().alias("motohours_last"),
                pl.sum("dtime"),
                pl.lit("hours").alias("units"),
            ]
        )
        if AGG_PERIOD is not None:
            data = data.with_columns(
                [
                    pl.col("motohours_first"),
                    pl.col("motohours_last"),
                    pl.col("motohours_fraud"),
                    pl.col("motohours"),
                    pl.lit("seconds").alias("units"),
                ]
            )
    
    motohours_fraud =  df["motohours_fraud"].sum() + df["motohours_fraud_by_sensor"].sum()
    return {
        "motohours_start": df["motohours"].first(),
        "motohours_end": df["motohours"].last(),
        "data": data,
        "motohours_fraud": motohours_fraud,
        "motohours": df["motohours_diff"].sum(),
        "sensor_check": sensor_check,
        "sensor": "motohours"
    }


def _compute_motohours_by_ign(df: pl.DataFrame, AGG_PERIOD: int | None):
    df = df.with_columns(
        pl.col("timestamp")
        .diff()
        .dt.total_seconds()
        .abs()
        .cast(pl.Int16)
        .over(["auto", pl.col("timestamp").dt.truncate("1h")])
        .alias("dtime")
    )
    df = df.with_columns(
        (pl.col("ign") * pl.col("dtime") / 3600).alias("engine_working")
    )

    data = None
    sensor_check = "ign"

    if AGG_PERIOD is not None:
        data = df.group_by_dynamic(
            index_column="timestamp", every=f"{AGG_PERIOD}m", group_by="auto"
        ).agg(
            [
                pl.lit(0).alias("motohours_start"),
                pl.lit(0).alias("motohours_fraud"),
                pl.lit("ign").alias("criterion"),
                pl.col("engine_working").sum().alias("motohours"),
                pl.col("engine_working").sum().alias("motohours_end"),
            ]
        )

    hours = (df["timestamp"].last() - df["timestamp"].first()).total_seconds() / 3600

    motohours = df["engine_working"].sum()
    return {
        "motohours_start": 0,
        "motohours_end": motohours,
        "data": data,
        "motohours_fraud": 0,  # because it can't be other way
        "motohours": motohours,
        "sensor_check": sensor_check,
        "sensor": "ign"
    }