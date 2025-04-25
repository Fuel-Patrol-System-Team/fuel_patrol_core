import pandas as pd
import numpy as np
import polars as pl
import warnings



###
###
###
###
###
def preprocess(df: pd.DataFrame, ANTI_BUG_TIME_SECONDS=10, PRE_PERIOD_TIME = 3, PERIOD_2_MIN = 30, VOLTAGE_LIMIT = 4) -> pd.DataFrame:
    """ Функция для препроцессинга
    
    Получает датафрейм, вычислеяет расход топлива между записями, максимальный уровень напряжения и агрегриует данные по ANTI_BUG_TIME_SECONDS, отбрасывая лишние данные, в которых замечается резкое падение уровня напряжения. Пополнение уровня топлива при отсутствии движения считается заправкой, и заменяется нулями, таким образом оставляя только данные повышение и паденмя уровня топлива. Показатель max_fuel используется для последующего опеределения колебания, берется максимальный уровень топлива за период. Рассчитывает пройденное расстояние, используя время между пакетами и перемножая на скорость.
    
    
    Parameters
    ----------
    df : pd.DataFrame
        Датафрейм
    ANTI_BUG_TIME_SECONDS : int
        Время первого периода агрегации
    PRE_PERIOD_TIME : int
        Время второгоо периода агрекации
    PERIOD_2_MIN : int
        Время последнего периода агрегации
    VOLTAGE_LIMIT : int
        Лимит разницы между максимальным и обычным напряжением, если превышен, то данные отрасываются
    """
    with warnings.catch_warnings():
        warnings.simplefilter(action="ignore")
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='ignore')

        df = df[df['calc_sensors_fuel_level'].between(0, 4096)]

        df['auto'] = df['auto'].astype(str)

        df['dtime'] = df.groupby(['auto', df['timestamp'].dt.floor('1h')])['timestamp'].diff().abs()

        df['dtime_per_hour'] = df['dtime'].dt.total_seconds() / 3600

        df['voltage_max'] = df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq='1h')])['calc_sensors_voltage'].transform(max)
        
        df = df[df['voltage_max'].sub(df['calc_sensors_voltage']).lt(VOLTAGE_LIMIT)]
        
        df['spent_fuel'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(lambda x: x.diff())
        
        anti_bug_aggregation = df.groupby(
            ['auto', df['timestamp'].dt.floor(f'{ANTI_BUG_TIME_SECONDS}s')]
        ).agg({
            'calc_sensors_fuel_level': 'median',
            'pos_s': 'mean',
            'spent_fuel': 'sum',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        }).reset_index()
        
        anti_bug_aggregation['max_fuel'] = anti_bug_aggregation.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')])['calc_sensors_fuel_level'].transform('max')
        
        pre_period_df = anti_bug_aggregation.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')]).agg({
            'pos_s': 'median',
            'spent_fuel': 'sum',
            'max_fuel': 'max',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        }).reset_index()
        
        pre_period_df['spent_fuel'].mask(pre_period_df['pos_s'].eq(0) & pre_period_df['spent_fuel'].gt(0.0) & pre_period_df['spent_fuel'].lt(8), np.nan, inplace=True)

        pre_period_df['spent_fuel'].replace(np.nan, 0, inplace=True)
        
        period_1_df = pre_period_df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PERIOD_2_MIN}min')]).agg( {
            
            'pos_s': 'mean',
            'spent_fuel': 'sum',
            'max_fuel': 'max',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        } ).reset_index()
        
        period_1_df['travel'] = period_1_df['pos_s'].mul(period_1_df['dtime_per_hour'])
        period_1_df['spent_per_100'] = period_1_df['spent_fuel'].div(period_1_df['travel']).mul(100)
        
        return period_1_df

def merge(car_data: pd.DataFrame, preprocessed_df: pd.DataFrame):
    """ Функция для объединения данных машин и данных после препроцессинга
    
    Получает данные машин и данные препроцессинга и делает inner join для машин  и данных препроцессинга, датафреймы должны иметь следующие поля
    
    preprocessed_df:
        timestamp
        pos_s
        spent_fuel
        max_fuel
        dtime
        dtime_per_hour
        travel
        spent_per_100
        max_fuel
    car_data:
        name
        description
    
    Parameters
    ----------
    df : pd.DataFrame
        Датафрейм
    ANTI_BUG_TIME_SECONDS : int
        Время первого периода агрегации
    PRE_PERIOD_TIME : int
        Время второгоо периода агрекации
    PERIOD_2_MIN : int
        Время последнего периода агрегации
    VOLTAGE_LIMIT : int
        Лимит разницы между максимальным и обычным напряжением, если превышен, то данные отрасываются
    """
    result_df = preprocessed_df.merge(right=car_data, how='inner', left_on='auto', right_on='id')
    # опустим касты к numeric, надеясь что прокатит
    return result_df


# принимает пару median и std для данной машины
def fuel_leak_calculate_standart(df_values: pd.DataFrame, norma_rasx_df: pd.DataFrame, LEAK_LIMIT = 9, SIGMA_LIMIT = 3, SPEED_ETALON = 60):
    """Функция для рассчета сливов по объединенным данным
    
    Функция для рассчета объединенных данных. Для начала отсекает записи, в которых было только повышение топлива за период времени, далее рассчитывает предполагаемый расход на 100 километов. После используя специальную форму делает поправку этого расхода на скорость автомобиля. Далее сравнивает с полученными показателями. Имеет две метрики.Первая определяет, что уровень топлива превышает предполагаемый расхода. Вторая метрика смотрит превышает ли машина средний средний расход среди других записей. Далее идет поиск на колебания, если уровень топлива поднялся, (а так как мы исключили заправка, поднятся он может только из-за колебаний) мы помечаем эту запись как колебание.
    
    Parameters
    ----------
    df_values : pd.DataFrame
        датафрейм с объединенными данными
    norma_rasx_df : pd.DataFrame
        датафрейм с нормами расхода
    LEAK_LIMIT : int
        если слив меньше этого значения, этот слив отсекается
    SIGMA_LIMIT: int
        уровень отклонения от среднего в разнице между уровнем расхода и полученным значением в сигмах
    SPEED_ETALON: int
        эталонная скорость чем выше она, тем сильнее алгоритм будет строже к машинам с низкой скоростью
    
    """
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore')

        df_values['spent_fuel'].mask(df_values['spent_fuel'].lt(0), other=0, inplace=True)
        df_values['spent_fuel'] = df_values['spent_fuel'].abs()


        df_values['spent_per_100'] = df_values['spent_fuel'].mul(100).div(df_values['travel'])

        df_values['timestamp'] = pd.to_datetime(df_values['timestamp'])

        season_result = df_values.merge(norma_rasx_df, how='inner', left_on='auto', right_on='sl_avto')
        season_result['norma_rasx'] = np.where(
            season_result['timestamp'].dt.month.between(3, 10),
            season_result['norma_rasx_summer'],
            season_result['norma_rasx_winter']
        )
        season_result['norma_rasx_per_travel'] = season_result['norma_rasx'].mul(season_result['travel']).div(100)

        season_result['norma_rasx_per_travel'] = season_result['norma_rasx_per_travel'].mul(season_result['pos_s'].div(SPEED_ETALON).pow(3))
        season_result['is_leak'] = np.select([season_result['spent_fuel'].gt(season_result['norma_rasx_per_travel']),season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17/6) & season_result['sl_tip_dvigat'].eq(0),
                                            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1/6) & season_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)
        season_result['leak'] = np.select(
            [
            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17/6) & season_result['sl_tip_dvigat'].eq(0),
            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17/6) & season_result['sl_tip_dvigat'].eq(1),
            ],
            [
                season_result['spent_fuel'].sub(1.17/6).abs().apply(lambda x: max(x, 0)),
                season_result['spent_fuel'].sub(1/6).abs().apply(lambda x: max(x, 0))
            ], default=season_result['spent_fuel'].sub(season_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

        season_result['is_leak'] = season_result['leak'].gt(LEAK_LIMIT)
        season_result['timestamp'] = pd.to_datetime(season_result['timestamp'], errors='ignore')

        season_result['delta_sp'] = season_result['spent_fuel'].sub(season_result['norma_rasx_per_travel'])
        season_result['delta_sp_median'] = season_result.groupby([pd.Grouper(key='id')])['delta_sp'].transform('median')
        season_result['delta_sp_std'] = season_result.groupby([pd.Grouper(key='id')])['delta_sp'].transform('std')
        season_result['is_leak_delta_sp'] = season_result['delta_sp'].ge(season_result['delta_sp_median'].add(season_result['delta_sp_std'].mul(SIGMA_LIMIT))) & season_result['leak'].ge(LEAK_LIMIT)
        season_result['max_fuel_diff'] = season_result.groupby([pd.Grouper('id'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff().shift(-1)
        season_result['max_fuel_diff_back'] = season_result.groupby([pd.Grouper('id'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff()

        season_result['max_fuel_diff'].replace(to_replace=np.nan, value=0, inplace=True)

        season_result['is_max_fuel_diff_2'] = season_result['max_fuel_diff'].lt(0) & ( season_result['leak'].le(-season_result['max_fuel_diff']) )
        season_result['is_max_fuel_diff'] = season_result['max_fuel_diff'].lt(0) & season_result['max_fuel_diff_back'].lt(0)
        season_result['is_leak'] = season_result['is_leak'] & season_result['is_leak_delta_sp']

        return season_result


def fuel_leak_calculate_tricky(df_values: pd.DataFrame, norma_rasx_df: pd.DataFrame, SIGMA_VALUE, LEAK_LIMIT = 9, SIGMA_LIMIT = 3, SPEED_ETALON = 60):
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore') 
        df_values['spent_fuel'].mask(df_values['spent_fuel'].gt(0), other=0, inplace=True)
        df_values['spent_fuel'] = df_values['spent_fuel'].abs()
        df_values['spent_per_100'] = df_values['spent_fuel'].mul(100).div(df_values['travel'])
        df_values['timestamp'] = pd.to_datetime(df_values['timestamp'])

        season_result = df_values.merge(norma_rasx_df, how='inner', left_on='auto', right_on='sl_avto')
        season_result['current_norma'] = np.where(
            season_result['timestamp'].dt.month.between(3, 10),
            season_result['norma_rasx_summer'],
            season_result['norma_rasx_winter']
        )

        season_result['norma_rasx_per_travel'] = season_result['norma_rasx'].mul(season_result['travel']).div(100)


        season_result['norma_rasx_per_travel'] = season_result['norma_rasx_per_travel'].mul(season_result['pos_s'].div(SPEED_ETALON).pow(3))


        season_result['is_leak'] = np.select([season_result['spent_fuel'].gt(season_result['norma_rasx_per_travel']),season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17/6) & season_result['sl_tip_dvigat'].eq(0),
                                            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1/6) & season_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)


        season_result['leak'] = np.select(
            [
            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17/6) & season_result['sl_tip_dvigat'].eq(0),
            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17/6) & season_result['sl_tip_dvigat'].eq(1),
            ],
            [
                season_result['spent_fuel'].sub(1.17/6).abs().apply(lambda x: max(x, 0)),
                season_result['spent_fuel'].sub(1/6).abs().apply(lambda x: max(x, 0))
            ], default=season_result['spent_fuel'].sub(season_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

        season_result.dropna(subset=['sl_tip_dvigat'], inplace=True)
        season_result['is_leak'] = season_result['leak'].gt(LEAK_LIMIT)
        season_result['timestamp'] = pd.to_datetime(season_result['timestamp'], errors='ignore')

        season_result['max_fuel_diff'] = season_result.groupby([pd.Grouper('id'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff().shift(-1)
        season_result['max_fuel_diff_back'] = season_result.groupby([pd.Grouper('id'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff()

        season_result['max_fuel_diff'].replace(to_replace=np.nan, value=0, inplace=True)

        season_result['is_max_fuel_diff_2'] = season_result['max_fuel_diff'].lt(0) & ( season_result['leak'].le(-season_result['max_fuel_diff']) )
        season_result['is_max_fuel_diff'] = season_result['max_fuel_diff'].lt(0) & season_result['max_fuel_diff_back'].lt(0)

        return season_result


def compute_leaks_chunk(auto_df: pd.DataFrame, data_df: pl.DataFrame, norma_df: pd.DataFrame):
    result_df = fuel_leak_calculate_standart(merge(auto_df, preprocess(data_df)), norma_rasx_df=norma_df)
    return result_df
# TODO: перепроверить логику расчетов, убрать расчет лишних полей, если такие расчеты есть
def preprocess_influx(df: pd.DataFrame, ANTI_BUG_TIME_SECONDS=10, PRE_PERIOD_TIME = 2, PERIOD_2_MIN = 10, VOLTAGE_LIMIT = 4) -> pd.DataFrame:
    """Функция для очистки данных, для построения графиков
    
    Функция для очистки данных, которые далее используются для построения графиков, аналогично функционалу предоставляемому препроцессингом, за исключением более мелкого периода агрегации, аналогичным образом отсеивает неправильные данные.
    
    Parameters
    ----------
    df : pd.DataFrame
        Датафрейм
    ANTI_BUG_TIME_SECONDS : int
        Время первого периода агрегации
    PRE_PERIOD_TIME : int
        Время второгоо периода агрекации
    PERIOD_2_MIN : int
        Время последнего периода агрегации
    VOLTAGE_LIMIT : int
        Лимит разницы между максимальным и обычным напряжением, если превышен, то данные отрасываются
    """
    with warnings.catch_warnings():
        warnings.simplefilter(action="ignore")
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='ignore')

        df = df[df['calc_sensors_fuel_level'].between(0, 4096)]

        df['auto'] = df['auto'].astype(str)

        df['dtime'] = df.groupby(['auto', df['timestamp'].dt.floor('1h')])['timestamp'].diff().abs()

        df['dtime_per_hour'] = df['dtime'].dt.total_seconds() / 3600

        df['voltage_max'] = df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq='1h')])['calc_sensors_voltage'].transform(max)
        
        df = df[df['voltage_max'].sub(df['calc_sensors_voltage']).lt(VOLTAGE_LIMIT)]
        
        df['spent_fuel'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(lambda x: x.diff())
        
        anti_bug_aggregation = df.groupby(
            ['auto', df['timestamp'].dt.floor(f'{ANTI_BUG_TIME_SECONDS}s')]
        ).agg({
            'calc_sensors_fuel_level': 'median',
            'pos_s': 'mean',
            'spent_fuel': 'sum',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        }).reset_index()
        
        anti_bug_aggregation['max_fuel'] = anti_bug_aggregation.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')])['calc_sensors_fuel_level'].transform('max')
        
        pre_period_df = anti_bug_aggregation.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')]).agg({
            'pos_s': 'median',
            'spent_fuel': 'sum',
            'max_fuel': 'max',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        }).reset_index()
        
        pre_period_df['spent_fuel'].mask(pre_period_df['pos_s'].eq(0) & pre_period_df['spent_fuel'].gt(0.0) & pre_period_df['spent_fuel'].lt(8), np.nan, inplace=True)

        pre_period_df['spent_fuel'].replace(np.nan, 0, inplace=True)
        
        period_1_df = pre_period_df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PERIOD_2_MIN}min')]).agg( {
            
            'pos_s': 'mean',
            'spent_fuel': 'sum',
            'max_fuel': 'max',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        } ).reset_index()
        period_1_df = period_1_df[period_1_df.columns.difference(['dtime', 'dtime_per_hour'])]
        return period_1_df