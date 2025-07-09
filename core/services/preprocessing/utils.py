import pandas as pd
import polars as pl
import numpy as np
import warnings
from datetime import datetime  # не нужен убери в production

warnings.filterwarnings("ignore")


def preprocess_measured(df: pd.DataFrame, VOLTAGE_LIMIT=.16, REFUELING_LIMIT=4000):
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='ignore')

    df = df[df['calc_sensors_fuel_level'].between(0, 4096)]

    df['auto'] = df['auto'].astype(str)

    df['dtime'] = df.groupby(['auto', df['timestamp'].dt.floor('1h')])['timestamp'].diff().abs()

    df['dtime_per_hour'] = df['dtime'].dt.total_seconds() / 3600

    df['voltage_max'] = df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq='1h')])[
        'calc_sensors_voltage'].transform(max)

    df = df[df['voltage_max'].sub(df['calc_sensors_voltage']).div(df["voltage_max"]).lt(VOLTAGE_LIMIT)]

    df['spent_fuel'] = df.groupby(['auto', df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(
        lambda x: x.diff())
    df['pos_a'] = df.groupby(['auto', df['timestamp'].dt.floor('2h')])['pos_s'].transform(lambda x: x.diff())
    df['spent_fuel_abs'] = df.groupby(['auto', df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(
        lambda x: x.diff().abs())

    df['spent_fuel'].mask(df['pos_a'].eq(0) & df['spent_fuel'].gt(0.0) & df['spent_fuel'].lt(REFUELING_LIMIT), np.nan,
                          inplace=True)
    # pos_a для дополнительных рассчетов по поводу заправки
    df['pos_a'] = np.where(df['pos_a'] == np.nan, df['pos_s'], df['pos_a'])
    df['spent_fuel'].replace(np.nan, 0, inplace=True)
    df['spent_fuel_abs'].replace(np.nan, 0, inplace=True)
    df['calc_sensors_fuel_level'] = df['calc_sensors_fuel_level'].div(df["input"]).mul(df["output"])
    df['calc_sensors_fuel_level'] = df['calc_sensors_fuel_level'].div(df["input"]).mul(df["output"])
    return df


def preprocess(df: pd.DataFrame, ANTI_BUG_TIME_SECONDS=10, PRE_PERIOD_TIME = 3, PERIOD_2_MIN = 30, VOLTAGE_LIMIT = .16, REFUELING_LIMIT = 4000, FUEL_JUMP_BARRIER = 100, AMTR_IGNORE_LIMIT = 3, RPM_DRIVING_VALUE = 20, is_debug = False) -> pd.DataFrame:
    # новые колонки
    if 'amtr_x' not in df:
        df['amtr_x'] = 0
    if 'amtr_y' not in df:
        df['amtr_y'] = 0
    if 'amtr_z' not in df:
        df['amtr_z'] = 0 
    if 'rpm' not in df:
        df['rpm'] = 65535
    
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='ignore')

    df = df[df['calc_sensors_fuel_level'].between(0, 4096)]

    df['auto'] = df['auto'].astype(str)

    df['dtime'] = df.groupby(['auto', df['timestamp'].dt.floor('1h')])['timestamp'].diff().abs()

    df['dtime_per_hour'] = df['dtime'].dt.total_seconds() / 3600

    df['voltage_max'] = df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq='1h')])['calc_sensors_voltage'].transform(max)
    
    df = df[df['voltage_max'].sub(df['calc_sensors_voltage']).div(df["voltage_max"]).lt(VOLTAGE_LIMIT)]
    
    df['spent_fuel'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(lambda x: x.diff())
    df['pos_a'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['pos_s'].transform(lambda x: x.diff())
    df['spent_fuel_abs'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(lambda x: x.diff().abs())
    
    df['fuel_recover'] = np.where(df['spent_fuel'].ge(0), df['spent_fuel'], 0)
    df['fuel_recover_span'] = np.where(df['fuel_recover'].gt(0) & df['pos_s'].eq(0), 1, -1)
    # pos_a для дополнительных рассчетов по поводу заправки
    df['pos_a'] = np.where(df['pos_a'] == np.nan, df['pos_s'], df['pos_a'])
    df['spent_fuel'].replace(np.nan, 0, inplace=True)
    df['jumps'] = np.where(df['spent_fuel'].abs().gt(FUEL_JUMP_BARRIER), 1, 0)
    df['amtr'] = df['amtr_x'].abs().add(df['amtr_y'].abs()).add(df['amtr_z'].abs())
    df['fd'] = np.where(df['spent_fuel'].lt(0), 1, 0)
    anti_bug_aggregation = df.groupby(
        ['auto', df['timestamp'].dt.floor(f'{ANTI_BUG_TIME_SECONDS}s')]
    ).agg({
        'calc_sensors_fuel_level': 'median',
        'pos_s': 'mean',
        'spent_fuel': 'sum',
        'dtime': 'sum',
        'dtime_per_hour': 'sum',
        'pos_a': 'count', # to count stuff
        'fuel_recover': 'sum',
        'fuel_recover_span': "sum",
        "jumps": "sum",
        "amtr": "sum",
        "rpm": "mean",
        "fd": "sum"
    }).reset_index()

    anti_bug_aggregation = anti_bug_aggregation.rename(columns={"pos_a": "count"})
    anti_bug_aggregation['spent_fuel'].mask(df['pos_a'].eq(0) & df['spent_fuel'].gt(0.0) & df['spent_fuel'].lt(REFUELING_LIMIT), np.nan, inplace=True)
    # return anti_bug_aggregation
    pre_period_df = anti_bug_aggregation.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')]).agg({
        'pos_s': 'median',
        'spent_fuel': 'sum',
        'dtime': 'sum',
        'dtime_per_hour': 'sum',
        "count": "sum",
        "fuel_recover": "sum",
        "fuel_recover_span": "sum",
        "jumps": "sum",
        "amtr": "sum",
        "rpm": "mean",
        "fd": "sum"
    }).reset_index()
    
    pre_period_df['spent_fuel'].mask(pre_period_df['pos_s'].eq(0) & pre_period_df['spent_fuel'].gt(0.0) & pre_period_df['spent_fuel'].lt(REFUELING_LIMIT), 0, inplace=True)
    pre_period_df['spent_fuel'].mask(pre_period_df['pos_s'].eq(0) & pre_period_df['spent_fuel'].lt(0.0) & pre_period_df['amtr'].ge(AMTR_IGNORE_LIMIT), 0, inplace=True)
    pre_period_df['fuel_recover_rate_value'] = pre_period_df['fuel_recover'].mul(pre_period_df['fuel_recover_span'])
    pre_period_df['travel'] = pre_period_df['pos_s'].mul(pre_period_df['dtime_per_hour'])

    
    period_1_df = pre_period_df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PERIOD_2_MIN}min')]).agg( {
        'pos_s': 'mean',
        'spent_fuel': 'sum',
        'dtime': 'sum',
        'dtime_per_hour': 'sum',
        "travel": "sum",
        "count": "sum",
        "amtr": "sum",
        "jumps": "sum",
        "fd": "sum"
    } ).reset_index()
    
    period_1_df['spent_per_100'] = period_1_df['spent_fuel'].div(period_1_df['travel']).mul(100)
    if is_debug:
        return (df, pre_period_df, period_1_df)
    else:
        return period_1_df


def merge(car_data: pd.DataFrame, preprocessed_df: pd.DataFrame):
    result_df = preprocessed_df.merge(right=car_data, how='inner', left_on='auto', right_on='id')
    # опустим касты к numeric, надеясь что прокатит
    return result_df


#TODO:save_bad_data
def fuel_leak_calculate_standart(df_values: pd.DataFrame, norma_rasx_df: pd.DataFrame, FUEL_PURITY_VALUE = 12, DECREASE_INCREASE_RATIO = 0.75, LEAK_LIMIT = 12, SIGMA_LIMIT = 3.5, MULT_STD_MEAN_DIFF = 2.25, FUEL_JUMPS_AMOUNT = 30, LEAK_FACTOR = 0.05, SMALL_SPEED_FACTOR = 2.25, UNREASONABLE_FUEL_LEVEL = 1000 , UNTARIFF_LEVEL = 400, is_save_bad_data = False):
    df_values['is_bad_data_count'] = df_values['count'].le(5)
    df_values['is_bad_data_jitter'] = df_values['jumps'].gt(FUEL_JUMPS_AMOUNT)
    
    df_values['spent_fuel'] = df_values['spent_fuel'].mask(df_values['spent_fuel'].ge(0), other=0)
    df_values['spent_fuel'] = df_values['spent_fuel'].abs()

    df_values['is_bad_data_unreasonable_fuel'] = df_values['spent_fuel'].gt(UNREASONABLE_FUEL_LEVEL)

    df_values['is_bad_data'] = df_values['is_bad_data_count'].eq(True) | df_values['is_bad_data_jitter'].eq(True) | df_values['is_bad_data_unreasonable_fuel'].eq(True)
    
    df_values['ratio'] = df_values['fd'].div(df_values['count'])
    bad_data = None
    if is_save_bad_data == True:
        bad_data = df_values[df_values['is_bad_data'].eq(True)]
        bad_data['reason'] = np.where(bad_data['is_bad_data_count'].eq(True), "Слишком малое число записей", "")
        bad_data['reason'] = np.where(bad_data['is_bad_data_jitter'].eq(True), "Скачки уровня топлива", bad_data['reason'])
        bad_data['reason'] = np.where(bad_data['is_bad_data_unreasonable_fuel'].eq(True), "Невозможный уровень расхода топлива", bad_data['reason'])
    df_values = df_values[df_values['is_bad_data'].eq(False)]
    # РАСЧЕТЫ

    # тарирование
    df_values['spent_fuel'] = df_values['spent_fuel'].div(df_values['input']).mul(df_values['output'])

    # остальные скучные вычисления
    df_values['spent_per_100'] = df_values['spent_fuel'].mul(100).div(df_values['travel'])

    df_values['timestamp'] = pd.to_datetime(df_values['timestamp'])

    season_result = df_values.merge(norma_rasx_df, how='inner', left_on='auto', right_on='sl_avto')
    season_result['norma_rasx'] = np.where(
        season_result['timestamp'].dt.month.between(3, 10),
        season_result['norma_rasx_summer'],
        season_result['norma_rasx_winter']
    )

    season_result['norma_rasx_per_travel'] = season_result['norma_rasx'].mul(season_result['travel']).div(100)

    season_result['norma_rasx_per_travel'] = season_result['norma_rasx_per_travel'].mul(
        season_result['pos_s'].div(season_result['speed_etalon']))

    season_result['is_leak'] = np.select([season_result['spent_fuel'].gt(season_result['norma_rasx_per_travel']),
                                          season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17 / 6) &
                                          season_result['sl_tip_dvigat'].eq(0),
                                          season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1 / 6) &
                                          season_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)

    season_result['leak'] = np.select(
        [
            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17 / 6) & season_result[
                'sl_tip_dvigat'].eq(0),
            season_result['travel'].eq(0) & season_result['spent_fuel'].ge(1.17 / 6) & season_result[
                'sl_tip_dvigat'].eq(1),
        ],
        [
            season_result['spent_fuel'].sub(1.17 / 6).abs().apply(lambda x: max(x, 0)),
            season_result['spent_fuel'].sub(1 / 6).abs().apply(lambda x: max(x, 0))
        ], default=season_result['spent_fuel'].sub(season_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

    season_result['leak_factor'] = LEAK_LIMIT
    season_result['leak_factor'] = season_result['leak_factor'].mask(season_result['input'].eq(season_result['output']),
                                                                     season_result['max_fuel'].mul(LEAK_FACTOR))
    season_result['untariffed'] = season_result['input'].eq(season_result['output'])
    season_result['is_leak'] = season_result['leak'].gt(season_result['leak_factor'])
    season_result['timestamp'] = pd.to_datetime(season_result['timestamp'], errors='ignore')
    #
    season_result['leak_to_volume'] = season_result['leak'].div(season_result['max_fuel'])
    season_result['spent_per_volume'] = season_result['leak'].div(season_result['max_fuel'])
    season_result['is_leak'] = season_result['untariffed'].eq(False) & season_result['leak'].gt(LEAK_LIMIT) | (
                season_result['untariffed'].eq(True) & season_result['leak'].gt(UNTARIFF_LEVEL))

    # season_result['delta_sp'] = season_result['spent_fuel'].sub(season_result['norma_rasx_per_travel'])
    # season_result['delta_sp_median'] = season_result.groupby([pd.Grouper(key='id')])['delta_sp'].transform('median')
    # season_result['delta_sp_std'] = season_result.groupby([pd.Grouper(key='id')])['delta_sp'].transform('std')
    # season_result['is_leak_delta_sp'] = season_result['delta_sp'].ge(season_result['delta_sp_median'].add(season_result['delta_sp_std'].mul(SIGMA_LIMIT))) & season_result['leak'].ge(LEAK_LIMIT)
    # season_result['max_fuel_diff'] = season_result.groupby([pd.Grouper('id'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff().shift(-1)
    # season_result['max_fuel_diff_back'] = season_result.groupby([pd.Grouper('id'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff()
    season_result['leak_mean'] = season_result.groupby(by="auto")['leak'].transform("mean")
    season_result['leak_median'] = season_result.groupby(by="auto")['leak'].transform("median")
    season_result['leak_std'] = season_result.groupby(by="auto")['leak'].transform("std")
    season_result['leak_std'].where(season_result['leak_std'].gt(season_result['leak_median'].mul(MULT_STD_MEAN_DIFF)),
                                    other=season_result['leak_std'], inplace=True)
    season_result['is_leak_2'] = season_result['leak_mean'].add(season_result['leak_std'].mul(SIGMA_LIMIT)).le(
        season_result['leak'])
    # season_result['max_fuel_diff'].replace(to_replace=np.nan, value=0, inplace=True)

    # season_result['is_max_fuel_diff_2'] = season_result['max_fuel_diff'].lt(0) & ( season_result['leak'].le(-season_result['max_fuel_diff']) )
    # season_result['is_max_fuel_diff'] = season_result['max_fuel_diff'].lt(0) & season_result['max_fuel_diff_back'].lt(0)

    season_result['low_speed'] = season_result['pos_s'].le(season_result['speed_etalon'].div(SMALL_SPEED_FACTOR))
    season_result['is_leak'] = season_result['low_speed'].eq(True) & season_result['is_leak'].eq(True)
    season_result['expected_purity'] = season_result['ratio'].gt((FUEL_PURITY_VALUE / season_result['spent_fuel']).clip(0, 1))
    season_result['is_leak'] = season_result['is_leak'] & season_result['ratio'].ge(season_result['expected_purity'])
    if is_save_bad_data:
        return (season_result, bad_data)

    return (season_result, None)



def merge_everything(results: list):
    return pd.concat(results)


# нельзя разделять
def calculate_default_speed(df: pd.DataFrame, HIGH_SPEED_DEFAULT_ETALON=60):
    speeds = df.groupby(by="auto")['pos_s'].max().reset_index()
    speeds['pos_s'] = speeds['pos_s'].div(2)
    speeds['pos_s'] = speeds['pos_s'].where(speeds.lt(HIGH_SPEED_DEFAULT_ETALON), other=HIGH_SPEED_DEFAULT_ETALON)
    return speeds


def preprocess_influx(df: pd.DataFrame, ANTI_BUG_TIME_SECONDS=10, PRE_PERIOD_TIME=2, PERIOD_2_MIN=10,
                      VOLTAGE_LIMIT=4) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter(action="ignore")
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='ignore')

        df = df[df['calc_sensors_fuel_level'].between(0, 4096)]

        df['auto'] = df['auto'].astype(str)

        df['dtime'] = df.groupby(['auto', df['timestamp'].dt.floor('1h')])['timestamp'].diff().abs()

        df['dtime_per_hour'] = df['dtime'].dt.total_seconds() / 3600

        df['voltage_max'] = df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq='1h')])[
            'calc_sensors_voltage'].transform(max)

        df = df[df['voltage_max'].sub(df['calc_sensors_voltage']).lt(VOLTAGE_LIMIT)]

        df['spent_fuel'] = df.groupby(['auto', df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(
            lambda x: x.diff())

        anti_bug_aggregation = df.groupby(
            ['auto', df['timestamp'].dt.floor(f'{ANTI_BUG_TIME_SECONDS}s')]
        ).agg({
            'calc_sensors_fuel_level': 'median',
            'pos_s': 'mean',
            'spent_fuel': 'sum',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        }).reset_index()

        anti_bug_aggregation['max_fuel'] = anti_bug_aggregation.groupby(
            [pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')])[
            'calc_sensors_fuel_level'].transform('max')

        pre_period_df = anti_bug_aggregation.groupby(
            [pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')]).agg({
            'pos_s': 'median',
            'spent_fuel': 'sum',
            'max_fuel': 'max',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        }).reset_index()

        pre_period_df['spent_fuel'].mask(
            pre_period_df['pos_s'].eq(0) & pre_period_df['spent_fuel'].gt(0.0) & pre_period_df['spent_fuel'].lt(8),
            np.nan, inplace=True)

        pre_period_df['spent_fuel'].replace(np.nan, 0, inplace=True)

        period_1_df = pre_period_df.groupby(
            [pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PERIOD_2_MIN}min')]).agg({

            'pos_s': 'mean',
            'spent_fuel': 'sum',
            'max_fuel': 'max',
            'dtime': 'sum',
            'dtime_per_hour': 'sum',
        }).reset_index()
        period_1_df = period_1_df[period_1_df.columns.difference(['dtime', 'dtime_per_hour'])]
        return period_1_df
