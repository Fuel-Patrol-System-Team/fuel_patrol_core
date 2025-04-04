import redis
from django.conf import settings


def get_redis_client():
    return redis.Redis(
        host=settings.REDIS_CONFIG['HOST'],
        port=settings.REDIS_CONFIG['PORT'],
        password=settings.REDIS_CONFIG['PASSWORD'],
        decode_responses=settings.REDIS_CONFIG['DECODE_RESPONSES'],
        db=settings.REDIS_CONFIG['DB']
    )


def cache_task_status(redis_client, task_id, status):
    redis_client.set(f"task_status:{task_id}", status)
