import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Iterable

PENDING = "pending"
FETCHED = "fetched"
MAPPED = "mapped"
FAILED = "failed"
SPLIT = "split"

CAR_DONE = "done"
CAR_EMPTY = "empty"


@dataclass
class Window:
    car: str
    start: str
    end: str
    status: str = PENDING
    attempts: int = 0
    rows: int = 0
    wire_bytes: int = 0
    error: str = ""


class Manifest:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS windows ("
            "car TEXT, start TEXT, end TEXT, status TEXT, attempts INT DEFAULT 0, "
            "rows INT DEFAULT 0, wire_bytes INT DEFAULT 0, error TEXT DEFAULT '', updated REAL, "
            "PRIMARY KEY (car, start, end))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cars (car TEXT PRIMARY KEY, status TEXT, rows INT DEFAULT 0, "
            "out_path TEXT DEFAULT '', error TEXT DEFAULT '', updated REAL)"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self._db.commit()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self._db.commit()

    def add_windows(self, windows: Iterable[tuple[str, str, str]]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR IGNORE INTO windows (car, start, end, status, updated) VALUES (?, ?, ?, ?, ?)",
                [(c, s, e, PENDING, time.time()) for c, s, e in windows],
            )
            self._db.commit()

    def update(self, car: str, start: str, end: str, **fields) -> None:
        if not fields:
            return
        fields["updated"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(
                f"UPDATE windows SET {sets} WHERE car = ? AND start = ? AND end = ?",
                [*fields.values(), car, start, end],
            )
            self._db.commit()

    def bump_attempts(self, car: str, start: str, end: str) -> int:
        with self._lock:
            self._db.execute(
                "UPDATE windows SET attempts = attempts + 1, updated = ? WHERE car = ? AND start = ? AND end = ?",
                (time.time(), car, start, end),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT attempts FROM windows WHERE car = ? AND start = ? AND end = ?", (car, start, end)
            ).fetchone()
            return row[0] if row else 0

    def split(self, car: str, start: str, end: str, children: list[tuple[str, str]]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE windows SET status = ?, updated = ? WHERE car = ? AND start = ? AND end = ?",
                (SPLIT, time.time(), car, start, end),
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO windows (car, start, end, status, updated) VALUES (?, ?, ?, ?, ?)",
                [(car, s, e, PENDING, time.time()) for s, e in children],
            )
            self._db.commit()

    def windows(self, statuses: Iterable[str] | None = None, car: str | None = None) -> list[Window]:
        query = "SELECT car, start, end, status, attempts, rows, wire_bytes, error FROM windows"
        where, args = [], []
        if statuses is not None:
            statuses = list(statuses)
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args += statuses
        if car is not None:
            where.append("car = ?")
            args.append(car)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY car, start"
        with self._lock:
            return [Window(*r) for r in self._db.execute(query, args).fetchall()]

    def attempts(self, car: str, start: str, end: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT attempts FROM windows WHERE car = ? AND start = ? AND end = ?", (car, start, end)
            ).fetchone()
            return row[0] if row else 0

    def leaf_windows(self, car: str) -> list[Window]:
        return [w for w in self.windows(car=car) if w.status != SPLIT]

    def reset_failed(self) -> int:
        with self._lock:
            cur = self._db.execute(
                "UPDATE windows SET status = ?, attempts = 0, error = '', updated = ? WHERE status = ?",
                (PENDING, time.time(), FAILED),
            )
            self._db.commit()
            return cur.rowcount

    def set_car(self, car: str, status: str, rows: int = 0, out_path: str = "", error: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cars (car, status, rows, out_path, error, updated) VALUES (?, ?, ?, ?, ?, ?)",
                (car, status, rows, out_path, error, time.time()),
            )
            self._db.commit()

    def car_status(self, car: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT status FROM cars WHERE car = ?", (car,)).fetchone()
            return row[0] if row else None

    def cars(self) -> dict[str, tuple[str, int, str]]:
        with self._lock:
            return {r[0]: (r[1], r[2], r[3]) for r in self._db.execute("SELECT car, status, rows, error FROM cars")}

    def close(self) -> None:
        with self._lock:
            self._db.close()
