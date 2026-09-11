"""
Этап 1 ML-пайплайна: сбор обучающих данных в CSV.

Прогоняет исторические данные по ВСЕМ машинам организации (org_id) через ТЕ ЖЕ
production-функции парсинга/предобработки/расчёта, что использует
process_single_car_data_task / MileageCalculationService /
MotohoursCalculationService — то есть все чистки, калибровки и
инженерные признаки, которые уже "зашиты" в текущие алгоритмы, автоматически
попадают в CSV. Мы не переизобретаем препроцессинг - мы его переиспользуем,
чтобы обучающие данные были честной параллелью тому, что видит текущая
rule-based система.

Использование:
    python manage.py collect_ml_training_data --org-id <uuid> --date-start 2026-05-01

    # с явным концом периода и только по топливу
    python manage.py collect_ml_training_data --org-id <uuid> \\
        --date-start 2026-05-01 --date-end 2026-07-01 --domains fuel

Организация может быть привязана к нескольким DataProvider — забираются
машины со всех провайдеров этой организации, всегда весь автопарк целиком
(без выборки подмножества).

Домены собираются в отдельные CSV (fuel_training_data.csv,
mileage_training_data.csv, motohours_training_data.csv) в --out-dir,
дозаписью по мере обработки каждой машины/окна - скрипт можно прервать
и продолжить (уже обработанные (car, window) пары пропускаются при
повторном запуске, см. --resume).

Подключение нового фактора (например, давление в шинах) в будущем:
1. Завести сенсор в core/services/providers/glonass/constants.py (GL_PARAM_KEYS)
   так же, как заведены остальные (voltage, rpm, ...).
2. Добавить его в список колонок нужного mode в GlonassGeneralProvider.parse_raw_data.
3. Он автоматически попадёт в raw_df -> primary_df/leaks_result каждого домена,
   где он физически используется, и в этот CSV его нужно будет просто
   прокинуть отдельной колонкой (см. EXTRA_RAW_COLUMNS ниже) - без изменения
   остальной логики сбора.
"""
import csv
import logging
import traceback
from datetime import datetime, timedelta, timezone as py_timezone
from pathlib import Path
from typing import Any, Iterable

import polars as pl
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone as dj_timezone

from core.models import Car, DataProvider
from core.services.providers import leaks_service

# Импортируем из app.tasks, а не из отдельных core.services.providers.* модулей
# напрямую - в этом проекте у car_data_service.py есть циклическая зависимость
# на app.tasks (через fuelreport_service), и app.tasks - единственная точка
# входа, которая уже резолвит этот цикл в правильном порядке (тот же приём
# использует core/services/providers/mileage_calculation_service.py).
from app.tasks import (
    CarDataService,
    FilteringService,
    GlonassGeneralProvider,
    MileageAlgorithms,
    MileageCalculationService,
    MotohoursCalculationService,
    NormsService,
)

logger = logging.getLogger(__name__)

# Сырые GLONASS-поля, которых сегодня нет в фиче-наборе алгоритмов, но которые
# стоит утащить "про запас" уже сейчас, раз мы всё равно ходим за raw_df -
# дёшево иметь их в CSV с первого дня, даже если модель первой итерации их не использует.
EXTRA_RAW_COLUMNS = ["latitude", "longitude", "event_code"]

DOMAIN_CHOICES = ["fuel", "mileage", "motohours"]


def daterange_chunks(
    start: datetime, end: datetime, window_days: int, overlap_days: int = 0
) -> Iterable[tuple[datetime, datetime, datetime]]:
    """
    Бьёт [start, end) на окна по window_days и возвращает
    (fetch_start, fetch_end, keep_from) для каждого.

    GlonassGeneralProvider не рассчитан на запросы дольше ~90 дней за раз
    (см. TODO в glonass_general_provider.py: "time limit больше 93 = краш,
    собирать по частям") - поэтому дробим весь диапазон здесь, а не только
    ради размера батча.

    overlap_days: скользящие/span-вычисления (fall_eligble и т.п. в
    fuel.py) требуют контекста ДО начала окна - без него на границе
    каждого чанка алгоритм детекции "провалов" стартует с чистого листа
    и может не найти паттерн, который непрерывная обработка в проде
    нашла бы. fetch_start уходит на overlap_days раньше номинального
    начала чанка (кроме самого первого), а keep_from остаётся номинальным
    началом - после обработки строки из зоны нахлёста отбрасываются
    (см. _collect_fuel), дублей на стыке не будет.
    """
    cur = start
    first = True
    while cur < end:
        nxt = min(cur + timedelta(days=window_days), end)
        fetch_start = cur if first else cur - timedelta(days=overlap_days)
        yield fetch_start, nxt, cur
        cur = nxt
        first = False


class CsvSink:
    """Инкрементальная запись в CSV: открывает файл один раз, пишет header по первой строке."""

    def __init__(self, path: Path, resume: bool):
        self.path = path
        self._writer: csv.DictWriter | None = None
        self._file = None
        mode = "a" if resume and path.exists() else "w"
        self._file = open(path, mode, newline="", encoding="utf-8")
        self._existing_header = mode == "a"

    def write_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        if self._writer is None:
            fieldnames = sorted({k for row in rows for k in row.keys()})
            self._writer = csv.DictWriter(self._file, fieldnames=fieldnames, extrasaction="ignore")
            if not self._existing_header:
                self._writer.writeheader()
        for row in rows:
            self._writer.writerow(row)
        self._file.flush()
        return len(rows)

    def close(self):
        if self._file:
            self._file.close()


def _drop_overlap_rows(rows: list[dict[str, Any]], keep_from: datetime) -> list[dict[str, Any]]:
    """Фильтрует dict-строки (mileage/motohours) по timestamp >= keep_from, отбрасывая зону нахлёста."""
    if not rows or "timestamp" not in rows[0]:
        return rows
    result = []
    for row in rows:
        ts = row.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts is None:
            result.append(row)
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=py_timezone.utc)
        if ts >= keep_from:
            result.append(row)
    return result


def _to_dicts(value: Any) -> list[dict[str, Any]]:
    """Результаты сервисов бывают то polars.DataFrame, то уже list[dict] - приводим к одному виду."""
    if value is None:
        return []
    if isinstance(value, pl.DataFrame):
        return value.to_dicts() if not value.is_empty() else []
    if isinstance(value, list):
        return value
    return []


class Command(BaseCommand):
    help = "Собирает обучающие данные (fuel/mileage/motohours) в CSV, переиспользуя production-пайплайн"

    def add_arguments(self, parser):
        parser.add_argument("--org-id", type=str, required=True, help="UUID организации (DataProvider.org_id) — обязательный")
        parser.add_argument("--date-start", type=str, required=True, help="ISO-дата начала периода — обязательный")
        parser.add_argument("--date-end", type=str, default=None, help="ISO-дата конца периода (умолчание: сегодня)")
        parser.add_argument("--window-days", type=int, default=30, help="Размер окна одного запроса к провайдеру (умолчание: 30)")
        parser.add_argument("--overlap-days", type=int, default=3,
                             help="Нахлёст перед каждым чанком (кроме первого) для контекста "
                                  "скользящих/span-вычислений (fall_eligble и т.п.) - строки из "
                                  "зоны нахлёста отбрасываются после обработки, дублей не будет (умолчание: 3)")
        parser.add_argument("--domains", type=str, default=",".join(DOMAIN_CHOICES), help="Список доменов через запятую: fuel,mileage,motohours")
        parser.add_argument("--out-dir", type=str, default="data/ml_training", help="Куда писать CSV")
        parser.add_argument("--agg-minutes", type=int, default=60, help="Окно агрегации для mileage/motohours (умолчание: 60 мин, как у fuel)")
        parser.add_argument("--resume", action="store_true", help="Дописывать в уже существующие CSV, а не перезаписывать")
        parser.add_argument("--car-ids", type=str, default=None,
                             help="UUID машин через запятую - собрать только по ним, а не по всему автопарку "
                                  "организации (умолчание: все машины, как раньше). Для точечного ad-hoc сбора.")

    def handle(self, *args, **options):
        org_id: str = options["org_id"]
        domains = [d.strip() for d in options["domains"].split(",") if d.strip()]
        for d in domains:
            if d not in DOMAIN_CHOICES:
                raise CommandError(f"Неизвестный домен '{d}', допустимые: {DOMAIN_CHOICES}")

        end_date = (
            datetime.fromisoformat(options["date_end"]).astimezone(py_timezone.utc)
            if options["date_end"]
            else dj_timezone.now()
        )
        start_date = datetime.fromisoformat(options["date_start"]).astimezone(py_timezone.utc)
        if start_date >= end_date:
            raise CommandError(f"--date-start ({start_date}) должен быть раньше --date-end ({end_date})")

        providers = list(DataProvider.objects.filter(org_id=org_id))
        if not providers:
            raise CommandError(f"Нет DataProvider с org_id={org_id}")

        # Организация может иметь несколько провайдеров - собираем машины со всех,
        # всегда весь автопарк (без выборки подмножества). Если машина почему-то
        # привязана сразу к нескольким провайдерам этой организации - берём первого
        # найденного и предупреждаем, а не дублируем строки.
        car_provider_map: dict[str, tuple[Car, DataProvider]] = {}
        for provider in providers:
            for car in provider.cars.all():
                key = str(car.id)
                if key in car_provider_map:
                    logger.warning(
                        "Машина %s привязана к нескольким провайдерам организации %s, "
                        "использую первого найденного (%s)",
                        car.id, org_id, car_provider_map[key][1].name,
                    )
                    continue
                car_provider_map[key] = (car, provider)

        if options["car_ids"]:
            requested_ids = {s.strip() for s in options["car_ids"].split(",") if s.strip()}
            missing = requested_ids - set(car_provider_map.keys())
            if missing:
                raise CommandError(f"Машины не найдены в организации {org_id}: {missing}")
            car_provider_map = {k: v for k, v in car_provider_map.items() if k in requested_ids}

        cars_with_providers = sorted(car_provider_map.values(), key=lambda cp: str(cp[0].id))
        if not cars_with_providers:
            raise CommandError(f"У организации {org_id} нет машин ни у одного провайдера")

        out_dir = Path(options["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.NOTICE(
            f"Организация: {org_id} | провайдеров: {len(providers)} | машин: {len(cars_with_providers)} | "
            f"период: {start_date.date()} .. {end_date.date()} | домены: {domains}"
        ))

        sinks = {
            "fuel": CsvSink(out_dir / "fuel_training_data.csv", options["resume"]),
            "mileage": CsvSink(out_dir / "mileage_training_data.csv", options["resume"]),
            "motohours": CsvSink(out_dir / "motohours_training_data.csv", options["resume"]),
        }

        totals = {d: 0 for d in DOMAIN_CHOICES}
        errors = 0

        try:
            for i, (car, provider) in enumerate(cars_with_providers, start=1):
                self.stdout.write(f"[{i}/{len(cars_with_providers)}] машина {car.id} ({car.name}) / провайдер {provider.name}")
                for chunk_start, chunk_end, keep_from in daterange_chunks(
                    start_date, end_date, options["window_days"], options["overlap_days"]
                ):
                    if "fuel" in domains:
                        try:
                            n = self._collect_fuel(car, provider, chunk_start, chunk_end, keep_from, sinks["fuel"])
                            totals["fuel"] += n
                        except Exception:
                            errors += 1
                            logger.error(
                                "fuel: сбой на машине %s, окно %s..%s\n%s",
                                car.id, chunk_start, chunk_end, traceback.format_exc(),
                            )

                    if "mileage" in domains:
                        try:
                            n = self._collect_mileage(car, chunk_start, chunk_end, keep_from, options["agg_minutes"], sinks["mileage"])
                            totals["mileage"] += n
                        except Exception:
                            errors += 1
                            logger.error(
                                "mileage: сбой на машине %s, окно %s..%s\n%s",
                                car.id, chunk_start, chunk_end, traceback.format_exc(),
                            )

                    if "motohours" in domains:
                        try:
                            n = self._collect_motohours(car, chunk_start, chunk_end, keep_from, options["agg_minutes"], sinks["motohours"])
                            totals["motohours"] += n
                        except Exception:
                            errors += 1
                            logger.error(
                                "motohours: сбой на машине %s, окно %s..%s\n%s",
                                car.id, chunk_start, chunk_end, traceback.format_exc(),
                            )
        finally:
            for sink in sinks.values():
                sink.close()

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Строк собрано: fuel={totals['fuel']} mileage={totals['mileage']} "
            f"motohours={totals['motohours']}. Ошибок окон: {errors}."
        ))
        self.stdout.write(f"CSV в {out_dir.resolve()}")

    # ---------------------------------------------------------------- fuel

    def _collect_fuel(self, car: Car, provider: DataProvider, chunk_start: datetime, chunk_end: datetime,
                       keep_from: datetime, sink: CsvSink) -> int:
        parser = GlonassGeneralProvider(None, car, provider, chunk_start, chunk_end, "fuel")
        status, raw_df, sensors = parser.parse_raw_data("fuel", return_df=True)
        if not status or raw_df is None or raw_df.is_empty():
            return 0

        auto_df = CarDataService.prepare_auto_data(car)

        primary_df = CarDataService.calculate_primary_single(raw_df, auto_df)
        if primary_df is None or primary_df.is_empty():
            return 0

        norms_df = NormsService.calculate_norms_single(raw_df, primary_df, auto_df)
        if norms_df is None or norms_df.is_empty():
            return 0

        leak_service = leaks_service.LeaksService()
        leaks_result, intermediate_df, reports = leak_service.compute_leaks(
            auto_df=auto_df,
            data_df=raw_df,
            sensors=sensors,
            primary_df=primary_df,
            norma_df=norms_df,
            is_save_bad_data=False,
            is_filter_bad_data=True,
        )
        if leaks_result is None or leaks_result.is_empty():
            return 0

        filtering_service = FilteringService()
        # true_leaks_df = только то, что прошло все фильтры (сегодняшний rule-based вердикт).
        # filtered_df = ВСЕ окна с посчитанными признаками + is_picked_leak/picked_by/filtered -
        # именно filtered_df и есть обучающая выборка (позитивы и негативы вместе).
        true_leaks_df, filtered_df = filtering_service.apply_filters(leaks_result)
        if filtered_df is None or filtered_df.is_empty():
            return 0

        # Отбрасываем зону нахлёста (см. daterange_chunks) - она была нужна
        # только чтобы дать скользящим/span-вычислениям контекст ДО chunk_start,
        # сами эти строки не должны попасть в обучающую выборку дважды.
        if "timestamp" in filtered_df.columns:
            # колонка timestamp в filtered_df - naive datetime[us] (внутри пайплайна
            # всё в UTC без явной таймзоны), а keep_from - aware (py_timezone.utc) -
            # приводим к naive той же UTC-отметки перед сравнением, иначе polars
            # роняет SchemaError на сравнении разных dtype.
            keep_from_naive = keep_from.replace(tzinfo=None)
            filtered_df = filtered_df.filter(pl.col("timestamp") >= keep_from_naive)
        if filtered_df.is_empty():
            return 0

        extra_cols = [c for c in EXTRA_RAW_COLUMNS if c in raw_df.columns]
        rows = filtered_df.to_dicts()
        for row in rows:
            row["car_id"] = str(car.id)
            row["car_name"] = car.name
            row["window_start"] = chunk_start.isoformat()
            row["window_end"] = chunk_end.isoformat()

        return sink.write_rows(rows)

    # ------------------------------------------------------------- mileage

    def _collect_mileage(self, car: Car, chunk_start: datetime, chunk_end: datetime,
                          keep_from: datetime, agg_minutes: int, sink: CsvSink) -> int:
        result, status_code = MileageCalculationService.calculate_mileage(
            car_id=str(car.id),
            alg=MileageAlgorithms.fraud,  # mileage_test_fraud_new - активный в проде алгоритм
            agg=agg_minutes,
            start_date=chunk_start,
            end_date=chunk_end,
            is_save_bad_data=False,
        )
        if status_code != 200:
            return 0

        payload = result.get("result", {})
        rows = _to_dicts(payload.get("data"))
        rows = _drop_overlap_rows(rows, keep_from)
        if not rows:
            return 0

        for row in rows:
            row["car_id"] = str(car.id)
            row["car_name"] = car.name

        return sink.write_rows(rows)

    # ----------------------------------------------------------- motohours

    def _collect_motohours(self, car: Car, chunk_start: datetime, chunk_end: datetime,
                            keep_from: datetime, agg_minutes: int, sink: CsvSink) -> int:
        result, status_code = MotohoursCalculationService.calculate_motohours(
            car_id=str(car.id),
            agg=agg_minutes,
            start_date=chunk_start,
            end_date=chunk_end,
            is_save_bad_data=False,
        )
        if status_code != 200:
            return 0

        payload = result.get("result", {})
        rows = _to_dicts(payload.get("data"))
        rows = _drop_overlap_rows(rows, keep_from)
        if not rows:
            return 0

        for row in rows:
            row["car_id"] = str(car.id)
            row["car_name"] = car.name

        return sink.write_rows(rows)
