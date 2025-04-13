import os
from datetime import datetime

from celery.exceptions import CeleryError
from django.db.models import Count, Sum
from django.db.models.functions import TruncDay
from django.shortcuts import render, get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework.filters import SearchFilter
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework import status
import logging
import mimetypes
import uuid

from app.tasks import parse_merged_data, process_raw_data_task, fetch_data_from_provider
from drf_yasg.utils import swagger_auto_schema
from .models import Media, Organization, ReportQuery, OrgUser, Car, CarConsumption, CarReport, Driver, DataProvider, \
    DriverCar
from .pagination import StandardResultsSetPagination
from .rest import (
    MEDIA_UPLOAD_SCHEMA, LEAKS_VOLUME_SCHEMA, LEAKS_COUNT_SCHEMA,
    DAILY_LEAKS_SUM_SCHEMA, DAILY_LEAKS_COUNT_SCHEMA, CAR_METRICS_SCHEMA, PROVIDER_DATA_REQUEST_SCHEMA
)
from .serializers import (
    UserRegistrationSerializer, OrganizationOutputSerializer, OrgUserOutputSerializer, CarOutputSerializer,
    CarConsumptionOutputSerializer, ReportQueryOutputSerializer, MediaOutputSerializer,
    CarReportOutputSerializer, DriverOutputSerializer, UserOutputSerializer,
    CarMetricsQuerySerializer, DailyLeaksSerializer, CarLeaksSerializer, DriverCarOutputSerializer,
    DataProviderOutputSerializer
)
from .services.databases.influx_db import query_influxdb

from .services.media.utils import get_upload_path, calculate_file_hash
from .responses import error_response, user_registered_response, attach_media_response, user_response, success_response
from .permissions import IsOrgMember

logger = logging.getLogger(__name__)

INFLUXDB_URL = os.getenv('INFLUXDB_URL')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN')
INFLUXDB_ORG = os.getenv('INFLUXDB_ORG')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET')


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

        try:
            car = get_object_or_404(Car, id=car_id, organization=request.user.org)
        except Car.DoesNotExist:
            logger.info(f"Автомобиль {car_id} не найден или не принадлежит организации {request.user.org.id}")
            return error_response("Автомобиль не найден или не принадлежит вашей организации",
                                  status.HTTP_404_NOT_FOUND)

        org_id = str(request.user.org.id)

        result = query_influxdb(
            org_id=org_id,
            car_id=car_id,
            metric=metric,
            period_from=period_from,
            period_due=period_due,
            agg_window=agg_window,
            agg_func=agg_func
        )

        logger.info(f"Выполнен запрос к InfluxDB для автомобиля {car_id}")

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

        queryset = CarReport.objects.filter(car__organization=request.user.org, status=True)
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)

        daily_counts = (queryset
                        .annotate(day=TruncDay('datetime'))
                        .values('day')
                        .annotate(value=Count('id'))
                        .order_by('day'))

        result = [{"value": item['value'], "day": item['day'].strftime('%Y-%m-%d')} for item in daily_counts]
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

        queryset = CarReport.objects.filter(car__organization=request.user.org, status=True)
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)

        daily_sums = (queryset
                      .annotate(day=TruncDay('datetime'))
                      .values('day')
                      .annotate(value=Sum('volume'))
                      .order_by('day'))

        result = [{"value": item['value'], "day": item['day'].strftime('%Y-%m-%d')} for item in daily_sums]
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

        queryset = CarReport.objects.filter(car__organization=request.user.org, status=True)
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)

        leaks_count = (queryset
                       .values('car')
                       .annotate(value=Count('id'))
                       .order_by('car'))

        car_ids = [item['car'] for item in leaks_count]
        cars = Car.objects.filter(id__in=car_ids).select_related('organization')

        result = [
            CarLeaksSerializer({
                'id': str(car.id),
                'label': car.name,
                'value': next(item['value'] for item in leaks_count if item['car'] == car.id)
            }).data
            for car in cars
        ]
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

        queryset = CarReport.objects.filter(car__organization=request.user.org, status=True)
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)

        leaks_volume = (queryset
                        .values('car')
                        .annotate(value=Sum('volume'))
                        .order_by('car'))

        car_ids = [item['car'] for item in leaks_volume]
        cars = Car.objects.filter(id__in=car_ids).select_related('organization')

        result = [
            CarLeaksSerializer({
                'id': str(car.id),
                'label': car.name,
                'value': next(item['value'] for item in leaks_volume if item['car'] == car.id)
            }).data
            for car in cars
        ]
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


class ProviderDataRequestAPIView(APIView):
    # permission_classes = [IsOrgMember]

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
        if not provider_name:
            logger.error("Имя провайдера не указано")
            return error_response("Provider name is required", status.HTTP_400_BAD_REQUEST)

        organization = request.user.org
        if not organization:
            logger.error("Организация не найдена для пользователя")
            return error_response("User must be associated with an organization", status.HTTP_400_BAD_REQUEST)

        try:
            provider = DataProvider.objects.get(name=provider_name)
        except DataProvider.DoesNotExist:
            logger.error(f"Провайдер {provider_name} не найден")
            return error_response(f"Provider {provider_name} not found", status.HTTP_400_BAD_REQUEST)

        metadata = provider.metadata or {}
        if not metadata:
            logger.error(f"Метаданные провайдера {provider_name} отсутствуют")
            return error_response(f"Provider {provider_name} metadata is required", status.HTTP_400_BAD_REQUEST)

        report_query = ReportQuery.objects.create(
            organization_id=organization,
            provider_id=provider,
            status="created"
        )

        try:

            fetch_data_from_provider.delay(
                provider_name=provider_name,
                metadata=metadata,
                report_query_id=report_query.id
            )
            logger.info(f"Заявка на получение данных от провайдера {provider_name} создана: {report_query.id}")
            return success_response({"report_query_id": report_query.id}, status.HTTP_201_CREATED)

        except CeleryError as e:
            logger.error(f"Ошибка Celery при запуске задачи: {e}")
            report_query.status = "error"
            report_query.save()
            return error_response(f"Failed to launch provider data task: {e}", status.HTTP_503_SERVICE_UNAVAILABLE)


class MediaUploadAPIView(APIView):
    parser_classes = [MultiPartParser]

    # permission_classes = [IsOrgMember]

    @swagger_auto_schema(**MEDIA_UPLOAD_SCHEMA)
    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            logger.error("Файл не загружен")
            return error_response("No file uploaded", status.HTTP_400_BAD_REQUEST)

        file_type = request.data.get('type')
        if file_type not in ["raw", "auto"]:
            logger.error(f"Неверный тип файла: {file_type}")
            return error_response("Invalid file type", status.HTTP_400_BAD_REQUEST)

        file_id = uuid.uuid4()
        media_type, _ = mimetypes.guess_type(uploaded_file.name)
        file_extension = uploaded_file.name.split('.')[-1].lower()
        file_hash = calculate_file_hash(uploaded_file)
        new_filename = f"{file_id}.{file_extension}"
        file_size = uploaded_file.size
        organization = request.user.org_id

        try:
            existing_media = Media.objects.get(
                filename=uploaded_file.name,
                size=file_size,
                type=file_type,
                file_hash=file_hash
            )
            if existing_media.report_query_id.status == "completed":
                logger.info(f"Идентичный файл уже существует и заявка завершена успешно: {existing_media.id}")
                return error_response("Такой файл уже был загружен и обработан", status.HTTP_400_BAD_REQUEST)
            else:
                logger.info(
                    f"Идентичный файл существует, но заявка не завершена успешно (статус: {existing_media.report_query_id.status}). Разрешаем повторную загрузку.")
        except Media.DoesNotExist:
            existing_media = None

        try:
            organization = Organization.objects.get(id=organization)
        except Organization.DoesNotExist:
            return error_response({"error": f"Organization with id {organization} not found"},
                                  status.HTTP_404_NOT_FOUND)

        provider, _ = DataProvider.objects.get_or_create(
            name='csv',
            defaults={'metadata': {}}
        )
        report_query = ReportQuery.objects.create(
            organization_id=organization,
            provider_id=provider,
            status="created"
        )

        media = Media(
            id=file_id,
            media_type=media_type,
            size=file_size,
            filename=uploaded_file.name,
            type=file_type,
            report_query_id=report_query,
            file_hash=file_hash
        )
        upload_path = get_upload_path(new_filename)
        media.file.save(upload_path, uploaded_file)
        media.save()
        report_query.save()

        try:

            if file_type == "auto":
                parse_merged_data.delay(report_query.id)
            elif file_type == "raw":
                # if not ReportQuery.objects.filter(
                #         organization_id=organization,
                #         status="completed"
                # ).exists():
                #     return error_response("Загрузите 'auto' сначала", status.HTTP_400_BAD_REQUEST)
                process_raw_data_task.delay(report_query.id)

            logger.info(f"Медиафайл успешно загружен: {media.id}")
            return attach_media_response(report_query)

        except CeleryError as e:
            logger.error(f"Ошибка Celery при запуске задачи: {e}")
            return error_response(f"Failed to launch processing task: {e}", status.HTTP_503_SERVICE_UNAVAILABLE)


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


class OrganizationListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = OrganizationOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name', 'bot_token', 'chat_id']

    def get_queryset(self):
        return Organization.objects.filter(users=self.request.user).order_by('id')


class OrganizationDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
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
    filterset_fields = ['name']
    search_fields = ['name', 'description']

    def get_queryset(self):
        return Car.objects.all().order_by('id')


class CarDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarOutputSerializer
    queryset = Car.objects.all()
    lookup_field = 'pk'


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
    filterset_fields = ['status', 'organization_id']
    search_fields = ['organization_id__name', 'status']

    def get_queryset(self):
        return ReportQuery.objects.filter(organization_id=self.request.user.org_id).select_related(
            'organization_id', 'provider_id').order_by('id')


class ReportQueryDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = ReportQueryOutputSerializer
    queryset = ReportQuery.objects.all()
    lookup_field = 'pk'


class MediaListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = MediaOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['type', 'report_query_id']
    search_fields = ['filename', 'media_type', 'type']

    def get_queryset(self):
        return Media.objects.filter(report_query_id__organization_id=self.request.user.org_id).select_related(
            'report_query_id__organization_id').order_by('id')


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
        return CarReport.objects.all().select_related('car_id').order_by('datetime')


class CarReportDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    queryset = CarReport.objects.all()
    lookup_field = 'pk'


class DriverListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['phone']
    search_fields = ['fullname', 'address', 'phone']

    def get_queryset(self):
        return Driver.objects.filter(driver_cars__car_id__in=Car.objects.all()).prefetch_related(
            'driver_cars__car_id').order_by('id')


class DriverDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverOutputSerializer
    queryset = Driver.objects.all()
    lookup_field = 'pk'


class DataProviderListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DataProviderOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name', 'car_id']
    search_fields = ['name', 'car_id__name']

    def get_queryset(self):
        return DataProvider.objects.all().select_related('car_id').order_by('id')


class DataProviderDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DataProviderOutputSerializer
    queryset = DataProvider.objects.all()
    lookup_field = 'pk'


class DriverCarListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverCarOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['driver_id', 'car_id']
    search_fields = ['driver_id__fullname', 'car_id__name']

    def get_queryset(self):
        return DriverCar.objects.all().select_related('driver_id', 'car_id').order_by('driver_id')


class DriverCarDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverCarOutputSerializer
    queryset = DriverCar.objects.all()
    lookup_field = 'pk'


def api_docs_view(request):
    return render(request, 'api_docs.html', {
        'api_description_url': '/api/v1/swagger.json'
    })
