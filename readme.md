# Для запуска celery_worker
- celery -A app worker --loglevel=info --pool=solo
# Для запуска celery_beat
- celery -A app beat --loglevel=info
# Для запуска celery_flower
- celery -A app flower --port=5555 --basic_auth=admin:admin