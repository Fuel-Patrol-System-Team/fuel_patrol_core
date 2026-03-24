import json
import logging
import polars as pl
from typing import Optional, Tuple, Dict
from datetime import datetime, timedelta
from pathlib import Path
from django.conf import settings

logger = logging.getLogger(__name__)


class NormsService:
    """Сервис для расчета норм расхода топлива"""

    @staticmethod
    def calculate_norms_single(
            raw_df: pl.DataFrame,
            primary_df: pl.DataFrame,
            auto_df: pl.DataFrame
    ) -> Optional[pl.DataFrame]:
        """
        Вычисляет нормы расхода топлива для одной машины
        """
        try:
            logger.info("=== НАЧАЛО РАСЧЕТА НОРМ ===")
            logger.info(f"Размер raw_df: {raw_df.shape}")
            logger.info(f"Колонки raw_df: {raw_df.columns}")
            logger.info(f"Размер primary_df: {primary_df.shape}")
            logger.info(f"Колонки primary_df: {primary_df.columns}")
            logger.info(f"Размер auto_df: {auto_df.shape}")
            logger.info(f"Колонки auto_df: {auto_df.columns}")


            cars_dict = NormsService._prepare_cars_dict(auto_df)
            primary_dict = NormsService._prepare_primary_dict(primary_df)

            logger.info(f"Подготовлено cars_dict для {len(cars_dict)} машин")
            logger.info(f"Подготовлено primary_dict для {len(primary_dict)} машин")

            if not cars_dict:
                logger.error("Не удалось подготовить словарь машин")
                return None

            if not primary_dict:
                logger.error("Не удалось подготовить словарь первичных показателей")
                return None


            logger.info("Начало предобработки данных...")
            processed_df = NormsService._preprocess_norms(raw_df, cars_dict, primary_dict)

            logger.info(f"Результат предобработки: {processed_df.shape}")
            if processed_df.is_empty():
                logger.warning("Нет данных после предобработки")
                return None


            logger.info("Начало постобработки...")
            filtered_df, filter_reason = NormsService._fuel_afterprocess_norms(processed_df, cars_dict)

            logger.info(f"Результат фильтрации: {filtered_df.shape}, причина: {filter_reason}")
            if filtered_df.is_empty():
                logger.warning(f"Нет данных после фильтрации: {filter_reason}")
                return None

            logger.info("Расчет финальных норм...")
            result_df = NormsService._calculate_by_car(filtered_df, cars_dict)

            logger.info(f"Финальный результат: {result_df.shape}")
            if result_df.is_empty():
                logger.warning("Не удалось рассчитать нормы")
                return None

            logger.info(f"=== РАСЧЕТ НОРМ ЗАВЕРШЕН: {len(result_df)} машин ===")
            return result_df

        except Exception as e:
            logger.error(f"Ошибка в calculate_norms_single: {e}", exc_info=True)
            return None

    @staticmethod
    def _prepare_cars_dict(auto_df: pl.DataFrame) -> Dict[str, Dict[str, float]]:
        """Подготавливает словарь с параметрами тарировки для каждой машины"""
        cars_dict = {}
        for row in auto_df.to_dicts():
            auto_id = row['auto']
            cars_dict[auto_id] = {
                'input': float(row['input']) if row['input'] is not None else 1.0,
                'grades': row['grades'],
                'output': float(row['output']) if row['output'] is not None else 1.0
            }
            logger.debug(
                f"Машина {auto_id}: input={cars_dict[auto_id]['input']}, output={cars_dict[auto_id]['output']}")
        return cars_dict

    @staticmethod
    def _prepare_primary_dict(primary_df: pl.DataFrame) -> Dict[str, Dict[str, float]]:
        """Подготавливает словарь с первичными показателями для каждой машины"""
        primary_dict = {}
        for row in primary_df.to_dicts():
            auto_id = row['auto']
            primary_dict[auto_id] = {
                'max_fuel': float(row['max_fuel']) if row['max_fuel'] is not None else 100.0
            }
            logger.debug(f"Первичные показатели {auto_id}: max_fuel={primary_dict[auto_id]['max_fuel']}")
        return primary_dict

    @staticmethod
    def _preprocess_norms(
            df: pl.DataFrame,
            cars_dict: Dict[str, Dict[str, float]],
            primary_dict: Dict[str, Dict[str, float]],
            ANTI_BUG_TIME_SECONDS: int = 10,
            PRE_PERIOD_TIME: int = 3,
            PERIOD_2_MIN: int = 60, 
            VOLTAGE_LIMIT: float = 0.16,
            REFUELING_LIMIT: int = 4000,
            FUEL_JUMP_BARRIER_PERC: float = 0.05,
            AMTR_IGNORE_LIMIT: int = 3,
            RPM_DRIVING_VALUE: int = 20,
            is_debug: bool = False,
    ) -> pl.DataFrame:
        """Предобработка данных для расчета норм"""

        logger.info("Базовая предобработка норм...")
        anti_bug = NormsService._preprocess_basic_norms(
            df,
            cars_dict,
            primary_dict,
            VOLTAGE_LIMIT,
            FUEL_JUMP_BARRIER_PERC,
            ANTI_BUG_TIME_SECONDS,
            REFUELING_LIMIT,
            PRE_PERIOD_TIME,
        )

        if anti_bug.is_empty():
            logger.warning("Нет данных после базовой предобработки")
            return anti_bug

        logger.info(f"После базовой предобработки: {anti_bug.shape}")

        logger.info("Группировка по 3-минутным интервалам...")
        try:
            anti_bug = (
                anti_bug.group_by_dynamic(
                    index_column="timestamp",
                    every=f"{PRE_PERIOD_TIME}m",
                    group_by="auto"
                )
                .agg([
                    pl.col("pos_s").median(),
                    pl.col("spent_fuel").sum(),
                    pl.col("dtime").sum(),
                    pl.col("count").sum(),
                    pl.col("jumps").sum(),
                    pl.col("max_local_fuel_level").max(),
                    pl.col("amtr").sum(),
                    pl.col("rpm_mean").mean(),
                    pl.col("fd").sum(),
                    pl.col("fuel_level_nan").max(),
                    pl.col("no_sat_data").sum(),
                    pl.col("ign").sum(),
                ])
            )
            logger.info(f"После 3-минутной группировки: {anti_bug.shape}")
        except Exception as e:
            logger.error(f"Ошибка при 3-минутной группировке: {e}")
            return pl.DataFrame()

        anti_bug = anti_bug.with_columns(
            (pl.col("pos_s") * (pl.col("dtime") / 3600)).alias("travel")
        )

        logger.info("Группировка по 30-минутным интервалам...")
        try:
            result = (
                anti_bug.group_by_dynamic(
                    index_column="timestamp",
                    every=f"{PERIOD_2_MIN}m",
                    group_by="auto"
                )
                .agg([
                    pl.col("pos_s").mean(),
                    pl.col("spent_fuel").sum(),
                    pl.col("dtime").sum(),
                    pl.col("travel").sum(),
                    pl.col("count").sum(),
                    pl.col("amtr").sum(),
                    pl.col("jumps").sum(),
                    pl.col("rpm_mean").mean(),
                    pl.col("fd").sum(),
                    pl.col("fuel_level_nan").max(),
                    pl.col("no_sat_data").sum(),
                    pl.col("max_local_fuel_level").max(),
                    pl.col("ign").sum(),
                ])
                .sort(["auto", "timestamp"])
            )
            logger.info(f"После 30-минутной группировки: {result.shape}")
        except Exception as e:
            logger.error(f"Ошибка при 30-минутной группировке: {e}")
            return pl.DataFrame()

        result = result.with_columns(
            (pl.col("spent_fuel") / pl.col("travel") * 100).alias("spent_per_100")
        )

        result = result.filter(pl.col("spent_fuel").lt(0))
        logger.info(f"После фильтрации отрицательных расходов: {result.shape}")

        return result

    @staticmethod
    def _preprocess_basic_norms(
            df: pl.DataFrame,
            cars_dict: Dict[str, Dict[str, float]],
            primary_dict: Dict[str, Dict[str, float]],
            VOLTAGE_LIMIT: float,
            FUEL_JUMP_BARRIER_PERC: float,
            ANTI_BUG_TIME_SECONDS: int,
            REFUELING_LIMIT: int,
            PRE_PERIOD_TIME: int,
    ) -> pl.DataFrame:
        """Базовая предобработка данных в соответствии с оригинальной логикой"""

        logger.info("Начало базовой предобработки...")
        logger.info(f"Входные данные: {df.shape}, колонки: {df.columns}")

        if "rpm" not in df.columns:
            df = df.with_columns(pl.lit(0).alias("rpm"))
            logger.debug("Добавлена колонка rpm")
        if "satellites" not in df.columns:
            df = df.with_columns(pl.lit(20).alias("satellites"))
            logger.debug("Добавлена колонка satellites")

        processed_dfs = []
        auto_ids = df["auto"].unique().to_list()
        logger.info(f"Обработка машин: {auto_ids}")

        for auto_id in auto_ids:
            logger.info(f"Обработка машины: {auto_id}")
            car_df = df.filter(pl.col("auto") == auto_id)

            if car_df.is_empty():
                logger.warning(f"Нет данных для машины {auto_id}")
                continue

            car_params = cars_dict.get(auto_id, {'input': 1.0, 'output': 1.0})
            primary_params = primary_dict.get(auto_id, {'max_fuel': 100.0})

            logger.debug(f"Параметры машины {auto_id}: {car_params}, primary: {primary_params}")

            processed_car_df = NormsService._process_single_car_norms(
                car_df, car_params, primary_params,
                VOLTAGE_LIMIT, FUEL_JUMP_BARRIER_PERC,
                ANTI_BUG_TIME_SECONDS, REFUELING_LIMIT, PRE_PERIOD_TIME
            )

            if processed_car_df is not None and not processed_car_df.is_empty():
                processed_dfs.append(processed_car_df)
                logger.info(f"Машина {auto_id} обработана успешно: {processed_car_df.shape}")
            else:
                logger.warning(f"Машина {auto_id} не дала результатов")

        if not processed_dfs:
            logger.warning("Нет обработанных данных ни для одной машины")
            return pl.DataFrame()

        result = pl.concat(processed_dfs)
        logger.info(f"Объединенный результат базовой предобработки: {result.shape}")
        return result

    @staticmethod
    def _process_single_car_norms(
            car_df: pl.DataFrame,
            car_params: Dict[str, float],
            primary_params: Dict[str, float],
            VOLTAGE_LIMIT: float,
            FUEL_JUMP_BARRIER_PERC: float,
            ANTI_BUG_TIME_SECONDS: int,
            REFUELING_LIMIT: int,
            PRE_PERIOD_TIME: int,
    ) -> pl.DataFrame:
        """Обрабатывает данные для одной машины"""
        try:
            input_val = car_params['input']
            output_val = car_params['output']
            max_fuel = primary_params['max_fuel']

            logger.debug(f"Обработка машины: input={input_val}, output={output_val}, max_fuel={max_fuel}")
            logger.debug(f"Первые 5 значений timestamp: {car_df['timestamp'].head(5)}")

            df_processed = car_df.with_columns(
                pl.when(pl.col("calc_sensors_fuel_level") > max_fuel)
                .then(None)
                .otherwise(pl.col("calc_sensors_fuel_level"))
                .cast(pl.Float32)
                .alias("calc_sensors_fuel_level"),
            )

            logger.debug("Преобразование timestamp...")

            timestamp_dtype = df_processed.schema['timestamp']
            logger.debug(f"Тип timestamp: {timestamp_dtype}")

            if isinstance(timestamp_dtype, pl.Datetime):
                logger.debug("Timestamp уже в datetime формате")
                df_processed = df_processed.with_columns(
                    pl.col("rpm").fill_null(0)
                )
            else:
                try:

                    df_processed = df_processed.with_columns(
                        pl.col("timestamp").cast(pl.Datetime),
                        pl.col("rpm").fill_null(0)
                    )
                    logger.debug("Успешно преобразован timestamp с форматом '%Y-%m-%d %H:%M:%S%.f'")
                except Exception as e1:
                    logger.warning(f"Не удалось преобразовать с форматом пробела: {e1}")
                    try:
                        df_processed = df_processed.with_columns(
                            pl.col("timestamp").cast(pl.Datetime),
                            pl.col("rpm").fill_null(0)
                        )
                        logger.debug("Успешно преобразован timestamp с форматом '%Y-%m-%dT %H:%M:%S%.fZ'")
                    except Exception as e2:
                        logger.error(f"Не удалось преобразовать timestamp: {e2}")
                        return pl.DataFrame()


            logger.debug(f"Тип timestamp после преобразования: {df_processed.schema['timestamp']}")
            logger.debug(f"Первые 5 значений после преобразования: {df_processed['timestamp'].head(5)}")


            col_dtime_half = pl.col("timestamp").dt.truncate("30m")
            col_dtime_2hour = pl.col("timestamp").dt.truncate("2h")

            df_processed = df_processed.with_columns(
                pl.col("calc_sensors_fuel_level").is_not_nan()
                .over(["auto", col_dtime_half])
                .cast(pl.Int16)
                .alias("fuel_level_nan")
            )


            initial_count = len(df_processed)
            df_processed = df_processed.filter(
                pl.col("calc_sensors_fuel_level").is_not_nan()
            )
            filtered_count = len(df_processed)
            logger.debug(f"После фильтрации NaN: {filtered_count}/{initial_count} записей")

            if df_processed.is_empty():
                logger.warning("Нет данных после фильтрации NaN")
                return df_processed


            df_processed = df_processed.with_columns(
                pl.col("timestamp")
                .diff()
                .dt.total_seconds()
                .abs()
                .cast(pl.Int16)
                .over(["auto", pl.col("timestamp").dt.truncate("1h")])
                .alias("dtime")
            )

            grades = car_params['grades']
            unique = list({tuple(sorted(d.items())): d for d in grades["grades"]}.values())
            fp_pos = len(unique) // 3
            fp = unique[fp_pos]
            sp = unique[-1]
            slope = (sp["output"] - fp["output"]) / (sp["input"] - fp["input"])
            b = fp["output"] - slope * fp["input"]

            df_processed = df_processed.with_columns(
                [
                    pl.max("calc_sensors_voltage").over(["auto", col_dtime_2hour]).alias("voltage_max"),
                    (pl.col("calc_sensors_fuel_level").mul(slope).add(b) ).alias("calc_sensors_fuel_level"),
                ]
            )


            initial_count = len(df_processed)
            df_processed = df_processed.filter(
                (pl.col("voltage_max") - pl.col("calc_sensors_voltage")) / pl.col("voltage_max")
                < VOLTAGE_LIMIT
            )
            filtered_count = len(df_processed)
            logger.debug(f"После фильтрации по напряжению: {filtered_count}/{initial_count} записей")

            if df_processed.is_empty():
                logger.warning("Нет данных после фильтрации по напряжению")
                return df_processed


            df_processed = df_processed.with_columns(
                [
                    pl.col("calc_sensors_fuel_level")
                    .diff()
                    .over(["auto", col_dtime_2hour])
                    .fill_null(0)
                    .cast(pl.Float32)
                    .alias("spent_fuel"),
                    pl.col("pos_s").diff().over(["auto", col_dtime_2hour]).fill_null(0).cast(pl.Float32).alias("pos_a"),
                ]
            )


            df_processed = df_processed.with_columns(
                [
                    pl.col("spent_fuel").abs().alias("spent_fuel_abs"),
                    pl.when(pl.col("spent_fuel") > 0)
                    .then(pl.col("spent_fuel"))
                    .otherwise(0)
                    .cast(pl.Float32)
                    .alias("recover_fuel"),
                    pl.when(pl.col("pos_a").is_null())
                    .then(pl.col("pos_s"))
                    .otherwise(pl.col("pos_a"))
                    .alias("pos_a"),
                    pl.col("pos_s").rolling_max(window_size=4).alias("pos_s"),
                    pl.when(pl.col("spent_fuel") < 0).then(1).otherwise(0).cast(pl.Int16).alias("fd"),
                    pl.when((pl.col("satellites") == 0)).then(1).otherwise(0).cast(pl.Int16).alias("no_sat_data"),
                    pl.when(
                        pl.col("spent_fuel").abs() > max_fuel * FUEL_JUMP_BARRIER_PERC
                    )
                    .then(1)
                    .otherwise(0)
                    .cast(pl.Int16)
                    .alias("jumps"),
                ]
            )


            df_processed = df_processed.with_columns(
                pl.when((pl.col("no_sat_data") == 1) & (pl.col("spent_fuel") != 0))
                .then(0)
                .otherwise(pl.col("spent_fuel"))
                .alias("spent_fuel")
            )


            df_processed = df_processed.with_columns(
                (pl.col("spent_fuel") / pl.col("dtime")).cast(pl.Float32).alias("fps")
            )


            df_processed = df_processed.with_columns(
                pl.when(pl.col("fps") < -1).then(0).otherwise(pl.col("spent_fuel")).alias("spent_fuel")
            )


            df_processed = df_processed.with_columns(pl.lit(1).alias("count"))


            logger.debug("Группировка по анти-баг интервалам...")
            initial_count = len(df_processed)
            df_processed = (
                df_processed.group_by_dynamic(
                    index_column="timestamp",
                    every=f"{ANTI_BUG_TIME_SECONDS}s",
                    group_by="auto"
                ).agg([
                    pl.col("calc_sensors_fuel_level").mean(),
                    pl.col("pos_s").mean(),
                    pl.col("spent_fuel").sum(),
                    pl.col("dtime").sum(),
                    pl.col("jumps").sum(),
                    pl.col("amtr").sum(),
                    pl.col("rpm").mean().alias("rpm_mean"),
                    pl.col("fd").sum(),
                    pl.col("fuel_level_nan").sum(),
                    pl.col("no_sat_data").sum(),
                    pl.col("count").sum(),
                    pl.col("recover_fuel").sum(),
                    pl.col("ign").sum()
                ])
            )
            logger.debug(f"После группировки: {len(df_processed)}/{initial_count} записей")


            df_processed = df_processed.with_columns(
                pl.when(
                    (pl.col("pos_s") == 0)
                    & (pl.col("spent_fuel") > 0)
                    & (pl.col("spent_fuel") < REFUELING_LIMIT)
                )
                .then(0)
                .otherwise(pl.col("spent_fuel"))
                .alias("spent_fuel")
            )


            df_processed = df_processed.with_columns(
                pl.max("calc_sensors_fuel_level")
                .over(["auto", pl.col("timestamp").dt.truncate(f"{PRE_PERIOD_TIME}m")])
                .alias("max_local_fuel_level")
            )

            logger.debug(f"Машина обработана успешно: {df_processed.shape}")
            return df_processed

        except Exception as e:
            logger.error(f"Ошибка обработки машины: {e}", exc_info=True)
            return pl.DataFrame()

    @staticmethod
    def _fuel_afterprocess_norms(
            df_values: pl.DataFrame,
            cars_dict: Dict[str, Dict[str, float]],
            FUEL_JUMPS_AMOUNT: int = 30,
            UNREASONABLE_FUEL_LEVEL: int = 1000,
            INSIGNIFICANT_MEAN_SPEED: float = 0.45,
    ) -> Tuple[pl.DataFrame, str]:
        """Постобработка и фильтрация данных норм"""

        df = df_values.with_columns([
            (pl.col("count") <= 5).alias("is_bad_data_count"),
            (pl.col("jumps") > FUEL_JUMPS_AMOUNT).alias("is_bad_data_jitter"),
        ])


        df = df.with_columns(
            pl.when(pl.col("spent_fuel") < 0)
            .then(pl.col("spent_fuel").abs())
            .otherwise(None)
            .alias("spent_fuel")
        )

        df = df.with_columns([
            (pl.col("spent_fuel") > UNREASONABLE_FUEL_LEVEL).alias("is_bad_data_unreasonable_fuel"),
        ])

        df = df.with_columns([
            (
                    pl.col("is_bad_data_count") |
                    pl.col("is_bad_data_jitter") |
                    pl.col("is_bad_data_unreasonable_fuel")
            ).alias("is_bad_data"),
            (pl.col("fd") / pl.col("count")).alias("ratio"),
            (1 - pl.col("no_sat_data") / pl.col("count")).alias("sat_coverage"),
        ])


        df = df.filter(pl.col("is_bad_data").eq(False))
        if df.is_empty():
            return df, "filtered a car due to bad_data"


        df = df.filter(
            (pl.col("spent_fuel") > 0) &
            (pl.col("sat_coverage") > 0.85)
        )

        if df.is_empty():
            return df, "filtered due to sat_coverage and spent_fuel"

        return df, ""

    @staticmethod
    def _calculate_by_car(df: pl.DataFrame, cars_dict: Dict[str, Dict[str, float]]) -> pl.DataFrame:
        """Расчет финальных норм по каждой машине"""
        logger.info("=== НАЧАЛО РАСЧЕТА ФИНАЛЬНЫХ НОРМ ===")
        logger.info(f"Входные данные для расчета норм: {df.shape}")
        logger.info(f"Колонки: {df.columns}")

        if df.is_empty():
            logger.warning("Нет данных для расчета норм")
            return pl.DataFrame()


        logger.info("Статистика по данным:")
        try:
            stats = df.select([
                pl.col("auto").n_unique().alias("unique_cars"),
                pl.col("spent_fuel").mean().alias("mean_spent_fuel"),
                pl.col("spent_fuel").std().alias("std_spent_fuel"),
                pl.col("pos_s").mean().alias("mean_speed"),
                pl.col("max_local_fuel_level").mean().alias("mean_max_fuel"),
            ])
            logger.info(f"Статистика: {stats.row(0)}")
        except Exception as e:
            logger.warning(f"Не удалось получить статистику: {e}")

        results = []
        auto_ids = df["auto"].unique().to_list()
        logger.info(f"Расчет норм для машин: {auto_ids}")

        for i, group in enumerate(df.partition_by("auto", include_key=True)):
            if group.is_empty():
                logger.warning(f"Группа {i} пустая")
                continue

            auto = group["auto"][0]
            logger.info(f"Обработка машины {auto}: {group.shape} записей")

            spent_fuel: float = group["spent_fuel"].mean()
            if spent_fuel is None:
                spent_fuel = 0
                logger.warning(f"Машина {auto}: spent_fuel is None, установлено 0")
            else:
                logger.info(f"Машина {auto}: средний расход = {spent_fuel:.2f}")

            std = group["spent_fuel"].std()
            if std is None:
                std = 0
                logger.warning(f"Машина {auto}: std is None, установлено 0")
            else:
                logger.info(f"Машина {auto}: std = {std:.2f}")

            std = min(spent_fuel * 2.5, std)

            max_speed = group["pos_s"].quantile(0.75, interpolation="nearest")
            if max_speed is not None and max_speed < 0.2:
                max_speed = group["pos_s"].quantile(0.95, interpolation="nearest")
                logger.info(f"Машина {auto}: низкая скорость, использован 95% квантиль = {max_speed:.2f}")
            
            if max_speed == 0:
                logger.info(f"Машина {auto} нулевую скорость за весь период. Проблема с датчиком?")
                continue

            max_fuel = group["max_local_fuel_level"].max()
            rpm_max = group["rpm_mean"].max()
            rpm_mean = group["rpm_mean"].mean()
            rpm_std = group["rpm_mean"].std()

            logger.info(f"Машина {auto}: max_speed={max_speed}, max_fuel={max_fuel}, "
                        f"rpm_mean={rpm_mean}, rpm_std={rpm_std}, rpm_max={rpm_max}")

            if max_speed is not None and spent_fuel is not None:
                results.append({
                    "max_fuel": max_fuel,
                    "sl_avto": auto,
                    "norma_rasx_summer": spent_fuel,
                    "norma_rasx_winter": spent_fuel * 1.1,
                    "norma_mean": spent_fuel,
                    "norma_std": std,
                    "speed_etalon": float(min(max_speed, 60)),
                    "norma_rpm_mean": rpm_mean,
                    "norma_rpm_std": rpm_std,
                    "norma_rpm_max": rpm_max,
                    "period": datetime.now() + timedelta(days=365),
                    "is_special_car": max_speed > 30,
                })
                logger.info(f"Машина {auto}: нормы рассчитаны успешно")
            else:
                logger.warning(f"Машина {auto}: не удалось рассчитать нормы - "
                               f"max_speed={max_speed}, spent_fuel={spent_fuel}")

        logger.info(f"Всего рассчитано норм для {len(results)} машин")

        if not results:
            logger.warning("Не удалось рассчитать нормы ни для одной машины")
            return pl.DataFrame()

        schema = {
            "max_fuel": pl.Float32,
            "sl_avto": pl.Categorical,
            "norma_rasx_summer": pl.Float32,
            "norma_rasx_winter": pl.Float32,
            "norma_mean": pl.Float32,
            "norma_std": pl.Float32,
            "speed_etalon": pl.Float32,
            "norma_rpm_mean": pl.Float32,
            "norma_rpm_std": pl.Float32,
            "norma_rpm_max": pl.Float32,
            "period": pl.Datetime,
            "is_special_car": pl.Boolean
        }

        result_df = pl.DataFrame(results, schema=schema)
        logger.info(f"=== РАСЧЕТ ФИНАЛЬНЫХ НОРМ ЗАВЕРШЕН: {result_df.shape} ===")
        return result_df

    @staticmethod
    def save_norms_to_csv(norms_df: pl.DataFrame, car_id: str, prefix: str = "norms") -> str:
        """Сохраняет нормы в CSV файл"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{car_id}_{timestamp}.csv"
            filepath = Path(settings.MEDIA_ROOT) / "norms_data" / filename


            filepath.parent.mkdir(parents=True, exist_ok=True)

            norms_df.write_csv(filepath)
            logger.info(f"Нормы сохранены в {filepath}")

            return str(filepath)

        except Exception as e:
            logger.error(f"Ошибка сохранения норм в CSV: {e}")
            raise
