import pandas as pd
import numpy as np
import polars as pl



def preprocess(df: pd.DataFrame, ANTI_BUG_TIME_SECONDS=10, PRE_PERIOD_TIME = 3, PERIOD_2_MIN = 30, VOLTAGE_LIMIT = 4) -> pd.DataFrame:
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
    result_df = preprocessed_df.merge(right=car_data, how='inner', left_on='auto', right_on='guid')
    # опустим касты к numeric, надеясь что прокатит
    return result_df


# принимает пару median и std для данной машины
def fuel_leak_calculate_standart(df_values: pd.DataFrame, norma_rasx_df: pd.DataFrame, LEAK_LIMIT=9, SIGMA_LIMIT=3,
                                 SPEED_ETALON=60):

    tmp_df = pd.DataFrame(norma_rasx_df,
                          columns=['sl_avto', 'period', 'deystvuet', 'deystvuet_do', 'vid_topliva', 'vid_norm_rasx',
                                   'norma_rasx'])
    tmp_df.drop_duplicates(inplace=True)

    tmp_df['deystvuet_do'] = pd.to_datetime(tmp_df['deystvuet_do'])
    tmp_df['period'] = pd.to_datetime(tmp_df['period'])

    df_values['spent_fuel'].mask(df_values['spent_fuel'].gt(0), other=0, inplace=True)
    df_values['spent_fuel'] = df_values['spent_fuel'].abs()

    df_values['spent_per_100'] = df_values['spent_fuel'].mul(100).div(df_values['travel'])

    df_values['timestamp'] = pd.to_datetime(df_values['timestamp'])

    summer_values = df_values[df_values['timestamp'].dt.month.between(3, 10)]

    winter_values = df_values[~df_values['timestamp'].dt.month.between(3, 10)]
    tmp_df.reset_index(inplace=True)
    data_unique = tmp_df.iloc[tmp_df.groupby(['sl_avto', 'vid_norm_rasx'])['period'].idxmax()]

    summer_norms = data_unique[
        data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Летняя')]

    winter_norms = data_unique[
        data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Зимняя')]

    winter_result = winter_values.merge(right=winter_norms, how='inner', left_on='guid', right_on='sl_avto')
    summer_result = summer_values.merge(right=summer_norms, left_on='guid', right_on='sl_avto')

    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx'].mul(winter_result['travel']).div(100)
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx'].mul(summer_result['travel']).div(100)

    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx_per_travel'].mul(
        winter_result['pos_s'].div(SPEED_ETALON).pow(3))
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx_per_travel'].mul(
        summer_result['pos_s'].div(SPEED_ETALON).pow(3))

    winter_result['is_leak'] = np.select([winter_result['spent_fuel'].gt(winter_result['norma_rasx_per_travel']),
                                          winter_result['travel'].eq(0.0) & winter_result['spent_fuel'].ge(1.17 / 6) &
                                          winter_result['sl_tip_dvigat'].eq(0),
                                          winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1 / 6) &
                                          winter_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)
    summer_result['is_leak'] = np.select([summer_result['spent_fuel'].gt(summer_result['norma_rasx_per_travel']),
                                          summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17 / 6) &
                                          summer_result['sl_tip_dvigat'].eq(0),
                                          summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1 / 6) &
                                          summer_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)

    summer_result['leak'] = np.select(
        [
            summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17 / 6) & summer_result[
                'sl_tip_dvigat'].eq(0),
            summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17 / 6) & summer_result[
                'sl_tip_dvigat'].eq(1),
        ],
        [
            summer_result['spent_fuel'].sub(1.17 / 6).abs().apply(lambda x: max(x, 0)),
            summer_result['spent_fuel'].sub(1 / 6).abs().apply(lambda x: max(x, 0))
        ], default=summer_result['spent_fuel'].sub(summer_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

    winter_result['leak'] = np.select(
        [
            winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17 / 6) & winter_result[
                'sl_tip_dvigat'].eq(0),
            winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17 / 6) & winter_result[
                'sl_tip_dvigat'].eq(1),
        ],
        [
            winter_result['spent_fuel'].sub(1.17 / 6).abs().apply(lambda x: max(x, 0)),
            winter_result['spent_fuel'].sub(1 / 6).abs().apply(lambda x: max(x, 0))
        ], default=winter_result['spent_fuel'].sub(winter_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

    result_df = pd.concat([winter_result, summer_result])
    result_df.dropna(subset=['sl_tip_dvigat'], inplace=True)

    result_df = result_df[result_df['krit_uc_narab'].ne(1)]
    result_df['is_leak'] = result_df['leak'].gt(LEAK_LIMIT)
    result_df['timestamp'] = pd.to_datetime(result_df['timestamp'], errors='ignore')

    result_df['delta_sp'] = result_df['spent_fuel'].sub(result_df['norma_rasx_per_travel'])

    result_df['delta_sp_median'] = result_df.groupby([pd.Grouper(key='guid')])['delta_sp'].transform('median')

    result_df['delta_sp_std'] = result_df.groupby([pd.Grouper(key='guid')])['delta_sp'].transform('std')

    result_df['is_leak_delta_sp'] = result_df['delta_sp'].ge(
        result_df['delta_sp_median'].add(result_df['delta_sp_std'].mul(SIGMA_LIMIT))) & result_df['leak'].ge(LEAK_LIMIT)

    result_df['max_fuel_diff'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])[
        'max_fuel'].diff().shift(-1)
    result_df['max_fuel_diff_back'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])[
        'max_fuel'].diff()

    result_df['max_fuel_diff'].replace(to_replace=np.nan, value=0, inplace=True)

    result_df['is_max_fuel_diff_2'] = result_df['max_fuel_diff'].lt(0) & (
        result_df['leak'].le(-result_df['max_fuel_diff']))
    result_df['is_max_fuel_diff'] = result_df['max_fuel_diff'].lt(0) & result_df['max_fuel_diff_back'].lt(0)

    return result_df


def fuel_leak_calculate_tricky(df_values: pd.DataFrame, norma_rasx_df: pd.DataFrame, SIGMA_VALUE, LEAK_LIMIT=9,
                               SIGMA_LIMIT=3, SPEED_ETALON=60):
    tmp_df = pd.DataFrame(norma_rasx_df,
                          columns=['sl_avto', 'period', 'deystvuet', 'deystvuet_do', 'vid_topliva', 'vid_norm_rasx',
                                   'norma_rasx'])

    tmp_df['deystvuet_do'] = pd.to_datetime(tmp_df['deystvuet_do'])
    tmp_df['period'] = pd.to_datetime(tmp_df['period'])

    df_values['spent_fuel'].mask(df_values['spent_fuel'].gt(0), other=0, inplace=True)
    df_values['spent_fuel'] = df_values['spent_fuel'].abs()

    df_values['spent_per_100'] = df_values['spent_fuel'].mul(100).div(df_values['travel'])

    df_values['timestamp'] = pd.to_datetime(df_values['timestamp'])

    print(tmp_df.groupby(['sl_avto', 'vid_norm_rasx'])['period'].idxmax().tolist())
    summer_values = df_values[df_values['timestamp'].dt.month.between(3, 10)]

    winter_values = df_values[~df_values['timestamp'].dt.month.between(3, 10)]

    data_unique = tmp_df.iloc[tmp_df.groupby(['sl_avto', 'vid_norm_rasx'])['period'].idxmax()]

    summer_norms = data_unique[
        data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Летняя')]

    winter_norms = data_unique[
        data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Зимняя')]

    winter_result = winter_values.merge(right=winter_norms, how='inner', left_on='guid', right_on='sl_avto')
    summer_result = summer_values.merge(right=summer_norms, how='inner', left_on='guid', right_on='sl_avto')

    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx'].mul(winter_result['travel']).div(100)
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx'].mul(summer_result['travel']).div(100)

    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx_per_travel'].mul(
        winter_result['pos_s'].div(SPEED_ETALON).pow(3))
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx_per_travel'].mul(
        summer_result['pos_s'].div(SPEED_ETALON).pow(3))

    winter_result['is_leak'] = np.select(
        [winter_result['spent_fuel'].gt(winter_result['norma_rasx_per_travel'] + SIGMA_LIMIT * SIGMA_VALUE),
         winter_result['travel'].eq(0.0) & winter_result['spent_fuel'].ge(1.17 / 6) & winter_result['sl_tip_dvigat'].eq(
             0),
         winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1 / 6) & winter_result['sl_tip_dvigat'].eq(1)],
        [True, True, True], default=False)
    summer_result['is_leak'] = np.select([summer_result['spent_fuel'].gt(summer_result['norma_rasx_per_travel']),
                                          summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17 / 6) &
                                          summer_result['sl_tip_dvigat'].eq(0),
                                          summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1 / 6) &
                                          summer_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)

    summer_result['leak'] = np.select(
        [
            summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17 / 6) & summer_result[
                'sl_tip_dvigat'].eq(0),
            summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17 / 6) & summer_result[
                'sl_tip_dvigat'].eq(1),
        ],
        [
            summer_result['spent_fuel'].sub(1.17 / 6).abs().apply(lambda x: max(x, 0)),
            summer_result['spent_fuel'].sub(1 / 6).abs().apply(lambda x: max(x, 0))
        ], default=summer_result['spent_fuel'].sub(summer_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

    winter_result['leak'] = np.select(
        [
            winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17 / 6) & winter_result[
                'sl_tip_dvigat'].eq(0),
            winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17 / 6) & winter_result[
                'sl_tip_dvigat'].eq(1),
        ],
        [
            winter_result['spent_fuel'].sub(1.17 / 6).abs().apply(lambda x: max(x, 0)),
            winter_result['spent_fuel'].sub(1 / 6).abs().apply(lambda x: max(x, 0))
        ], default=winter_result['spent_fuel'].sub(winter_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

    result_df = pd.concat([winter_result, summer_result])
    result_df.dropna(subset=['sl_tip_dvigat'], inplace=True)

    result_df['is_leak'] = result_df['leak'].gt(LEAK_LIMIT)
    result_df['timestamp'] = pd.to_datetime(result_df['timestamp'], errors='ignore')

    result_df['max_fuel_diff'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])[
        'max_fuel'].diff().shift(-1)
    result_df['max_fuel_diff_back'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])[
        'max_fuel'].diff()

    result_df['max_fuel_diff'].replace(to_replace=np.nan, value=0, inplace=True)

    result_df['is_max_fuel_diff_2'] = result_df['max_fuel_diff'].lt(0) & (
        result_df['leak'].le(-result_df['max_fuel_diff']))
    result_df['is_max_fuel_diff'] = result_df['max_fuel_diff'].lt(0) & result_df['max_fuel_diff_back'].lt(0)

    return result_df


def compute_leaks_chunk(auto_df: pd.DataFrame, data_df: pl.DataFrame, norma_df: pd.DataFrame):
    result_df = fuel_leak_calculate_standart(merge(auto_df, preprocess(data_df)), norma_rasx_df=norma_df)
    return result_df