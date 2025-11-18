import logging
import polars as pl
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class BaseLeaksCalculator(ABC):
    """Базовый класс для расчета утечек топлива"""

    @abstractmethod
    def compute_leaks(
            self,
            auto_df: pl.DataFrame,
            data_df: pl.DataFrame,
            primary_df: pl.DataFrame,
            norma_df: pl.DataFrame,
            initial_df: Optional[pl.DataFrame] = None,
            is_save_bad_data: bool = False,
            is_filter_bad_data: bool = True
    ) -> Tuple[Optional[pl.DataFrame], Optional[pl.DataFrame]]:
        """
        Вычисляет утечки топлива

        Args:
            auto_df: Данные автомобилей
            data_df: Сырые данные
            primary_df: Первичные показатели
            norma_df: Нормы расхода
            initial_df: Начальные данные (опционально)
            is_save_bad_data: Сохранять ли плохие данные
            is_filter_bad_data: Фильтровать ли плохие данные

        Returns:
            Tuple с результатами и промежуточными данными
        """
        pass

    @abstractmethod
    def _preprocess_basic(
            self,
            df: pl.DataFrame,
            cars: Dict[str, Any],
            primary: Dict[str, Any],
            VOLTAGE_LIMIT: float,
            FUEL_JUMP_BARRIER_PERC: float,
            ANTI_BUG_TIME_SECONDS: int,
            REFUELING_LIMIT: int,
            PRE_PERIOD_TIME: int,
            DTIME_LIMIT: int
    ) -> pl.DataFrame:
        """Базовая предобработка данных"""
        pass

    @abstractmethod
    def _preprocess(
            self,
            df: pl.DataFrame,
            cars: Dict[str, Any],
            norms: Dict[str, Any],
            initial_df: Optional[pl.DataFrame],
            **kwargs
    ) -> Tuple[pl.DataFrame, pl.DataFrame, Optional[pl.DataFrame]]:
        """Основная предобработка данных"""
        pass

    @abstractmethod
    def _fuel_leak_calculate(
            self,
            df_values: pl.DataFrame,
            norma: Dict[str, Any],
            cars: Dict[str, Any],
            **kwargs
    ) -> Tuple[pl.DataFrame, Optional[pl.DataFrame]]:
        """Расчет утечек топлива"""
        pass