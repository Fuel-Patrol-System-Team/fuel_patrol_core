"""
Сбор обучающих данных из уже выгруженных сырых файлов (raw_mapped) вместо
живого API - те же production-функции (calculate_primary_single ->
calculate_norms_single -> compute_leaks -> apply_filters), но источник
данных - локальные CSV в ZIP, а не GlonassGeneralProvider._fetch_messages_for_period.

Файлы ожидаются в формате auto<ID>_raw_mapped.csv.zip - именно то, что
производит GlonassGeneralProvider.parse_raw_data(mode="raw_mapped").
Колонка "auto" в самом CSV уже содержит UUID машины - car_id берём прямо
оттуда, кросс-сверка с DataProvider/org_id не нужна.

Никакой нарезки на чанки не требуется (в отличие от collect_ml_training_data.py) -
весь период обрабатывается за один проход, это заодно снимает проблему
фрагментации скользящих/span-вычислений по границам чанков.

Использование:
    python manage.py collect_from_raw_files --raw-dir raw_data_20260101_20260901 \\
        --out-dir data/ml_training/rt_full
"""
import logging
import zipfile
from pathlib import Path

import polars as pl
from django.core.management.base import BaseCommand, CommandError

from core.models import Car, DataProvider
from core.services.providers import leaks_service

from app.tasks import (
    CarDataService,
    FilteringService,
    GlonassGeneralProvider,
    NormsService,
)

logger = logging.getLogger(__name__)

EXTRA_RAW_COLUMNS = ["latitude", "longitude", "event_code"]

# Машины, у которых при сборке через raw_mapped-файлы обнаружена ДВОЙНАЯ
# калибровка ДУТ: raw_mapped формат уже отдаёт calc_sensors_fuel_level
# ОТКАЛИБРОВАННЫМ (mode="raw_mapped" сам прогоняет _chart_preprocess/tarify
# при экспорте), а дальше LeaksService.compute_leaks() калибрует его ЕЩЁ
# РАЗ через тот же grades-кривую (рассчитана на приём сырого, ещё не
# откалиброванного сигнала - так это работает в live-режиме "fuel", откуда
# и был скопирован этот шаг). Для датчиков с ADC-доменной кривой (0-4095)
# это гарантированно ломает результат (уже финальные литры интерпретируются
# как сырой код АЦП). Подтверждено: реальные объёмы сливов в БД CarReport
# (живой прод, без этого бага) для этих же машин - нормальные 8-46Л, а не
# доли литра, как получалось бы из повторной калибровки.
# Обходной путь: для ЭТИХ конкретных машин считаем fuel_first/fuel_last/
# spent_fuel/spent_fuel_refuel напрямую из calc_sensors_fuel_level
# raw_mapped-файла (он уже корректно откалиброван, второй раз калибровать
# не нужно), остальные метрики (rpm/load/travel/dtime) не затронуты багом,
# не трогаем.
DOUBLE_CALIBRATED_FUEL_CAR_IDS = {
    "019ef36e-f6b0-7c00-bcc8-02b7a2b3c717",  # Е630ЕА797
    "b9b6817d-c813-430d-9bc3-0a72771bf70d",  # в431но 64
    "968a1ea8-7add-47cb-9e70-2ef0de824de6",  # а186сн 797
}


FUEL_LEVEL_PLAUSIBLE_MAX_L = 500.0  # отсекаем явные выбросы датчика (напр. 13000+ у в431но 64) -
                                     # ни один бак РТ не вмещает больше этого


def _fix_double_calibrated_fuel(raw_df: pl.DataFrame, rows: list[dict]) -> None:
    """Пересчитывает fuel_first/fuel_last/spent_fuel/spent_fuel_refuel по
    каждому часовому окну напрямую из calc_sensors_fuel_level raw_mapped-файла
    (уже корректно откалиброван) - in-place поверх rows, которые пришли из
    задвоенно откалиброванного filtered_df. Сообщения с физически
    невозможным значением (глитч датчика) отбрасываются до агрегации."""
    hourly = (
        raw_df.filter(pl.col("calc_sensors_fuel_level").is_between(0, FUEL_LEVEL_PLAUSIBLE_MAX_L))
        .sort("timestamp")
        .with_columns(pl.col("timestamp").dt.truncate("1h").alias("_hour"))
        .group_by("_hour")
        .agg(
            pl.col("calc_sensors_fuel_level").first().alias("_level_first"),
            pl.col("calc_sensors_fuel_level").last().alias("_level_last"),
        )
    )
    by_hour = {
        r["_hour"]: (r["_level_first"], r["_level_last"])
        for r in hourly.to_dicts()
        if r["_level_first"] is not None and r["_level_last"] is not None
    }
    for row in rows:
        ts = row.get("timestamp")
        if ts is None:
            continue
        hour = ts.replace(minute=0, second=0, microsecond=0)
        levels = by_hour.get(hour)
        if levels is None:
            continue
        level_first, level_last = levels
        old_spent = row.get("spent_fuel") or 0.0
        new_spent = max(level_first - level_last, 0.0)
        new_refuel = max(level_last - level_first, 0.0)
        # spent_fuel_standing - доли не знаем заново без пересчёта
        # moving/standing сегментов, переносим ту же пропорцию, что была
        ratio = (new_spent / old_spent) if old_spent > 0 else 0.0
        row["fuel_first"] = level_first
        row["fuel_last"] = level_last
        row["spent_fuel"] = new_spent
        row["spent_fuel_refuel"] = new_refuel
        if "spent_fuel_standing" in row and row["spent_fuel_standing"]:
            row["spent_fuel_standing"] = row["spent_fuel_standing"] * ratio


class CsvSink:
    def __init__(self, path: Path, resume: bool):
        self.path = path
        self._header_written = path.exists() and resume
        mode = "a" if resume and path.exists() else "w"
        self._fh = open(path, mode, newline="", encoding="utf-8-sig")
        self._writer = None

    def write_rows(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        import csv
        if self._writer is None:
            fieldnames = list(rows[0].keys())
            self._writer = csv.DictWriter(self._fh, fieldnames=fieldnames, extrasaction="ignore")
            if not self._header_written:
                self._writer.writeheader()
                self._header_written = True
        self._writer.writerows(rows)
        self._fh.flush()
        return len(rows)

    def close(self):
        self._fh.close()


class Command(BaseCommand):
    help = "Собирает обучающие данные из локальных raw_mapped ZIP-файлов (без API)"

    def add_arguments(self, parser):
        parser.add_argument("--raw-dir", type=str, required=True, help="Папка с auto<ID>_raw_mapped.csv.zip")
        parser.add_argument("--out-dir", type=str, default="data/ml_training/rt_full")
        parser.add_argument("--resume", action="store_true")

    def handle(self, *args, **options):
        raw_dir = Path(options["raw_dir"])
        if not raw_dir.exists():
            raise CommandError(f"Папка не найдена: {raw_dir}")

        zip_files = sorted(raw_dir.glob("*_raw_mapped.csv.zip"))
        if not zip_files:
            raise CommandError(f"Нет файлов *_raw_mapped.csv.zip в {raw_dir}")

        out_dir = Path(options["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        sink = CsvSink(out_dir / "fuel_training_data.csv", options["resume"])

        self.stdout.write(self.style.NOTICE(f"Найдено файлов: {len(zip_files)}"))

        total_rows = 0
        for i, zpath in enumerate(zip_files, 1):
            try:
                n = self._process_one_file(zpath, sink)
                total_rows += n
                self.stdout.write(f"[{i}/{len(zip_files)}] {zpath.name} -> {n} строк")
            except Exception as e:
                logger.exception(f"Ошибка обработки {zpath.name}: {e}")
                self.stdout.write(self.style.ERROR(f"[{i}/{len(zip_files)}] {zpath.name} -> ОШИБКА: {e}"))

        sink.close()
        self.stdout.write(self.style.SUCCESS(f"Готово. Всего строк: {total_rows}. CSV: {out_dir / 'fuel_training_data.csv'}"))

    def _process_one_file(self, zpath: Path, sink: CsvSink) -> int:
        # Числовые колонки принудительно типизируем - в некоторых файлах
        # столбец (чаще всего "ign") полностью пуст на всём файле, и без
        # явной схемы polars определяет его как String (нет ни одного
        # числового значения, чтобы угадать тип), что дальше роняет
        # calculate_primary_single ("cannot compare string with numeric type").
        NUMERIC_COLS = [
            "pos_s", "motohours", "mileage", "satellites", "msg_number",
            "calc_sensors_voltage", "calc_sensors_fuel_level", "ign", "rpm",
            "longitude", "latitude", "fuel_consumpt", "event_code", "amtr",
        ]
        with zipfile.ZipFile(zpath) as z:
            csv_name = z.namelist()[0]
            with z.open(csv_name) as f:
                raw_df = pl.read_csv(
                    f, try_parse_dates=False,
                    schema_overrides={
                        "timestamp": pl.String, "timestamp_server": pl.String,
                        **{c: pl.Float64 for c in NUMERIC_COLS},
                    },
                )

        if raw_df.is_empty():
            return 0

        car_id = raw_df["auto"][0]
        if car_id is None:
            return 0
        car = Car.objects.filter(id=car_id).first()
        if car is None:
            logger.warning(f"Машина {car_id} не найдена в БД, пропуск {zpath.name}")
            return 0

        # timestamp -> naive datetime[us] UTC, как в живом пайплайне;
        # auto -> Categorical, чтобы совпасть с dtype auto_df["id"] при join
        # в leaks_service._merge_with_additional_data (иначе SchemaError)
        raw_df = raw_df.with_columns(
            pl.col("timestamp").str.to_datetime(strict=False).alias("timestamp"),
            pl.col("timestamp_server").str.to_datetime(strict=False).alias("timestamp_server"),
            pl.col("auto").cast(pl.Categorical).alias("auto"),
        ).drop_nulls(subset=["timestamp"]).sort("timestamp")

        if raw_df.is_empty():
            return 0

        window_start = raw_df["timestamp"].min()
        window_end = raw_df["timestamp"].max()

        # DataProvider - берём первого связанного с машиной, только чтобы
        # получить sensors_mapping через тот же метод, что и живой провайдер
        provider = DataProvider.objects.filter(cars=car).first()
        if provider is None:
            logger.warning(f"У машины {car.name} нет DataProvider, пропуск")
            return 0

        parser_helper = GlonassGeneralProvider(None, car, provider, window_start, window_end, "fuel")
        sensors_mapping = parser_helper._get_sensors_mapping(car)

        auto_df = CarDataService.prepare_auto_data(car)

        primary_df = CarDataService.calculate_primary_single(raw_df, auto_df)
        if primary_df is None or primary_df.is_empty():
            return 0

        norms_df = NormsService.calculate_norms_single(raw_df, primary_df, auto_df)
        if norms_df is None or norms_df.is_empty():
            return 0

        leak_service = leaks_service.LeaksService()
        leaks_result, intermediate_df, reports = leak_service.compute_leaks(
            auto_df=auto_df, data_df=raw_df, sensors=sensors_mapping,
            primary_df=primary_df, norma_df=norms_df,
            is_save_bad_data=False, is_filter_bad_data=True,
        )
        if leaks_result is None or leaks_result.is_empty():
            return 0

        filtering_service = FilteringService()
        true_leaks_df, filtered_df = filtering_service.apply_filters(leaks_result)
        if filtered_df is None or filtered_df.is_empty():
            return 0

        extra_cols = [c for c in EXTRA_RAW_COLUMNS if c in raw_df.columns]
        rows = filtered_df.to_dicts()
        if str(car.id) in DOUBLE_CALIBRATED_FUEL_CAR_IDS:
            _fix_double_calibrated_fuel(raw_df, rows)
        for row in rows:
            row["car_id"] = str(car.id)
            row["car_name"] = car.name
            row["window_start"] = window_start.isoformat()
            row["window_end"] = window_end.isoformat()

        return sink.write_rows(rows)
