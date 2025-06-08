import redis
from django.conf import settings


def get_redis_client():
    return redis.Redis(
        host=settings.KEYDB_CONFIG['HOST'],
        port=settings.KEYDB_CONFIG['PORT'],
        password=settings.KEYDB_CONFIG['PASSWORD'],
        decode_responses=settings.KEYDB_CONFIG['DECODE_RESPONSES'],
        db=settings.KEYDB_CONFIG['DB']
    )


def cache_task_status(redis_client, task_id, status):
    redis_client.set(f"task_status:{task_id}", status)
