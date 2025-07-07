import pandas as pd
import numpy as np

from core.services.preprocessing.utils import preprocess
from datetime import datetime, timedelta


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
