from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

FORMAT_PARQUET = "parquet"
FORMAT_CSV_GZ = "csv.gz"
FORMATS = (FORMAT_PARQUET, FORMAT_CSV_GZ)

MODE_MAPPED = "raw_mapped"
MODE_RAW = "raw"
MODES = (MODE_MAPPED, MODE_RAW)


@dataclass
class CarCtx:
    uuid: str
    vehicle_id: int
    name: str
    car: Any                      # core.models.Car, воркеры используют только .id
    sensors_mapping: dict = field(default_factory=dict)


@dataclass
class JobConfig:
    provider_name: str
    login: str
    password: str
    start: datetime               # UTC, начало периода
    end: datetime                 # UTC, конец периода
    mode: str                     # raw_mapped | raw
    fmt: str                      # parquet | csv.gz
    job_dir: Path
    workers: int = 12             # максимум одновременных запросов
    start_workers: int = 6        # с чего начинаем; дальше +1 за каждые N успешных запросов
    per_car: int = 1              # максимум одновременных запросов по одной машине
    car_group: int = 24           # машины обрабатываются группами: окна внутри группы идут вперемешку
    transform_workers: int = 2
    window_days: int = 90
    interval: float = 0.55
    max_attempts: int = 5
    read_timeout: int = 900
    progress_interval: int = 30
    keep_raw: bool = False
    retry_failed: bool = False

    @property
    def raw_dir(self) -> Path:
        return self.job_dir / "raw"

    @property
    def parts_dir(self) -> Path:
        return self.job_dir / "parts"

    @property
    def out_dir(self) -> Path:
        return self.job_dir / "output"
