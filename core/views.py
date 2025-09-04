from django.db.models import Count
from django.shortcuts import render
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework.filters import SearchFilter
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework import status
import logging

from drf_yasg.utils import swagger_auto_schema

from core.helpers.cars import get_car_or_error, fetch_car_metrics, filter_leaks_by_period, aggregate_daily_counts, \
    get_daily_leaks_sum, get_car_leaks_count, get_car_leaks_volume, update_car_active_status, check_car_exists, \
    filter_car_leaks
from .helpers.data_provider import create_provider_data_request, validate_provider_request, validate_provider_cars, \
    create_data_provider
from .helpers.media import create_media_instance, validate_media_upload, process_media_task
from .models import Media, Organization, ReportQuery, OrgUser, Car, CarConsumption, CarReport, Driver, DataProvider, \
    CarBadData, SensorsMapping
from core.helpers.pagination import StandardResultsSetPagination
from core.helpers.rest import (
    MEDIA_UPLOAD_SCHEMA, LEAKS_VOLUME_SCHEMA, LEAKS_COUNT_SCHEMA,
    DAILY_LEAKS_SUM_SCHEMA, DAILY_LEAKS_COUNT_SCHEMA, CAR_METRICS_SCHEMA, PROVIDER_DATA_REQUEST_SCHEMA,
    CAR_LEAKS_SCHEMA, DATA_PROVIDER_CREATE_SCHEMA, CAR_ACTIVE_STATUS_SCHEMA
)
from .serializers import (
    SensorsMappingOutputSerializer, UserRegistrationSerializer, OrganizationOutputSerializer, OrgUserOutputSerializer, CarOutputSerializer,
    CarConsumptionOutputSerializer, ReportQueryOutputSerializer, MediaOutputSerializer,
    CarReportOutputSerializer, DriverOutputSerializer, UserOutputSerializer,
    CarMetricsQuerySerializer, DailyLeaksSerializer, DataProviderOutputSerializer, CarLeaksFilterSerializer,
    DataProviderSerializer, CarBadDataOutputSerializer
)

from core.helpers.responses import error_response, user_registered_response, attach_media_response, user_response, \
    success_response
from core.helpers.permissions import IsOrgMember

logger = logging.getLogger(__name__)


class CarMetricsAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**CAR_METRICS_SCHEMA)
    def get(self, request):
        serializer = CarMetricsQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        car_id = data['car']
        period_from = data.get('periodFrom')
        period_due = data.get('periodDue')
        agg_window = data.get('agg')
        agg_func = data.get('func')
        metric = data.get('metric')

        car, error = get_car_or_error(car_id, request.user.org.id)
        if error:
            return error

        result = fetch_car_metrics(car_id, metric, period_from, period_due, agg_window, agg_func, request.user.org.id)
        if not result:
            logger.info(f"Данные для автомобиля {car_id} не найдены")
            return error_response("Данные не найдены", status.HTTP_404_NOT_FOUND)

        return success_response(result, status.HTTP_200_OK)


class DailyLeaksCountAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**DAILY_LEAKS_COUNT_SCHEMA)
    def get(self, request):
        serializer = DailyLeaksSerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        period_from = data.get('periodFrom')
        period_due = data.get('periodDue')

        queryset = filter_leaks_by_period(request.user.org.id, period_from, period_due)
        result = aggregate_daily_counts(queryset)
        return success_response(result, status.HTTP_200_OK)


class DailyLeaksSumAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**DAILY_LEAKS_SUM_SCHEMA)
    def get(self, request):
        serializer = DailyLeaksSerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        period_from = data.get('periodFrom')
        period_due = data.get('periodDue')

        result = get_daily_leaks_sum(request.user.org.id, period_from, period_due)
        return success_response(result, status.HTTP_200_OK)


class CarLeaksCountAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**LEAKS_COUNT_SCHEMA)
    def get(self, request):
        serializer = DailyLeaksSerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        period_from = data.get('periodFrom')
        period_due = data.get('periodDue')

        result = get_car_leaks_count(request.user.org.id, period_from, period_due)
        return success_response(result, status.HTTP_200_OK)


class CarLeaksVolumeAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**LEAKS_VOLUME_SCHEMA)
    def get(self, request):
        serializer = DailyLeaksSerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        period_from = data.get('periodFrom')
        period_due = data.get('periodDue')

        result = get_car_leaks_volume(request.user.org.id, period_from, period_due)
        return success_response(result, status.HTTP_200_OK)


class CarActiveStatusAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**CAR_ACTIVE_STATUS_SCHEMA)
    def post(self, request):
        car_id = request.query_params.get('car_id')
        result, error = update_car_active_status(car_id, request.data, request)
        if error:
            return error
        return success_response(result, status.HTTP_200_OK)


class UserInfoAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(responses={200: UserOutputSerializer(), 401: "Unauthorized"})
    def get(self, request):
        user = request.user
        if not user:
            logger.error("Пользователь не авторизован")
            return error_response("Unauthorized", status.HTTP_401_UNAUTHORIZED)

        serializer = UserOutputSerializer({
            'id': user.id,
            'username': user.username,
            'organization': user.org.name
        })
        return user_response(serializer.data, status.HTTP_200_OK)


# Замените этот код в файле с вашей вьюхой

class ProviderDataRequestAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(
        operation_description="Создаёт заявку на получение данных от провайдера.",
        request_body=PROVIDER_DATA_REQUEST_SCHEMA,
        responses={
            201: "Заявка успешно создана",
            400: "Неверные данные",
            503: "Ошибка при запуске задачи"
        }
    )
    def post(self, request):
        provider_name = request.data.get('provider_name')
        start_date = request.data.get('start_date')
        end_date = request.data.get('end_date')
        is_save_bad_data = request.data.get('is_save_bad_data', False)

        is_valid, result = validate_provider_request(provider_name, start_date, end_date, request.user.org)
        if not is_valid:
            return result

        provider, metadata, start_date, end_date = result


        report_query_id, error = create_provider_data_request(
            provider,  start_date, end_date, is_save_bad_data
        )
        if error:
            return error

        return success_response({"report_query_id": report_query_id}, status.HTTP_201_CREATED)

class MediaUploadAPIView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**MEDIA_UPLOAD_SCHEMA)
    def post(self, request):
        uploaded_file = request.FILES.get('file')
        file_type = request.data.get('type')

        is_valid, error = validate_media_upload(uploaded_file, file_type)
        if not is_valid:
            return error

        media, report_query = create_media_instance(uploaded_file, file_type, request.user.org.id)
        success, error = process_media_task(report_query.id, file_type)
        if not success:
            return error

        return attach_media_response(report_query)


class UserRegistrationAPIView(APIView):
    @swagger_auto_schema(
        responses={201: UserRegistrationSerializer(), 400: "Bad Request", 404: "Organization not found"})
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации данных пользователя: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        user = serializer.save()
        logger.info(f"Пользователь {user.username} успешно зарегистрирован")
        return user_registered_response(user)


class DataProviderCreateAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**DATA_PROVIDER_CREATE_SCHEMA)
    def post(self, request):
        serializer = DataProviderSerializer(data=request.data)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации данных провайдера: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        cars = validated_data.pop('cars', [])

        is_valid, valid_cars = validate_provider_cars(cars, request.user.org.id)
        if not is_valid:
            return valid_cars

        data_provider = create_data_provider(validated_data, request.user.org.id, valid_cars)
        output_serializer = DataProviderOutputSerializer(data_provider)
        logger.info(f"Создан DataProvider: {data_provider.id} пользователем {request.user.username}")
        return success_response(output_serializer.data, status.HTTP_201_CREATED)


class OrganizationListAPIView(ListAPIView):
    serializer_class = OrganizationOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name', 'bot_token', 'chat_id']

    def get_queryset(self):
        return Organization.objects.filter(users=self.request.user).order_by('id')


class OrganizationDetailAPIView(RetrieveAPIView):
    serializer_class = OrganizationOutputSerializer
    queryset = Organization.objects.all()
    lookup_field = 'pk'


class OrgUserListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = OrgUserOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['org_id', 'username']
    search_fields = ['username', 'org_id__name']

    def get_queryset(self):
        return OrgUser.objects.filter(org_id=self.request.user.org_id).select_related('org_id').order_by('id')


class OrgUserDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = OrgUserOutputSerializer
    queryset = OrgUser.objects.all()
    lookup_field = 'pk'


class CarListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name', 'is_active', 'is_tarrified']
    search_fields = ['name', 'description']

    def get_queryset(self):
        result = Car.objects.filter(
            data_providers__org_id=self.request.user.org.id
        ).annotate(bad_data_count=Count('bad_data')).order_by('id')
        return result


class CarDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return Car.objects.filter(
            data_providers__org_id=self.request.user.org
        )


class CarConsumptionListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarConsumptionOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car_id', 'valid_period']
    search_fields = ['car_id__name', 'valid_period']

    def get_queryset(self):
        return CarConsumption.objects.filter(car_id__in=Car.objects.all()).select_related('car_id').order_by('id')


class CarConsumptionDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarConsumptionOutputSerializer
    queryset = CarConsumption.objects.all()
    lookup_field = 'pk'


class ReportQueryListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = ReportQueryOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['status']
    search_fields = ['provider_id__name', 'status']

    def get_queryset(self):
        return ReportQuery.objects.filter(
            provider_id__org_id=self.request.user.org
        ).select_related('provider_id')


class ReportQueryDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = ReportQueryOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return ReportQuery.objects.filter(
            provider_id__org_id=self.request.user.org
        ).select_related('provider_id')


class MediaListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = MediaOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['type', 'report_query_id']
    search_fields = ['filename', 'media_type', 'type']

    def get_queryset(self):
        return Media.objects.filter(
            report_query_id__provider_id__org_id=self.request.user.org
        ).select_related('report_query_id__provider_id').order_by('id')


class MediaDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = MediaOutputSerializer
    queryset = Media.objects.all()
    lookup_field = 'pk'


class CarReportListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car_id', 'datetime', 'status']
    search_fields = ['car_id__name', 'datetime']

    def get_queryset(self):
        return CarReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        ).select_related('car_id').order_by('datetime')


class CarReportDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return CarReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        )


class DriverListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['phone']
    search_fields = ['fullname', 'address', 'phone']

    def get_queryset(self):
        return Driver.objects.filter(
            driver_cars__car_id__data_providers__org_id=self.request.user.org
        ).prefetch_related('driver_cars__car_id').order_by('id')


class DriverDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return Driver.objects.filter(
            driver_cars__car_id__data_providers__org_id=self.request.user.org
        )


class DataProviderListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DataProviderOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name']

    def get_queryset(self):
        return DataProvider.objects.filter(
            org_id=self.request.user.org
        ).order_by('id')


class DataProviderDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DataProviderOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return DataProvider.objects.filter(
            org_id=self.request.user.org
        )


class CarLeaksAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['datetime', 'status', 'volume']
    search_fields = ['car_id__name']

    @swagger_auto_schema(**CAR_LEAKS_SCHEMA)
    def get(self, request, *args, **kwargs):
        serializer = CarLeaksFilterSerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        car_id = data['car_id']
        period_from = data.get('periodFrom')
        period_due = data.get('periodDue')
        volume_from = data.get('volume_from')
        volume_to = data.get('volume_to')

        exists, error = check_car_exists(car_id, request.user.org.id)
        if not exists:
            return error

        queryset = filter_car_leaks(self.get_queryset(), car_id, period_from, period_due, volume_from, volume_to)
        self.queryset = queryset
        return self.list(request, *args, **kwargs)

    def get_queryset(self):
        return CarReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org,
            status=True
        ).select_related('car_id').order_by('-datetime')


class CarBadDataListByCarAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarBadDataOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['datetime']
    search_fields = ['reason']

    def get_queryset(self):
        car_id = self.kwargs['car_id']
        return CarBadData.objects.filter(
            car_id=car_id,
            car_id__data_providers__org_id=self.request.user.org.id
        ).select_related('car_id').order_by('-datetime')

class SensorsMappingListByCardAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = SensorsMappingOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter]
    search_fields = ['label']

    def get_queryset(self):
        return SensorsMapping.objects.filter(
            car_id__data_providers__org_id = self.request.user.org.id
        )

def api_docs_view(request):
    return render(request, 'api_docs.html', {
        'api_description_url': '/api/v1/swagger.json'
    })
