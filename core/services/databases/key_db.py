import os
import redis


def get_redis_client():
    if not hasattr(get_redis_client, "client"):
        get_redis_client.client = redis.Redis(
            host=os.getenv("KEYDB_HOST", "127.0.0.1"),
            port=int(os.getenv("KEYDB_PORT", 6379)),
            password=os.getenv("KEYDB_PASSWORD", "roottoor"),
            decode_responses=True
        )
    return get_redis_client.client

def cache_task_status(redis_client,task_id, status):
    redis_client.set(f"task_status:{task_id}", status)