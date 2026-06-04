# fuel_patrol_core

Django REST API для Fuel Patrol. В production запускается как часть общего `docker-compose` из корня проекта `fuel_patrol`.

## Состав

- Django 5 + DRF
- PostgreSQL 15 (сервис `db`)
- KeyDB / Redis (сервис `keydb`)
- Celery worker + beat

## Ключевые переменные окружения

| Переменная | Описание |
|------------|----------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | `True` / `False` |
| `ALLOWED_HOSTS` | Список хостов через пробел |
| `POSTGRES_DB` | Имя БД |
| `POSTGRES_USER` | Пользователь БД |
| `POSTGRES_PASS` | Пароль БД (**именно `POSTGRES_PASS`**, не `PASSWORD`) |
| `POSTGRES_HOST` | Хост БД (в compose: `db`) |
| `KEYDB_HOST` | Хост KeyDB (в compose: `keydb`) |
| `KEYDB_PASSWORD` | Пароль KeyDB |
| `STATIC_ROOT` | Путь к статике внутри контейнера (`/app/static`) |
| `MEDIA_ROOT` | Путь к media внутри контейнера (`/app/media`) |
| `BASE_URL` | Публичный URL API |
| `TIME_ZONE` | Часовой пояс (`Europe/Moscow`) |

## Локальная разработка (без Docker)

```bash
# Установка зависимостей через uv
pip install uv
uv sync
uv pip install -e .

# Справка по скриптам
uv run help

# Запуск
cp .env.example .env   # заполни .env (нужны локальные postgres и keydb)
uv run python manage.py migrate
uv run python manage.py runserver
```
