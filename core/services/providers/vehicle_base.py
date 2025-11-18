from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List


class BaseProvider(ABC):
    """Базовый класс для всех провайдеров данных"""

    def __init__(self, metadata: Dict[str, Any], report_query_id: str = None):
        self.metadata = metadata
        self.report_query_id = report_query_id
        self.auth_token = None

    @abstractmethod
    def authenticate(self) -> bool:
        """Аутентификация в API провайдера"""
        pass

    @abstractmethod
    def get_vehicles(self) -> Optional[List[Dict[str, Any]]]:
        """Получение списка транспортных средств"""
        pass

    @abstractmethod
    def get_vehicle_details(self, vehicle_id: int) -> Optional[Dict[str, Any]]:
        """Получение детальной информации о транспортном средстве"""
        pass