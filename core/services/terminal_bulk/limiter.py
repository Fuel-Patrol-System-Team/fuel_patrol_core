import threading
import time


class SpacingLimiter:
    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._pause_until = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot, self._pause_until)
            self._next_slot = slot + self.interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._pause_until = max(self._pause_until, time.monotonic() + seconds)


class ConcurrencyGate:
    def __init__(self, max_limit: int, min_limit: int = 1, shrink_cooldown: float = 20.0, start_limit: int | None = None):
        self.max_limit = max_limit
        self.min_limit = min_limit
        self.limit = min(start_limit or max_limit, max_limit)
        self.active = 0
        self._streak = 0
        self._last_shrink = 0.0
        self._shrink_cooldown = shrink_cooldown
        self._cond = threading.Condition()

    def acquire(self, stop: threading.Event) -> bool:
        with self._cond:
            while self.active >= self.limit:
                if stop.is_set():
                    return False
                self._cond.wait(1.0)
            if stop.is_set():
                return False
            self.active += 1
            return True

    def release(self) -> None:
        with self._cond:
            self.active -= 1
            self._cond.notify_all()

    def on_success(self) -> None:
        with self._cond:
            self._streak += 1
            if self._streak >= self.limit and self.limit < self.max_limit:
                self.limit += 1
                self._streak = 0
                self._cond.notify_all()

    def on_pressure(self) -> int:
        with self._cond:
            now = time.monotonic()
            if now - self._last_shrink >= self._shrink_cooldown:
                self.limit = max(self.min_limit, self.limit // 2)
                self._last_shrink = now
            self._streak = 0
            return self.limit
