"""
Swagger / ReDoc views с разграничением доступа.

Публичный  (/api/v1/docs/)         — открыт для всех, только GET-эндпоинты
Приватный  (/api/v1/docs/private/) — только IsAdminUser или BasicAuth
"""

from django.conf import settings
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework.authentication import BasicAuthentication, SessionAuthentication


class IsStaffOrBasicAuth(permissions.BasePermission):
    """
    Разрешает доступ только:
    - staff/superuser пользователям (через любую авторизацию)
    - BasicAuth (для CI/CD или прямого доступа по логину/паролю)
    """

    def has_permission(self, request, view):
        # BasicAuth уже прошёл — достаточно is_staff
        if request.user and request.user.is_authenticated:
            return request.user.is_staff or request.user.is_superuser
        return False


# ============================================================
# Публичная схема — только GET, без авторизации на просмотр
# ============================================================
public_schema_view = get_schema_view(
    openapi.Info(
        title="FuelPatrol API — Public",
        default_version="v1",
        description=(
            "Публичная документация API FuelPatrol.\n\n"
            "Доступны только операции чтения (GET).\n\n"
            "**Demo-токен**: используйте Bearer-токен из переменной `DEMO_ACCESS_TOKEN` "
            "для авторизации в Swagger UI."
        ),
        contact=openapi.Contact(email="support@fuelpatrol.ru"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
    # generator_class подключается в urls.py через kwargs
)

# ============================================================
# Приватная схема — полная документация, только для staff
# ============================================================
private_schema_view = get_schema_view(
    openapi.Info(
        title="FuelPatrol API — Private (Full)",
        default_version="v1",
        description=(
            "Полная документация API FuelPatrol.\n\n"
            "Включает все эндпоинты: мутации, parsing, staff.\n\n"
            "⚠️ Доступ только для авторизованных сотрудников."
        ),
        contact=openapi.Contact(email="support@fuelpatrol.ru"),
    ),
    public=False,
    permission_classes=[IsStaffOrBasicAuth],
    authentication_classes=[BasicAuthentication, SessionAuthentication],
)
