from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core.views import MediaUploadAPIView, OrganizationListAPIView, \
    OrganizationDetailAPIView, OrgUserListAPIView, OrgUserDetailAPIView, CarListAPIView, CarDetailAPIView, \
    CarConsumptionListAPIView, CarConsumptionDetailAPIView, ReportQueryListAPIView, ReportQueryDetailAPIView, \
    MediaListAPIView, MediaDetailAPIView, CarReportListAPIView, CarReportDetailAPIView, DriverListAPIView, \
    DriverDetailAPIView, UserRegistrationAPIView, UserInfoAPIView, CarLeaksCountAPIView, CarLeaksVolumeAPIView, \
    DailyLeaksSumAPIView, DailyLeaksCountAPIView, CarMetricsAPIView, DataProviderListAPIView, DataProviderDetailAPIView, \
    ProviderDataRequestAPIView, CarLeaksAPIView, DataProviderCreateAPIView, CarBadDataListByCarAPIView

urlpatterns = [

    path('token', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh', TokenRefreshView.as_view(), name='token_refresh'),

    path('user/info', UserInfoAPIView.as_view(), name='user_info'),

    path('register', UserRegistrationAPIView.as_view(), name='register'),

    path('car-metrics', CarMetricsAPIView.as_view(), name='car-metrics'),

    path('media/upload', MediaUploadAPIView.as_view(), name='media-upload'),

    path('leaks/count', CarLeaksCountAPIView.as_view(), name='leaks-count'),
    path('leaks/volume', CarLeaksVolumeAPIView.as_view(), name='leaks-volume'),
    path('leaks/daily-sum', DailyLeaksSumAPIView.as_view(), name='daily-leaks-sum'),
    path('leaks/daily-count', DailyLeaksCountAPIView.as_view(), name='daily-leaks-count'),
    path('leaks/history', CarLeaksAPIView.as_view(), name='car-leaks-list'),

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
    path('dataprovider', DataProviderListAPIView.as_view(), name='dataprovider-list'),
    path('dataprovider/<uuid:pk>', DataProviderDetailAPIView.as_view(), name='dataprovider-detail'),
    path('dataprovider/create', DataProviderCreateAPIView.as_view(), name='data-provider-create'),
    path('provider-data-request', ProviderDataRequestAPIView.as_view(), name='provider-data-request'),
    path('cars/<uuid:car_id>/bad-data', CarBadDataListByCarAPIView.as_view(), name='car-bad-data-list'),
]
