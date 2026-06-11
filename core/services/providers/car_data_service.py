import logging
from datetime import datetime

import polars as pl
from typing import Optional
from pathlib import Path
from django.conf import settings

from app.tasks import SensorsValues
from core.models import CarPrimary, Car

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
            tmp = df.filter(pl.col("pos_s") > 0)
            return tmp.group_by("auto").agg(
                [
                    pl.col("pos_s").quantile(0.50).alias("norm_speed"),
                    pl.col("pos_s").quantile(0.75).alias("norm_speed_2"),
                ]
            )
        except Exception as e:
            logger.error(f"Ошибка вычисления скорости: {e}")
            return pl.DataFrame()

    @staticmethod
    def _calculate_primary_is_special(df: pl.DataFrame):
        col_dtime_period = pl.col("timestamp").dt.truncate("2h")

        df_test = df.with_columns(
            pl.col("calc_sensors_fuel_level")
            .diff()
            .over(["auto", col_dtime_period])
            .fill_null(0)
            .cast(pl.Float32)
            .alias("spent_fuel"),
        )
        df_test = df_test.filter([pl.col("spent_fuel").gt(0)])

        df_test = df_test.with_columns(
            [pl.when(pl.col("pos_s").gt(0)).then(1).otherwise(0).alias("movable")]
        )

        cars_active = df_test.group_by("auto").agg(
            [
                (pl.col("ign").max() > 0).alias("ign_working"),
                (pl.col("movable").sum() / pl.col("movable").count()).alias("sp_factor"),
            ]
        )
        cars_active = cars_active.with_columns(
            [
                (pl.col("sp_factor").lt(0.75)).alias("is_special_car"),
            ]
        )
        cars_active = cars_active.select(
            ["auto", "ign_working", "sp_factor", "is_special_car"]
        )
        return cars_active

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
    def prepare_auto_data(car, return_dict = False) -> pl.DataFrame:
        """Подготавливает auto DataFrame для расчета норм"""
        try:
            fuel_sensor = SensorsValues.objects.filter(car_id__id=car.id, key__key="calc_sensors_fuel_level", is_active=True).first()
            fuel_sensors_amount= SensorsValues.objects.filter(car_id__id=car.id, key__key="calc_sensors_fuel_level", is_active=True).count()
            mileage_sensor = SensorsValues.objects.filter(car_id__id=car.id, key__key="mileage", is_active=True).first()
            grades = None
            if fuel_sensor and fuel_sensor.metadata is not None:
                grades = fuel_sensor.metadata.get("grades")
                
            auto_data = [{
                "id": str(car.id),
                "auto": str(car.id),
                "input": float(car.input) if car.input else 1.0,
                "output": float(car.output) if car.output else 1.0,
                "grades": grades,
                "name": car.name,
                "engine_type": float(car.engine_type) if car.engine_type else 0.0,
                "is_tarrified": car.is_tarrified,
                "fuel_sensor": fuel_sensor if fuel_sensor is None else fuel_sensor.value,
                "fuel_sensors_amount": fuel_sensors_amount,
                "mileage_grading": mileage_sensor.grades if mileage_sensor else None,
            }]
            if return_dict:
                return auto_data

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


    @staticmethod
    def save_primary_to_db(car: Car, primary_df: pl.DataFrame, start_date: datetime = None,
                           end_date: datetime = None) -> bool:
        """
        Сохраняет первичные показатели в модель CarPrimary
        """
        try:
            if primary_df is None or primary_df.is_empty():
                logger.warning(f"Нет данных primary для сохранения для машины {car.id}")
                return False

            primary_data = []
            for row in primary_df.to_dicts():
                row_with_meta = {
                    **row,
                    "period_start": start_date.isoformat() if start_date else None,
                    "period_end": end_date.isoformat() if end_date else None,
                }
                primary_data.append(row_with_meta)

            car_primary, created = CarPrimary.objects.update_or_create(
                car=car,
                defaults={
                    'primary': primary_data,
                }
            )

            if created:
                logger.info(f"Создана новая запись CarPrimary для машины {car.id}")
            else:
                logger.info(f"Обновлена существующая запись CarPrimary для машины {car.id}")

            return True

        except Exception as e:
            logger.error(f"Ошибка сохранения primary данных для машины {car.id}: {e}")
            return False
