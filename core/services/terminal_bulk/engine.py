import json
import logging
import os
import queue
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from core.services.terminal_bulk.config import CarCtx, JobConfig
from core.services.terminal_bulk.fetcher import (
    AuthExpired,
    BadRequest,
    MAX_PERIOD_DAYS,
    Retryable,
    Throttled,
    TokenManager,
    request_window,
)
from core.services.terminal_bulk.limiter import ConcurrencyGate, SpacingLimiter
from core.services.terminal_bulk.manifest import (
    CAR_DONE,
    CAR_EMPTY,
    FAILED,
    FETCHED,
    MAPPED,
    Manifest,
    PENDING,
    SPLIT,
    Window,
)
from core.services.terminal_bulk.transform import (
    cleanup_car,
    finalize_car,
    map_window,
    part_path,
    raw_path,
    write_atomic,
)

logger = logging.getLogger("terminal_bulk")

MAX_THROTTLE_RETRIES = 50
MAX_AUTH_RETRIES = 5


class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.requests = 0
        self.ok = 0
        self.throttled = 0
        self.errors = 0
        self.splits = 0
        self.wire_bytes = 0

    def add(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)

    def snapshot(self) -> dict:
        with self._lock:
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


def plan_windows(start: datetime, end: datetime, window_days: int) -> list[tuple[str, str]]:
    if not 1 <= window_days <= MAX_PERIOD_DAYS:
        raise ValueError(f"window_days должен быть от 1 до {MAX_PERIOD_DAYS} (ограничение API)")
    result = []
    cur = start
    while cur < end:
        win_end = min(cur + timedelta(days=window_days), end)
        result.append((cur.isoformat(), win_end.isoformat()))
        cur = win_end + timedelta(seconds=1)
    return result


def _span_days(w: Window) -> float:
    return (datetime.fromisoformat(w.end) - datetime.fromisoformat(w.start)).total_seconds() / 86400.0


class Engine:
    def __init__(self, cfg: JobConfig, cars: list[CarCtx]):
        self.cfg = cfg
        self.cars = cars
        self.by_uuid = {c.uuid: c for c in cars}
        cfg.job_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = Manifest(cfg.job_dir / "manifest.sqlite")
        self.limiter = SpacingLimiter(cfg.interval)
        self.gate = ConcurrencyGate(cfg.workers, start_limit=cfg.start_workers)
        self._car_busy: dict[str, int] = {}
        self._car_busy_lock = threading.Lock()
        self.tokens = TokenManager(cfg.login, cfg.password, self.limiter)
        self.stats = Stats()
        self.stop = threading.Event()
        self._all_done = threading.Event()
        self.q: queue.Queue[Window] = queue.Queue()
        self.tpool = ThreadPoolExecutor(max_workers=cfg.transform_workers, thread_name_prefix="map")
        self._car_locks = {c.uuid: threading.Lock() for c in cars}
        self._started = time.time()
        self._base_done_days = 0.0

    def _prepare(self) -> None:
        signature = f"{self.cfg.start.isoformat()}|{self.cfg.end.isoformat()}|{self.cfg.mode}|{self.cfg.fmt}|{self.cfg.window_days}"
        stored = self.manifest.get_meta("signature")
        if stored is not None and stored != signature:
            raise RuntimeError(
                f"Каталог задания уже содержит состояние с другими параметрами ({stored}); "
                f"текущие: {signature}. Запустите с --fresh или укажите другой --job-dir."
            )
        self.manifest.set_meta("signature", signature)
        plan = plan_windows(self.cfg.start, self.cfg.end, self.cfg.window_days)
        self.manifest.add_windows([(c.uuid, s, e) for c in self.cars for s, e in plan])
        if self.cfg.retry_failed:
            n = self.manifest.reset_failed()
            if n:
                logger.info(f"Сброшено проваленных окон для повтора: {n}")

        cars_state = self.manifest.cars()
        for w in self.manifest.windows():
            if w.status == SPLIT or cars_state.get(w.car, ("",))[0] in (CAR_DONE, CAR_EMPTY):
                continue
            ctx = self.by_uuid.get(w.car)
            if ctx is None:
                continue
            raw_exists = raw_path(self.cfg, ctx, w.start, w.end).exists()
            if w.status == PENDING and raw_exists:
                self.manifest.update(w.car, w.start, w.end, status=FETCHED)
            elif w.status == FETCHED and not raw_exists:
                self.manifest.update(w.car, w.start, w.end, status=PENDING)
            elif w.status == MAPPED and w.rows > 0 and not part_path(self.cfg, ctx, w.start, w.end).exists():
                self.manifest.update(w.car, w.start, w.end, status=FETCHED if raw_exists else PENDING)

    def _day_totals(self) -> tuple[float, float]:
        total = done = 0.0
        for w in self.manifest.windows():
            if w.status == SPLIT:
                continue
            d = _span_days(w)
            total += d
            if w.status in (FETCHED, MAPPED):
                done += d
        return total, done

    def _fetch(self, w: Window) -> None:
        ctx = self.by_uuid[w.car]
        start = datetime.fromisoformat(w.start)
        end = datetime.fromisoformat(w.end)
        throttled = auth_retries = 0
        label = f"{ctx.name} [{ctx.vehicle_id}] {start.date()}..{end.date()}"

        while not self.stop.is_set():
            attempts = self.manifest.attempts(w.car, w.start, w.end)
            if attempts >= self.cfg.max_attempts:
                self._fail(w, f"исчерпаны попытки ({attempts})")
                return
            if not self.gate.acquire(self.stop):
                return

            outcome, error, wire, retry_after = "ok", "", None, 30.0
            t0 = time.time()
            self.stats.add(requests=1)
            try:
                wire = request_window(self.tokens, self.limiter, ctx.vehicle_id, start, end, self.cfg.read_timeout)
            except AuthExpired:
                outcome = "auth"
            except Throttled as e:
                outcome, error, retry_after = "throttled", str(e), e.retry_after
            except Retryable as e:
                outcome, error = "retry", str(e)
            except BadRequest as e:
                outcome, error = "bad", str(e)
            except Exception as e:
                outcome, error = "retry", f"{type(e).__name__}: {str(e)[:200]}"
            finally:
                self.gate.release()

            if outcome == "ok":
                write_atomic(raw_path(self.cfg, ctx, w.start, w.end), wire)
                self.manifest.update(w.car, w.start, w.end, status=FETCHED, wire_bytes=len(wire), error="")
                self.stats.add(ok=1, wire_bytes=len(wire))
                self.gate.on_success()
                logger.info(f"OK {label}: {len(wire) / 1e6:.2f} МБ за {time.time() - t0:.0f} с")
                fetched = Window(w.car, w.start, w.end, FETCHED, attempts, 0, len(wire))
                self.tpool.submit(self._map, fetched)
                return

            if outcome == "auth":
                auth_retries += 1
                if auth_retries > MAX_AUTH_RETRIES:
                    self._fail(w, "повторные 401 после переавторизации")
                    return
                continue

            if outcome == "throttled":
                throttled += 1
                self.stats.add(throttled=1)
                limit = self.gate.on_pressure()
                delay = min(retry_after, 300.0)
                self.limiter.pause(delay)
                logger.warning(f"429 для {label}; {error}; пауза {delay:.0f} с, лимит параллельности -> {limit}")
                if throttled > MAX_THROTTLE_RETRIES:
                    self._fail(w, "слишком много 429 подряд")
                    return
                self.stop.wait(delay + random.uniform(0, 3))
                continue

            attempts = self.manifest.bump_attempts(w.car, w.start, w.end)
            self.stats.add(errors=1)
            logger.warning(f"Ошибка {label} (попытка {attempts}/{self.cfg.max_attempts}): {error}")
            if outcome == "bad":
                if attempts >= 2:
                    self._fail(w, error)
                    return
                self.stop.wait(2)
                continue

            limit = self.gate.on_pressure()
            if attempts >= 2 and (end - start) >= timedelta(days=2):
                self._split(w, start, end)
                return
            backoff = min(5 * 2 ** (attempts - 1), 120) + random.uniform(0, 3)
            logger.info(f"Повтор {label} через {backoff:.0f} с, лимит параллельности {limit}")
            self.stop.wait(backoff)

    def _split(self, w: Window, start: datetime, end: datetime) -> None:
        mid = (start + (end - start) / 2).replace(microsecond=0)
        children = [(start.isoformat(), mid.isoformat()), ((mid + timedelta(seconds=1)).isoformat(), end.isoformat())]
        self.manifest.split(w.car, w.start, w.end, children)
        self.stats.add(splits=1)
        logger.warning(f"Окно {w.start}..{w.end} разбито пополам: {children}")
        for s, e in children:
            self.q.put(Window(w.car, s, e))

    def _fail(self, w: Window, error: str) -> None:
        self.manifest.update(w.car, w.start, w.end, status=FAILED, error=error[:300])
        logger.error(f"ПРОВАЛ окна {w.car} {w.start}..{w.end}: {error}")

    def _map(self, w: Window) -> None:
        ctx = self.by_uuid[w.car]
        try:
            rows = map_window(self.cfg, ctx, w.start, w.end)
            self.manifest.update(w.car, w.start, w.end, status=MAPPED, rows=rows, error="")
        except Exception as e:
            logger.exception(f"Ошибка преобразования окна {ctx.name} {w.start}..{w.end}")
            self.manifest.update(w.car, w.start, w.end, status=FAILED, error=f"map: {type(e).__name__}: {str(e)[:250]}")
        self._maybe_finalize(w.car)

    def _maybe_finalize(self, car: str) -> None:
        ctx = self.by_uuid[car]
        with self._car_locks[car]:
            if self.manifest.car_status(car) in (CAR_DONE, CAR_EMPTY):
                return
            leaf = self.manifest.leaf_windows(car)
            if not leaf or any(x.status != MAPPED for x in leaf):
                return
            try:
                rows, out = finalize_car(self.cfg, ctx, leaf)
                self.manifest.set_car(car, CAR_DONE if rows else CAR_EMPTY, rows=rows, out_path=out)
                cleanup_car(self.cfg, ctx, leaf)
                logger.info(f"МАШИНА ГОТОВА {ctx.name} [{ctx.vehicle_id}]: {rows} строк -> {out or 'данных нет'}")
            except Exception as e:
                logger.exception(f"Ошибка сборки итогового файла {ctx.name}")
                self.manifest.set_car(car, "error", error=f"{type(e).__name__}: {str(e)[:250]}")

    def _worker(self) -> None:
        while True:
            try:
                w = self.q.get(timeout=1)
            except queue.Empty:
                if self._all_done.is_set():
                    return
                continue
            requeued = False
            try:
                if not self.stop.is_set():
                    if self._take_car(w.car):
                        try:
                            self._fetch(w)
                        finally:
                            self._release_car(w.car)
                    else:
                        self.q.put(w)
                        requeued = True
            except Exception:
                logger.exception(f"Необработанная ошибка в воркере для окна {w.car} {w.start}..{w.end}")
                self.manifest.update(w.car, w.start, w.end, status=FAILED, error="необработанная ошибка воркера")
            finally:
                self.q.task_done()
            if requeued:
                time.sleep(0.2)

    def _take_car(self, car: str) -> bool:
        with self._car_busy_lock:
            if self._car_busy.get(car, 0) >= self.cfg.per_car:
                return False
            self._car_busy[car] = self._car_busy.get(car, 0) + 1
            return True

    def _release_car(self, car: str) -> None:
        with self._car_busy_lock:
            self._car_busy[car] -= 1

    def _progress_line(self) -> dict:
        total, done = self._day_totals()
        elapsed = time.time() - self._started
        gained = done - self._base_done_days
        eta = None
        if gained > 0 and total > done:
            eta = (total - done) / (gained / elapsed)
        windows = [w for w in self.manifest.windows() if w.status != SPLIT]
        by_status: dict[str, int] = {}
        for w in windows:
            by_status[w.status] = by_status.get(w.status, 0) + 1
        cars = self.manifest.cars()
        snap = self.stats.snapshot()
        info = {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_s": round(elapsed),
            "eta_s": round(eta) if eta is not None else None,
            "percent": round(done / total * 100, 1) if total else 100.0,
            "windows": by_status,
            "cars_done": sum(1 for s in cars.values() if s[0] in (CAR_DONE, CAR_EMPTY)),
            "cars_total": len(self.cars),
            "active_requests": self.gate.active,
            "concurrency_limit": self.gate.limit,
            "wire_mb": round(snap["wire_bytes"] / 1e6, 1),
            **{k: snap[k] for k in ("requests", "ok", "throttled", "errors", "splits")},
        }
        return info

    def _reporter(self) -> None:
        path = self.cfg.job_dir / "progress.json"
        while not self._all_done.wait(self.cfg.progress_interval):
            self._write_progress(path)

    def _write_progress(self, path) -> None:
        try:
            info = self._progress_line()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            eta = f"{info['eta_s'] / 3600:.1f} ч" if info["eta_s"] is not None else "?"
            logger.info(
                f"ПРОГРЕСС {info['percent']}% | машин {info['cars_done']}/{info['cars_total']} | окна {info['windows']} | "
                f"в полёте {info['active_requests']}/{info['concurrency_limit']} | 429: {info['throttled']}, "
                f"ошибок: {info['errors']}, сетевых МБ: {info['wire_mb']} | осталось ≈ {eta}"
            )
        except Exception:
            logger.exception("Не удалось записать прогресс")

    def run(self) -> dict:
        self._prepare()
        total, done = self._day_totals()
        self._base_done_days = done
        logger.info(
            f"Старт: машин {len(self.cars)}, период {self.cfg.start.date()}..{self.cfg.end.date()}, режим {self.cfg.mode}, "
            f"формат {self.cfg.fmt}, потоков {self.cfg.workers}, окно {self.cfg.window_days} дн., интервал {self.cfg.interval} с, "
            f"готово заранее {done / total * 100 if total else 100:.1f}%"
        )

        cars_state = self.manifest.cars()
        active = [c for c in self.cars if cars_state.get(c.uuid, ("",))[0] not in (CAR_DONE, CAR_EMPTY)]
        for c in active:
            for w in self.manifest.leaf_windows(c.uuid):
                if w.status == FETCHED:
                    self.tpool.submit(self._map, w)
            self.tpool.submit(self._maybe_finalize, c.uuid)

        group = max(1, self.cfg.car_group)
        for i in range(0, len(active), group):
            pend = {c.uuid: [w for w in self.manifest.leaf_windows(c.uuid) if w.status == PENDING] for c in active[i:i + group]}
            for n in range(max((len(v) for v in pend.values()), default=0)):
                for c in active[i:i + group]:
                    if n < len(pend[c.uuid]):
                        self.q.put(pend[c.uuid][n])

        threads = [threading.Thread(target=self._worker, name=f"fetch-{i}", daemon=True) for i in range(self.cfg.workers)]
        for t in threads:
            t.start()
        reporter = threading.Thread(target=self._reporter, name="reporter", daemon=True)
        reporter.start()

        try:
            while self.q.unfinished_tasks:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.warning("Остановка по Ctrl+C: дожидаюсь текущих запросов, состояние сохранено, запуск той же команды продолжит")
            self.stop.set()
        self._all_done.set()
        for t in threads:
            t.join()
        self.tpool.shutdown(wait=True)
        self._write_progress(self.cfg.job_dir / "progress.json")
        return self._summary()

    def _summary(self) -> dict:
        cars = self.manifest.cars()
        problems = []
        for c in self.cars:
            st = cars.get(c.uuid, (None,))[0]
            if st in (CAR_DONE, CAR_EMPTY):
                continue
            bad = [w for w in self.manifest.leaf_windows(c.uuid) if w.status != MAPPED]
            problems.append({
                "car": c.uuid, "name": c.name, "car_status": st,
                "unfinished_windows": [(w.start, w.end, w.status, w.error) for w in bad][:5],
                "unfinished_count": len(bad),
            })
        self.manifest.close()
        return {
            "cars_total": len(self.cars),
            "cars_done": sum(1 for c in self.cars if cars.get(c.uuid, (None,))[0] == CAR_DONE),
            "cars_empty": sum(1 for c in self.cars if cars.get(c.uuid, (None,))[0] == CAR_EMPTY),
            "cars_incomplete": len(problems),
            "problems": problems,
            "stats": self.stats.snapshot(),
            "elapsed_s": round(time.time() - self._started),
        }
