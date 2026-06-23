from datetime import timedelta
from typing import Any, cast
import polars as pl
from textdistance import jaccard

from core.helpers.alg_pieces import rpm_almost_same_rpm, rpm_unefficient_cases
from core.helpers.alg_utils import alg_piece_remove_message_delays
from core.helpers.maintenance import maintenance_rpm_slow_change_on_speed


def compute_theoretical_rpm(df: pl.DataFrame):
    is_rpm_sensor = df["rpm"].is_not_null().any()
    if not is_rpm_sensor:
        return 800, 100, False, False
    test_calc = df.group_by_dynamic(
        index_column="timestamp", every="1m", group_by="auto"
    ).agg(
        pl.col("rpm").mean(),
        pl.col("pos_s").mean(),
        pl.col("satellites").mean(),
        pl.col("rpm").std().alias("rpm_var")
    )

    test_calc = test_calc.with_columns(
        pl.when(
            pl.col("pos_s").lt(0.2)
            & pl.col("satellites").gt(10)
            & pl.col("rpm_var").lt(100)
            & pl.col("rpm").gt(200)
        )
        .then(pl.col("rpm"))
        .otherwise(None)
        .alias("rpm_idle")
    )
    test_calc = test_calc.with_columns(
        pl.when(pl.col("pos_s").is_between(8, 20))
        .then(pl.col("rpm"))
        .otherwise(None)
        .alias("rpm_moving")
    )
    test = test_calc.group_by("auto").agg(
        [
            pl.col("rpm_idle").min(),
            pl.col("rpm_moving").mean().alias("rpm_moving"),
            pl.col("rpm").std().alias("rpm_std"),
        ]
    )

    rpm_wall = cast(float, test["rpm_idle"].first())
    rpm_std = cast(float, test["rpm_std"].first())
    trigger = False



    if rpm_wall is None or rpm_wall > 1200:
        trigger = True
        rpm_wall = 800
        rpm_std = 120
    
    if rpm_wall + rpm_std < 800:
        rpm_wall = 800
        

    return rpm_wall, rpm_std, trigger, True


def make_motohours_result(
    motohours_start: int,
    motohours_end: int,
    motohours: int,
    data: dict[str, Any],
    motohours_fraud: int,
    sensor: str,
    sensor_check: str,
    idle_motohours,
    active_motohours,
    rpm_idle: int | float | None = None,
    unefficient_cases: int | float | None = None,
    unefficient_time: int | float | None = None,
    rpm_same_cases: int | float | None = None,
    rpm_same_cases_time: int | float | None = None,
    count: int = 0
):

    return {
        "motohours_start": motohours_start,
        "motohours_end": motohours_end,
        "data": data,
        "motohours_fraud": motohours_fraud,  # because it can't be other way
        "motohours": motohours,
        "sensor_check": sensor_check,
        "sensor": sensor,
        "motohours_idle": idle_motohours,
        "motohours_active": active_motohours,
        "rpm_idle": rpm_idle,
        "unefficient_cases": unefficient_cases, 
        "unefficient_time": unefficient_time,
        "rpm_same_cases": rpm_same_cases,
        "rpm_same_cases_time": rpm_same_cases_time,
        "count": count
        
    }

def make_motohours_response_empty():
    return make_motohours_result(
        0, 0, 0, [], 0, "none", "none", 0, 0, None, None, None
    )

# def compute_motohours_total(df: pl.DataFrame, AGG_TIME: int | None):
#     result_total = None
#     for auto, data in df.partition_by("auto", as_dict=True, include_key=True).items():
#         auto = auto[0]
#         method, units = _criterion_detection(df)
#         result = _compute_motohours(data, method, units, AGG_TIME)
#         if result_total is None:
#             result_total = result["data"]
#         else:
#             print("merging two")
#             result_total = pl.concat([result_total, result["data"]])
#     return result_total


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


def compute_motohours(df: pl.DataFrame, AGG_TIME: int | None, stats: dict[str, Any]):
    method, units = _criterion_detection(df)
    result, reports = _compute_motohours(df, method, units, stats, AGG_TIME)
    return result, reports


def _compute_motohours(
    df: pl.DataFrame,
    method: str,
    units: str,
    stats: dict[str, Any],
    AGG_TIME: int | None,
):
    reports = []
    if df.shape[0] == 0:
        return make_motohours_response_empty(), reports
    result = None
    if method == "ign":
        result, reports = _compute_motohours_by_ign(df, stats, AGG_TIME)
    else:
        result, reports = _compute_motohours_by_motohours(df, units, stats, AGG_TIME)

    return result, reports


RPM_TEST_IDLE = 820

def _compute_motohours_active_idle(df: pl.DataFrame, is_rpm_present: bool, rpm_idle: int | float, col_dtime_idle_checking_period: pl.Expr):

    if is_rpm_present:
        df = df.with_columns(
            pl.col("rpm")
            .mean()
            .over(["auto", col_dtime_idle_checking_period])
            .alias("rpm_direct")
        )
        df = df.with_columns(
            pl.col("rpm_direct").is_between(1, rpm_idle).cast(pl.Int8).alias("is_idle")
        )
    else:
        df = df.with_columns(pl.lit(0).alias("is_idle"))
    
    df = df.with_columns(
        pl.col("is_idle").mul(pl.col("dtime")).truediv(3600).alias("dtime_idle")
    )
    return df
    


def _compute_motohours_by_motohours(
    df: pl.DataFrame, units: str, stats: dict[str, Any], AGG_PERIOD: int | None
):
    col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")
    col_dtime_idle_checking_period = pl.col("timestamp").dt.truncate("1m")
    is_rpm_present = "rpm" in df.columns
    rpm_idle = stats.get("rpm_idle", RPM_TEST_IDLE)
    sensor_check = "motohours"
    # if units == "seconds":
    # df = df.with_columns(pl.col("motohours") / 3600)
    reports = []
    df = alg_piece_remove_message_delays(df)
    if "rpm" in df.columns:
        sensor_check = "rpm"
        df = df.with_columns(pl.col("rpm").gt(0).cast(pl.Int8).alias("compare_active"))

    elif "ign" in df.columns:
        sensor_check = "ign"
        df = df.with_columns(pl.col("ign").gt(0).cast(pl.Int8).alias("compare_active"))
    else:
        df = df.with_columns(
            pl.col("motohours").diff().abs().gt(0).cast(pl.Int8).alias("compare_active")
        )


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
    reports = maintenance_rpm_slow_change_on_speed(df, reports)
    t = df.filter(pl.col("motohours_diff_raw").abs().gt(0))
    multiplier = (
        3600 if (t["motohours_diff_raw"].mean() / t["dtime"].mean()) > 0.08 else 1
    )

    df = df.with_columns(
        pl.col("motohours_diff_raw").truediv(multiplier).alias("motohours_diff"),
        pl.col("motohours").truediv(multiplier).alias("motohours"),
    )

    df = _compute_motohours_active_idle(df, is_rpm_present, rpm_idle, col_dtime_idle_checking_period)

    df = df.with_columns(
        pl.when(pl.col("motohours_diff").ne(0)).then(pl.col("dtime")).otherwise(0)
    )

    df = df.with_columns(
        pl.col("dtime")
        .mul(pl.col("compare_active"))
        .truediv(3600)
        .alias("motohours_by_sensor")
    )
    df = df.with_columns(
        pl.when(pl.col("motohours_diff") < 0)
        .then(pl.col("motohours_diff"))
        .otherwise(0)
        .alias("motohours_fraud")
    )

    df = df.with_columns(
        pl.col("motohours_by_sensor")
        .sub(pl.col("motohours"))
        .clip(lower_bound=0)
        .alias("motohours_fraud_by_sensor")
    )

    data = None
    df = rpm_unefficient_cases(df)
    df = rpm_almost_same_rpm(df, 15)
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
                pl.sum("dtime_idle"),
                pl.lit("hours").alias("units"),
            ]
        )
    idle_motohours = df["dtime_idle"].sum()
    motohours_fraud = (
        df["motohours_fraud"].sum() + df["motohours_fraud_by_sensor"].sum()
    )

    unefficient_cases = df["unefficient_cases_total"].sum()
    unefficient_time = df["dtime_unefficient"].sum()
    rpm_same_cases = df["rpm_same_cases_total"].max()
    rpm_same_cases_time = df["rpm_same_cases_time"].sum()
    return make_motohours_result(
        motohours_start=df["motohours"].first(),
        motohours_end=df["motohours"].last(),
        data=data,
        motohours_fraud=motohours_fraud,
        sensor_check=sensor_check,
        motohours=df["motohours_diff"].sum(),
        sensor="motohours",
        rpm_idle=rpm_idle,
        idle_motohours=idle_motohours,
        active_motohours=df["motohours_diff"].sum() - idle_motohours,
        unefficient_cases=unefficient_cases,
        unefficient_time=unefficient_time,
        rpm_same_cases=rpm_same_cases,
        rpm_same_cases_time=rpm_same_cases_time,
        count=1
    ), reports


def _compute_motohours_by_ign(df: pl.DataFrame, stats: dict[str, Any], AGG_PERIOD: int | None):

    rpm_idle = stats.get("rpm_idle", RPM_TEST_IDLE)
    col_dtime_idle_checking_period = pl.col("timestamp").dt.truncate("10m")
    is_rpm_present = df["rpm"].is_not_null().any()
    df = alg_piece_remove_message_delays(df)
    if df.shape[0] == 0:
        return make_motohours_response_empty(), 

    sensor_check = "ign"
    if is_rpm_present:
        df = df.with_columns(pl.when(pl.col("ign").eq(1)).then(pl.col("rpm").fill_null(0)).otherwise(pl.col("rpm")).alias("rpm"))
        sensor_check = "rpm"
    
    reports = []
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
    reports = maintenance_rpm_slow_change_on_speed(df, reports)
    df = _compute_motohours_active_idle(df, is_rpm_present, rpm_idle, col_dtime_idle_checking_period)
    df = rpm_unefficient_cases(df)
    df = rpm_almost_same_rpm(df, 15, rpm_idle)

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

    hours = (df["timestamp"].last() - df["timestamp"].first()).total_seconds() / 3600

    motohours = df["engine_working"].sum()
    idle_motohours = df["dtime_idle"].sum()
    active_motohours = motohours - idle_motohours
    unefficient_cases = df["unefficient_cases_total"].max()
    unefficient_time = df["dtime_unefficient"].sum()
    rpm_same_cases = df["rpm_same_cases_total"].max()
    rpm_same_cases_time = df["rpm_same_cases_time"].sum() 
    if rpm_same_cases_time is not None:
        rpm_same_cases_time /= 3600
    return make_motohours_result(
        motohours_start=0,
        motohours_end=motohours,
        motohours_fraud=0,
        motohours=motohours,
        data=data,
        sensor="ign",
        sensor_check=sensor_check,
        rpm_idle=rpm_idle,
        idle_motohours=idle_motohours,
        active_motohours=active_motohours,
        unefficient_cases=unefficient_cases,
        unefficient_time=unefficient_time,
        rpm_same_cases=rpm_same_cases,
        rpm_same_cases_time=rpm_same_cases_time,
        count=1

    ), reports
