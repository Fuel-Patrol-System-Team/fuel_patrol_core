from django.contrib import admin
from django.urls import path, include
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from django.conf import settings
from django.conf.urls.static import static
from rest_framework import permissions

from core.views import api_docs_view

schema_view = get_schema_view(
    openapi.Info(
        title="Fuel API",
        default_version="v1",
        description="Документация API для системы учета топлива",
        terms_of_service="https://www.example.com/terms/",
        contact=openapi.Contact(email="support@example.com"),
        license=openapi.License(name="MIT License"),
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path('admin', admin.site.urls),
    path('api/v1/fuel/', include('core.urls')),

    path('api/v1/swagger', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('api/v1/redoc', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('api/v1/swagger.json', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('api/v1/docs', api_docs_view, name='api-docs'),

]


urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
