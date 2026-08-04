import re
from typing import Any, Dict, List, Literal
import polars as pl

from core.helpers.alg_utils import alg_piece_remove_message_delays
from core.helpers.maintenance import (
    maintenance_board_voltage_notify,
    maintenance_critical_raw_fuel_values,
    maintenance_event_codes,
    maintenance_fuel_consumpt_check,
)
from core.helpers.mileage import alg_piece_remove_skipped_messages


# НЕ ТРОГАТЬ, НЕ ПЕРЕНОСИТЬ
def reconcile_multisensor(
    df: pl.DataFrame, sensor_type: Literal["none"] | Literal["tank"] | Literal["can"]
):
    # TODO: обсудить на встрече. Из-за того, что часто бывает только один датчик, неясно как лучше сделать
    if sensor_type == "none":
        return df
    calc_sensor_sensors = list(
        filter(lambda x: "calc_sensors_fuel_level" in x, df.columns)
    )
    if sensor_type == "tank":
        df = df.with_columns(
            pl.sum_horizontal(calc_sensor_sensors).alias("calc_sensors_fuel_level")
        )
    if sensor_type == "can":
        df = df.with_columns(
            pl.mean_horizontal(calc_sensor_sensors).alias("calc_sensors_fuel_level")
        )
    return df


def make_primary_fast(df: pl.DataFrame):
    is_primary = True
    primary = {}
    fuel_sensors = list(filter(lambda x: "calc_sensors_fuel_level" in x, df.columns))
    for sensor in fuel_sensors:
        if df[sensor].is_null().all():
            df = df.with_columns(pl.lit(0).alias(sensor))
            is_primary = False
    primary["norm_speed"] = df.with_columns(pl.col("pos_s").gt(0))["pos_s"].mean()
    for sensor in fuel_sensors:
        postfix = _get_postfix(sensor)
        fuel = df[sensor].filter(df[sensor].is_between(0, 65000))
        max_fuel = fuel.max()
        if max_fuel > 100 and max_fuel < 103:
            max_fuel = 100
        primary[f"max_fuel{postfix}"] = max_fuel
    primary["is_special_car"] = True
    primary["ign_working"] = False

    return is_primary, primary


def make_fuel_spent(
    fuel_start: int,
    fuel_end: int,
    total_filling: int,
    agg: list[Any],
    fillings: list[Any],
):
    return {
        "fuel_start": fuel_start,
        "fuel_end": fuel_end,
        "total_fillings": total_filling,
        "agg": agg,
        "fillings": fillings,
        "count": 0,
    }


def fuel_spent_calculate(
    result: pl.DataFrame,
):
    spent_report = result.group_by_dynamic(
        index_column="timestamp", group_by="auto", every=f"24h"
    ).agg(
        pl.first("fuel_first").alias("fuel_start"),
        pl.last("fuel_last").alias("fuel_end"),
    )
    return spent_report


# НЕ ТРОГАТЬ, НЕ ПЕРЕНОСИТЬ


def tarify_car_by_sensor(
    df: pl.DataFrame,
    cars: dict[str, Any],
    column="calc_sensors_fuel_level",
    grading="grades",
):
    grades = cars["grades"]
    unique = list({tuple(sorted(d.items())): d for d in grades}.values())
    pairs = list(zip(unique, unique[1:]))
    mp = unique[0]
    lp = unique[-1]
    for fp, sp in pairs:
        slope = (sp["output"] - fp["output"]) / (sp["input"] - fp["input"])
        b = fp["output"] - slope * fp["input"]
        df = df.with_columns(
            pl.when(pl.col(column).is_between(fp["input"], sp["input"]))
            .then(pl.col(column).mul(slope).add(b))
            .otherwise(pl.col(column))
        )
        # последния тарировка (у некоторых машин есть адекватные значения выше тарировки JCB 3797 15c7e16f-355e-4b9d-bbaf-452f915863b3_day.csv)
    df = df.with_columns(
        pl.when(pl.col(column).gt(lp["input"]))
        .then(pl.col(column).mul(slope).add(b))
        .otherwise(pl.col(column))
    )
    df = df.filter(pl.col(column).ge(mp["output"]))

    df = df.with_columns(
        pl.when(pl.col(column).gt(lp["input"]))
        .then(pl.col(column).mul(slope).add(b))
        .otherwise(pl.col(column))
    )
    return df, lp, b, slope


def _get_postfix(sensor_name: str):
    match = re.search(r"(_\d)$", sensor_name)
    if match:
        affix = match.group(1)
        return affix
    return ""


def compute_data_standing(df: pl.DataFrame):
    # Use raw (non-rolling-max) speed if available so the moving gate isn't
    # polluted by the upstream rolling_max smoothing of pos_s (item B).
    speed_col = "pos_s_raw" if "pos_s_raw" in df.columns else "pos_s"

    # Shared 5-minute rolling mean for consistent moving/standing detection (item A).
    df = df.with_columns(
        pl.col(speed_col)
        .fill_null(0)
        .rolling_mean_by(by="timestamp", window_size="1m")
        .alias("pos_s_roll_mean")
    )

    # Dead-band hysteresis (item C): moving >= 1 km/h, standing <= 0.5 km/h,
    # in between carry forward the last known state.
    df = df.with_columns(
        pl.when(pl.col("pos_s_roll_mean") >= 1)
        .then(True)
        .when(pl.col("pos_s_roll_mean") <= 0.5)
        .then(False)
        .otherwise(None)
        .alias("is_moving_raw")
    )
    df = df.with_columns(
        pl.col("is_moving_raw")
        .fill_null(strategy="forward")
        .fill_null(False)
        .alias("is_moving")
    )

    # sgroupid derived from the same is_moving flag → clean moving/standing partition (item A)
    df = df.with_columns(
        (~pl.col("is_moving")).cast(pl.Int32).diff().abs().cum_sum().alias("sgroupid"),
    )

    df = df.with_columns(
        [
            pl.when(~pl.col("is_moving"))
            .then(pl.col("spent_fuel"))
            .otherwise(0)
            .alias("spent_fuel_standing_leaks"),
            pl.when(~pl.col("is_moving"))
            .then(pl.col("dtime"))
            .otherwise(0)
            .alias("dtime_standing"),
            pl.when(pl.col("is_moving"))
            .then(pl.col("dtime"))
            .otherwise(0)
            .alias("dtime_moving"),
        ]
    )

    df = df.with_columns(
        pl.when(pl.col("spent_fuel_standing_leaks").lt(0))
        .then(pl.col("spent_fuel_standing_leaks"))
        .otherwise(0)
        .alias("leak_standing")
        .abs()
    )
    df = df.with_columns(
        pl.when(pl.col("spent_fuel_standing_leaks").gt(0))
        .then(pl.col("spent_fuel_standing_leaks"))
        .otherwise(0)
        .alias("spent_fuel_refuel")
    )

    df = df.with_columns(
        pl.when(pl.col("is_moving"))
        .then(pl.col("spent_fuel"))
        .otherwise(None)
        .alias("sf_m"),
        pl.when(pl.col("is_moving"))
        .then(pl.col(speed_col))
        .otherwise(None)
        .alias("pos_s_m"),
    )

    aggs = [
        pl.col("sf_m").sum(),
        pl.col("pos_s_m").mean(),
        pl.col("spent_fuel_standing_leaks").sum(),
        pl.col("leak_standing").sum().abs(),
        pl.col("spent_fuel_refuel").sum(),
        pl.col("dtime_standing").sum(),
        pl.col("dtime_moving").sum(),
    ]

    return df, aggs


def preprocess_basic_one(
    df: pl.DataFrame,
    cars: dict[str, Any],
    sensors: Dict[str, List[Dict[str, Any]]],
    primary: dict[str, Any],
    VOLTAGE_LIMIT=0.16,
    FUEL_JUMP_BARRIER_PERC=0.05,
    ANTI_BUG_TIME_SECONDS=30,
    REFUELING_LIMIT=4000,
    PRE_PRIOD_TIME=3,
    DTIME_LIMIT=5,
    SMOOTH_WINDOW="30s",
    REFUEL_RISE_EPSILON_PERC=0.005,
    LEAK_FALL_EPSILON_PERC=0.005,
    REFUEL_MIN_DURATION_S=60,
    REFUEL_MIN_POINTS=2,
    FALL_MIN_DURATION_S=30,
    FALL_MIN_POINTS=2,
    is_fuel_processing=False,
    reports: List[Any] = [],
):
    calc_fuel_sensors = list(filter(lambda x: "calc_sensors_fuel" in x, df.columns))
    for sensor in calc_fuel_sensors:
        if is_fuel_processing and df[sensor].is_null().all():
            df = df.with_columns(pl.lit(0).alias(sensor))
    if "rpm" not in df.columns:
        df = df.with_columns(pl.lit(65535).alias("rpm"))
    if "satellites" not in df.columns:
        df = df.with_columns(pl.lit(20).alias("satellites"))
    df = alg_piece_remove_message_delays(df)
    reports = maintenance_critical_raw_fuel_values(df, sensors, reports)

    for sensor in calc_fuel_sensors:
        postfix = _get_postfix(sensor)
        df = df.with_columns(
            pl.when(pl.col(sensor) > primary[f"max_fuel{postfix}"])
            .then(None)
            .otherwise(pl.col(sensor))
            .cast(pl.Float32)
            .alias(sensor),
        )
    # fuel_level_nan per 30 minutes

    if "msg_number" in df.columns:
        df = df.filter(pl.col("msg_number").diff().fill_nan(0).fill_null(0).abs().lt(5))

    reports = maintenance_event_codes(df, reports)

    # STAGE: ОЧИСТКА
    col_dtime_half = pl.col("timestamp").dt.truncate("30m")
    col_dtime_hour = pl.col("timestamp").dt.truncate("30m")
    col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")
    col_dtime_period = pl.col("timestamp").dt.truncate(f"{PRE_PRIOD_TIME}m")
    col_dtime_period = pl.col("timestamp").dt.truncate(f"60m")
    col_dtime_day = pl.col("timestamp").dt.truncate("1d")

    # for sensor in calc_fuel_sensors:
    #     df = df.with_columns(
    #         pl.col(sensor).fill_null(strategy="forward")
    #     )

    for sensor in calc_fuel_sensors:
        df = df.with_columns(
            (pl.col(sensor).is_not_nan() | pl.col(sensor).is_not_null())
            .over(["auto", col_dtime_half])
            .cast(pl.Int16)
            .alias("fuel_level_nan")
        )
        break

    for sensor in calc_fuel_sensors:
        df = df.filter(
            [
                pl.col(sensor).is_not_nan(),
                pl.col(sensor).is_not_null(),
            ]
        )

    if is_fuel_processing:
        df = df.filter(pl.col("satellites").gt(0))
        df = df.with_columns(
            pl.col("fuel_consumpt").diff().abs().alias("fuel_consumpt_spent"),
        )
    else:
        df = df.with_columns(pl.lit(0).alias("fuel_consumpt_spent"))

    # if "flex_adc" in cars["fuel_sensor"]:
    #     df = df.filter(pl.col("pos_s").ge(12))

    # cleaning records for fuel

    # dtime per 1 hour
    df = df.with_columns(
        pl.col("timestamp")
        .diff()
        .dt.total_seconds()
        .abs()
        .cast(pl.Int16)
        .over(["auto", col_dtime_period])
        .fill_nan(1)
        .fill_null(1)
        .clip(upper_bound=DTIME_LIMIT * 60)
        .alias("dtime"),
        pl.col("rpm").fill_null(0),
    )
    df = alg_piece_remove_skipped_messages(df)

    df = df.with_columns(
        pl.when(pl.col("dtime") > 5 * 60)
        .then(0)
        .otherwise(pl.col("dtime").truediv(60))
        .alias("ptime")
    )
    df = df.with_columns(
        pl.when(pl.col("pos_s").gt(0))
        .then(pl.col("dtime"))
        .otherwise(None)
        .alias("dtime_m")
    )

    # df = df.with_columns(
    #     pl.col("calc_sensors_fuel_level").rolling_mean_by("timestamp", window_size="2m")
    # )
    for sensor in calc_fuel_sensors:
        postfix = _get_postfix(sensor)
        # Для мультисенсоров используем grades_N, для одиночного — grades
        degrees = cars.get(f"grades{postfix}") if postfix else cars.get("grades")
        if degrees is None:
            degrees = cars.get("grades")
        if degrees is not None:
            cars_for_sensor = {**cars, "grades": degrees}
            df, lp, b, slope = tarify_car_by_sensor(df, cars_for_sensor, sensor)
            if df[sensor].gt(lp["input"]).any():
                print("Car has problems")
    for i, sensor in enumerate(calc_fuel_sensors):
        degrees = cars.get("median_degrees")
        if len(degrees) != 0 and degrees is not None and degrees[i] is not None:
            df = df.with_columns(pl.col(sensor).rolling_median(window_size=degrees[i]))

    df = reconcile_multisensor(df, cars["fuel_sensor_multi_type"])
    if not is_fuel_processing:
        df = df.filter(pl.col("calc_sensors_fuel_level").is_not_null())

    # df = df.with_columns(
    # pl.when(pl.col("spent_fuel_boundary").gt(0) & pl.col("pos_s").eq(0)).then(0).otherwise(pl.col("spent_fuel_boundary")).alias("spent_fuel_boundary")
    # )
    df = df.with_columns(
        pl.col("calc_sensors_fuel_level")
        .diff()
        .over("auto", col_dtime_2hour)
        .fill_null(0)
        .fill_null(0)
        .alias("spent_fuel_clean")
    )

    reports = maintenance_fuel_consumpt_check(df, sensors, reports)
    reports = maintenance_board_voltage_notify(df, sensors, reports)

    df = df.with_columns((pl.col("spent_fuel_clean") / pl.col("dtime")).alias("fps"))

    # TODO: передумать этот фильтр
    # df = df.filter(pl.col("fps").gt(-1) | pl.col("satellites").lt(2))
    df = df.with_columns(
        [
            pl.max("calc_sensors_voltage")
            .over(["auto", col_dtime_2hour])
            .alias("voltage_max"),
        ]
    )

    # filter by voltage limit
    df = df.filter(
        (pl.col("voltage_max") - pl.col("calc_sensors_voltage")) / pl.col("voltage_max")
        < VOLTAGE_LIMIT,
    )
    df = df.with_columns(
        pl.col("calc_sensors_fuel_level")
        .diff()
        .over(["auto"])
        .fill_null(0)
        .cast(pl.Float32)
        .alias("spent_fuel_boundary"),
    )

    sum_fuel = (
        df.group_by_dynamic(index_column="timestamp", every="1d")
        .agg(pl.col("spent_fuel_boundary").sum())["spent_fuel_boundary"]
        .sum()
    )
    df = df.with_columns(pl.lit(sum_fuel).alias("spent_fuel_t"))

    # spent fuel / pos_a
    df = df.with_columns(
        [
            pl.col("calc_sensors_fuel_level")
            .diff()
            .over(["auto", col_dtime_2hour])
            .fill_null(0)
            .cast(pl.Float32)
            .alias("spent_fuel"),
            pl.col("pos_s")
            .diff()
            .over(["auto", col_dtime_2hour])
            .fill_null(0)
            .cast(pl.Float32)
            .alias("pos_a"),
        ]
    )

    if cars["fuel_sensor"] is not None and "flex_adc" in cars["fuel_sensor"]:
        df = df.with_columns(
            pl.col("calc_sensors_fuel_level").alias("fuel_level_standing")
        )

    else:
        df = df.with_columns(
            pl.when(
                (~pl.lit(primary["is_special_car"]) & (pl.col("pos_s") == 0))
                | ((pl.col("ign") == 0) & pl.lit(primary["is_special_car"]))
            )
            .then(pl.col("calc_sensors_fuel_level"))
            .otherwise(pl.lit(None))
            .forward_fill()
            .alias("fuel_level_standing"),
        )
    max_fuel_tmp = df["calc_sensors_fuel_level"].max()
    if max_fuel_tmp is None:
        max_fuel_tmp = 0
    df = df.with_columns(
        [
            pl.col("spent_fuel").clip(upper_bound=0).alias("spent_fuel_2"),
            pl.col("spent_fuel").abs().alias("spent_fuel_abs"),
            pl.when(pl.col("spent_fuel") > 0)
            .then(pl.col("spent_fuel"))
            .otherwise(0)
            .cast(pl.Float32)
            .alias("recover_fuel"),
            pl.when(pl.col("pos_a").is_null())
            .then(pl.col("pos_s"))
            .otherwise(pl.col("pos_a"))
            .alias("pos_a"),
            # Preserve raw speed for the moving/standing gate before rolling_max smoothing (item B)
            pl.col("pos_s").alias("pos_s_raw"),
            pl.col("pos_s").rolling_max(window_size=4).alias("pos_s"),
            pl.when(pl.col("spent_fuel") <= 0)
            .then(1)
            .otherwise(0)
            .cast(pl.Int16)
            .alias("fd"),
            pl.when((pl.col("satellites") == 0))
            .then(1)
            .otherwise(0)
            .cast(pl.Int16)
            .alias("no_sat_data"),
            pl.when(
                pl.col("spent_fuel").abs().gt(max_fuel_tmp * FUEL_JUMP_BARRIER_PERC)
            )
            .then(1)
            .otherwise(0)
            .cast(pl.Int16)
            .alias("jumps"),
        ]
    )

    # df = df.with_columns(
    #     pl.when((pl.col("no_sat_data") == 1) & (pl.col("spent_fuel") != 0))
    #     .then(0)
    #     .otherwise(pl.col("spent_fuel"))
    #     .alias("spent_fuel")
    # )

    df = df.with_columns(pl.lit(1).alias("count"))
    df = df.with_columns(
        [
            ((pl.col("pos_s") / pl.lit(primary["norm_speed"])) * pl.col("dtime"))
            .fill_nan(0)
            .alias("load"),
        ]
    )
    df, aggs = compute_data_standing(df)

    df = df.with_columns(pl.col("dtime").mul(pl.col("ign")).alias("es"))
    df = df.with_columns(pl.col("rpm").mul(pl.col("dtime")).alias("rpm_total"))
    df = df.with_columns(pl.col("pos_s").mul(pl.col("dtime")).alias("energy"))
    df = df.group_by_dynamic(
        index_column="timestamp", every=f"{ANTI_BUG_TIME_SECONDS}s", group_by="auto"
    ).agg(
        [
            pl.col("calc_sensors_fuel_level").mean(),
            pl.col("pos_s").mean(),
            pl.col("pos_s").max().alias("pos_s_max"),
            pl.col("spent_fuel").sum(),
            pl.col("spent_fuel_2").sum(),
            pl.col("fuel_level_standing").first().alias("f1"),
            pl.col("fuel_level_standing").last().alias("f2"),
            pl.col("dtime").sum(),
            pl.col("jumps").sum(),
            pl.col("amtr").sum(),
            pl.col("rpm").mean(),
            pl.col("load").sum(),
            pl.col("fd").sum(),
            pl.col("fuel_level_nan").sum(),
            pl.col("no_sat_data").sum(),
            pl.col("count").sum(),
            pl.col("ign").max().alias("ign_max"),
            pl.col("ign").sum(),
            pl.col("ptime").sum(),
            pl.col("dtime_m").sum(),
            pl.col("rpm_total").sum(),
            pl.col("energy").sum(),
            pl.col("es").sum(),
            pl.sum("spent_fuel_boundary"),
            pl.sum("fuel_consumpt_spent"),
            pl.first("fuel_consumpt").alias("fuel_consumpt_first"),
            pl.last("fuel_consumpt").alias("fuel_consumpt_last"),
            pl.first("spent_fuel_t"),
            *aggs,
        ]
    )

    # убрано т.к. убивает шум и заправки, занижая слив
    # df = df.with_columns(
    #     pl.when(
    #         (
    #             (pl.col("pos_s") == 0)
    #             & (pl.col("spent_fuel") > 0)
    #         )
    #     )
    #     .then(0)
    #     .otherwise(pl.col("spent_fuel"))
    #     .alias("spent_fuel")
    # )

    df = df.with_columns(
        pl.col("spent_fuel")
        .rolling_mean_by("timestamp", window_size="30m", closed="both")
        .alias("spent_fuel_rolling")
    )

    # Plan B: smoothed signal + rising-span detection (robust to noise).
    # 1. Short rolling median of the fuel level → fuel_smooth.
    df = df.with_columns(
        pl.col("calc_sensors_fuel_level")
        .rolling_median_by(by="timestamp", window_size=SMOOTH_WINDOW, closed="both")
        .over("auto")
        .alias("fuel_smooth")
    )

    # 2. Delta of the smoothed signal.
    df = df.with_columns(
        pl.col("fuel_smooth").diff().over("auto").fill_null(0).alias("fuel_delta")
    )

    # 3. Rising flag with epsilon proportional to max fuel.
    rise_epsilon = max_fuel_tmp * REFUEL_RISE_EPSILON_PERC
    fall_epsilon = max_fuel_tmp * LEAK_FALL_EPSILON_PERC
    # currently it is real_refuel - o(n), pick better contants loss about 30%
    df = df.with_columns(
        (pl.col("fuel_delta") > rise_epsilon).cast(pl.Int8).alias("is_rising")
    )

    df = df.with_columns(
        pl.col("fuel_delta").lt(-fall_epsilon).cast(pl.Int8).alias("is_falling")
    )

    # 4. Span grouping on the smoothed rising flag.
    df = df.with_columns(
        pl.col("is_rising")
        .ne(pl.col("is_rising").shift())
        .cum_sum()
        .over("auto")
        .alias("refuel_group")
    )

    df = df.with_columns(
        pl.col("is_falling")
        .ne(pl.col("is_falling").shift())
        .cum_sum()
        .over("auto")
        .alias("fall_group")
    )

    df = df.with_columns(
        pl.col("calc_sensors_fuel_level")
        .mean()
        .over([col_dtime_hour])
        .sub(pl.col("calc_sensors_fuel_level"))
        .abs()
        .alias("noise"),
    )
    df = df.with_columns(
        pl.col("noise").mean().over([col_dtime_hour]).alias("noise_mean"),
        pl.col("noise").std().over([col_dtime_hour]).alias("noise_std"),
    )
    aggs = [*aggs, pl.col("noise_mean").first(), pl.col("noise_std").first()]

    # 5. Net rise over each eligible span (more accurate than summing per-message deltas).
    df = df.with_columns(
        pl.col("fuel_smooth")
        .first()
        .over(["refuel_group"])
        .alias("_refuel_span_first"),
        pl.col("fuel_smooth").last().over(["refuel_group"]).alias("_refuel_span_last"),
        pl.col("fuel_smooth").first().over(["fall_group"]).alias("_fall_span_first"),
        pl.col("fuel_smooth").last().over(["fall_group"]).alias("_fall_span_last"),
        pl.col("dtime").sum().over(["refuel_group"]).alias("dtime_refuel"),
        pl.col("dtime").sum().over(["fall_group"]).alias("dtime_fall"),
    )
    df = df.with_columns(
        [
            ((pl.col("fuel_delta")).clip(lower_bound=0).mul(pl.col("is_rising"))).alias(
                "refuel"
            ),
            (
                (pl.col("fuel_delta")).clip(upper_bound=0).mul(pl.col("is_falling")).abs()
            ).alias("fall"),
        ]
    )
    df = df.with_columns(
        [
            pl.col("is_rising").sum().over(["refuel_group"]).alias("refuel_count"),
            pl.col("is_falling").sum().over(["fall_group"]).alias("fall_count"),
        ]
    )

    # 6. Collapse to 30-minute buckets.
    df = df.with_columns(
        [
            pl.col("refuel").sum().over(["refuel_group"]).alias("refuel_span"),
            pl.col("dtime_refuel")
            .sum()
            .over(["refuel_group"])
            .alias("dtime_refuel_span"),
            pl.col("fall").sum().over(["fall_group"]).alias("fall_span"),
            pl.col("dtime_fall").sum().over(["fall_group"]).alias("dtime_fall_span"),
            pl.col("pos_s").mean().over(["fall_group"]).alias("speed_fall_group"),
        ]
    )
    # 7. Eligibility using the new Plan B parameters.
    df = df.with_columns(
        [
            (
                pl.col("dtime_refuel_span").gt(REFUEL_MIN_DURATION_S)
                & pl.col("refuel_span").gt(8)
                & pl.col("refuel_count").ge(REFUEL_MIN_POINTS)
            )
            .cast(pl.Int8)
            .alias("is_refuel_eligble"),
            (
                pl.col("dtime_fall_span").gt(FALL_MIN_DURATION_S)
                & pl.col("fall_span").gt(8)
                & pl.col("speed_fall_group").lt(8)
                & pl.col("fall_count").ge(FALL_MIN_POINTS)
            )
            .cast(pl.Int8)
            .alias("is_fall_eligble"),
        ]
    )
    # df = df.with_columns(
    #     pl.col("is_refuel_eligble")
    #     .mul(pl.col("refuel"))
    #     .sum()
    #     .over([col_dtime_half])
    #     .alias("refuel")
    # )
    df = df.with_columns(
        pl.col("is_refuel_eligble").mul(pl.col("refuel")).alias("refuel_eligble")
    )
    df = df.with_columns(
        pl.col("is_fall_eligble").mul(pl.col("fall")).alias("fall_eligble")
    )
    # Drop transient helper columns so they don't leak into downstream consumers.
    df = df.drop(["_refuel_span_first", "_refuel_span_last"])
    aggs = [
        *aggs,
        pl.sum("fall_eligble"),
    ]

    return df, reports, aggs
