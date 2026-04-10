from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core.views import AutoDataListAPIView, CarListBySensorGroupAPIView, OrganizationListAPIView, \
    OrganizationDetailAPIView, OrgUserListAPIView, OrgUserDetailAPIView, CarListAPIView, CarDetailAPIView, \
    CarConsumptionListAPIView, CarConsumptionDetailAPIView, ReportQueryListAPIView, ReportQueryDetailAPIView, \
    CarReportListAPIView, CarReportDetailAPIView, DriverListAPIView, \
    DriverDetailAPIView, UserRegistrationAPIView, UserInfoAPIView, \
    CarLeaksCountAPIView, CarLeaksVolumeAPIView, \
    DailyLeaksSumAPIView, DailyLeaksCountAPIView, DataProviderListAPIView, DataProviderDetailAPIView, \
    CarLeaksAPIView, DataProviderCreateAPIView, \
    CarActiveStatusAPIView, MileageCalculationAPIView, SensorsKeyListAPIView, LanguageListAPIView, \
    CustomTokenObtainPairView, \
    CustomTokenRefreshView, MotohoursCalculationAPIView, VehicleSyncAPIView, CarDataRequestAPIView, CarBadDataAPIView, \
    StartTerminalMessagesParsingView, CarUnitListAPIView, UserCarListListView, UserCarListDetailView, \
    CarSensorsRawDataAPIView, CarMileageReportListAPIView, CarMileageReportDetailAPIView, TelegramRegisterAPIView, \
    CarFuelReportListAPIView, CarFuelReportDetailAPIView, TimezoneListAPIView

urlpatterns = [

    path('token', CustomTokenObtainPairView.as_view(), name='custom_token_obtain_pair'),
    path('token/refresh', CustomTokenRefreshView.as_view(), name='custom_token_refresh'),

    path('client/token', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('client/token/refresh', TokenRefreshView.as_view(), name='token_refresh'),

    path('user/info', UserInfoAPIView.as_view(), name='user_info'),

    path('timezones', TimezoneListAPIView.as_view(), name='timezone-list'),

    path('register', UserRegistrationAPIView.as_view(), name='register'),
    path('register/telegram', TelegramRegisterAPIView.as_view(), name='telegram-register'),

    path('leaks/count', CarLeaksCountAPIView.as_view(), name='leaks-count'),
    path('leaks/volume', CarLeaksVolumeAPIView.as_view(), name='leaks-volume'),
    path('leaks/daily-sum', DailyLeaksSumAPIView.as_view(), name='daily-leaks-sum'),
    path('leaks/daily-count', DailyLeaksCountAPIView.as_view(), name='daily-leaks-count'),
    path('leaks/history', CarLeaksAPIView.as_view(), name='car-leaks-list'),

    path('organizations', OrganizationListAPIView.as_view(), name='organization-list'),
    path('organizations/<uuid:pk>', OrganizationDetailAPIView.as_view(), name='organization-detail'),
    path('org-users', OrgUserListAPIView.as_view(), name='orguser-list'),
    path('org-users/<uuid:pk>', OrgUserDetailAPIView.as_view(), name='orguser-detail'),
    path("cars/bySensorGroup", CarListBySensorGroupAPIView.as_view(), name="car-sensor-list"),
    path('cars', CarListAPIView.as_view(), name='car-list'),

    path('cars/<uuid:pk>', CarDetailAPIView.as_view(), name='car-detail'),
    path('cars/car-units', CarUnitListAPIView.as_view(), name='car-units'),
    path('cars/bad-data', CarBadDataAPIView.as_view(), name='car-bad-data'),
    path('car-consumptions', CarConsumptionListAPIView.as_view(), name='car-consumption-list'),
    path('car-consumptions/<uuid:pk>', CarConsumptionDetailAPIView.as_view(), name='car-consumption-detail'),
    path('parsing/car-sensors-raw-data-charts', CarSensorsRawDataAPIView.as_view(), name='car-sensors-raw-data'),

    path('user-car-lists', UserCarListListView.as_view(), name='user-car-list-list'),
    path('user-car-lists/<uuid:pk>', UserCarListDetailView.as_view(), name='user-car-list-detail'),

    path('report-queries', ReportQueryListAPIView.as_view(), name='report-query-list'),
    path('report-queries/<uuid:pk>', ReportQueryDetailAPIView.as_view(), name='report-query-detail'),
    path('car-reports', CarReportListAPIView.as_view(), name='car-report-list'),
    path('car-reports/<uuid:pk>', CarReportDetailAPIView.as_view(), name='car-report-detail'),

    path('car-fuel-reports', CarFuelReportListAPIView.as_view(), name='fuel-report-list'),
    path('car-fuel-reports/<uuid:pk>', CarFuelReportDetailAPIView.as_view(), name='fuel-report-detail'),

    path('car-reports-mileage', CarMileageReportListAPIView.as_view(), name='car-report-mileage-list'),
    path('car-reports-mileage/<uuid:pk>', CarMileageReportDetailAPIView.as_view(), name='car-report-mileage-detail'),
    path('staff/auto-data', AutoDataListAPIView.as_view(), name="staff-auto-data"),
    path('drivers', DriverListAPIView.as_view(), name='driver-list'),
    path('drivers/<uuid:pk>', DriverDetailAPIView.as_view(), name='driver-detail'),
    path('dataprovider', DataProviderListAPIView.as_view(), name='dataprovider-list'),
    path('dataprovider/<uuid:pk>', DataProviderDetailAPIView.as_view(), name='dataprovider-detail'),
    path('dataprovider/create', DataProviderCreateAPIView.as_view(), name='data-provider-create'),

    path('car-active-status', CarActiveStatusAPIView.as_view(), name='car-active-status'),

    # path('sensors', SensorsMappingListByCardAPIView.as_view(), name='sensors'), ##TODO: Переписать

    path('sensors/keys', SensorsKeyListAPIView.as_view(), name='sensors-keys-list'),

    path('parsing/mileage', MileageCalculationAPIView.as_view(), name='mileage-test'),

    path('parsing/motohours', MotohoursCalculationAPIView.as_view(), name='motohours-test'),

    path('parsing/cars', VehicleSyncAPIView.as_view(), name='parsing-cars'),

    path('parsing/terminal-messages', CarDataRequestAPIView.as_view(), name='parsing-terminal-messages'),

    path(
        'parsing/parse-terminal-messages',
        StartTerminalMessagesParsingView.as_view(),
        name='parse_terminal_messages'
    ),

    path('languages', LanguageListAPIView.as_view(), name='languages-list')
]
