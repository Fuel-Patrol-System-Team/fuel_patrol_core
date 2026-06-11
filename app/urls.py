from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static



from core.swagger_config.docs_urls import docs_urlpatterns

urlpatterns = [
    path('admin', admin.site.urls),
    path('api/v1/fuel/', include('core.urls')),
]

urlpatterns += docs_urlpatterns
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)