import logging
from datetime import datetime

import polars as pl
from typing import Optional, Tuple
from pathlib import Path
from django.conf import settings

logger = logging.getLogger(__name__)


class CarDataService:
    """Сервис для обработки данных одной машины"""

    @staticmethod
    def calculate_primary_single(df: pl.DataFrame, auto_df: pl.DataFrame) -> Optional[pl.DataFrame]:
        """
        Вычисляет первичные показатели для одной машины
        Аналог функции calculate_primary из примера
        """
        try:

            df = df.with_columns(
                pl.when(pl.col("calc_sensors_fuel_level") > 4096)
                .then(None)
                .otherwise(pl.col("calc_sensors_fuel_level"))
                .alias("calc_sensors_fuel_level")
            )

            if "rpm" not in df.columns:
                df = df.with_columns(pl.lit(65535).alias("rpm"))

            df = df.drop_nulls("calc_sensors_fuel_level")

            if df.is_empty():
                logger.warning("Нет данных после удаления NaN")
                return None

            result = df.group_by("auto").agg([
                pl.col("calc_sensors_fuel_level").max().alias("max_fuel"),
                pl.col("rpm").max().alias("rpm_max"),
                pl.col("rpm").std().alias("rpm_std"),
                pl.col("ign").max().alias("ign_functional"),
            ])

            result = result.with_columns(
                pl.when(pl.col("max_fuel").is_between(100, 120))
                .then(99.9)
                .otherwise(pl.col("max_fuel"))
                .alias("max_fuel"),
                pl.col("rpm_max").fill_null(0),
            )

            reason = None

            speed = CarDataService._calculate_primary_speed(df)
            if speed.is_empty() and reason is None:
                reason = "no speed record"

            special = CarDataService._calculate_primary_is_special(df)
            if special.is_empty() and reason is None:
                reason = "no special record"

            if result.is_empty() and reason is None:
                reason = "the result is 0"

            if not speed.is_empty():
                result = result.join(speed, on="auto", how="inner")
            if not special.is_empty():
                result = result.join(special, on="auto", how="inner")

            if reason:
                logger.warning(f"Обработка завершена с предупреждением: {reason}")

            return result

        except Exception as e:
            logger.error(f"Ошибка в calculate_primary_single: {e}")
            return None

    @staticmethod
    def _calculate_primary_speed(df: pl.DataFrame) -> pl.DataFrame:
        """Вычисляет показатели скорости"""
        try:
            speed_stats = df.filter(pl.col("pos_s").is_not_null()).group_by("auto").agg([
                pl.col("pos_s").mean().alias("speed_mean"),
                pl.col("pos_s").std().alias("speed_std"),
                pl.col("pos_s").max().alias("speed_max")
            ])
            return speed_stats
        except Exception as e:
            logger.error(f"Ошибка вычисления скорости: {e}")
            return pl.DataFrame()

    @staticmethod
    def _calculate_primary_is_special(df: pl.DataFrame) -> pl.DataFrame:
        """Вычисляет специальные показатели"""
        try:

            special_stats = df.group_by("auto").agg([
                pl.col("amtr").mean().alias("amtr_mean"),
                pl.col("engine_temp").mean().alias("engine_temp_mean")
            ])
            return special_stats
        except Exception as e:
            logger.error(f"Ошибка вычисления специальных показателей: {e}")
            return pl.DataFrame()

    @staticmethod
    def save_result_to_csv(result_df: pl.DataFrame, car_id: str, prefix: str = "primary") -> str:
        """Сохраняет результат в CSV файл"""
        try:

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{car_id}_{timestamp}.csv"
            filepath = Path(settings.MEDIA_ROOT) / "processed_data" / filename

            filepath.parent.mkdir(parents=True, exist_ok=True)

            result_df.write_csv(filepath)
            logger.info(f"Результат сохранен в {filepath}")

            return str(filepath)

        except Exception as e:
            logger.error(f"Ошибка сохранения CSV: {e}")
        raise

    @staticmethod
    def prepare_auto_data(car) -> pl.DataFrame:
        """Подготавливает auto DataFrame для расчета норм"""
        try:
            auto_data = [{
                "id": str(car.id),
                "auto": str(car.id),
                "input": float(car.input) if car.input else 1.0,
                "output": float(car.output) if car.output else 1.0,
                "name": car.name,
                "engine_type": float(car.engine_type) if car.engine_type else 0.0,
                "is_tarrified": car.is_tarrified
            }]

            auto_df = pl.DataFrame(auto_data).with_columns([
                pl.col("id").cast(pl.Categorical),
                pl.col("auto").cast(pl.Categorical),
                pl.col("input").cast(pl.Float32),
                pl.col("output").cast(pl.Float32)
            ])

            return auto_df

        except Exception as e:
            logger.error(f"Ошибка подготовки auto данных: {e}")
            raise
