import copy
import gzip
import logging
import os
from datetime import datetime
from pathlib import Path

import orjson
import polars as pl

from core.services.providers.glonass.constants import (
    ALL_COLUMNS,
    GL_ACTION_KEYS,
    GLOBAL_GLONASS_ACTIONS,
)
from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.terminal_bulk.config import (
    CarCtx,
    FORMAT_CSV_GZ,
    FORMAT_PARQUET,
    JobConfig,
    MODE_MAPPED,
)

logger = logging.getLogger("terminal_bulk")

TIME_KEY = "%Y%m%dT%H%M%S"


def window_key(start: str, end: str) -> str:
    s = datetime.fromisoformat(start).strftime(TIME_KEY)
    e = datetime.fromisoformat(end).strftime(TIME_KEY)
    return f"{s}_{e}"


def raw_path(cfg: JobConfig, ctx: CarCtx, start: str, end: str) -> Path:
    return cfg.raw_dir / f"auto{ctx.vehicle_id}" / f"{window_key(start, end)}.json.gz"


def part_path(cfg: JobConfig, ctx: CarCtx, start: str, end: str) -> Path:
    return cfg.parts_dir / f"auto{ctx.vehicle_id}" / f"{window_key(start, end)}.parquet"


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _provider_shell() -> GlonassGeneralProvider:
    gp = object.__new__(GlonassGeneralProvider)
    gp.processed_messages = 0
    gp.total_messages = 0
    gp.sensors_mapping_cache = {}
    gp.old_car_id = None
    return gp


def load_sensors_mapping(car) -> dict:
    return copy.deepcopy(_provider_shell()._get_sensors_mapping(car))


def map_window(cfg: JobConfig, ctx: CarCtx, start: str, end: str) -> int:
    wire = raw_path(cfg, ctx, start, end).read_bytes()
    messages = orjson.loads(gzip.decompress(wire)).get("messages") or []
    del wire
    if not messages:
        return 0

    gp = _provider_shell()
    if cfg.mode == MODE_MAPPED:
        df = gp._process_general(
            ctx.car,
            messages,
            copy.deepcopy(ctx.sensors_mapping),
            ALL_COLUMNS,
            [GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.auto_column), GLOBAL_GLONASS_ACTIONS.get(GL_ACTION_KEYS.amtr_merge)],
            return_df=True,
        )
    else:
        df = gp._process_unmapped(ctx.car, messages)
    del messages

    if "auto" in df.columns:
        df = df.with_columns(pl.col("auto").cast(pl.String))
    if df.is_empty():
        return 0
    path = part_path(cfg, ctx, start, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)
    return df.shape[0]


def finalize_car(cfg: JobConfig, ctx: CarCtx, windows) -> tuple[int, str]:
    parts = [p for p in (part_path(cfg, ctx, w.start, w.end) for w in windows) if p.exists()]
    if not parts:
        return 0, ""

    df = pl.concat([pl.read_parquet(p) for p in parts], how="diagonal_relaxed")
    sort_key = "timestamp" if "timestamp" in df.columns else "deviceTime"
    if sort_key in df.columns:
        df = df.sort(sort_key, maintain_order=True)
    if cfg.mode == MODE_MAPPED:
        df = df.unique(maintain_order=True)

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    base = cfg.out_dir / f"auto{ctx.vehicle_id}_{cfg.mode}"
    if cfg.fmt == FORMAT_PARQUET:
        out = base.with_suffix(".parquet")
        tmp = out.with_name(out.name + ".tmp")
        df.write_parquet(tmp, compression="zstd", compression_level=6)
    elif cfg.fmt == FORMAT_CSV_GZ:
        out = Path(str(base) + ".csv.gz")
        tmp = out.with_name(out.name + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=6) as f:
            df.write_csv(f)
    else:
        raise ValueError(f"Неизвестный формат: {cfg.fmt}")
    os.replace(tmp, out)
    return df.shape[0], str(out)


def cleanup_car(cfg: JobConfig, ctx: CarCtx, windows) -> None:
    for w in windows:
        part_path(cfg, ctx, w.start, w.end).unlink(missing_ok=True)
        if not cfg.keep_raw:
            raw_path(cfg, ctx, w.start, w.end).unlink(missing_ok=True)
    for d in (cfg.parts_dir / f"auto{ctx.vehicle_id}", cfg.raw_dir / f"auto{ctx.vehicle_id}"):
        try:
            d.rmdir()
        except OSError:
            pass
