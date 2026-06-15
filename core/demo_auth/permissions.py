from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsDemoUser(BasePermission):

    message = "Demo users are not allowed to perform write operations."

    def has_permission(self, request, view):
        # Если это не демо-юзер — не вмешиваемся, пусть следующий permission решает
        if not getattr(request, "is_demo_user", False):
            return True

        # Демо-юзер — только безопасные методы
        return request.method in SAFE_METHODS


class IsNotDemoUser(BasePermission):

    message = "This endpoint is not available for demo access."

    def has_permission(self, request, view):
        if getattr(request, "is_demo_user", False):
            return False
        return True


class IsOrgMemberOrReadOnlyDemo(BasePermission):
    message = "Authentication required. Demo users can only read data."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if getattr(request, "is_demo_user", False):
            return request.method in SAFE_METHODS

        return hasattr(request.user, "org") and request.user.org is not None
