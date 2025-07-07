import pandas as pd
import numpy as np

from datetime import datetime, timedelta

def preprocess(df: pd.DataFrame, ANTI_BUG_TIME_SECONDS=10, PRE_PERIOD_TIME = 3, PERIOD_2_MIN = 30, VOLTAGE_LIMIT = .16, REFUELING_LIMIT = 4000, is_debug = False) -> pd.DataFrame:
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='ignore')

    df = df[df['calc_sensors_fuel_level'].between(0, 4096)]

    df['auto'] = df['auto'].astype(str)

    df['dtime'] = df.groupby(['auto', df['timestamp'].dt.floor('1h')])['timestamp'].diff().abs()

    df['dtime_per_hour'] = df['dtime'].dt.total_seconds() / 3600

    df['voltage_max'] = df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq='1h')])['calc_sensors_voltage'].transform(max)
    
    df['max_speed'] = df['pos_s']
    df = df[df['voltage_max'].sub(df['calc_sensors_voltage']).div(df["voltage_max"]).lt(VOLTAGE_LIMIT)]
    
    df['spent_fuel'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(lambda x: x.diff())
    df['pos_a'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['pos_s'].transform(lambda x: x.diff())
    df['spent_fuel_abs'] = df.groupby(['auto',df['timestamp'].dt.floor('2h')])['calc_sensors_fuel_level'].transform(lambda x: x.diff().abs())
    
    df['spent_fuel'].mask(df['pos_a'].eq(0) & df['spent_fuel'].gt(0.0) & df['spent_fuel'].lt(REFUELING_LIMIT), np.nan, inplace=True)
    # pos_a для дополнительных рассчетов по поводу заправки
    df['pos_a'] = np.where(df['pos_a'] == np.nan, df['pos_s'], df['pos_a'])
    df['spent_fuel'].replace(np.nan, 0, inplace=True)
    df['spent_fuel_abs'].replace(np.nan, 0, inplace=True)
    pre_period_df = df.groupby(
        ['auto', df['timestamp'].dt.floor(f'{ANTI_BUG_TIME_SECONDS}s')]
    ).agg({
        'calc_sensors_fuel_level': 'median',
        'pos_s': 'mean',
        'spent_fuel': 'sum',
        'dtime': 'sum',
        'dtime_per_hour': 'sum',
        'spent_fuel_abs': 'sum',
        'max_speed': 'max'
    }).reset_index()
    
    pre_period_df['max_fuel'] = pre_period_df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')])['calc_sensors_fuel_level'].transform('max')
    
    pre_period_df = pre_period_df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PRE_PERIOD_TIME}min')]).agg({
        'pos_s': 'median',
        'spent_fuel': 'sum',
        'max_fuel': 'max',
        'dtime': 'sum',
        'dtime_per_hour': 'sum',
        'spent_fuel_abs': 'sum',
        'max_speed': 'max'
    }).reset_index()
    
    pre_period_df['spent_fuel'].mask(pre_period_df['pos_s'].eq(0) & pre_period_df['spent_fuel'].gt(0.0) & pre_period_df['spent_fuel'].lt(REFUELING_LIMIT), np.nan, inplace=True)
    pre_period_df['spent_fuel'].replace(np.nan, 0, inplace=True)
    pre_period_df = pre_period_df.groupby([pd.Grouper(key='auto'), pd.Grouper(key='timestamp', freq=f'{PERIOD_2_MIN}min')]).agg( {
        'pos_s': 'mean',
        'spent_fuel': 'sum',
        'max_fuel': 'max',
        'dtime': 'sum',
        'dtime_per_hour': 'sum',
        'spent_fuel_abs': 'sum',
        'max_speed': 'max'
    } ).reset_index()
    pre_period_df['travel'] = pre_period_df['pos_s'].mul(pre_period_df['dtime_per_hour'])
    pre_period_df['spent_per_100'] = pre_period_df['spent_fuel'].div(pre_period_df['travel']).mul(100)
    return pre_period_df


# TODO(YShipik): У НАС ПО ДЕФОЛТУ ТЕПЕРЬ ИНПУТ АУТПУТ NONE БУДЕТ
# TODO: проверить если в таблице норм поля speed_etalon, max_fuel, default 60 2000 (из функции calculate_norms) (REALIZED)
# TODO: второй датафрейм должен содержать по крайней мере колонки guid, input, output (как второй параметр функции передать) (REALZIED)
def calculate_norms(raw_df: pd.DataFrame, tariffied_df: pd.DataFrame):
    df_preprocess = preprocess(raw_df)

    df_preprocess = df_preprocess[df_preprocess['spent_fuel_abs'].div(df_preprocess['max_fuel']).lt(15)]

    df_preprocess['spent_per_100'].replace(to_replace=[-np.inf, np.inf, np.nan], value=0, inplace=True)

    df_preprocess = df_preprocess[df_preprocess['spent_fuel'].lt(0) & df_preprocess['travel'].gt(0.1)]

    norma_compute = pd.DataFrame(data={"auto": df_preprocess['auto'].unique()})

    norma_compute['quant_25'] = df_preprocess.groupby('auto')['spent_per_100'].quantile(.25).reset_index()[
        'spent_per_100']
    norma_compute['quant_75'] = df_preprocess.groupby('auto')['spent_per_100'].quantile(.75).reset_index()[
        'spent_per_100']
    norma_compute['travel_25'] = df_preprocess.groupby('auto')['travel'].quantile(.25).reset_index()['travel']
    norma_compute['travel_75'] = df_preprocess.groupby('auto')['travel'].max().reset_index()['travel']
    norma_compute['maxfuel'] = df_preprocess.groupby('auto')['max_fuel'].max().reset_index()['max_fuel']
    norma_compute['speed'] = df_preprocess.groupby('auto')['pos_s'].max().reset_index()['pos_s']
    norma_compute['speed_25'] = norma_compute['speed'].div(4)
    norma_compute['speed_50'] = norma_compute['speed'].div(2)
    norm_merge = df_preprocess.merge(right=norma_compute, right_on='auto', left_on='auto')
    slice_merge = norm_merge[norm_merge['pos_s'].between(norm_merge['speed_25'], norm_merge['speed_50'])]
    slice_merge = slice_merge[slice_merge['spent_per_100'].between(slice_merge['max_fuel'].mul(-2), 0)]
    new_norms = pd.DataFrame()

    new_norms['sl_avto'] = norm_merge['auto'].unique()

    groups = slice_merge.groupby(by="auto").agg({
        "max_fuel": "max",
        "spent_fuel": "mean",
        "spent_per_100": "mean",
        "pos_s": ['max', 'mean']
    })
    groups['pos_s']['max'].reset_index()['max']

    norms = groups['max_fuel']['max'].reset_index()
    norms['pos_s'] = groups['pos_s']['max'].reset_index()['max']
    norms['pos_s_mean'] = groups['pos_s']['mean'].reset_index()['mean']
    norms['mean_spent_fuel'] = groups['spent_fuel']['mean'].reset_index()['mean']
    norms['spent_per_100'] = groups['spent_per_100']['mean'].reset_index()['mean']

    norms['spent_per_100'] = norms['spent_per_100'].abs()

    norms['norma_rasx_summer'] = norms['spent_per_100']
    norms['norma_rasx_winter'] = norms['spent_per_100']
    norms['period'] = datetime.now() + timedelta(days=365)
    norms = norms.reset_index()

    norms = norms[['auto', 'norma_rasx_summer', 'norma_rasx_winter', 'period', 'pos_s_mean',"max"]]

    norms_speed = df_preprocess.groupby(by="auto")['max_speed'].max().reset_index()
    norms_speed['max_speed'] = norms_speed['max_speed'].div(2)
    norms_speed['max_speed'] = norms_speed['max_speed'].mask(norms_speed['max_speed'].ge(60), other=60)

    norms = norms.merge(norms_speed, on='auto')

    norms = norms.rename(columns={
        "auto": "sl_avto",
        "max_speed": "speed_etalon",
        "max": "max_fuel"
    })
    norms['norma_rasx_summer'] = norms['norma_rasx_summer'].mul(norms['speed_etalon']).div(norms['pos_s_mean'])
    norms['norma_rasx_winter'] = norms['norma_rasx_summer']
    norms = norms.merge(right=tariffied_df, left_on='sl_avto', right_on='guid')
    norms['norma_rasx_winter'] = norms['norma_rasx_summer'].mul(norms['input']).div(norms['output'])
    norms['norma_rasx_winter'] = norms['norma_rasx_winter'].mul(norms['input']).div(norms['output'])
    norms['max_fuel'] = norms['max_fuel'].mul(norms['input'].div(norms['output']))
    return norms
