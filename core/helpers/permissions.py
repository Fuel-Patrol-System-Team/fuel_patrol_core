from rest_framework.permissions import BasePermission

class IsOrgMember(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, 'org') and request.user.org is not None

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'organization'):
            return obj.organization == request.user.org
        elif hasattr(obj, 'org'):
            return obj.org == request.user.org
        elif hasattr(obj, 'car') and hasattr(obj.car, 'organization'):
            return obj.car.organization == request.user.org
        elif hasattr(obj, 'report_query'):
            return obj.report_query.organization == request.user.org
        return False