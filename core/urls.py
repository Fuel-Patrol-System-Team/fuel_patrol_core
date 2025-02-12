from django.urls import path
from core import views

urlpatterns = [
    path('media/upload', views.media_upload, name='media_upload'),
    path('media/attach', views.attach_media_to_org, name='attach_media_to_org'),
]
