"""Process-wide storage for GlonassSoft X-Auth tokens.

Tokens are keyed by login (the value from ``DataProvider.metadata["login"]``),
mirroring how GlonassSoft issues ``AuthId`` per account. The store is a
thread-safe singleton analogue of :class:`GlobalRateLimiter` so that every
``GlonassGeneralProvider`` instance running in the same worker process can
reuse a cached token instead of re-authenticating.

Usage::

    from core.services.providers.glonass.auth_token_store import glonass_auth_token_store

    token = glonass_auth_token_store.get_token("user@example.com")
    if token is None:
        # ...perform login...
        glonass_auth_token_store.set_token("user@example.com", auth_id)
"""

import threading
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Считаем токен валидным 15 минут (как в оригинальном authenticate()).
DEFAULT_TOKEN_TTL_SECONDS = 60 * 15


class GlonassAuthTokenStore:
    """Thread-safe singleton кэш X-Auth токенов по логину."""

    _instance: Optional["GlonassAuthTokenStore"] = None
    _creation_lock = threading.Lock()

    def __new__(cls) -> "GlonassAuthTokenStore":
        with cls._creation_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.Lock()
        # login -> (token, issued_at)
        self._tokens: dict[str, tuple[str, datetime]] = {}
        self._ttl = timedelta(seconds=DEFAULT_TOKEN_TTL_SECONDS)
        self._initialized = True
        logger.info("GlonassAuthTokenStore инициализирован")

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def set_ttl_seconds(self, seconds: int) -> None:
        """Переопределить TTL (полезно для тестов)."""
        self._ttl = timedelta(seconds=seconds)

    def get_token(self, login: str, ttl_seconds: Optional[int] = None) -> Optional[str]:
        """Возвращает валидный токен или ``None`` (и вытесняет устаревший)."""
        if not login:
            return None
        with self._lock:
            entry = self._tokens.get(login)
            if entry is None:
                return None
            token, issued_at = entry
            ttl = timedelta(seconds=ttl_seconds) if ttl_seconds is not None else self._ttl
            age = datetime.now() - issued_at
            if age >= ttl:
                # Токен протух — выкидываем, чтобы следующий authenticate() перевыдал его.
                del self._tokens[login]
                logger.debug(f"Токен для {login} устарел (возраст {age}), удалён из кэша")
                return None
            logger.debug(f"Найден активный токен для {login} (возраст {age})")
            return token

    def set_token(self, login: str, token: str) -> None:
        if not login or not token:
            return
        with self._lock:
            self._tokens[login] = (token, datetime.now())
            logger.debug(f"Сохранён токен для {login}")

    def invalidate(self, login: str) -> None:
        """Удаляет токен (например, при 401 от API)."""
        if not login:
            return
        with self._lock:
            if login in self._tokens:
                del self._tokens[login]
                logger.info(f"Токен для {login} инвалидирован")

    def clear(self) -> None:
        """Очищает всё хранилище (для тестов)."""
        with self._lock:
            self._tokens.clear()


glonass_auth_token_store = GlonassAuthTokenStore()