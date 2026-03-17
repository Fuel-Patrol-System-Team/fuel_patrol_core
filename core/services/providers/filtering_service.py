import logging
import polars as pl
from typing import Tuple, List
from core.services.providers.filtering_base import BaseFilteringService

logger = logging.getLogger(__name__)


class FilteringService(BaseFilteringService):
    """Сервис для фильтрации результатов утечек"""

    def apply_filters(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Применяет все фильтры к данным
        """
        logger.info("Применение фильтров к результатам утечек")

        filtered_df = df


        filtered_df, _ = self.filtering_count(filtered_df, COUNT_VALUE=5)

        filtered_df, _ = self.filtering_ptime(filtered_df, )


        filtered_df, _ = self.filtering_special_required(filtered_df)


        filtered_df, _ = self.filtering_standing_hard(filtered_df)


        filtered_df, _ = self.filtering_sattelites(filtered_df)


        filtered_df, _ = self.filtering_spent_fuel_std(filtered_df, SIGMAS=2.5)

        filtered_df, _ = self.filtering_nan(filtered_df)

        # filtered_df, _ = self.filtering_certains_ids(filtered_df, ["example_id"])

        logger.info(f"После фильтрации осталось {len(filtered_df)} записей")
        return filtered_df

    def filtering_low_speed(self, result_df: pl.DataFrame, LOW_SPEED_FACTOR: int = 3) -> Tuple[
        pl.DataFrame, pl.DataFrame]:
        """Фильтрация по низкой скорости"""
        result_df = result_df.with_columns((pl.col("pos_s") <= LOW_SPEED_FACTOR).alias("low_speed"))
        filtered_df = result_df.filter(pl.col("low_speed") & pl.col("is_leak"))
        return filtered_df, result_df

    def filtering_ptime(self, result_df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        filtered_df = result_df.filter(pl.col("ptime") > 10)
        return filtered_df, result_df

    def filtering_nan(self, result_df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        filtering_df = result_df.filter(pl.col("leak").is_not_nan())
        return filtering_df, result_df

    def filtering_spent_fuel_std(self, result_df: pl.DataFrame, SIGMAS: float = 3.5) -> Tuple[
        pl.DataFrame, pl.DataFrame]:
        """Фильтрация по стандартному отклонению расхода"""
        _, result_df = self.filtering_low_speed(result_df, 1)
        result_df = result_df.with_columns([
            (pl.col("spent_fuel") > (pl.col("norma_rasx_per_travel") + pl.col("norma_std") * pl.lit(SIGMAS))).alias(
                "is_leak_sigma"),
            ((pl.col("spent_fuel") - pl.col("norma_rasx_per_travel")) / pl.col("norma_std")).alias("z_values"),
        ])
        filtered_df = result_df.filter(pl.col("is_leak_sigma") | pl.col("low_speed"))
        return filtered_df, result_df

    def filtering_standing_hard(self, result_df: pl.DataFrame, HARD_FUEL_FILTER: int = 15) -> Tuple[
        pl.DataFrame, pl.DataFrame]:
        """Жесткая фильтрация стоянок"""
        filtered_df = result_df.filter(
            pl.col("pos_s").ge(2.5) | (pl.col("pos_s").lt(2.5) & (pl.col("leak") > HARD_FUEL_FILTER))
        )
        return filtered_df, result_df

    def filtering_special_required(self, result_df: pl.DataFrame) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация специальных требований"""
        filtered_df = result_df.filter(~pl.col("is_special_car") )
        return filtered_df, result_df

    def filtering_sattelites(self, result_df: pl.DataFrame, SAT_AMOUNT: float = 0.65) -> Tuple[
        pl.DataFrame, pl.DataFrame]:
        """Фильтрация по спутникам"""
        result_df = result_df.with_columns((1 - (pl.col("no_sat_data") / pl.col("count"))).alias("sat_coverage"))
        filtered_df = result_df.filter(pl.col("sat_coverage") > SAT_AMOUNT)
        return filtered_df, result_df

    def filtering_count(self, result_df: pl.DataFrame, COUNT_VALUE: int = 20) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по количеству записей"""
        filtered_df = result_df.filter(pl.col("is_leak") & (pl.col("count") > COUNT_VALUE))
        return filtered_df, result_df

    def filtering_certains_ids(self, result_df: pl.DataFrame, ids: List[str]) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация определенных ID"""
        filtered_df = result_df.filter(~pl.col("auto").is_in(ids))
        return filtered_df, result_df

    def filtering_remove_special_car(self, result_df: pl.DataFrame) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация специальных машин"""
        filtered_df = result_df.filter(pl.col("is_special_car"))
        return filtered_df, result_df

    def filtering_high_load(self, result_df: pl.DataFrame, LOAD_RATIO: float = 0.75) -> Tuple[
        pl.DataFrame, pl.DataFrame]:
        """Фильтрация по высокой нагрузке"""
        filtered_df = result_df.filter(pl.col("load_ratio") < pl.lit(LOAD_RATIO))
        return filtered_df, result_df

    def filtering_ratio(self, result_df: pl.DataFrame, RATIO_FOR_SPECIAL: float = 0.75) -> Tuple[
        pl.DataFrame, pl.DataFrame]:
        """Фильтрация по соотношению"""
        result_df = result_df.with_columns(
            pl.when(pl.col("is_special_car")).then(RATIO_FOR_SPECIAL).otherwise(0).alias("expected_ratio")
        )
        filtered_df = result_df.filter(pl.col("is_leak") & (pl.col("ratio") > pl.col("expected_ratio")))
        return filtered_df, result_df