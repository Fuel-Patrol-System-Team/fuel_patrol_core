"""
Кастомный Authentication backend для бессрочного демо-токена.

Регистрируется в settings.py:
    REST_FRAMEWORK = {
        'DEFAULT_AUTHENTICATION_CLASSES': [
            'core.demo_auth.authentication.DemoTokenAuthentication',
            'rest_framework_simplejwt.authentication.JWTAuthentication',
            ...
        ]
    }
"""

import logging
from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import UntypedToken

logger = logging.getLogger(__name__)


class DemoTokenAuthentication(JWTAuthentication):
    """
    Расширяет стандартный JWTAuthentication:
    - Если токен содержит `is_demo: True` — пропускаем проверку exp
    - Аттачим флаг `request.is_demo_user = True` для Permission классов
    """

    def get_validated_token(self, raw_token):
        # Сначала пробуем декодировать без валидации exp
        try:
            untyped = UntypedToken(raw_token)
            if untyped.payload.get("is_demo"):
                # Это демо-токен — валидируем без exp
                return _DemoUntypedToken(raw_token)
        except TokenError:
            pass

        # Обычный JWT — стандартная валидация
        return super().get_validated_token(raw_token)

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        # Помечаем запрос как демо если токен демо
        if getattr(token, "is_demo", False) or token.payload.get("is_demo"):
            request.is_demo_user = True
        else:
            request.is_demo_user = False

        return user, token


class _DemoUntypedToken(UntypedToken):
    """
    UntypedToken который не проверяет exp и token_type.
    Используется только для демо-токенов.
    """
    token_type = "demo_access"

    # Флаг для идентификации в Permission классах
    is_demo = True

    def verify(self):
        # Пропускаем проверку exp и token_type
        pass
