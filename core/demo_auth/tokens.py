"""
Бессрочный демо-токен для публичного Swagger.

Использование:
    from core.demo_auth.tokens import generate_demo_token
    token = generate_demo_token()  # запускается один раз, токен сохраняется в .env

Токен не имеет `exp` клейма → никогда не протухает.
Пользователь с этим токеном идентифицируется как demo-юзер и может
выполнять ТОЛЬКО безопасные методы (GET / HEAD / OPTIONS).
"""
from datetime import timedelta

from rest_framework_simplejwt.tokens import Token


class DemoAccessToken(Token):
    """
    Токен без срока действия для демо-пользователя.
    Убираем lifetime → `exp` клейм не добавляется.
    """
    token_type = "demo_access"
    lifetime = timedelta(days=365 * 100)  # бессрочный

    # Убираем проверку exp при валидации
    def verify(self):
        self.verify_token_type()
        # намеренно НЕ вызываем verify_exp()


def generate_demo_token(demo_user) -> str:
    """
    Генерирует бессрочный токен для demo_user.
    Вызывать один раз через management command, результат класть в .env.

    Example:
        python manage.py generate_demo_token
    """
    token = DemoAccessToken.for_user(demo_user)
    # Явно удаляем exp если SimpleJWT всё же его добавил
    token.payload.pop("exp", None)
    token.payload.pop("jti", None)  # отключаем blacklist-проверку
    token.payload["is_demo"] = True
    return str(token)
