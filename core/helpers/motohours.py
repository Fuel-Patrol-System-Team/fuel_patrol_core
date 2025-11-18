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

    # if units == "seconds":
    # df = df.with_columns(pl.col("motohours") / 3600)

    df = df.filter(pl.col("motohours").is_not_null())
    df = df.with_columns(
        pl.col("motohours")
        .diff()
        .over(["auto", col_dtime_2hour])
        .alias("motohours_diff")
    )

    df = df.with_columns(
        pl.when(pl.col("motohours_diff") < 0)
        .then(pl.col("motohours_diff"))
        .otherwise(0)
        .alias("motohours_fraud")
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
                pl.lit("hours").alias("units"),
            ]
        )

    # hours = (df["timestamp"].last() - df["timestamp"].first()).total_seconds() / 3600
    # motohours = df["motohours"].max()
    # print(hours, motohours)
    # if motohours > hours:
    #     motohours /= 3600
    #     if AGG_PERIOD is not None:
    #         data = data.with_columns(
    #             [
    #                 pl.col("motohours_first") / 3600,
    #                 pl.col("motohours_last") / 3600,
    #                 pl.col("motohours_fraud") / 3600,
    #                 pl.col("motohours") / 3600,
    #                 pl.lit("seconds").alias("units"),
    #             ]
    #         )

    return {
        "motohours_start": df["motohours"].first(),
        "motohours_end": df["motohours"].last(),
        "data": data,
        "motohours_fraud": df["motohours_fraud"].sum(),
        "motohours": df["motohours_diff"].sum(),
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

    hours = (df["timestamp"].last() - df["timestamp"].first()).timestamp() / 3600

    motohours = df["engine_working"].sum()
    if hours > motohours:
        motohours /= 3600
    return {
        "motohours_start": None,
        "motohours_end": None,
        "data": data,
        "motohours_fraud": 0,  # because it can't be other way
        "motohours": motohours,
    }