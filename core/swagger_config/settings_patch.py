"""
Изменения в settings.py — вставить/заменить соответствующие блоки.
"""

# ======================
# REST FRAMEWORK
# ======================
# ИЗМЕНЕНИЕ: добавляем DemoTokenAuthentication первым
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'core.demo_auth.authentication.DemoTokenAuthentication',  # <-- НОВОЕ (первым!)
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    # Опционально: глобальный permission — демо не может мутировать
    # Не обязательно если используем IsNotDemoUser на конкретных вьюхах
    # 'DEFAULT_PERMISSION_CLASSES': [
    #     'core.demo_auth.permissions.IsDemoUser',
    # ],
}

# ======================
# SWAGGER SETTINGS  (заменяет старый блок)
# ======================
SWAGGER_SETTINGS = {
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
            'description': (
                'JWT токен. Формат: **Bearer &lt;токен&gt;**\n\n'
                'Demo-токен (только чтение): см. переменную DEMO_ACCESS_TOKEN'
            ),
        }
    },
    'USE_SESSION_AUTH': False,
    # Публичный swagger не требует авторизации для просмотра
    'DEFAULT_API_URL': None,
}

# ======================
# DEMO USER CONFIG  (новый блок — добавить)
# ======================
import os
DEMO_ACCESS_TOKEN = os.getenv('DEMO_ACCESS_TOKEN', '')
DEMO_USER_USERNAME = os.getenv('DEMO_USER_USERNAME', 'demo_user')
