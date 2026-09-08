# %%
import polars as pl

# %%
import plotly.express as px

# %%
idf = pl.read_csv(
    "/data/datasets/fuel/raw_data_norms_rpm.csv",
    schema_overrides={
        "timestamp": pl.Datetime,
        "timestamp_server": pl.Datetime,
        "rpm": pl.Float32,
    },
)

# %%
cars = (
    idf.group_by("auto")
    .agg(pl.col("rpm").max())
    .filter(pl.col("rpm").is_not_null())["auto"]
    .to_list()
)

# %%
cars

# %%
id = "4979c0aa-d659-49c9-877e-fd8335a3af79"

# %%
df = idf.filter(pl.col("auto").is_in(cars))

# %%
df = df.filter(pl.col("auto").eq(id))

# %%
df = df.filter(
    pl.col("timestamp").sub(pl.col("timestamp_server")).dt.total_hours().abs().lt(2)
    & pl.col("msg_number").gt(0)
    & pl.col("satellites").gt(0)
)

# %%
df = df.with_columns(
    pl.col("calc_sensors_fuel_level")
    .diff()
    .over([pl.col("timestamp").dt.truncate("1h")])
    .alias("spent_fuel")
)

# %%
df = df.with_columns(
    pl.col("pos_s")
    .rolling_mean_by(by="timestamp", window_size="1m", closed="both")
    .over("auto")
    .alias("pos_s_rolling"),
)
df = df.with_columns(
    pl.col("pos_s_rolling").lt(1).cast(pl.Int32).over("auto").alias("is_standing")
)
df = df.with_columns(pl.col("is_standing").diff().abs().cum_sum().alias("speed_group"))

# %%
df = df.with_columns(
    pl.col("timestamp")
    .diff()
    .dt.total_seconds()
    .over(["auto", pl.col("timestamp").dt.truncate("30m")])
    .alias("dtime")
)

# %%
df = df.with_columns(
    pl.col("rpm")
    .fill_null(0)
    .rolling_mean_by(by="timestamp", window_size="30s")
    .alias("rpm_rolling")
)

# %%
df = df.with_columns(
    pl.col("spent_fuel")
    .sum()
    .over(pl.col("timestamp").dt.truncate("20m"))
    .alias("spent_fuel_cum_sum")
)

# %%
# # chart = px.line(df, x="timestamp", y="spent_fuel_cum_sum")
# chart.show()

# %%
df = df.with_columns(
    pl.when((pl.col("pos_s_rolling").lt(0.9)) & pl.col("spent_fuel").gt(0))
    .then(0)
    .otherwise(pl.col("spent_fuel"))
    .alias("spent_fuel")
)

# %%
df = df.with_columns(
    pl.col("spent_fuel")
    .sum()
    .over([pl.col("timestamp").dt.truncate("30m")])
    .alias("spent_fuel_30")
)

# %%


agg = df.group_by_dynamic(index_column="timestamp", every="1h", group_by="auto").agg(
    pl.col("spent_fuel").sum(),
    pl.col("spent_fuel").truediv(pl.col("dtime")).abs().sum().alias("fpm"),
    pl.col("pos_s").mul(pl.col("dtime")).sum().alias("travel"),
    pl.col("pos_s").mean(),
    pl.col("dtime").sum(),
    pl.col("rpm").mean(),
    pl.col("satellites").mean(),
    pl.col("rpm").mul(pl.col("dtime")).sum().alias("rpm_total"),
    pl.col("spent_fuel_cum_sum").sum(),
    pl.col("spent_fuel_30").first(),
)

# %%
# chart = px.line(
#     pl.col("spent_fuel_cum_sum").alias("")
# )

# %%
agg = agg.filter(pl.col("satellites").gt(0) & pl.col("spent_fuel").lt(0))

# %%
agg = agg.with_columns(pl.col("spent_fuel").abs())

# %%
arr = agg.select(["travel", "spent_fuel"]).to_numpy()
x = arr[:, 0].reshape(-1, 1)
y = arr[:, 1].reshape(-1, 1)

# %%
from sklearn import linear_model

reg = linear_model.Lasso(alpha=0.1)
reg = reg.fit(X=x, y=y)

# %%
reg.coef_

# %%
result = reg.predict(x)
agg = agg.with_columns(pl.Series(result).alias("prediction_fuel"))

# %%
arr = agg.select(["rpm_total", "spent_fuel"]).to_numpy()
x = arr[:, 0].reshape(-1, 1)
y = arr[:, 1].reshape(-1, 1)

# %%
from sklearn import linear_model

reg = linear_model.Lasso(alpha=0.1)
reg = reg.fit(X=x, y=y)

# %%
result = reg.predict(x)
agg = agg.with_columns(pl.Series(result).alias("prediction_rpm"))

# %%
agg = agg.with_columns(
    pl.col("prediction_fuel")
    .sub(pl.col("spent_fuel"))
    .truediv(pl.col("spent_fuel").max())
    .alias("dp_fuel_p"),
    pl.col("prediction_fuel").sub(pl.col("spent_fuel")).alias("dp_fuel"),
    pl.col("prediction_rpm")
    .sub(pl.col("spent_fuel"))
    .truediv(pl.col("spent_fuel").max())
    .alias("dp_rpm_p"),
    pl.col("prediction_rpm").sub(pl.col("spent_fuel")).alias("dp_rpm"),
)
agg = agg.with_columns(
    pl.col("prediction_rpm")
    .add(pl.col("dp_rpm").std().mul(2.5))
    .alias("prediction_rpm_limit"),
    pl.col("prediction_fuel")
    .add(pl.col("dp_fuel").std().mul(2.5))
    .alias("prediction_fuel_limit"),
)

# %%
chart = px.histogram(agg, x="spent_fuel", y="dp_fuel", marginal="box")
chart.show()

# %%
agg["dp_fuel"].std()

# %%
agg["dp_rpm"].std()

# %%
chart = px.scatter(
    agg, x="travel", y="spent_fuel", color="dtime", hover_data=["timestamp"]
)
chart.add_scatter(x=agg["travel"], y=agg["prediction_fuel"])
chart.add_scatter(x=agg["travel"], y=agg["prediction_fuel_limit"])
chart.show()

# %%
chart = px.scatter(
    agg, x="rpm_total", y="spent_fuel", color="fpm", hover_data=["timestamp"]
)
chart.add_scatter(x=agg["rpm_total"], y=agg["prediction_rpm"])
chart.add_scatter(x=agg["rpm_total"], y=agg["prediction_rpm_limit"])
chart.show()

# %%
agg = agg.with_columns(
    [
        pl.col("spent_fuel").gt(pl.col("prediction_rpm_limit")).alias("is_leak_rpm"),
        pl.col("spent_fuel").gt(pl.col("prediction_fuel_limit")).alias("is_leak_fuel"),
    ]
)

# %% [markdown]
# ## Leaks

# %%
leaks = agg.filter(pl.col("is_leak_fuel"))

# %%
n = -1

# %%
n = min(n + 1, leaks.shape[0] - 1)

# %%
n

# %%
record["spent_fuel"]

# %%
from datetime import timedelta

record = leaks.row(n, named=True)
tmp = record["timestamp"]
tmp_start = record["timestamp"] - timedelta(hours=1)
tmp_end = record["timestamp"] + timedelta(hours=1)

# %%
slice = df.filter(pl.col("timestamp").is_between(tmp_start, tmp_end))

# %%
slice_r = slice.select(
    [
        "timestamp",
        "pos_s",
        "spent_fuel",
        "calc_sensors_fuel_level",
        "rpm",
        "pos_s_rolling",
        "spent_fuel_30",
    ]
)

# %%
import plotly.graph_objects as go
from plotly.subplots import make_subplots

chart = go.Scatter(x=slice["timestamp"], y=slice["calc_sensors_fuel_level"])
chart2 = go.Scatter(x=slice["timestamp"], y=slice["pos_s"])
chart3 = go.Scatter(x=slice["timestamp"], y=slice["rpm"])

fig = make_subplots(rows=2, cols=2)
fig.add_trace(chart, row=1, col=1).add_vline(tmp)
fig.add_trace(chart2, row=1, col=2).add_vline(tmp)
fig.add_trace(chart3, row=2, col=1).add_vline(tmp)

fig.show()
