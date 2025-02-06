import pandas as pd
import numpy as np
import polars as pl

def preprocess(df: pl.DataFrame, ANTI_BUG_TIME_PERIOD= 10, PRE_PERIOD_TIME = 3, PERIOD_2_MIN = 30, VOLTAGE_LIMIT = 4, ANTI_BUG_FUEL = 8) -> pl.DataFrame:
    df = df[['timestamp', 'pos_s', 'calc_sensors_fuel_level', 'calc_sensors_voltage', 'auto']]
    df = df.filter(pl.col("calc_sensors_fuel_level").is_between(0, 4096))
    df = df.with_columns(pl.col("timestamp").dt.truncate("1h").alias("timestamp_hour"))
    df = df.with_columns(pl.col("timestamp").diff().abs().over(["auto", "timestamp_hour"]).alias("dtime"))
    df = df.with_columns(
    (pl.col("dtime").dt.total_seconds()   / 3600).fill_null(0).alias("dtime_per_hour")
    )
    df = df.with_columns(pl.col("calc_sensors_voltage").max().over(["auto", "timestamp_hour"]).alias("voltage_max"))
    df = df.filter(pl.col("voltage_max").sub(pl.col("calc_sensors_voltage")).lt(VOLTAGE_LIMIT))
    df = df.with_columns(pl.col('calc_sensors_fuel_level').diff().over(["auto", "timestamp_hour"]).alias("spent_fuel"))
    anti_bug = df.sort(["auto", "timestamp"]).group_by_dynamic(index_column='timestamp',  every=f"{ANTI_BUG_TIME_PERIOD}s", group_by=['auto']).agg(pl.col("calc_sensors_fuel_level").median(), pl.col("pos_s").mean(), pl.col("spent_fuel").sum(), pl.col("dtime_per_hour").sum())
    anti_bug = anti_bug.with_columns(pl.col("calc_sensors_fuel_level").max().over(["auto", pl.col("timestamp").dt.truncate(f"{PRE_PERIOD_TIME}m")]).alias("max_fuel"))
    pre_period_df = anti_bug.group_by_dynamic(index_column='timestamp', every=f'{PRE_PERIOD_TIME}m', group_by=['auto']).agg(pl.col("pos_s").mean(), pl.col("spent_fuel").sum(), pl.col("max_fuel").max(),  pl.col("dtime_per_hour").sum())
    pre_period_df = pre_period_df.with_columns(pl.when((pl.col("pos_s") == 0 ) & (pl.col("spent_fuel") > 0) &  (pl.col("spent_fuel") < ANTI_BUG_FUEL)).then(0).otherwise(pl.col("spent_fuel")).alias("spent_fuel"))
    period_df = pre_period_df.group_by_dynamic(index_column='timestamp', every=f'{PERIOD_2_MIN}m', group_by=['auto']).agg(pl.col("pos_s").mean(), pl.col("spent_fuel").sum(), pl.col("max_fuel").max(), pl.col("dtime_per_hour").sum())
    period_df = period_df.with_columns(pl.col("pos_s").mul(pl.col("dtime_per_hour")).alias("travel"))
    period_df = period_df.with_columns(pl.col("spent_fuel").truediv(pl.col("travel")).pow(100).alias("spent_per_100"))
    result = period_df.to_pandas(use_pyarrow_extension_array=True)
    return result

def merge(car_data: pd.DataFrame, preprocessed_df: pd.DataFrame):
    result_df = preprocessed_df.merge(right=car_data, how='inner', left_on='auto', right_on='guid')
    # опустим касты к numeric, надеясь что прокатит
    return result_df

# принимает пару median и std для данной машины
def fuel_leak_calculate_standart(df_values: pd.DataFrame, norma_rasx_df: pd.DataFrame, LEAK_LIMIT = 9, SIGMA_LIMIT = 3, SPEED_ETALON = 60):
    print(df_values.columns())


    tmp_df = pd.DataFrame(norma_rasx_df, columns=['sl_avto', 'period', 'deystvuet', 'deystvuet_do', 'vid_topliva', 'vid_norm_rasx', 'norma_rasx'])
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


    summer_norms = data_unique[data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Летняя')]


    winter_norms = data_unique[data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Зимняя')]


    winter_result = winter_values.merge(right=winter_norms, how='inner', left_on='guid', right_on='sl_avto')
    summer_result = summer_values.merge(right=summer_norms, left_on='guid', right_on='sl_avto')


    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx'].mul(winter_result['travel']).div(100)
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx'].mul(summer_result['travel']).div(100)


    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx_per_travel'].mul(winter_result['pos_s'].div(SPEED_ETALON).pow(3))
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx_per_travel'].mul(summer_result['pos_s'].div(SPEED_ETALON).pow(3))


    winter_result['is_leak'] = np.select([winter_result['spent_fuel'].gt(winter_result['norma_rasx_per_travel']),winter_result['travel'].eq(0.0) & winter_result['spent_fuel'].ge(1.17/6) & winter_result['sl_tip_dvigat'].eq(0), 
                                        winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1/6) & winter_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)
    summer_result['is_leak'] = np.select([summer_result['spent_fuel'].gt(summer_result['norma_rasx_per_travel']),summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17/6) & summer_result['sl_tip_dvigat'].eq(0), 
                                        summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1/6) & summer_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)


    summer_result['leak'] = np.select(
        [
        summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17/6) & summer_result['sl_tip_dvigat'].eq(0),
        summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17/6) & summer_result['sl_tip_dvigat'].eq(1),
        ],
        [
            summer_result['spent_fuel'].sub(1.17/6).abs().apply(lambda x: max(x, 0)),
            summer_result['spent_fuel'].sub(1/6).abs().apply(lambda x: max(x, 0))
        ], default=summer_result['spent_fuel'].sub(summer_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

    winter_result['leak'] = np.select(
        [
        winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17/6) & winter_result['sl_tip_dvigat'].eq(0),
        winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17/6) & winter_result['sl_tip_dvigat'].eq(1),
        ],
        [
            winter_result['spent_fuel'].sub(1.17/6).abs().apply(lambda x: max(x, 0)),
            winter_result['spent_fuel'].sub(1/6).abs().apply(lambda x: max(x, 0))
        ], default=winter_result['spent_fuel'].sub(winter_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))


    result_df = pd.concat([winter_result, summer_result])
    result_df.dropna(subset=['sl_tip_dvigat'], inplace=True)


    result_df = result_df[result_df['krit_uc_narab'].ne(1)]
    result_df['is_leak'] = result_df['leak'].gt(LEAK_LIMIT)
    result_df['timestamp'] = pd.to_datetime(result_df['timestamp'], errors='ignore')


    result_df['delta_sp'] = result_df['spent_fuel'].sub(result_df['norma_rasx_per_travel'])



    result_df['delta_sp_median'] = result_df.groupby([pd.Grouper(key='guid')])['delta_sp'].transform('median')


    result_df['delta_sp_std'] = result_df.groupby([pd.Grouper(key='guid')])['delta_sp'].transform('std')


    result_df['is_leak_delta_sp'] = result_df['delta_sp'].ge(result_df['delta_sp_median'].add(result_df['delta_sp_std'].mul(SIGMA_LIMIT))) & result_df['leak'].ge(LEAK_LIMIT)


    result_df['max_fuel_diff'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff().shift(-1)
    result_df['max_fuel_diff_back'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff()

    result_df['max_fuel_diff'].replace(to_replace=np.nan, value=0, inplace=True)

    result_df['is_max_fuel_diff_2'] = result_df['max_fuel_diff'].lt(0) & ( result_df['leak'].le(-result_df['max_fuel_diff']) )
    result_df['is_max_fuel_diff'] = result_df['max_fuel_diff'].lt(0) & result_df['max_fuel_diff_back'].lt(0)
    
    return result_df

def fuel_leak_calculate_tricky(df_values: pd.DataFrame, norma_rasx_df: pd.DataFrame, SIGMA_VALUE, LEAK_LIMIT = 9, SIGMA_LIMIT = 3, SPEED_ETALON = 60):



    tmp_df = pd.DataFrame(norma_rasx_df, columns=['sl_avto', 'period', 'deystvuet', 'deystvuet_do', 'vid_topliva', 'vid_norm_rasx', 'norma_rasx'])


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


    summer_norms = data_unique[data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Летняя')]


    winter_norms = data_unique[data_unique['vid_norm_rasx'].eq('Норма на 100 км (Норма за час для ТС по моточасам) Зимняя')]


    winter_result = winter_values.merge(right=winter_norms, how='inner', left_on='guid', right_on='sl_avto')
    summer_result = summer_values.merge(right=summer_norms, how='inner', left_on='guid', right_on='sl_avto')


    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx'].mul(winter_result['travel']).div(100)
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx'].mul(summer_result['travel']).div(100)


    winter_result['norma_rasx_per_travel'] = winter_result['norma_rasx_per_travel'].mul(winter_result['pos_s'].div(SPEED_ETALON).pow(3))
    summer_result['norma_rasx_per_travel'] = summer_result['norma_rasx_per_travel'].mul(summer_result['pos_s'].div(SPEED_ETALON).pow(3))


    winter_result['is_leak'] = np.select([winter_result['spent_fuel'].gt(winter_result['norma_rasx_per_travel'] + SIGMA_LIMIT * SIGMA_VALUE),winter_result['travel'].eq(0.0) & winter_result['spent_fuel'].ge(1.17/6) & winter_result['sl_tip_dvigat'].eq(0), 
                                        winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1/6) & winter_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)
    summer_result['is_leak'] = np.select([summer_result['spent_fuel'].gt(summer_result['norma_rasx_per_travel']),summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17/6) & summer_result['sl_tip_dvigat'].eq(0), 
                                        summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1/6) & summer_result['sl_tip_dvigat'].eq(1)], [True, True, True], default=False)


    summer_result['leak'] = np.select(
        [
        summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17/6) & summer_result['sl_tip_dvigat'].eq(0),
        summer_result['travel'].eq(0) & summer_result['spent_fuel'].ge(1.17/6) & summer_result['sl_tip_dvigat'].eq(1),
        ],
        [
            summer_result['spent_fuel'].sub(1.17/6).abs().apply(lambda x: max(x, 0)),
            summer_result['spent_fuel'].sub(1/6).abs().apply(lambda x: max(x, 0))
        ], default=summer_result['spent_fuel'].sub(summer_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))

    winter_result['leak'] = np.select(
        [
        winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17/6) & winter_result['sl_tip_dvigat'].eq(0),
        winter_result['travel'].eq(0) & winter_result['spent_fuel'].ge(1.17/6) & winter_result['sl_tip_dvigat'].eq(1),
        ],
        [
            winter_result['spent_fuel'].sub(1.17/6).abs().apply(lambda x: max(x, 0)),
            winter_result['spent_fuel'].sub(1/6).abs().apply(lambda x: max(x, 0))
        ], default=winter_result['spent_fuel'].sub(winter_result['norma_rasx_per_travel']).apply(lambda x: max(0, x)))


    result_df = pd.concat([winter_result, summer_result])
    result_df.dropna(subset=['sl_tip_dvigat'], inplace=True)


    result_df['is_leak'] = result_df['leak'].gt(LEAK_LIMIT)
    result_df['timestamp'] = pd.to_datetime(result_df['timestamp'], errors='ignore')

    result_df['max_fuel_diff'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff().shift(-1)
    result_df['max_fuel_diff_back'] = result_df.groupby([pd.Grouper('guid'), pd.Grouper(key='timestamp', freq='1d')])['max_fuel'].diff()

    result_df['max_fuel_diff'].replace(to_replace=np.nan, value=0, inplace=True)

    result_df['is_max_fuel_diff_2'] = result_df['max_fuel_diff'].lt(0) & ( result_df['leak'].le(-result_df['max_fuel_diff']) )
    result_df['is_max_fuel_diff'] = result_df['max_fuel_diff'].lt(0) & result_df['max_fuel_diff_back'].lt(0)
    
    return result_df

def compute_leaks_chunk(auto_df: pd.DataFrame, data_df: pl.DataFrame, norma_df: pd.DataFrame):
    result_df = fuel_leak_calculate_standart(merge(auto_df, preprocess(data_df)), norma_rasx_df=norma_df)
    return result_df