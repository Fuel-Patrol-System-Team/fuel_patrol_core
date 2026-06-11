"""
Permission классы для разграничения публичного и приватного доступа.

IsDemoUser     — разрешает ТОЛЬКО безопасные методы (GET/HEAD/OPTIONS)
IsPrivateRoute — блокирует демо-юзера на приватных эндпоинтах
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsDemoUser(BasePermission):
    """
    Если запрос пришёл с демо-токеном — разрешаем только SAFE_METHODS.
    Для обычных авторизованных пользователей — пропускаем без изменений.

    Добавляется ПЕРВЫМ в permission_classes на приватных вьюхах,
    либо устанавливается глобально как дополнительный guard.
    """

    message = "Demo users are not allowed to perform write operations."

    def has_permission(self, request, view):
        # Если это не демо-юзер — не вмешиваемся, пусть следующий permission решает
        if not getattr(request, "is_demo_user", False):
            return True

        # Демо-юзер — только безопасные методы
        return request.method in SAFE_METHODS


class IsNotDemoUser(BasePermission):
    """
    Полностью блокирует демо-юзеров.
    Используется на приватных/мутирующих эндпоинтах.

    Пример:
        permission_classes = [IsAuthenticated, IsNotDemoUser]
    """

    message = "This endpoint is not available for demo access."

    def has_permission(self, request, view):
        if getattr(request, "is_demo_user", False):
            return False
        return True


class IsOrgMemberOrReadOnlyDemo(BasePermission):
    """
    Комбинированный permission:
    - Демо-юзер: только SAFE_METHODS + должен быть аутентифицирован
    - Обычный юзер: должен быть членом организации

    Используется как замена IsOrgMember на публичных GET-эндпоинтах,
    которые также должны работать для демо.
    """

    message = "Authentication required. Demo users can only read data."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Демо-юзер: только чтение
        if getattr(request, "is_demo_user", False):
            return request.method in SAFE_METHODS

        # Обычный юзер: проверяем org
        return hasattr(request.user, "org") and request.user.org is not None
