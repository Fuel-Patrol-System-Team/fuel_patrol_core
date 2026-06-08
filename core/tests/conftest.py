import pytest
import os

@pytest.fixture(scope="session")
def django_db_setup():
    from django.conf import settings

    settings.DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB'),
        'USER': os.getenv('POSTGRES_USER'),
        'PASSWORD': os.getenv('POSTGRES_PASS'),
        'HOST': os.getenv('POSTGRES_HOST'),
        'PORT': os.getenv('POSTGRES_PORT'),
        'CONN_MAX_AGE': 120,
        'OPTIONS': {
            'connect_timeout': 60,
        },
        "ATOMIC_REQUESTS": False
    }

@pytest.fixture(autouse=True)
def celery_eager(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True