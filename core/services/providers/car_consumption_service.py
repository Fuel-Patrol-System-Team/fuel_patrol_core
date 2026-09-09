from datetime import datetime, timedelta
import logging
from django.utils import timezone
from django.db import transaction
import polars as pl
from core.models import CarConsumption, Car

logger = logging.getLogger(__name__)


class CarConsumptionService:
    """Сервис для работы с нормами расхода топлива"""

    @staticmethod
    @transaction.atomic
    def save_consumption_rates(norms_df: pl.DataFrame, car: Car) -> CarConsumption:
        """
        Сохраняет расчетные нормы расхода в модель CarConsumption
        """
        try:
            car_norms = norms_df.filter(
                pl.col("sl_avto") == str(car.id)
            )

            if car_norms.is_empty():
                raise ValueError(f"Нет данных норм для машины {car.id}")

            norm_data = car_norms.to_dicts()[0]
            for k, v in norm_data.items():
                if isinstance(v, datetime):
                    norm_data[k] = v.isoformat()


            consumption, created = CarConsumption.objects.update_or_create(
                car_id=car,
                defaults={
                    'winter_volume': norm_data.get('norma_rasx_winter'),
                    'summer_volume': norm_data.get('norma_rasx_summer'),
                    'speed_etalon': norm_data.get('speed_etalon', 60.0),
                    'max_fuel': norm_data.get('max_fuel', 2000.0),
                    'valid_period': timezone.now().date() + timedelta(days=365),
                    'json_data': norm_data,
                    'metadata': None
                }
            )

            action = "созданы" if created else "обновлены"
            logger.info(f"Нормы расхода {action} для машины {car.id}")

            return consumption

        except Exception as e:
            logger.error(f"Ошибка сохранения норм расхода для машины {car.id}: {e}")
            raise