import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import polars as pl
from datetime import datetime

from core.services.providers.rate_limiter import global_rate_limiter

class BaseDataProvider(ABC):
    """Базовый класс для провайдеров данных (terminalMessages)"""

    def __init__(self, metadata: Dict[str, Any], car_id: str = None):
        self.metadata = metadata
        self.car_id = car_id
        self.auth_token = None

    def _enforce_rate_limit(self) -> None:
        """Применяет глобальный rate limit"""
        if global_rate_limiter is None:
            time.sleep(1.05)
            return

        global_rate_limiter.wait_for_rate_limit()

    @abstractmethod
    def authenticate(self) -> bool:
        """Аутентификация в API провайдера"""
        pass

    @abstractmethod
    def get_car_data(
            self,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None
    ) -> Optional[pl.DataFrame]:
        """Получает данные terminalMessages для конкретной машины"""
        pass