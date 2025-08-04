import os
import sys
from typing import Optional
import subprocess


def run_command(cmd: str, check: bool = False) -> Optional[int]:
    """Универсальный запуск команд через os.system"""
    print(f"Выполняется команда: {cmd}")
    returncode = os.system(cmd)
    if check and returncode != 0:
        print(f"Ошибка выполнения команды: {cmd}", file=sys.stderr)
        sys.exit(returncode)
    return returncode


def start():
    """Запуск сервера разработки через uv"""
    run_command("uv run python manage.py runserver")


def collectstatic():
    """Сбор статических файлов через uv"""
    run_command("uv run python manage.py collectstatic --noinput")


def makemigrations(app: Optional[str] = None):
    """Создание миграций через uv"""
    cmd = "uv run python manage.py makemigrations"
    if app:
        cmd += f" {app}"
    run_command(cmd)


def migrate(app: Optional[str] = None):
    """Применение миграций через uv"""
    cmd = "uv run python manage.py migrate"
    if app:
        cmd += f" {app}"
    run_command(cmd)


def inspectdb():
    """Инспекция БД через uv"""
    run_command("uv run python manage.py inspectdb", check=True)
    with open("real_models.py", "w") as f:
        process = subprocess.run(
            ["uv", "run", "python", "manage.py", "inspectdb"],
            capture_output=True,
            text=True,
            check=True,
        )
        f.write(process.stdout)


def superuser():
    """Создание суперпользователя через uv"""
    run_command("uv run python manage.py createsuperuser")


def shell():
    """Запуск Django shell через uv"""
    run_command("uv run python manage.py shell")


def celery_purge():
    """Очистка очереди Celery через uv"""
    run_command("uv run celery -A app purge -f")


def celery_worker():
    """Запуск Celery worker через uv"""
    ##TODO: Юлик запускает либо командой ниже в комментах, либо гуглит как у него работают потоки в линкусе и берёт себе команду
    # run_command("uv run celery -A app worker --pool=solo --loglevel=info")
    run_command("uv run celery -A app worker --pool=gevent --concurrency=6 --loglevel=info")


def celery_beat():
    """Запуск Celery beat через uv"""
    run_command("uv run celery -A app beat --loglevel=info")


def requirements():
    """Установка зависимостей из requirements.txt через uv"""
    run_command("uv pip install -r requirements.txt")


def install_editable():
    """Установка проекта в редактируемом режиме через uv"""
    run_command("uv pip install -e .")


def sync():
    """Синхронизация зависимостей из pyproject.toml через uv"""
    run_command("uv sync")


def cache_clean():
    """Очистка кэша uv"""
    run_command("uv cache clean")


def export_requirements():
    """Экспорт зависимостей из pyproject.toml в requirements.txt через uv"""
    run_command("uv pip compile pyproject.toml -o requirements.txt")


def show_help():
    """Показать список всех команд"""
    print("Доступные команды:")
    commands = {
        "start": "Запуск сервера разработки",
        "collectstatic": "Сбор статических файлов",
        "makemigrations [app]": "Создание миграций",
        "migrate [app]": "Применение миграций",
        "inspectdb": "Инспекция БД и генерация моделей",
        "superuser": "Создание суперпользователя",
        "shell": "Запуск Django shell",
        "celery-purge": "Очистка очереди Celery",
        "celery-worker": "Запуск Celery worker",
        "celery-beat": "Запуск Celery beat",
        "requirements": "Установка зависимостей из requirements.txt",
        "install-editable": "Установка проекта в редактируемом режиме",
        "sync": "Синхронизация зависимостей из pyproject.toml",
        "cache-clean": "Очистка кэша uv",
        "export-requirements": "Экспорт зависимостей в requirements.txt",
    }
    for cmd, desc in commands.items():
        print(f"  {cmd.ljust(30)} {desc}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        show_help()
        sys.exit(1)

    command = sys.argv[1]
    commands = {
        "start": start,
        "collectstatic": collectstatic,
        "makemigrations": lambda: makemigrations(sys.argv[2] if len(sys.argv) > 2 else None),
        "migrate": lambda: migrate(sys.argv[2] if len(sys.argv) > 2 else None),
        "inspectdb": inspectdb,
        "superuser": superuser,
        "shell": shell,
        "celery-purge": celery_purge,
        "celery-worker": celery_worker,
        "celery-beat": celery_beat,
        "requirements": requirements,
        "install-editable": install_editable,
        "sync": sync,
        "cache-clean": cache_clean,
        "export-requirements": export_requirements,
        "help": show_help,
    }

    if command not in commands:
        print(f"Неизвестная команда: {command}")
        show_help()
        sys.exit(1)

    try:
        commands[command]()
    except Exception as e:
        print(f"Ошибка выполнения команды: {e}", file=sys.stderr)
        sys.exit(1)
