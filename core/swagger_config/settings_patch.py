"""
Изменения в settings.py — вставить/заменить соответствующие блоки.
"""

# ======================
# REST FRAMEWORK
# ======================

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'core.demo_auth.authentication.DemoTokenAuthentication',
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
}

# ======================
# SWAGGER SETTINGS
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
    'DEFAULT_API_URL': None,
}

# ======================
# DEMO USER CONFIG
# ======================
import os
DEMO_ACCESS_TOKEN = os.getenv('DEMO_ACCESS_TOKEN', '')
DEMO_USER_USERNAME = os.getenv('DEMO_USER_USERNAME', 'demo_user')
