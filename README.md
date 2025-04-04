# Fuel Patrol System - Документация по развертыванию

## Требования
- Docker 20.10+
- Docker Compose 1.29+
- Python 3.11 (для локальной разработки)

## Конфигурация

# 1. Настройка переменных окружения

Скопируйте и отредактируйте файл окружения:
```bash
cp .env.example .env
nano .env  # или ваш любимый редактор
```
# 2. Команды для запуска и администрирования
### Сборка и запуск
```bash
docker-compose up -d --build
```

### Применение миграций
```bash
docker-compose exec fuel-patrol-api python manage.py migrate

```
#### Создание суперпользователя
```bash
docker-compose exec fuel-patrol-api python manage.py createsuperuser
```

### Сбор статики
```bash
docker-compose exec fuel-patrol-api python manage.py collectstatic --noinput
```

# 3. Управления сервисами
### Остановка
```bash
docker-compose down
```


### Перезапуск
```bash
docker-compose restart
```

### Просмотр логов
```bash
docker-compose logs -f [service_name]  # api|db|redis|celery|beat|flower|influxdb
```
# 4. Доступ к сервисам
После запуска сервисы будут доступны по следующим адресам:

Django API: http://сервер:8001

Flower (мониторинг Celery): http://сервер:5556

InfluxDB UI: http://сервер:8087


