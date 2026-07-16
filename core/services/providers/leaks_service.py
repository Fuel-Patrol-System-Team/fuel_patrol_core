import json
import logging
import polars as pl
from typing import Any, Dict, List, Optional, Tuple
from core.helpers.fuel import preprocess_basic_one
from core.helpers.maintenance import maintenance_fuel_level_check
from core.services.providers.leaks_base import BaseLeaksCalculator

logger = logging.getLogger(__name__)


class LeaksService(BaseLeaksCalculator):
    """Сервис для расчета утечек топлива"""

    def compute_leaks(
        self,
        auto_df: pl.DataFrame,
        data_df: pl.DataFrame,
        sensors: Dict[str, List[Dict[str, Any]]],
        primary_df: pl.DataFrame,
        norma_df: pl.DataFrame,
        initial_df: Optional[pl.DataFrame] = None,
        is_save_bad_data: bool = False,
        is_filter_bad_data: bool = True,
    ) -> Tuple[Optional[pl.DataFrame], Optional[pl.DataFrame], List[Any]]:
        """
        Вычисляет утечки топлива для всех машин
        """
        logger.info("🚀 НАЧАЛО РАСЧЕТА УТЕЧЕК")
        logger.info(
            f"📊 Входные данные: auto_df={len(auto_df)} машин, data_df={len(data_df)} записей, "
            f"primary_df={len(primary_df)}, norma_df={len(norma_df)}"
        )

        total_result = None
        intermediate_df = None
        processed_cars = 0
        skipped_cars = 0

        reports = []
        logger.debug(f"Структура auto_df: {auto_df.columns}")
        logger.debug(f"Структура data_df: {data_df.columns}")
        logger.debug(f"Структура primary_df: {primary_df.columns}")
        logger.debug(f"Структура norma_df: {norma_df.columns}")

        for auto, data in data_df.partition_by("auto", as_dict=True).items():
            auto_id = auto[0]
            logger.info(f"🔧 ОБРАБОТКА МАШИНЫ {auto_id}, записей: {len(data)}")

            try:
                auto_info_record = auto_df.filter(pl.col("id") == auto_id).to_dicts()
                primary_info_record = primary_df.filter(
                    pl.col("auto") == auto_id
                ).to_dicts()
                norma_info_record = norma_df.filter(
                    pl.col("sl_avto") == auto_id
                ).to_dicts()

                logger.debug(
                    f"Машина {auto_id}: найдено auto_info={len(auto_info_record)}, "
                    f"primary_info={len(primary_info_record)}, norma_info={len(norma_info_record)}"
                )

                if not auto_info_record:
                    logger.warning(f"❌ Машина {auto_id}: нет данных в auto_df")
                    skipped_cars += 1
                    continue

                if not primary_info_record:
                    logger.warning(f"❌ Машина {auto_id}: нет данных в primary_df")
                    skipped_cars += 1
                    continue

                if not norma_info_record:
                    logger.warning(f"❌ Машина {auto_id}: нет данных в norma_df")
                    skipped_cars += 1
                    continue

                auto_info = auto_info_record[0]
                primary_info = primary_info_record[0]
                norma_info = norma_info_record[0]

                logger.debug(
                    f"Машина {auto_id}: auto_info keys={list(auto_info.keys())}"
                )
                logger.debug(
                    f"Машина {auto_id}: primary_info keys={list(primary_info.keys())}"
                )
                logger.debug(
                    f"Машина {auto_id}: norma_info keys={list(norma_info.keys())}"
                )

                logger.info(f"🔄 Машина {auto_id}: предобработка данных")
                result_prep, inter_prep, _, reports = self._preprocess(
                    data, sensors,  auto_info, primary_info, initial_df=initial_df
                )

                logger.info(
                    f"✅ Машина {auto_id}: предобработка завершена, "
                    f"результат={len(result_prep)}, промежуточные={len(inter_prep)}"
                )

                if result_prep.is_empty():
                    logger.warning(
                        f"⚠️ Машина {auto_id}: нет данных после предобработки"
                    )
                    skipped_cars += 1
                    continue

                logger.info(f"🔍 Машина {auto_id}: расчет утечек")
                result_df, errors = self._fuel_leak_calculate(
                    result_prep,
                    norma_info,
                    auto_info,
                    is_save_bad_data=is_save_bad_data,
                    is_filter_bad_data=is_filter_bad_data,
                )

                reports = maintenance_fuel_level_check(result_df, reports)

                logger.info(
                    f"✅ Машина {auto_id}: расчет утечек завершен, "
                    f"результат={len(result_df)}, ошибок={len(errors) if errors is not None else 0}"
                )

                if result_df.is_empty():
                    logger.warning(
                        f"⚠️ Машина {auto_id}: нет данных после расчета утечек"
                    )
                    skipped_cars += 1
                    continue

                if intermediate_df is None:
                    intermediate_df = inter_prep
                    logger.debug(f"Машина {auto_id}: создана intermediate_df")
                else:
                    intermediate_df = pl.concat([intermediate_df, inter_prep])
                    logger.debug(f"Машина {auto_id}: добавлена в intermediate_df")

                if total_result is None:
                    total_result = result_df
                    logger.debug(f"Машина {auto_id}: создан total_result")
                else:
                    total_result = pl.concat([total_result, result_df])
                    logger.debug(f"Машина {auto_id}: добавлена в total_result")

                processed_cars += 1
                logger.info(f"✅ Машина {auto_id}: обработка завершена успешно")

            except Exception as e:
                logger.error(
                    f"💥 КРИТИЧЕСКАЯ ОШИБКА обработки машины {auto_id}: {e}",
                    exc_info=True,
                )
                skipped_cars += 1
                continue

        logger.info(
            f"📈 ИТОГИ ОБРАБОТКИ: обработано {processed_cars} машин, пропущено {skipped_cars}"
        )

        if total_result is not None:
            logger.info(f"🔗 Объединение результатов с дополнительными данными")
            total_result = self._merge_with_additional_data(
                total_result, auto_df, primary_df, norma_df
            )
            logger.info(
                f"✅ Объединение завершено, финальный результат: {len(total_result)} записей"
            )
        else:
            logger.warning("❌ Нет результатов для объединения")

        return total_result, intermediate_df, reports

    def _merge_with_additional_data(
        self,
        result_df: pl.DataFrame,
        auto_df: pl.DataFrame,
        primary_df: pl.DataFrame,
        norma_df: pl.DataFrame,
    ) -> pl.DataFrame:
        """Объединяет результаты с дополнительными данными"""
        logger.debug("🔄 Начало объединения данных")

        initial_count = len(result_df)
        logger.debug(f"Исходный результат: {initial_count} записей")

        result_df = result_df.join(auto_df, left_on="auto", right_on="id", how="left")
        logger.debug(f"После объединения с auto_df: {len(result_df)} записей")

        result_df = result_df.join(
            norma_df, left_on="auto", right_on="sl_avto", how="left"
        )
        logger.debug(f"После объединения с norma_df: {len(result_df)} записей")

        result_df = result_df.join(primary_df, on="auto", how="left")
        logger.debug(f"После объединения с primary_df: {len(result_df)} записей")

        logger.debug(
            f"✅ Объединение завершено: {initial_count} -> {len(result_df)} записей"
        )
        return result_df

    def _preprocess(
        self,
        df: pl.DataFrame,
        sensors: Dict[str, List[Dict[str, Any]]],
        cars: Dict[str, Any],
        norms: Dict[str, Any],
        initial_df: Optional[pl.DataFrame] = None,
        reports: List[Any] = [],
        ANTI_BUG_TIME_SECONDS: int = 30,
        PRE_PERIOD_TIME: int = 3,
        PERIOD_2_MIN: int = 30,
        VOLTAGE_LIMIT: float = 0.16,
        REFUELING_LIMIT: int = 4000,
        FUEL_JUMP_BARRIER_PERC: float = 0.05,
        AMTR_IGNORE_LIMIT: int = 3,
        RPM_DRIVING_VALUE: int = 20,
        DTIME_LIMIT: int = 5,
        is_debug: bool = False,
    ) -> Tuple[pl.DataFrame, pl.DataFrame, Optional[pl.DataFrame], List[Any]]:
        """Основная предобработка данных"""
        logger.debug("🔄 Начало основной предобработки")

        anti_bug = None
        if initial_df is None:
            logger.debug("Используется базовая предобработка")
            anti_bug, reports = preprocess_basic_one(
                df,
                cars,
                sensors,
                norms,
                VOLTAGE_LIMIT,
                FUEL_JUMP_BARRIER_PERC,
                ANTI_BUG_TIME_SECONDS,
                REFUELING_LIMIT,
                PRE_PERIOD_TIME,
                DTIME_LIMIT,
            )
        else:
            logger.debug("Используется переданный initial_df")
            anti_bug = initial_df

        if anti_bug.is_empty():
            logger.warning("⚠️ Нет данных после базовой предобработки")
            return pl.DataFrame(), pl.DataFrame(), None, reports

        logger.debug(f"Группировка по {PRE_PERIOD_TIME}-минутным интервалам")
        initial_count = len(anti_bug)
        anti_bug = anti_bug.group_by_dynamic(
            index_column="timestamp", every=f"{PRE_PERIOD_TIME}m", group_by="auto"
        ).agg(
            [
                pl.median("pos_s").alias("pos_s"),
                pl.max("pos_s_max").alias("pos_s_max"),
                pl.sum("spent_fuel").alias("spent_fuel"),
                pl.mean("spent_fuel_rolling").alias("spent_fuel_rolling"),
                pl.sum("spent_fuel_2").alias("spent_fuel_2"),
                pl.first("f1").alias("f1"),
                pl.last("f2").alias("f2"),
                pl.sum("dtime").alias("dtime"),
                pl.sum("count").alias("count"),
                pl.sum("jumps").alias("jumps"),
                pl.sum("amtr").alias("amtr"),
                pl.mean("rpm").alias("rpm"),
                pl.sum("fd").alias("fd"),
                pl.max("fuel_level_nan").alias("fuel_level_nan"),
                pl.sum("no_sat_data").alias("no_sat_data"),
                pl.sum("load").alias("load"),
                pl.max("ign").alias("ign_max"),
                pl.first("calc_sensors_fuel_level").alias("fuel_first"),
                pl.last("calc_sensors_fuel_level").alias("fuel_last"),
                pl.sum("ign"),
                pl.sum("ptime"),
                pl.sum("es"),
                pl.sum("spent_fuel_boundary"),
            ]
        )
        grouped_count = len(anti_bug)
        logger.debug(
            f"Группировка {PRE_PERIOD_TIME}м: {initial_count} -> {grouped_count} записей"
        )

        anti_bug = anti_bug.with_columns(
            [
                pl.col("f1")
                .diff()
                .over(["auto"])
                .fill_null(0)
                .cast(pl.Float32)
                .alias("spent_fuel_standing"),
            ]
        )
        logger.debug("Рассчитан spent_fuel_standing")

        anti_bug = anti_bug.with_columns(
            (pl.col("pos_s") * (pl.col("dtime") / 3600)).alias("travel")
        )
        logger.debug("Рассчитано пройденное расстояние")

        logger.debug(f"Финальная группировка по {PERIOD_2_MIN}-минутным интервалам")
        initial_count = len(anti_bug)
        result = anti_bug.group_by_dynamic(
            index_column="timestamp", every=f"{PERIOD_2_MIN}m", group_by="auto"
        ).agg(
            [
                pl.mean("pos_s").alias("pos_s"),
                pl.sum("spent_fuel").alias("spent_fuel"),
                pl.max("pos_s_max").alias("pos_s_max"),
                pl.sum("spent_fuel_2").alias("spent_fuel_2"),
                pl.sum("spent_fuel_standing"),
                pl.first("f1").alias("f1"),
                pl.last("f2").alias("f2"),
                pl.mean("spent_fuel_rolling").alias("spent_fuel_rolling"),
                pl.sum("dtime").alias("dtime"),
                pl.sum("travel").alias("travel"),
                pl.sum("count").alias("count"),
                pl.sum("amtr").alias("amtr"),
                pl.sum("jumps").alias("jumps"),
                pl.mean("rpm").alias("rpm_mean"),
                pl.sum("fd").alias("fd"),
                pl.max("fuel_level_nan").alias("fuel_level_nan"),
                pl.sum("no_sat_data").alias("no_sat_data"),
                pl.sum("load").alias("load"),
                pl.max("ign").alias("ign_max"),
                pl.sum("ign"),
                pl.sum("ptime"),
                pl.first("fuel_first"),
                pl.last("fuel_last"),
                pl.sum("es"),
                pl.sum("spent_fuel_boundary"),
            ]
        )
        result = result.filter(pl.col("fuel_level_nan").ne(pl.col("count")))
        result = result.with_columns(
            pl.col("timestamp").shift(-1).alias("next_period")
        )
        final_count = len(result)
        logger.debug(
            f"Финальная группировка {PERIOD_2_MIN}м: {initial_count} -> {final_count} записей"
        )

        result = result.with_columns(
            [
                (pl.col("spent_fuel") / pl.col("travel") * 100).alias("spent_per_100"),
                (pl.col("f1").diff().fill_nan(0).fill_null(0).alias("f1_diff")),
            ]
        )
        logger.debug("Рассчитан расход на 100 км")

        logger.info(
            f"✅ Основная предобработка завершена: результат={len(result)}, промежуточные={len(anti_bug)}"
        )
        

        if is_debug:
            logger.debug("Режим отладки: возвращаются все данные")
            return result, anti_bug, df, reports
        else:
            return result, anti_bug, None, reports

    def _fuel_leak_calculate(
        self,
        df_values: pl.DataFrame,
        norma: Dict[str, Any],
        cars: Dict[str, Any],
        LEAK_LIMIT: int = 12,
        SIGMA_LIMIT: float = 3.5,
        MULT_STD_MEAN_DIFF: float = 2.25,
        FUEL_JUMPS_AMOUNT: int = 30,
        LEAK_FACTOR: float = 0.05,
        SMALL_SPEED_FACTOR: float = 3.25,
        UNREASONABLE_FUEL_LEVEL: int = 1000,
        UNTARIFF_LEVEL: int = 400,
        is_save_bad_data: bool = False,
        is_filter_bad_data: bool = True,
    ) -> Tuple[pl.DataFrame, Optional[pl.DataFrame]]:
        """Расчет утечек топлива"""
        logger.info("🔍 Начало расчета утечек")
        logger.debug(
            f"Входные данные: {len(df_values)} записей, параметры: LEAK_LIMIT={LEAK_LIMIT}, "
            f"FUEL_JUMPS_AMOUNT={FUEL_JUMPS_AMOUNT}"
        )

        df_values = df_values.with_columns([pl.lit(0).alias("bad_flags")])
        logger.debug("Инициализированы bad_flags")

        df_values = df_values.with_columns(
            [
                (
                    pl.when(pl.col("count") <= 5).then(pl.lit(1 << 0)).otherwise(0)
                    | pl.when(pl.col("jumps") > FUEL_JUMPS_AMOUNT)
                    .then(pl.lit(1 << 1))
                    .otherwise(0)
                    | pl.when(pl.col("spent_fuel") > UNREASONABLE_FUEL_LEVEL)
                    .then(pl.lit(1 << 2))
                    .otherwise(0)
                ).alias("bad_flags")
            ]
        )
        logger.debug("Рассчитаны bad_flags")

        df_values = df_values.with_columns(
            [
                pl.when(pl.col("spent_fuel") >= 0)
                .then(0)
                .otherwise(pl.col("spent_fuel"))
                .alias("spent_fuel")
            ]
        )
        df_values = df_values.with_columns(
            pl.col("spent_fuel").abs().alias("spent_fuel")
        )
        logger.debug("Обработан spent_fuel")

        df_values = df_values.with_columns(
            [(pl.col("bad_flags") > 0).alias("is_bad_data")]
        )
        bad_count = df_values.filter(pl.col("is_bad_data")).height
        logger.debug(f"Найдено плохих данных: {bad_count} записей")

        df_values = df_values.with_columns(
            [(pl.col("fd") / pl.col("count")).alias("decrease_ratio")]
        )
        logger.debug("Рассчитан decrease_ratio")

        bad_data = None
        if is_save_bad_data:
            bad_data = df_values.filter(pl.col("is_bad_data"))
            logger.debug(f"Сохранены плохие данные: {len(bad_data)} записей")

        if is_filter_bad_data:
            initial_count = len(df_values)
            df_values = df_values.filter(~pl.col("is_bad_data"))
            filtered_count = len(df_values)
            logger.debug(
                f"Фильтрация плохих данных: {initial_count} -> {filtered_count} записей"
            )

        df_values = df_values.with_columns(
            [
                pl.when(pl.col("timestamp").dt.month().is_between(3, 10))
                .then(norma["norma_rasx_summer"])
                .otherwise(norma["norma_rasx_winter"])
                .alias("norma_rasx")
            ]
        )
        logger.debug("Определена норма расхода по сезону")

        df_values = df_values.with_columns(
            [
                (pl.col("norma_rasx") * (pl.col("pos_s") / norma["speed_etalon"]))
                .pow(2)
                .clip(lower_bound=pl.col("norma_rasx").truediv(5))
                .alias("norma_rasx_per_travel")
            ]
        )
        logger.debug("Рассчитана норма на пройденное расстояние")

        df_values = df_values.with_columns(
            [
                (pl.col("spent_fuel") - pl.col("norma_rasx_per_travel"))
                .clip(lower_bound=0)
                .alias("leak")
            ]
        )
        logger.debug("Рассчитана утечка")

        df_values = df_values.with_columns(pl.lit(LEAK_LIMIT).alias("leak_factor"))
        logger.debug("Установлен leak_factor")
        grades = cars["grades"] is None
        df_values = df_values.with_columns(
            [pl.lit(grades).alias("untariffed")]
        )
        untariffed_count = df_values.filter(pl.col("untariffed")).height
        logger.debug(f"Нетарированных записей: {untariffed_count}")

        df_values = df_values.with_columns(
            [
                (pl.col("load") / pl.col("dtime")).alias("load_ratio"),
                pl.col("rpm_mean").fill_null(0),
            ]
        )
        logger.debug("Рассчитаны load_ratio и rpm_mean")

        df_values = df_values.with_columns(
            [
                (
                    (~pl.col("untariffed") & (pl.col("leak") > LEAK_LIMIT))
                    | (pl.col("untariffed") & (pl.col("leak") > UNTARIFF_LEVEL))
                ).alias("is_leak")
            ]
        )
        leak_count = df_values.filter(pl.col("is_leak")).height
        logger.info(
            f"✅ Расчет утечек завершен: всего утечек {leak_count} из {len(df_values)} записей"
        )

        # 
        df_values = df_values.with_columns(
            ((pl.col("spent_fuel") - pl.col("norma_rasx_per_travel")) / norma["norma_std"]).alias("z_values"),
        )

        df_values = df_values.with_columns(
            pl.when(pl.col("ign").gt(0)).then(1).otherwise(0).alias("ign_spread")
        )

        df_values = df_values.with_columns(
            pl.col("spent_fuel").truediv(pl.col("dtime")).mul(60).alias("fpm")
        )

        if is_save_bad_data:
            logger.debug("Возвращаются данные с плохими записями")
            return df_values, bad_data
        return df_values, None
