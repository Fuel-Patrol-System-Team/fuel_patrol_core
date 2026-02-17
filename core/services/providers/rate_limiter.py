import time
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GlobalRateLimiter:
    """
    Глобальный rate limiter для всех запросов к API провайдера
    Гарантирует не более 1 запроса в секунду для всего приложения
    """
    _instance: Optional['GlobalRateLimiter'] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):

        if not hasattr(self, '_initialized') or not self._initialized:
            self._lock = threading.Lock()
            self._last_request_time = 0.0
            self._min_interval = 1.55
            self._initialized = True
            logger.info("GlobalRateLimiter инициализирован")

    def wait_for_rate_limit(self):
        """
        Блокирует выполнение до тех пор, пока не пройдет достаточно времени
        с последнего запроса
        """
        with self._lock:
            current_time = time.time()
            time_since_last_request = current_time - self._last_request_time

            if time_since_last_request < self._min_interval:
                sleep_time = self._min_interval - time_since_last_request
                logger.debug(f"Глобальный rate limit: ожидание {sleep_time:.3f} сек")
                time.sleep(sleep_time)

            self._last_request_time = time.time()

global_rate_limiter = GlobalRateLimiter()