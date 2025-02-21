from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core.views import MediaUploadAPIView, AttachMediaToOrgAPIView, OrganizationListAPIView, \
    OrganizationDetailAPIView, OrgUserListAPIView, OrgUserDetailAPIView, CarListAPIView, CarDetailAPIView, \
    CarConsumptionListAPIView, CarConsumptionDetailAPIView, ReportQueryListAPIView, ReportQueryDetailAPIView, \
    MediaListAPIView, MediaDetailAPIView, CarReportListAPIView, CarReportDetailAPIView, DriverListAPIView, \
    DriverDetailAPIView, UserRegistrationAPIView

urlpatterns = [

    path('token', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh', TokenRefreshView.as_view(), name='token_refresh'),
    path('register', UserRegistrationAPIView.as_view(), name='register'),

    path('media/upload', MediaUploadAPIView.as_view(), name='media-upload'),
    path('media/attach', AttachMediaToOrgAPIView.as_view(), name='attach-media'),
    path('organizations', OrganizationListAPIView.as_view(), name='organization-list'),
    path('organizations/<uuid:pk>', OrganizationDetailAPIView.as_view(), name='organization-detail'),
    path('org-users', OrgUserListAPIView.as_view(), name='orguser-list'),
    path('org-users/<uuid:pk>', OrgUserDetailAPIView.as_view(), name='orguser-detail'),
    path('cars', CarListAPIView.as_view(), name='car-list'),
    path('cars/<uuid:pk>', CarDetailAPIView.as_view(), name='car-detail'),
    path('car-consumptions', CarConsumptionListAPIView.as_view(), name='car-consumption-list'),
    path('car-consumptions/<uuid:pk>', CarConsumptionDetailAPIView.as_view(), name='car-consumption-detail'),
    path('report-queries', ReportQueryListAPIView.as_view(), name='report-query-list'),
    path('report-queries/<uuid:pk>', ReportQueryDetailAPIView.as_view(), name='report-query-detail'),
    path('media', MediaListAPIView.as_view(), name='media-list'),
    path('media/<uuid:pk>', MediaDetailAPIView.as_view(), name='media-detail'),
    path('car-reports', CarReportListAPIView.as_view(), name='car-report-list'),
    path('car-reports/<uuid:pk>', CarReportDetailAPIView.as_view(), name='car-report-detail'),
    path('drivers', DriverListAPIView.as_view(), name='driver-list'),
    path('drivers/<uuid:pk>', DriverDetailAPIView.as_view(), name='driver-detail'),
]