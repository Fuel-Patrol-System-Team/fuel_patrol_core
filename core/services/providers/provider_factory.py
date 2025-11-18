import logging
from typing import Dict, Any, Optional
from core.services.providers.glonass.glonasssoft_vehicles import GlonassSoftVehiclesProvider
from core.services.providers.glonass.glonasssoft_data_provider import GlonassSoftDataProvider

logger = logging.getLogger(__name__)


class ProviderFactory:
    @staticmethod
    def create_provider(metadata: Dict[str, Any], provider_type: str = "vehicles", car_id: str = None) -> Optional[Any]:
        """
        Создает экземпляр провайдера

        Args:
            metadata: Метаданные провайдера
            provider_type: Тип провайдера ('vehicles' или 'data')
            car_id: ID машины (только для provider_type='data')
        """
        provider_type_str = metadata.get("provider_type", "").lower()

        if provider_type_str == "glonasssoft":
            if provider_type == "vehicles":
                return GlonassSoftVehiclesProvider(metadata)
            elif provider_type == "data":
                if not car_id:
                    logger.error("car_id required for data provider")
                    return None
                return GlonassSoftDataProvider(metadata, car_id)

        logger.error(f"Неизвестный тип провайдера: '{provider_type_str}'")
        return None