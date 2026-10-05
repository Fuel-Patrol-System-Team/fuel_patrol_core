import logging
from typing import cast

import polars as pl

logger = logging.getLogger(__name__)


def alg_piece_remove_skipped_messages(df: pl.DataFrame, msg_skip_time_small=2, msg_skip_big=10, msg_skip_critical=120):
    """
        dtime, msg_number - нужны в df
        Убирает пропуски в сообщениях(если пропуск во времени достаточно большой)
    """
    if "dtime" not in df.columns:
        logger.warning("No dtime column")
        return df
    dtime_mean = df["dtime"].mean()
    df = df.with_columns(
        pl.col("msg_number").diff().abs().mul(pl.lit(dtime_mean)).gt(msg_skip_time_small * 60).cast(pl.Int32).alias("msg_skip")
    )
    df = df.with_columns(
        pl.col("msg_number").diff().abs().mul(pl.lit(dtime_mean)).gt(msg_skip_big * 60).cast(pl.Int32).alias("msg_skip_big")
    )
    df = df.with_columns(
        pl.col("msg_number").diff().abs().mul(pl.lit(dtime_mean)).gt(msg_skip_critical * 60).cast(pl.Int32).alias("msg_skip_critical")
    )

    df = df.filter(pl.col("msg_skip_critical").eq(0))
    return df

def alg_remove_max_pieces(df: pl.DataFrame, sensor: str):
    is_max_6550= ( df[sensor].is_between(6550, 6555) & df[sensor].lt(6555) ).any()
    if is_max_6550:
        df = df.filter(
            pl.col(sensor).le(6549)
        )
        return df
    is_max_100 = (df[sensor].is_between(100, 102) & df[sensor].lt(102) ).any()
    if is_max_100:
        df = df.filter(
            pl.col(sensor).le(100)
        )
    is_max_4090 = (df[sensor].is_between(4090, 4096) & df[sensor].lt(4096)).any()
    if is_max_4090:
        df =df.filter(
            pl.col(sensor).le(4090)
        )
    return df

def alg_piece_remove_messages_jumps(df: pl.DataFrame):
    df = df.with_columns(
        pl.col("msg_number").diff().replace([-1, 1], 0).cum_sum().alias("msg_number_cum")
    )
    df = df.filter(
        pl.col("msg_number").diff().replace([-1, 1], 0).cum_sum().abs().gt(8)
    )
    return df
    
def alg_piece_remove_message_delays(df: pl.DataFrame):
    """
        dtime, timestamp, 
        Убирает сообщения где есть задержка между клиентом и сервером
    """
    
    if "timestamp_server" in df.columns:
        # remove first line
        df = df.with_columns(
            pl.col("timestamp_server").cast(pl.Datetime).alias("timestamp_server")
        )

        df = df.with_columns(
            pl.col("timestamp_server")
            .sub(pl.col("timestamp"))
            .abs()
            .dt.total_minutes()
            .alias("server_delay")
        )
        usual_delay = df["server_delay"].mean()
        if usual_delay is None:
            return df
        usual_delay = cast(float, usual_delay)
        df = df.filter(pl.col("server_delay").le(usual_delay * 3))
        return df
    return df