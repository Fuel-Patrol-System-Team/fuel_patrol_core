#### README
# Установка переменных среды
```bash
    cp .env.dev .env
```
# Для запуска проекта
```bash
    python3 manage.py runserver
```
# Для сбора статики
```bash
    python manage.py collectstatic
```
# Celery
### Для запуска celery_worker
```bash
    celery -A app worker --loglevel=info --pool=solo
```
### Для запуска celery_beat
```bash
    celery -A app beat --loglevel=info
```
### Для запуска celery_flower
```bash
    celery -A app flower --port=5555 --basic_auth=admin:admin
```
### Для очистки очереди celery
```bash
    celery -A app purge
```