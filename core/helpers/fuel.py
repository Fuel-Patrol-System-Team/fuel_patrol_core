from typing import Any, List
import polars as pl

# НЕ ТРОГАТЬ, НЕ ПЕРЕНОСИТЬ

def make_primary_fast(df: pl.DataFrame):
    primary = {}
    primary["norm_speed"] = df.with_columns(pl.col("pos_s").gt(0))["pos_s"].mean() 
    fuel = df["calc_sensors_fuel_level"].filter(df["calc_sensors_fuel_level"].is_between(0, 65000))
    max_fuel = fuel.max()
    if max_fuel > 100 and max_fuel < 103:
        max_fuel = 100
    primary["max_fuel"] = max_fuel
    primary["is_special_car"] = True
    primary["ign_working"] = False
    
    return primary

def make_fuel_spent(fuel_start: int, fuel_end: int, total_filling: int, agg: list[Any], fillings: list[Any]):
    return {
        "fuel_start": fuel_start,
        "fuel_end": fuel_end,
        "total_fillings": total_filling,
        "agg": agg,
        "fillings": fillings
    }
    



def fuel_spent_calculate(result: pl.DataFrame, ):
    spent_report = result.group_by_dynamic(
        index_column="timestamp", group_by="auto", every=f"24h"
    ).agg(
        pl.first("fuel_first").alias("fuel_start"),
        pl.last("fuel_last").alias("fuel_end"),
    )
    return spent_report

# НЕ ТРОГАТЬ, НЕ ПЕРЕНОСИТЬ

def tarify_car_by_sensor(df: pl.DataFrame, cars: dict[str, Any], column = "calc_sensors_fuel_level", grading = "grades"):
    grades = cars["grades"]
    unique = list({tuple(sorted(d.items())): d for d in grades}.values())
    pairs = list(zip(unique, unique[1:]))
    mp = unique[0]
    lp = unique[-1]
    for fp, sp in pairs:
        slope = (sp["output"] - fp["output"]) / (sp["input"] - fp["input"])
        b = fp["output"] - slope * fp["input"]
        df = df.with_columns(
            pl.when(
                pl.col(column).is_between(fp["input"], sp["input"])
            )
            .then(pl.col(column).mul(slope).add(b))
            .otherwise(pl.col(column))
        )
        # последния тарировка (у некоторых машин есть адекватные значения выше тарировки JCB 3797 15c7e16f-355e-4b9d-bbaf-452f915863b3_day.csv)
    df = df.with_columns(
            pl.when(pl.col(column).gt(lp["input"]))
            .then(pl.col(column).mul(slope).add(b))
            .otherwise(pl.col(column))
    )
    df = df.filter(pl.col(column).ge(mp["input"]))
    
    df = df.with_columns(
        pl.when(pl.col(column).gt(lp["input"]))
        .then(pl.col(column).mul(slope).add(b))
        .otherwise(pl.col(column))
    )
    return df, lp, b, slope
    
def preprocess_basic_one(
    df: pl.DataFrame,
    cars: dict[str, Any],
    primary: dict[str, Any],
    VOLTAGE_LIMIT = 0.16,
    FUEL_JUMP_BARRIER_PERC = 0.05,
    ANTI_BUG_TIME_SECONDS = 30,
    REFUELING_LIMIT = 4000,
    PRE_PRIOD_TIME = 3,
    DTIME_LIMIT =5,
    reports: List[Any] = [],
):
    if "rpm" not in df.columns:
        df = df.with_columns(pl.lit(65535).alias("rpm"))
    if "satellites" not in df.columns:
        df = df.with_columns(pl.lit(20).alias("satellites"))

    df = df.with_columns(
        pl.when(pl.col("calc_sensors_fuel_level") > primary["max_fuel"])
        .then(None)
        .otherwise(pl.col("calc_sensors_fuel_level"))
        .cast(pl.Float32)
        .alias("calc_sensors_fuel_level"),
    )
    # fuel_level_nan per 30 minutes

    if "msg_number" in df.columns:
        df = df.filter(
            pl.col("msg_number").diff().fill_nan(0).fill_null(0).abs().lt(5)
        )

    # STAGE: ОЧИСТКА
    col_dtime_half = pl.col("timestamp").dt.truncate("30m")
    col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")
    col_dtime_period = pl.col("timestamp").dt.truncate(f"{PRE_PRIOD_TIME}m")
    col_dtime_period = pl.col("timestamp").dt.truncate(f"60m")

    df = df.with_columns(
        pl.col("calc_sensors_fuel_level")
        .is_not_nan()
        .over(["auto", col_dtime_half])
        .cast(pl.Int16)
        .alias("fuel_level_nan")
    )

    df = df.with_columns(
        pl.when(pl.col("pos_s").gt(0))
        .then(pl.col("pos_s"))
        .otherwise(None)
        .alias("pos_s_m")
    )
    df = df.filter(
        [
            pl.col("calc_sensors_fuel_level").is_not_nan(),
            pl.col("calc_sensors_fuel_level").is_not_null(),
        ]
    )

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

    df = df.with_columns(
        pl.col("calc_sensors_fuel_level").rolling_mean_by("timestamp", window_size="2m")
    )

    df, lp, b, slope = tarify_car_by_sensor(df, cars)
    # clean для отсчения резких прыжков (с игнорированием записей)
    df = df.with_columns(
        pl.col("calc_sensors_fuel_level")
        .diff()
        .over("auto", col_dtime_2hour)
        .fill_null(0)
        .fill_null(0)
        .alias("spent_fuel_clean")
    )
    df = df.with_columns((pl.col("spent_fuel_clean") / pl.col("dtime")).alias("fps"))

    df = df.filter(pl.col("fps").gt(-1))


    if df["calc_sensors_fuel_level"].gt(lp["input"]).any():
        print("Car has problems")
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

    if "flex_adc" in cars["fuel_sensor"]:
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
                pl.col("spent_fuel").abs()
                > primary["max_fuel"] * FUEL_JUMP_BARRIER_PERC
            )
            .then(1)
            .otherwise(0)
            .cast(pl.Int16)
            .alias("jumps"),
        ]
    )

    df = df.with_columns(
        pl.when((pl.col("no_sat_data") == 1) & (pl.col("spent_fuel") != 0))
        .then(0)
        .otherwise(pl.col("spent_fuel"))
        .alias("spent_fuel")
    )

    df = df.with_columns(pl.lit(1).alias("count"))
    df = df.with_columns(
        [
            ((pl.col("pos_s") / pl.lit(primary["norm_speed"])) * pl.col("dtime"))
            .fill_nan(0)
            .alias("load"),
        ]
    )
    df = df.with_columns(
        pl.when(pl.col("pos_s_m").gt(0))
        .then(pl.col("spent_fuel"))
        .otherwise(None)
        .alias("spent_fuel_m")
    )

    df = df.with_columns(pl.col("dtime").mul(pl.col("ign")).alias("es"))
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
            pl.col("spent_fuel_m").sum(),
            pl.col("pos_s_m").mean(),
            pl.col("dtime_m").sum(),
            pl.col("es").sum(),
        ]
    )

    df = df.with_columns(
        pl.when(
            (
                ~pl.lit(primary["is_special_car"])
                & (pl.col("pos_s") == 0)
                & (pl.col("spent_fuel") > 0)
                & (pl.col("spent_fuel") < REFUELING_LIMIT)
            )
            | (
                pl.lit(primary["is_special_car"])
                & pl.lit(primary["ign_working"])
                & (pl.col("ign") == 0)
                & (pl.col("pos_s") == 0)
                & (pl.col("spent_fuel") > 0)
                & (pl.col("spent_fuel") < REFUELING_LIMIT)
            )
        )
        .then(0)
        .otherwise(pl.col("spent_fuel"))
        .alias("spent_fuel")
    )

    df = df.with_columns(
        pl.col("spent_fuel")
        .rolling_mean_by("timestamp", window_size="30m", closed="both")
        .alias("spent_fuel_rolling")
    )

    # повышение уровня топлива
    df = df.with_columns(
        (
            (pl.col("spent_fuel").gt(0) & pl.col("pos_s").lt(1 / 16))
            .alias("is_refuel")
            .cast(pl.Int8)
        )
    )
    df = df.with_columns(
        pl.col("is_refuel")
        .ne(pl.col("is_refuel").shift())
        .cum_sum()
        .alias("refuel_group")
    )
    df = df.with_columns(
        pl.col("dtime").sum().over(["refuel_group"]).alias("dtime_refuel")
    )

    df = df.with_columns(
        pl.col("spent_fuel")
        .mul(pl.col("is_refuel"))
        .sum()
        .over(["refuel_group"])
        .alias("refuel")
    )

    df = df.with_columns(
        pl.col("dtime_refuel").count().over(["dtime_refuel"]).alias("refuel_count")
    )

    df = df.with_columns(
        pl.col("refuel").sum().over([col_dtime_half]),
        pl.col("dtime_refuel").sum().over([col_dtime_half]),
    )
    df = df.with_columns(
        (
            pl.col("dtime_refuel").gt(60)
            & pl.col("refuel").gt(1)
            & pl.col("refuel_count").gt(1)
        )
        .cast(pl.Int8)
        .alias("is_refuel_eligble")
    )
    df = df.with_columns(
        pl.col("is_refuel_eligble")
        .mul(pl.col("refuel"))
        .max()
        .over([col_dtime_half])
        .alias("refuel")
    )

    return df, reports