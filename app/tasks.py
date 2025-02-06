## СЮДА СТАВИМ ТАСКИ В КРОН
from celery import chord, shared_task, group

from app.celery import app as celery_app
import pandas as pd
import polars as pl
from .utils import fuel_leak_calculate_standart, merge, preprocess

# тут код для батча
@shared_task
def thread_task(auto_df: pd.DataFrame, norma_df: pd.DataFrame, chunk_df: pl.DataFrame):
    result_df = fuel_leak_calculate_standart(merge(auto_df, preprocess(chunk_df)), norma_rasx_df=norma_df)
    return result_df


BATCH_SIZE = 100_000

@shared_task
def merge_parts_task(results):
    merge_result = pd.concat(results)
    print(merge_result.shape)
    return merge_result
    
@celery_app.task
def calcualate_leak_task():
    auto_df = pd.read_csv("./app/datasets/auto_info.csv")
    batches = pl.read_csv_batched("./app/datasets/auto.csv", try_parse_dates=True, schema_overrides={""}, batch_size=BATCH_SIZE)
    norma_df = pd.read_csv("./app/datasets/mart_norm_rasx_topl_202401261739.csv")
    items = batches.next_batches(4) # есть только такая функция надо придумать как разбивать и рассылать таски асинхронно (либо ПЕРЕПИСАТЬ ***** НА GOLANG или RUST)
    task_group = chord(
        (thread_task.s(auto_df, norma_df, batch) for batch in items), 
        merge_parts_task.s()
    )
    print("I'm here")
    result = task_group.apply_async()
    print("I'm around get")
    result_data = result.get()
    print(result_data.shape)
    print("Task was success")
    return result

import os
@celery_app.task
def test_task():
    print("I print here")
    with open("test.asdf", "w") as file:
        file.write("result")
    
    return None
    

# это запускает по дефолту
## calcualate_leak_task()

# Я поменял settings изменив параметр для celery (кодировщик) и закомменировал один из middlewarов
# работает вот это
    # auto_df = pd.read_csv("./../datasets/auto_info.csv")
    # data_df = pd.read_csv("./../datasets/auto.csv")
    # norma_df = pd.read_csv("./../datasets/mart_norm_rasx_topl_202401261739.csv")
    # print(data_df.columns)
    # print(auto_df.columns)
    # # 
    # result_df = fuel_leak_calculate_standart(merge(auto_df, preprocess(data_df)), norma_rasx_df=norma_df)
    # print(result_df.shape)
# или вот это
# auto_df = pd.read_csv("./datasets/auto_info.csv")
#     data_df = pl.read_csv("./datasets/auto.csv", try_parse_dates=True, schema_overrides= {"calc_sensors_mileage": pl.Float64, 'p_pwr_int': pl.String})
#     print(data_df.shape)
#     batches = pl.read_csv_batched("./datasets/auto.csv", try_parse_dates=True, batch_size=10_000, schema_overrides= {"calc_sensors_mileage": pl.Float64, 'p_pwr_int': pl.String})
#     norma_df = pd.read_csv("./datasets/mart_norm_rasx_topl_202401261739.csv")
#     results = []
#     total_size = 0
#     calls = 0
#     while True:
#         items = batches.next_batches(4) # есть только функция надо придумать как разбивать и рассылать таски асинхронно (либо ПЕРЕПИСАТЬ ***** НА GOLANG или RUST)
        
#         if items == None:
#             break
#         calls += 1
#         for item in items:
#             results.append(compute_leaks_chunk(auto_df, item, norma_df))
    
#     print(total_size)
#     print("Calls", calls)
#     result = pd.concat(results)
#     print(result.shape)