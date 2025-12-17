from rest_framework.permissions import BasePermission


class IsOrgMember(BasePermission):
    """
    Проверяет, принадлежит ли пользователь к организации и имеет ли доступ к объекту.
    Поддерживает проверку через различные связи с Organization.
    """

    def has_permission(self, request, view):
        """Общая проверка аутентификации и принадлежности к организации."""
        return (
                request.user.is_authenticated and
                hasattr(request.user, 'org') and
                request.user.org is not None
        )

    def has_object_permission(self, request, view, obj):
        """
        Проверяет доступ к конкретному объекту через различные связи с организацией.
        Возвращает True, если объект принадлежит организации пользователя.
        """
        user_org = request.user.org

        if hasattr(obj, 'organization'):
            return obj.organization == user_org

        elif hasattr(obj, 'org'):
            return obj.org == user_org

        elif hasattr(obj, 'provider_id') and hasattr(obj.provider_id, 'org_id'):
            return obj.provider_id.org_id == user_org

        elif hasattr(obj, 'report_query') and hasattr(obj.report_query, 'provider_id') and hasattr(
                obj.report_query.provider_id, 'org_id'):
            return obj.report_query.provider_id.org_id == user_org

        elif hasattr(obj, 'list_id') and hasattr(obj.list_id, 'user') and hasattr(obj.list_id.user, 'org'):
            return obj.list_id.user.org == user_org

        elif hasattr(obj, 'data_providers'):
            return obj.data_providers.filter(org_id=user_org).exists()

        elif hasattr(obj, 'car') and hasattr(obj.car, 'data_providers'):
            return obj.car.data_providers.filter(org_id=user_org).exists()

        elif hasattr(obj, 'car_id') and hasattr(obj.car_id, 'data_providers'):
            return obj.car_id.data_providers.filter(org_id=user_org).exists()

        elif hasattr(obj, 'car_id') and hasattr(obj.car_id, 'data_providers'):
            return obj.car_id.data_providers.filter(org_id=user_org).exists()

        elif hasattr(obj, 'car_id') and hasattr(obj.car_id, 'data_providers'):
            return obj.car_id.data_providers.filter(org_id=user_org).exists()

        elif hasattr(obj, 'car_id') and hasattr(obj.car_id, 'data_providers'):
            return obj.car_id.data_providers.filter(org_id=user_org).exists()

        elif hasattr(obj, 'car_id'):
            return obj.car_id.filter(data_providers__org_id=user_org).exists()

        elif hasattr(obj, 'org_id'):
            return obj.org_id == user_org

        elif hasattr(obj, 'user') and hasattr(obj.user, 'org'):
            return obj.user.org == user_org

        elif isinstance(obj, type(request.user)):
            return obj == request.user

        elif hasattr(obj, '_meta') and obj._meta.model_name == 'unitservice':
            return request.user.is_superuser

        elif hasattr(obj, '_meta'):
            model_name = obj._meta.model_name
            if model_name in ['language', 'carunit', 'sensorskey', 'sensorskeylocalization']:
                return request.user.is_superuser

            return False

        return False
