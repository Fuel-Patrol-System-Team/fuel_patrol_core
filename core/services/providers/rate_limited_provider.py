import logging
from typing import Dict, Any, Optional
from core.services.providers.data_provider_base import BaseDataProvider
from core.services.providers.rate_limiter import global_rate_limiter
from core.services.providers.vehicle_base import BaseProvider

logger = logging.getLogger(__name__)


class RateLimitedProvider(BaseDataProvider):
    """Базовый класс для провайдеров с глобальным rate limiting"""

    def _enforce_rate_limit(self) -> None:
        """Применяет глобальный rate limit (1 запрос в секунду)"""
        global_rate_limiter.wait_for_rate_limit()

class VehicleRateLimitedProvider(BaseProvider):
    """Базовый класс для vehicle провайдеров с глобальным rate limiting"""

    def _enforce_rate_limit(self) -> None:
        """Применяет глобальный rate limit (1 запрос в секунду)"""
        global_rate_limiter.wait_for_rate_limit()