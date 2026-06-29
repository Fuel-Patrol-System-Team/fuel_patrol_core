import logging
import polars as pl
from abc import ABC, abstractmethod
from typing import Tuple

logger = logging.getLogger(__name__)


class BaseFilteringService(ABC):
    """Базовый класс для фильтрации результатов утечек"""

    @abstractmethod
    def apply_filters(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Применяет все фильтры к данным

        Args:
            df: DataFrame с результатами утечек

        Returns:
            Отфильтрованный DataFrame
        """
        pass

    @abstractmethod
    def pick_low_speed(self, df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по низкой скорости"""
        pass

    @abstractmethod
    def pick_by_spent_fuel_std(self, df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по стандартному отклонению расхода"""
        pass

    @abstractmethod
    def filtering_ptime(self, df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по ptime(сколько времени были данные)"""
        pass

    @abstractmethod
    def filtering_standing_hard(self, df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Жесткая фильтрация стоянок"""
        pass

    @abstractmethod
    def filtering_special_required(self, df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация специальных требований"""
        pass

    @abstractmethod
    def filtering_sattelites(self, df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по спутникам"""
        pass

    @abstractmethod
    def filtering_count(self, df: pl.DataFrame, **kwargs) -> Tuple[pl.DataFrame, pl.DataFrame]:
        """Фильтрация по количеству записей"""
        pass