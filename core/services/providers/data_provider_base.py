from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import polars as pl
from datetime import datetime


class BaseDataProvider(ABC):
    """Базовый класс для провайдеров данных (terminalMessages)"""

    def __init__(self, metadata: Dict[str, Any], car_id: str = None):
        self.metadata = metadata
        self.car_id = car_id
        self.auth_token = None

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