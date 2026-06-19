import polars as pl

def alg_piece_remove_message_delays(df: pl.DataFrame):
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
        df = df.filter(pl.col("server_delay").lt(360))
        return df
    return df