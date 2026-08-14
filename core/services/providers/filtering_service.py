from enum import Enum
import logging
import polars as pl
from typing import Iterable, Tuple, List
from core.helpers.decorators import with_param_filters
from core.services.providers.filtering_base import BaseFilteringService
from core.services.providers.glonass.constants import FuelFilters

logger = logging.getLogger(__name__)


# Prerequisite filters shared by the terminal pick_by_* filters.
# They encode the manual ordering previously hard-coded in apply_filters:
#   leak_picker -> filtering_count -> filtering_ptime
#   -> filtering_sattelites -> filtering_nan
class FilteringService(BaseFilteringService):
    """Сервис для фильтрации результатов утечек"""

    
    def final_filter(self, df: pl.DataFrame):
        filtered_df = df.filter(pl.col("is_picked_leak").eq(True) & pl.col("filtered").eq(False))
        return filtered_df, df
    def apply_filters(self, df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Применяет все фильтры к данным.

        Параметрические фильтры (leak_picker, filtering_count, filtering_ptime,
        filtering_sattelites, filtering_nan) запускаются автоматически как
        prerequisites декорированных методов (см. @with_param_filters), поэтому
        здесь вызываются только терминальные фильтры цепочки.
        """
        logger.info("Применение пикеров для фильтров")

        # leak_picker + row filters run once as prerequisites of the first
        # terminal pick; subsequent picks reuse the already-prepared df so the
        # chain stays continuous (matches the original manual sequence).
        filtered_df, _ = self.leak_picker(df)
        # filtered_df, _ = self.filtering_special_required(filtered_df)
        # filtered_df, _ = self.filtering_standing_hard(filtered_df)
        # filtered_df, _ = self.pick_by_rpm_model(filtered_df)
        filtered_df, _ = self.filtering_count(filtered_df, 10)
        filtered_df, _ = self.filtering_sattelites(filtered_df)
        filtered_df, _ = self.filtering_possible_short_circuit(filtered_df)
        
        filtered_df, _ = self.pick_by_fpm_std(filtered_df, SIGMAS=3)
        filtered_df, _ = self.pick_by_speed_model(filtered_df, SIGMAS=2.8)
        # filtered_df, _ = self.pick_by_spent_fuel_std(filtered_df, SIGMAS=2.5)
        filtered_df, _ = self.pick_low_speed(filtered_df)
        filtered_df, _ = self.pick_boundary_spent_fuel(filtered_df)

        # filtered_df, _ = self.filtering_certains_ids(filtered_df, ["example_id"])

        true_leaks_df, _ = self.final_filter(filtered_df)
        logger.info(f"После фильтрации осталось {len(true_leaks_df)} записей")

        return true_leaks_df, filtered_df

    def leak_picker(self, result_df: pl.DataFrame):
        # Idempotent: initialize pick-tracking columns only once so that
        # re-entry via @with_param_filters doesn't wipe earlier picks.
        needs_init = "is_picked_leak" not in result_df.columns
        if needs_init:
            result_df = result_df.with_columns(
                [
                    pl.lit(False).alias("is_picked_leak"),
                    pl.lit("").alias("picked_by"),
                    pl.col("timestamp").shift(-1).alias("leak_end"),
                    pl.lit(-1).alias("leak_display"),
                    pl.lit(False).alias("filtered"),
                    pl.lit("").alias("filtered_reason"),
                ]
            )
        return result_df, result_df

    def pick_low_speed(
        self, result_df: pl.DataFrame, JITTER_FACTOR: float = 0.75, BARRIER_FUEL=8
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по низкой скорости"""
        result_df = self._tool_pick_leak(
            result_df,
            pl.col("fall_eligble").abs(),
            (pl.col("fall_eligble").abs().gt(BARRIER_FUEL)),
            "low_speed",
        )

        return result_df, result_df

    def filtering_ptime(
        self, result_df: pl.DataFrame, **kwargs
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        filtered_df = result_df.filter(pl.col("ptime") > 5)
        return filtered_df, result_df

    def filtering_nan(
        self, result_df: pl.DataFrame, **kwargs
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        filtering_df = result_df.filter(pl.col("leak").is_not_nan())
        return filtering_df, result_df

    def filter_last(self, result_df: pl.DataFrame):
        filtered_df = result_df.filter(pl.col("is_picked_leak").eq(True))
        return filtered_df, result_df

    def pick_boundary_spent_fuel(
        self, result_df: pl.DataFrame, HARD_LOSS_IN_BOUNDARY=15
    ):
        result_df = self._tool_pick_leak(
            result_df,
                pl.col("spent_fuel_boundary")
                .clip(upper_bound=0)
                .abs()
                .sub(pl.col("spent_fuel"))
            ,
            
                pl.col("spent_fuel_boundary")
                .clip(upper_bound=0)
                .abs()
                .sub(pl.col("spent_fuel"))
                .gt(HARD_LOSS_IN_BOUNDARY)
            ,
            FuelFilters.BOUNDARY.value,
        )
        result_df = result_df.with_columns(
            [
                pl.when(pl.col("picked_by").eq(FuelFilters.BOUNDARY.value))
                .then(
                    pl.col("spent_fuel_boundary")
                    .clip(upper_bound=0)
                    .abs()
                    .sub(pl.col("spent_fuel"))
                )
                .otherwise(pl.col("leak"))
                .alias("leak"),
            ]
        )

        return result_df, result_df
    
    def _tool_pick_filter(self,
        result_df: pl.DataFrame,
        expr: pl.Expr,
        filtered_name: str):
        return result_df.with_columns(
            pl.when(expr).then(True).otherwise(pl.col("filtered")).alias("filtered"),
            pl.when(expr).then(pl.lit(filtered_name)).otherwise(pl.col("filtered_reason")).alias("filtered_reason"),
        )
        

    def _tool_pick_leak(
        self,
        result_df: pl.DataFrame,
        target_expr_col: pl.Expr,
        expr: pl.Expr,
        picked_by_name: str,
    ):
        result_df = result_df.with_columns(
            [
                pl.when(expr)
                .then(True)
                .otherwise(pl.col("is_picked_leak"))
                .alias("is_picked_leak"),
                pl.when(expr & pl.col("is_picked_leak").eq(False))
                .then(pl.lit(picked_by_name))
                .otherwise(pl.col("picked_by"))
                .alias("picked_by"),
                pl.when(expr).then(target_expr_col).otherwise(pl.col("leak_display")).alias("leak_display"),
            ]
        )
        return result_df

    def pick_by_spent_fuel_std(
        self, result_df: pl.DataFrame, SIGMAS: float = 3.5
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по стандартному отклонению расхода"""
        # _, result_df = self.filtering_low_speed(result_df, 1)
        result_df = result_df.with_columns(
            [
                (
                    pl.col("spent_fuel")
                    > (
                        pl.col("norma_rasx_per_travel")
                        + pl.col("norma_std") * pl.lit(SIGMAS)
                    )
                ).alias("is_leak_sigma"),
                (
                    (pl.col("spent_fuel") - pl.col("norma_rasx_per_travel"))
                    / pl.col("norma_std")
                ).alias("z_values"),
            ]
        )
        result_df = self._tool_pick_leak(
            result_df, pl.col("leak"), pl.col("is_leak_sigma"), FuelFilters.SIGMA.value
        )
        return result_df, result_df

    def pick_by_fpm_std(
        self, result_df: pl.DataFrame, SIGMAS: float = 3, SF_M_BARRIER = 8, DTIME_BARRIER_MINUTES = 5
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по стандартному отклонению расхода (fpm -> spent_fuel)"""
        # Guard: if fpm_model_std is 0 (e.g. no training data), skip this filter
        # to avoid division-by-zero marking every row as a leak.
        if result_df["fpm_model_std"].max() == 0:
            logger.warning(
                "pick_by_fpm_std: fpm_model_std is 0 for all rows — skipping filter "
                "(would divide by zero and mark every row as a leak)"
            )
            result_df = result_df.with_columns(
                [
                    pl.lit(0).cast(pl.Float32).alias("z_values_fpm"),
                    pl.lit(0).cast(pl.Float32).alias("sf_m_predicted"),
                    pl.lit(0).cast(pl.Float32).alias("sf_m_diff"),
                    pl.lit(False).alias("is_spent_speed_legit"),
                    pl.lit(False).alias("is_leak_model_fpm"),
                ]
            )
            return result_df, result_df

        result_df = result_df.with_columns(
            [
                pl.col("fpm_model_predicted").mul(pl.col("dtime_moving").truediv(60)).alias("sf_m_predicted"),
                (
                    (pl.col("fpm") - pl.col("fpm_model_predicted") - pl.col("fpm_model_mean"))
                    / pl.col("fpm_model_std")
                ).alias("z_values_fpm"),
                pl.col("spent_fuel").mul(0.8).gt(pl.col("sf_m")).alias("is_spent_speed_legit"),
            ]
        )
        result_df = result_df.with_columns(
            [
            (pl.col("z_values_fpm").gt(SIGMAS) & pl.col("is_spent_speed_legit")).alias("is_leak_model_fpm"),
            pl.col("sf_m").sub(pl.col("sf_m_predicted")).clip(lower_bound=0).alias("sf_m_diff")
            ]
        )
        result_df = self._tool_pick_leak(
            result_df,
            pl.col("sf_m_diff"),
            pl.col("is_leak_model_fpm") & pl.col("sf_m_diff").gt(SF_M_BARRIER / 1.2),
            FuelFilters.SIGMA_FPM.value
        )
        return result_df, result_df


    def pick_by_rpm_model(
        self, result_df: pl.DataFrame, SIGMAS: float = 2.5
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        # нет вариации, передана машина без rpm
        if result_df["rpm_model_std"].max() == 0:
            result_df = result_df.with_columns(
                [
                pl.lit(False).alias("is_leak_model_rpm"),
                pl.lit(0).alias("z_values_rpm")
                ]
            )
        else:
            result_df = result_df.with_columns(
                [
                pl.col("spent_fuel")
                .gt(pl.col("rpm_model_predicted").add(pl.col("rpm_model_std").mul(SIGMAS)))
                .alias("is_leak_model_rpm"),
                pl.col("spent_fuel")
                .sub(pl.col("rpm_model_predicted"))
                .truediv(pl.col("rpm_model_std"))
                .alias("z_values_rpm"),
                ]
            )
            result_df = self._tool_pick_leak(
                result_df, pl.col("spent_fuel").sub("rpm_model_predicted").abs(), pl.col("is_leak_model_rpm"), FuelFilters.RPM_MODEL.value
            )

        return result_df, result_df

    def pick_by_speed_model(
        self, result_df: pl.DataFrame, SIGMAS: float = 3, BARRIER_FUEL = 8
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по линейной модели pos_s -> spent_fuel"""
        if result_df["speed_model_std"].max() == 0:
            result_df = result_df.with_columns(
                [
                    pl.lit(False).alias("is_leak_model_speed"),
                    pl.lit(0).cast(pl.Float32).alias("z_values_speed"),
                ]
            )
        else:
            result_df = result_df.with_columns(
                [
                    ( pl.col("spent_fuel")
                    .gt(pl.col("speed_model_predicted").add(pl.col("speed_model_std").mul(SIGMAS))) & pl.col("spent_fuel").gt(BARRIER_FUEL) )
                    .alias("is_leak_model_speed"),
                    pl.col("spent_fuel")
                    .sub(pl.col("speed_model_predicted"))
                    .truediv(pl.col("speed_model_std"))
                    .alias("z_values_speed"),
                ]
            )
            result_df = self._tool_pick_leak(
                result_df,
                pl.col("spent_fuel").sub("speed_model_predicted").clip(lower_bound=0),
                pl.col("is_leak_model_speed"),
                FuelFilters.SPEED_MODEL.value,
            )
        return result_df, result_df

    def filtering_standing_hard(
        self, result_df: pl.DataFrame, HARD_FUEL_FILTER: int = 10
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Жесткая фильтрация стоянок"""
        filtered_df = result_df.filter(
            pl.col("pos_s").ge(2.5)
            | (pl.col("pos_s").lt(2.5) & (pl.col("leak") > HARD_FUEL_FILTER))
        )
        return filtered_df, result_df

    def filtering_special_required(
        self, result_df: pl.DataFrame
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация специальных требований"""
        filtered_df = result_df.filter(
            ~pl.col("is_special_car")
            | (pl.col("is_special_car") & pl.col("ign_working"))
        )
        return filtered_df, result_df
    
    def filtering_possible_short_circuit(
        self, result_df: pl.DataFrame
    ):
        filtered_df = self._tool_pick_filter(result_df, pl.col("voltage_diff").lt(5), "short_curcuit")

        return filtered_df, result_df

    def filtering_sattelites(
        self, result_df: pl.DataFrame, SAT_AMOUNT: float = 0.65
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по спутникам"""
        result_df = result_df.with_columns(
            (1 - (pl.col("no_sat_data") / pl.col("count"))).alias("sat_coverage")
        )
        filtered_df = self._tool_pick_filter(result_df, pl.col("sat_coverage").lt(SAT_AMOUNT), "low_satellites")
        return filtered_df, result_df

    def filtering_count(
        self, result_df: pl.DataFrame, COUNT_VALUE: int = 12
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по количеству записей"""
        filtered_df = self._tool_pick_filter(result_df, pl.col("count").lt(COUNT_VALUE), "low_count")
        return filtered_df, result_df

    def filtering_certains_ids(
        self, result_df: pl.DataFrame, ids: List[str]
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация определенных ID"""
        filtered_df = result_df.filter(~pl.col("auto").is_in(ids))
        return filtered_df, result_df

    def filtering_remove_special_car(
        self, result_df: pl.DataFrame
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация специальных машин"""
        filtered_df = self._tool_pick_filter(result_df, pl.col("is_special_car"), "special_car")
        return filtered_df, result_df

    def filtering_high_load(
        self, result_df: pl.DataFrame, LOAD_RATIO: float = 0.75
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по высокой нагрузке"""
        filtered_df = self._tool_pick_filter(result_df, pl.col("load_ratio") < pl.lit(LOAD_RATIO), "load_ratio")
        return filtered_df, result_df

    def filtering_ratio(
        self, result_df: pl.DataFrame, RATIO_FOR_SPECIAL: float = 0.75
    ) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по соотношению"""
        result_df = result_df.with_columns(
            pl.when(pl.col("is_special_car"))
            .then(RATIO_FOR_SPECIAL)
            .otherwise(0)
            .alias("expected_ratio")
        )
        filtered_df = result_df.filter(
            pl.col("is_leak") & (pl.col("ratio") > pl.col("expected_ratio"))
        )
        return filtered_df, result_df