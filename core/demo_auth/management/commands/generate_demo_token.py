"""
Management command для генерации бессрочного демо-токена.

Использование:
    python manage.py generate_demo_token
    python manage.py generate_demo_token --username demo_user
    python manage.py generate_demo_token --create-user

После выполнения добавьте вывод в .env:
    DEMO_ACCESS_TOKEN=<сгенерированный токен>
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = "Генерирует бессрочный демо-токен для публичного Swagger"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            default="demo_user",
            help="Username демо-пользователя (default: demo_user)",
        )
        parser.add_argument(
            "--create-user",
            action="store_true",
            help="Создать демо-пользователя если не существует",
        )
        parser.add_argument(
            "--org-id",
            type=str,
            default=None,
            help="UUID организации для привязки демо-юзера (требуется при --create-user)",
        )

    def handle(self, *args, **options):
        from core.demo_auth.tokens import generate_demo_token

        username = options["username"]

        try:
            user = User.objects.get(username=username)
            self.stdout.write(f"Найден существующий пользователь: {username}")
        except User.DoesNotExist:
            if not options["create_user"]:
                self.stderr.write(
                    self.style.ERROR(
                        f"Пользователь '{username}' не найден. "
                        f"Используйте --create-user для создания."
                    )
                )
                return

            # Создаём демо-пользователя
            org_id = options.get("org_id")
            if not org_id:
                self.stderr.write(
                    self.style.ERROR(
                        "Для создания пользователя укажите --org-id <UUID организации>"
                    )
                )
                return

            try:
                from core.models import Organization
                org = Organization.objects.get(id=org_id)
            except Exception:
                self.stderr.write(self.style.ERROR(f"Организация {org_id} не найдена"))
                return

            user = User.objects.create_user(
                username=username,
                password=None,  # пароль не нужен — вход только по токену
                org=org,
            )
            self.stdout.write(self.style.SUCCESS(f"Создан демо-пользователь: {username}"))

        token = generate_demo_token(user)

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("Бессрочный демо-токен сгенерирован:"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"\n{token}\n")
        self.stdout.write("=" * 60)
        self.stdout.write("\nДобавьте в .env файл:")
        self.stdout.write(self.style.WARNING(f"DEMO_ACCESS_TOKEN={token}"))
        self.stdout.write(self.style.WARNING(f"DEMO_USER_USERNAME={username}"))
        self.stdout.write(
            "\nТокен действителен бессрочно. "
            "Для отзыва — удалите пользователя или измените SECRET_KEY.\n"
        )
