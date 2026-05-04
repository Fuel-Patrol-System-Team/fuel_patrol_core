from enum import Enum
from typing import Any, Dict, List, cast
import polars as pl

from core.helpers.fuel import tarify_car_by_sensor

class MileageModes(str, Enum):
    standart = "standart"
    agg = "agg"

    @staticmethod
    def convert(mode_str: str):
        if mode_str == MileageModes.standart:
            return MileageModes.standart
        elif mode_str == MileageModes.agg:
            return MileageModes.agg
        else:
            return None

def make_empty_mileage_result(mode: MileageModes):
    return {
        "travel": None,
        "travel_fraud": None,
        "first_mileage": None,
        "last_mileage": None,
        "data": [] if mode is not MileageModes.agg else None
    }


def mileage_test_compute(df: pl.DataFrame, auto_record: Dict[str, Any], AGG: int | None, regime: MileageModes = MileageModes("standart"), AGG_MINUTES_DEFAULT = 60):
    agg = AGG_MINUTES_DEFAULT if AGG is None else AGG
    if auto_record["mileage_grading"] is not None:
        input = auto_record["mileage_grading"][0]["input"]
        output = auto_record["mileage_grading"][0]["output"]
        df = df.with_columns(
            pl.col("mileage").truediv(pl.lit(input)).mul(pl.lit(output))
        )
    col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")
    df = df.filter(
        pl.col("mileage").is_not_nan()
    )
    df = df.with_columns(
        [
            pl.col("mileage").diff().over("auto", col_dtime_2hour).fill_null(0).cast(pl.Float32).alias("travel"),
        ]
    )
    
    df = df.with_columns(
        [
            pl.when(pl.col("travel").lt(0)).then(pl.col("travel").abs()).otherwise(0).alias("travel_fraud")
        ]
    )       
             
    df = (
        df.group_by_dynamic(index_column="timestamp", every=f"{agg}m", group_by="auto").agg([
            pl.col("travel").sum(),
            pl.col("travel_fraud").sum(),
            pl.col("mileage").max().alias("last_mileage"),
            pl.col("mileage").min().alias("first_mileage"),
        ])
    )
    df = df.with_columns(
        pl.col("last_mileage").fill_null(strategy="backward").over("auto")
    )

    return {
        "travel": df['travel'].sum(),
        "travel_fraud": df['travel_fraud'].sum(),
        "first_mileage": df['first_mileage'].first(),
        "last_mileage": df['last_mileage'].last(),
        "data": df.to_dicts() if regime is MileageModes.agg else None
    }

def mileage_test_fraud(
    auto: str,
    df: pl.DataFrame,
    auto_record: Dict[str, Any],
    AGG: int | None,
    regime: MileageModes = MileageModes("standart"),
    AGG_DEFAULT_MIN = 720,
    DEBUG=True,
):
    agg = AGG_DEFAULT_MIN if AGG is None else AGG
    col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")
    col_dtime_30 = pl.col("timestamp").dt.truncate("30m")
    PERIOD_AGG_FRAUD = 30
    MAX_SPEED_FOR_CHECK = 224
    MAX_CAP_MINUTES = 5
    df = df.filter(pl.col("mileage").is_not_null() & (pl.col("mileage") > 0))
    if df.shape[0] != 0:
        print(f"Car is being processed {auto}")

    if auto_record["mileage_grading"] is not None:
        input = auto_record["mileage_grading"][0]["input"]
        output = auto_record["mileage_grading"][0]["output"]
        df = df.with_columns(
            pl.col("mileage").truediv(pl.lit(input)).mul(pl.lit(output))
        )
    df = df.with_columns(
        [
            pl.col("mileage")
            .diff()
            .over(["auto", col_dtime_2hour])
            .fill_null(0)
            .replace([127.0, 254.0, -127.0, -254.0], 0)
            .alias("dmileage"),
            pl.col("timestamp")
            .diff()
            .over(["auto", col_dtime_2hour])
            .dt.total_seconds()
            .fill_null(0)
            .truediv(3600)
            .alias("dtime"),
        ]
    )

    # внутренний примивный фильтр
    is_zero_one_sensor = df.filter(pl.col("dmileage") < 1)["dmileage"].max() == 0

    df = df.with_columns(
        [
            pl.col("dtime")
            .clip(upper_bound=pl.lit(MAX_CAP_MINUTES / 3600))
            .alias("ptime"),
            pl.lit(is_zero_one_sensor).alias("sensortype"),
        ]
    )
    df = df.with_columns(
        [(pl.col("dmileage") / pl.col("dtime")).abs().fill_nan(0).alias("du")]
    )

    df = df.filter([~((pl.col("du") > 225) & (pl.col("satellites") == 0))])

    df = df.with_columns(
        pl.when(pl.col("dmileage").abs().gt(4) & pl.col("du").gt(200))
        .then(1)
        .otherwise(0)
        .alias("jumps")
    )
    df = df.with_columns(
        [
            pl.when(pl.col("du") < 224).then(pl.col("du")).otherwise(0),
            pl.when(pl.col("du") > 225).then(1).otherwise(0).alias("corrupted"),
        ]
    )
    df = df.with_columns(
        [
            (pl.col("du") * pl.col("dtime")).alias("dmileage_r"),
            pl.col("corrupted").rolling_max_by(by="timestamp", window_size="15m"),
            pl.col("corrupted")
            .rolling_sum_by(by="timestamp", window_size="3h")
            .alias("spikes"),
        ]
    )

    df = df.with_columns(
        pl.when((pl.col("spikes") > 1))
        .then(0)
        .otherwise(pl.col("dmileage"))
        .alias("dmileage_s")
    )

    df = df.with_columns(
        [
            pl.when(pl.col("corrupted") > 1)
            .then(None)
            .otherwise(pl.col("mileage"))
            .alias("ncm"),
            pl.col("ptime").truediv(agg / 60),
            pl.when(pl.col("dmileage").abs() > 1)
            .then(pl.col("dmileage"))
            .otherwise(0)
            .alias("dmileage_big"),
        ]
    )
    df = df.filter(pl.col("ncm").is_not_null())

    df = df.group_by_dynamic(
        index_column="timestamp", every=f"{agg}m", group_by="auto"
    ).agg(
        [
            pl.col("ncm").first().alias("first_mileage"),
            pl.col("mileage").last().alias("last_mileage_real"),
            pl.col("ncm").last().alias("last_mileage"),
            pl.col("dmileage").sum().alias("travel"),
            pl.col("dmileage").max().alias("max_change"),
            pl.col("dmileage_r").sum().alias("travel_r"),
            pl.col("ncm").last(),
            pl.col("ptime").sum(),
            pl.col("sensortype").first(),
            pl.col("dmileage_s").sum(),
            pl.col("dmileage_big").sum(),
            pl.max("spikes"),
            pl.sum("jumps"),
        ]
    )

    target_for_diff = "travel" if is_zero_one_sensor else "travel_r"

    df = df.with_columns(
        [
            pl.col("last_mileage")
            .sub(pl.col("first_mileage"))
            .sub(target_for_diff)
            .alias("mileage_diff"),
            pl.col("last_mileage")
            .sub(pl.col("first_mileage"))
            .sub(target_for_diff)
            .alias("mileage_fraud"),
            pl.col("travel").sub(pl.col("travel_r")).alias("mileage_diff_r"),
        ]
    )
    # (pl.col("travel") - pl.col("travel_fraud")),
    # df = df.with_columns(
    #     (pl.col("travel") - pl.col("travel_computed"))
    #     .clip(lower_bound=0)
    #     .alias("travel_fraud_2")
    # )

    # df = df.with_columns(
    #     pl.col("travel_fraud_2").mean().over(["auto"]).alias("fraud_mean"),
    #     pl.col("travel_fraud_2").std().over(["auto"]).alias("fraud_std"),
    # )
    show_df = df.select(["timestamp", "travel", "first_mileage", "last_mileage"]).to_dicts()
    return {
        "travel": df["travel_r"].sum(),
        "travel_fraud": df["mileage_fraud"].sum(),
        "first_mileage": df["first_mileage"].first(),
        "last_mileage": df["last_mileage"].last(),
        "data": show_df if regime is MileageModes.agg else None,
    }

def mileage_test_fraud_new(
    auto: str,
    df: pl.DataFrame,
    auto_record: Dict[str, Any],
    AGG_PERIOD_MINUTES: int | None =1,
    regime:  MileageModes = MileageModes.standart,
    TIME_PERIOD=24,
    WORKING_AGG_PERIOD_HOURS=24,
):
    """
    Функция для расчета пройденного расстояния и детектирования накрутки автомобиля
    
    auto: id машины
    df: датафрейм с колонками ["timestamp", "mileage", "pos_s", "ign"]
    AGG_PERIOD_MINUTES: кастомная агрегация времени для пользователя
    regime: режим парсинга(стандартный, с возвращаемой агрегацией)
    TIME_PERIOD=количество часов, которое использование для отсчения прыжков, связанных с помехами gps
    WORKING_AGG_PERIOD_HOURS=протестированное количество часов, которое отдает адекватные результаты в нахождении точного значения накрутки
    """
    COMMON_SENSOR_ISSUE_VALUES = [-127.0, 127.0, 254.0, -254.0]

    col_dtime_period = pl.col("timestamp").dt.truncate("48h")
    spikes_dtime_period = pl.col("timestamp").dt.truncate(f"{TIME_PERIOD}h")
    CLIPPING_FACTOR = 5  # 1 - infinity нужен для сравненения последней части пробега с со всем остальным, для правильного вычисления накрутки
    MAX_SPEED_FOR_CHECK = 224
    MAX_CAP_MINUTES = 240
    df = df.filter(pl.col("mileage").is_not_null() & (pl.col("mileage") > 0))
    if df.shape[0] != 0:
        print(f"Car is being processed {auto}")
    if auto_record["mileage_grading"] is not None:
        input = auto_record["mileage_grading"][0]["input"]
        output = auto_record["mileage_grading"][0]["output"]
        df = df.with_columns(
            pl.col("mileage").truediv(pl.lit(input)).mul(pl.lit(output))
        )
    df = df.with_columns(
        [
            pl.col("mileage")
            .diff()
            .over(["auto", col_dtime_period])
            .fill_null(0)
            .replace(COMMON_SENSOR_ISSUE_VALUES, 0)
            .alias("dmileage"),
            pl.col("timestamp")
            .diff()
            .over(["auto", col_dtime_period])
            .dt.total_seconds()
            .fill_null(0)
            .truediv(3600)
            .alias("dtime"),
        ]
    )

    # внутренний примивный фильтр
    is_zero_one_sensor = df.filter(pl.col("dmileage") < 1)["dmileage"].max() in [0, 0.5]
    df = df.with_columns(
        [
            pl.col("dtime")
            .clip(upper_bound=pl.lit(MAX_CAP_MINUTES / 3600))
            .alias("ptime"),
            pl.lit(is_zero_one_sensor).alias("sensortype"),
            pl.lit(df.filter(pl.col("dmileage") < 1)["dmileage"].max()).alias(
                "sensormax"
            ),
        ]
    )
    df = df.with_columns(
        [(pl.col("dmileage") / pl.col("dtime")).abs().fill_nan(0).alias("du")]
    )

    # плохо так как из-за чистой случайности колебания могут унести расчетные значения в минус
    # df = df.filter(
    #     [~((pl.col("du") > MAX_SPEED_FOR_CHECK) & (pl.col("satellites") == 0))]
    # )

    df = df.with_columns(
        pl.when(pl.col("dmileage").abs().gt(4) & pl.col("du").gt(200))
        .then(1)
        .otherwise(0)
        .alias("jumps")
    )
    df = df.with_columns(
        [
            pl.when(pl.col("du") < MAX_SPEED_FOR_CHECK).then(pl.col("du")).otherwise(0),
            pl.when(pl.col("du") > MAX_SPEED_FOR_CHECK)
            .then(1)
            .otherwise(0)
            .alias("corrupted"),
        ]
    )
    df = df.with_columns(
        [
            (pl.col("du") * pl.col("dtime")).alias("dmileage_r"),
            pl.col("corrupted").rolling_max_by(by="timestamp", window_size="15m"),
            pl.col("corrupted")
            .rolling_sum_by(by="timestamp", window_size="1h")
            .alias("spikes"),
        ]
    )

    df = df.with_columns(
        (pl.col("spikes").gt(1) & pl.col("dmileage").abs().gt(5))
        .cast(pl.Int16)
        .alias("spikes_big")
    )

    df = df.with_columns(
        [
            pl.when(pl.col("corrupted") > 1)
            .then(None)
            .otherwise(pl.col("mileage"))
            .alias("ncm"),
            pl.col("ptime").truediv(TIME_PERIOD / 60),
            pl.col("spikes_big")
            .sum()
            .over(["timestamp", spikes_dtime_period])
            .alias("spikes_big_sum"),
        ]
    )

    df = df.with_columns(
        pl.col("spikes_big_sum").gt(20).cast(pl.Int8).alias("sensor_mileage_broken")
    )
    # df = df.filter(pl.col("ncm").is_not_null())

    df = df.with_columns(
        pl.col("timestamp")
        .last()
        .over(["auto", spikes_dtime_period])
        .alias("last_timestamp"),
    )

    df = df.with_columns(
        [
            pl.col("last_timestamp")
            .sub(pl.col("timestamp"))
            .dt.total_seconds()
            .truediv(
                pl.col("last_timestamp")
                .sub(pl.col("timestamp").first().over((["auto", spikes_dtime_period])))
                .dt.total_seconds()
            )
            .sub(1)
            .abs()
            .sub(1 - 1 / CLIPPING_FACTOR)
            .clip(lower_bound=0)
            .alias("time_factor"),
        ]
    )

    df = df.with_columns(
        pl.when(pl.col("time_factor").gt(0)).then(1).otherwise(0).alias("time_factor")
    )

    ## debug part remove on integration
    df = df.with_columns(pl.col("dmileage").cum_sum().alias("dmileage_cum_sum"))
    ## end of debug part
    df = df.with_columns(
        pl.col("dmileage").mul(pl.col("time_factor")).alias("dmileage_factor")
    )
    agg = None
    if regime == MileageModes.agg:
        agg = df.group_by_dynamic(
            index_column="timestamp", every=f"{AGG_PERIOD_MINUTES}m", group_by="auto"
        ).agg(
            [
                pl.col("ncm").first().alias("first_mileage"),
                pl.col("mileage").last().alias("last_mileage_real"),
                pl.col("ncm").last().alias("last_mileage"),
                pl.col("dmileage").sum().alias("travel"),
                pl.col("dmileage").max().alias("max_change"),
                pl.col("dmileage_r").sum().alias("travel_r"),
                pl.col("ncm").last(),
                pl.col("ptime").sum(),
                pl.col("sensortype").first(),
                pl.col("sensormax").max(),
                pl.max("spikes"),
                pl.sum("spikes_big"),
                pl.sum("jumps"),
                pl.max("sensor_mileage_broken"),
                pl.sum("dmileage_factor"),
            ]
        )
        agg = agg.to_dicts()
    df = df.group_by_dynamic(
        index_column="timestamp", every=f"{WORKING_AGG_PERIOD_HOURS}h", group_by="auto"
    ).agg(
        [
            pl.col("ncm").first().alias("first_mileage"),
            pl.col("mileage").last().alias("last_mileage_real"),
            pl.col("ncm").last().alias("last_mileage"),
            pl.col("dmileage").sum().alias("travel"),
            pl.col("dmileage").max().alias("max_change"),
            pl.col("dmileage_r").sum().alias("travel_r"),
            pl.col("ncm").last(),
            pl.col("ptime").sum(),
            pl.col("sensortype").first(),
            pl.col("sensormax").max(),
            pl.max("spikes"),
            pl.sum("spikes_big"),
            pl.sum("jumps"),
            pl.max("sensor_mileage_broken"),
            pl.sum("dmileage_factor"),
        ]
    )

    target_for_diff = "travel" if is_zero_one_sensor else "travel_r"

    df = df.with_columns(
        pl.col("travel")
        .abs()
        .sub(pl.col("dmileage_factor").abs())
        .abs()
        .truediv(pl.col("travel"))
        .alias("dmileage_factor_diff")
    )
    df = df.with_columns(
        [
            pl.col("last_mileage")
            .sub(pl.col("first_mileage"))
            .sub(target_for_diff)
            .alias("mileage_diff"),
            pl.col("last_mileage")
            .sub(pl.col("first_mileage"))
            .sub(target_for_diff)
            .alias("mileage_fraud"),
            pl.col("travel").sub(pl.col("travel_r")).alias("mileage_diff_r"),
        ]
    )

    df = df.with_columns(
        pl.col("mileage_fraud").clip(upper_bound=0).truediv(127).abs().alias("resets")
    )

    df = df.with_columns(
        pl.when((pl.lit(is_zero_one_sensor) & pl.col("resets").is_between(0, 2))  ).then(pl.col("last_mileage").add(pl.col("mileage_fraud"))).otherwise(pl.col("last_mileage")).alias("last_mileage")
    )

    df = df.with_columns(
        (
            (
                (pl.col("spikes_big").lt(20) & pl.col("dmileage_factor_diff").gt(0.65))
                & pl.col("mileage_fraud").abs().gt(100)
            )
        )
        .alias("is_actual_fraud")
        .cast(pl.Int8)
    )
    df = df.with_columns(
        pl.col("is_actual_fraud")
        .mul(pl.col("mileage_fraud"))
        .alias("true_mileage_fraud")
    )
    last_mileage = cast(float , df["last_mileage"].last())
    first_mileage = cast(float, df["first_mileage"].first())
    travel = cast(float, df["travel"].sum() if is_zero_one_sensor else df["travel_r"].sum())
    if last_mileage is not None:
        if last_mileage < first_mileage:
            last_mileage = first_mileage + travel
    return {
        "travel":travel,
        "travel_fraud": df["true_mileage_fraud"].sum(),
        "first_mileage": first_mileage,
        "last_mileage": last_mileage,
        "data": agg if regime is MileageModes.agg else None,
    }