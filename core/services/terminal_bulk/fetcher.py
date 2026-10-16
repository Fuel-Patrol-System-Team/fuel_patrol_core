import gzip
import logging
import threading
import time
from datetime import datetime

import orjson
import requests

from core.services.terminal_bulk.limiter import SpacingLimiter

logger = logging.getLogger("terminal_bulk")

BASE_URL = "https://hosting.glonasssoft.ru/api/v3"
MAX_PERIOD_DAYS = 93
API_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class AuthExpired(Exception):
    pass


class Throttled(Exception):
    def __init__(self, retry_after: float, detail: str = ""):
        super().__init__(f"429, retry_after={retry_after}, ответ: {detail}")
        self.retry_after = retry_after


class Retryable(Exception):
    pass


class BadRequest(Exception):
    pass


def api_time(dt: datetime) -> str:
    return dt.strftime(API_TIME_FORMAT)[:-3]


class TokenManager:
    def __init__(self, login: str, password: str, limiter: SpacingLimiter, ttl_seconds: int = 600):
        self._login = login
        self._password = password
        self._limiter = limiter
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._token: str | None = None
        self._issued = 0.0

    def _do_login(self) -> None:
        self._limiter.wait()
        r = requests.post(f"{BASE_URL}/auth/login", json={"login": self._login, "password": self._password}, timeout=(15, 60))
        r.raise_for_status()
        token = orjson.loads(r.content).get("AuthId")
        if not token:
            raise BadRequest("AuthId не найден в ответе /auth/login")
        self._token = token
        self._issued = time.monotonic()
        logger.info("Аутентификация успешна")

    def get(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() - self._issued > self._ttl:
                self._do_login()
            return self._token

    def refresh(self, bad_token: str) -> None:
        with self._lock:
            if self._token == bad_token or self._token is None:
                self._do_login()


_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1)
        s.mount("https://", adapter)
        _local.session = s
    return s


def request_window(
    tokens: TokenManager,
    limiter: SpacingLimiter,
    vehicle_id: int,
    start: datetime,
    end: datetime,
    read_timeout: int,
) -> bytes:
    token = tokens.get()
    limiter.wait()
    try:
        r = _session().post(
            f"{BASE_URL}/terminalMessages",
            json={"vehicleId": vehicle_id, "from": api_time(start), "to": api_time(end)},
            headers={"X-Auth": token, "Accept-Encoding": "gzip"},
            timeout=(15, read_timeout),
            stream=True,
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        raise Retryable(f"{type(e).__name__}: {str(e)[:150]}")

    try:
        code = r.status_code
        if code == 200:
            try:
                wire = r.raw.read(decode_content=False)
            except Exception as e:
                raise Retryable(f"обрыв тела ответа: {type(e).__name__}: {str(e)[:150]}")
            if r.headers.get("Content-Encoding", "").lower() != "gzip":
                wire = gzip.compress(wire, 3)
            return wire
        if code == 401:
            tokens.refresh(token)
            raise AuthExpired()
        if code == 429:
            try:
                retry_after = float(r.headers.get("Retry-After", ""))
            except ValueError:
                retry_after = 30.0
            hdrs = {k: v for k, v in r.headers.items() if k.lower().startswith(("retry", "x-rate", "ratelimit"))}
            raise Throttled(retry_after, f"{r.text[:200]!r} {hdrs}")
        body = r.text[:300]
        if code >= 500:
            raise Retryable(f"HTTP {code}: {body}")
        raise BadRequest(f"HTTP {code}: {body}")
    finally:
        r.close()
