# Fuel Patrol System - Документация по развертыванию

## Требования
- Docker 20.10+
- Docker Compose 1.29+
- Python 3.12 (для локальной разработки)

## Конфигурация

# 1. Настройка переменных окружения

Скопируйте и отредактируйте файл окружения:
```bash
cp .env.example .env
nano .env 
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
docker-compose logs -f [service_name]  # api|db|redis|celery|beat
```
# 4. Доступ к сервисам
После запуска сервисы будут доступны по следующим адресам:

Django API: http://сервер:8001

# Настройка UV
### Установка uv
```bash
pip install uv
```
### Синхронизация uv, установка зависимостей из pyproject.toml
```bash
uv sync
```
### Установка проекта в редактируемом режиме (опционально)
```bash
uv pip install -e .
```
### В проекте есть ряд предзаготовленных скриптов взаимодействия, для их изучения введи 
```bash
uv run help
```
### Добавить зависимость в проект
```bash
uv add <package>
```
### Удалить зависимости
```bash
uv remove <package>
```
### Зафиксировать зависимости
```bash
uv lock
```

