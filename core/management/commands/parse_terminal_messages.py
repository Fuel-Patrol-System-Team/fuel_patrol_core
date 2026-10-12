import hashlib
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Car, DataProvider
from core.services.terminal_bulk.config import (
    CarCtx,
    FORMATS,
    FORMAT_PARQUET,
    JobConfig,
    MODES,
    MODE_MAPPED,
    MODE_RAW,
)
from core.services.terminal_bulk.engine import Engine, plan_windows
from core.services.terminal_bulk.fetcher import MAX_PERIOD_DAYS
from core.services.terminal_bulk.transform import load_sensors_mapping

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _parse_date(value: str, name: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise CommandError(f"{name}: неверный формат даты '{value}', нужен YYYY-MM-DD")


def _default_out_dir() -> Path:
    media = getattr(settings, "MEDIA_ROOT", None)
    return Path(media) / "raw_data" / "bulk" if media else Path("data") / "terminal_bulk"


JOB_ARTIFACTS = ("raw", "parts", "output", "manifest.sqlite", "manifest.sqlite-wal", "manifest.sqlite-shm", "progress.json", "job.log")


def _reset_job_dir(job_dir: Path) -> None:
    for name in JOB_ARTIFACTS:
        p = job_dir / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def _setup_logging(job_dir: Path) -> None:
    log = logging.getLogger("terminal_bulk")
    log.setLevel(logging.INFO)
    log.propagate = False
    for h in list(log.handlers):
        log.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = logging.FileHandler(job_dir / "job.log", encoding="utf-8")
    file.setFormatter(fmt)
    log.addHandler(console)
    log.addHandler(file)


class Command(BaseCommand):
    help = "Массовый парсинг terminalMessages провайдера в parquet/csv.gz с параллельной загрузкой и рестором"

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True, help="DataProvider.name")
        parser.add_argument("--start", required=True, help="Начало периода YYYY-MM-DD (UTC)")
        parser.add_argument("--end", required=True, help="Конец периода YYYY-MM-DD (UTC, не включая)")
        src = parser.add_mutually_exclusive_group(required=True)
        src.add_argument("--car-ids-file", help="Файл с UUID машин (формат свободный: кавычки/запятые игнорируются)")
        src.add_argument("--car-ids", help="UUID машин через запятую")
        src.add_argument("--all-cars", action="store_true", help="Все машины провайдера")
        parser.add_argument("--mode", choices=MODES, default=MODE_MAPPED,
                            help="raw_mapped = is_raw_data:false (22 колонки по маппингу сенсоров, по умолчанию); raw = все параметры (только parquet)")
        parser.add_argument("--format", dest="fmt", choices=FORMATS, default=FORMAT_PARQUET, help="Формат результата (по умолчанию parquet)")
        parser.add_argument("--workers", type=int, default=12, help="Макс. одновременных запросов (адаптивно снижается при 429/ошибках)")
        parser.add_argument("--start-workers", type=int, default=6, help="С какого числа запросов стартуем, дальше плавный рост до --workers")
        parser.add_argument("--per-car", type=int, default=1, help="Макс. одновременных запросов по одной машине")
        parser.add_argument("--car-group", type=int, default=24, help="Размер группы машин, окна которой идут вперемешку")
        parser.add_argument("--transform-workers", type=int, default=2, help="Потоки разбора ответов (память: до ~4 ГБ на окно 90 дней)")
        parser.add_argument("--window-days", type=int, default=90, help=f"Размер окна, дней (макс. {MAX_PERIOD_DAYS})")
        parser.add_argument("--interval", type=float, default=0.55, help="Минимум секунд между стартами запросов")
        parser.add_argument("--max-attempts", type=int, default=5)
        parser.add_argument("--read-timeout", type=int, default=900, help="Таймаут ожидания ответа, сек")
        parser.add_argument("--progress-interval", type=int, default=30, help="Период строки прогресса в логе, сек")
        parser.add_argument("--out-dir", default=None, help="Корневой каталог заданий (внутри создаётся подкаталог с именем задания)")
        parser.add_argument("--job-dir", default=None,
                            help="Точный каталог задания: output/, raw/, parts/, manifest.sqlite, job.log лежат прямо в нём "
                                 "(перекрывает --out-dir; другие файлы в каталоге, например список машин, не трогаются)")
        parser.add_argument("--keep-raw", action="store_true", help="Не удалять сырые gzip-ответы (для перемаппинга без API)")
        parser.add_argument("--retry-failed", action="store_true", help="Повторить окна, которые упали в прошлом запуске")
        parser.add_argument("--fresh", action="store_true", help="Удалить состояние задания и начать с нуля")
        parser.add_argument("--dry-run", action="store_true", help="Показать план и выйти")

    def handle(self, *args, **o):
        start, end = _parse_date(o["start"], "--start"), _parse_date(o["end"], "--end")
        if start >= end:
            raise CommandError("--start должен быть раньше --end")
        if o["mode"] == MODE_RAW and o["fmt"] != FORMAT_PARQUET:
            raise CommandError("Режим raw содержит вложенные колонки, он поддерживает только --format parquet")
        if not 1 <= o["window_days"] <= MAX_PERIOD_DAYS:
            raise CommandError(f"--window-days: от 1 до {MAX_PERIOD_DAYS}")

        try:
            provider = DataProvider.objects.get(name=o["provider"])
        except DataProvider.DoesNotExist:
            raise CommandError(f"Провайдер '{o['provider']}' не найден")
        meta = provider.metadata or {}
        if not meta.get("login") or not meta.get("password"):
            raise CommandError("В metadata провайдера нет login/password")

        cars = self._load_cars(provider, o)

        plan = plan_windows(start, end, o["window_days"])
        key = hashlib.sha1(
            "|".join([provider.name, o["fmt"], str(o["window_days"]), *sorted(c.uuid for c in cars)]).encode()
        ).hexdigest()[:8]
        if o["job_dir"]:
            job_dir = Path(o["job_dir"]).resolve()
        else:
            out_root = Path(o["out_dir"]) if o["out_dir"] else _default_out_dir()
            job_dir = out_root / f"{start:%Y%m%d}_{end:%Y%m%d}_{o['mode']}_{o['fmt'].replace('.', '')}_{key}"

        self.stdout.write(
            f"Машин: {len(cars)}, окон на машину: {len(plan)}, всего запросов: {len(cars) * len(plan)}, "
            f"минимум по лимитеру: {len(cars) * len(plan) * o['interval'] / 60:.0f} мин\nКаталог задания: {job_dir}"
        )
        if o["dry_run"]:
            return

        if o["fresh"]:
            _reset_job_dir(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        _setup_logging(job_dir)

        cfg = JobConfig(
            provider_name=provider.name, login=meta["login"], password=meta["password"],
            start=start, end=end, mode=o["mode"], fmt=o["fmt"], job_dir=job_dir,
            workers=o["workers"], start_workers=o["start_workers"], per_car=o["per_car"], car_group=o["car_group"],
            transform_workers=o["transform_workers"], window_days=o["window_days"],
            interval=o["interval"], max_attempts=o["max_attempts"], read_timeout=o["read_timeout"],
            progress_interval=o["progress_interval"], keep_raw=o["keep_raw"], retry_failed=o["retry_failed"],
        )
        try:
            summary = Engine(cfg, cars).run()
        except RuntimeError as e:
            raise CommandError(str(e))

        self.stdout.write(
            f"\nГотово за {summary['elapsed_s'] / 60:.1f} мин: машин с данными {summary['cars_done']}, "
            f"без данных {summary['cars_empty']}, незавершено {summary['cars_incomplete']} из {summary['cars_total']}\n"
            f"Запросов: {summary['stats']}\nРезультат: {cfg.out_dir}"
        )
        if summary["cars_incomplete"]:
            for p in summary["problems"][:20]:
                self.stdout.write(f"  - {p['name']} ({p['car']}): незавершённых окон {p['unfinished_count']}, пример: {p['unfinished_windows'][:2]}")
            raise CommandError("Есть незавершённые машины. Повторите команду с --retry-failed (состояние сохранено).")

    def _load_cars(self, provider: DataProvider, o) -> list[CarCtx]:
        if o["all_cars"]:
            cars = list(provider.cars.all())
        else:
            if o["car_ids_file"]:
                text = Path(o["car_ids_file"]).read_text(encoding="utf-8")
            else:
                text = o["car_ids"]
            ids = list(dict.fromkeys(m.lower() for m in UUID_RE.findall(text)))
            if not ids:
                raise CommandError("Не нашёл ни одного UUID машины во входных данных")
            found = {str(c.id): c for c in Car.objects.filter(id__in=ids, data_providers=provider).distinct()}
            missing = [i for i in ids if i not in found]
            if missing:
                self.stderr.write(f"ВНИМАНИЕ: {len(missing)} из {len(ids)} UUID не найдены у провайдера '{provider.name}': {missing[:10]}")
            cars = [found[i] for i in ids if i in found]

        ctxs = []
        for car in cars:
            if car.id_in_provider_system in (None, ""):
                self.stderr.write(f"Пропускаю {car.name} ({car.id}): нет id_in_provider_system")
                continue
            ctxs.append(CarCtx(
                uuid=str(car.id), vehicle_id=int(car.id_in_provider_system), name=car.name,
                car=car, sensors_mapping=load_sensors_mapping(car),
            ))
        if not ctxs:
            raise CommandError("Нет машин для обработки")
        return ctxs
