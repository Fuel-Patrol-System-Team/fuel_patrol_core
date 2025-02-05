## СЮДА СТАВИМ ТАСКИ В КРОН
from celery import shared_task, group

from app.celery import app as celery_app


@shared_task
def thread_task():
    return ('C таким декоратором пишем те таски, которые могут быть переиспользованы или требуют выполнения в потоке, '
            'например функция расчёта слива для чанка записей')


@celery_app.task
def threader_task():
    return 'Здесь функция, которая будет вызывать функцию для расчёта сливов по чанку - n раз'
