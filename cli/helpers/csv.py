import polars as pl
def write_csv_compute(df: pl.DataFrame, output_path: str):
    columns = []
    patterns = ["grades", "median_degrees"]
    for col in df.columns:
        is_pattern = False
        for pat in patterns:
            if pat in col:
                is_pattern = True
                break
        if not is_pattern:
            columns.append(col)
    return df.select(columns).write_csv(output_path)