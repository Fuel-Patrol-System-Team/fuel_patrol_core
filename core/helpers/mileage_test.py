from enum import Enum
import polars as pl

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

def mileage_test(df: pl.DataFrame, AGG_PERIOD_MINUTES = 1, regime: MileageModes = MileageModes("standart")):
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
        df.group_by_dynamic(index_column="timestamp", every=f"{AGG_PERIOD_MINUTES}m", group_by="auto").agg([
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

