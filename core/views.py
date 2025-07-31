from datetime import datetime

import pytz
from celery.exceptions import CeleryError
from django.db.models import Count, Sum
from django.db.models.functions import TruncDay
from django.shortcuts import render
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
    CarBadData
from .pagination import StandardResultsSetPagination
from .rest import (
    MEDIA_UPLOAD_SCHEMA, LEAKS_VOLUME_SCHEMA, LEAKS_COUNT_SCHEMA,
    DAILY_LEAKS_SUM_SCHEMA, DAILY_LEAKS_COUNT_SCHEMA, CAR_METRICS_SCHEMA, PROVIDER_DATA_REQUEST_SCHEMA,
    CAR_LEAKS_SCHEMA, DATA_PROVIDER_CREATE_SCHEMA, CAR_ACTIVE_STATUS_SCHEMA
)
from .serializers import (
    UserRegistrationSerializer, OrganizationOutputSerializer, OrgUserOutputSerializer, CarOutputSerializer,
    CarConsumptionOutputSerializer, ReportQueryOutputSerializer, MediaOutputSerializer,
    CarReportOutputSerializer, DriverOutputSerializer, UserOutputSerializer,
    CarMetricsQuerySerializer, DailyLeaksSerializer, CarLeaksSerializer,
    DataProviderOutputSerializer, CarLeaksFilterSerializer, DataProviderSerializer, CarBadDataOutputSerializer,
    CarActiveStatusSerializer
)
from .services.databases.influx_db import query_influxdb

from .services.media.utils import get_upload_path, calculate_file_hash
from .responses import error_response, user_registered_response, attach_media_response, user_response, success_response
from .permissions import IsOrgMember

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

        try:
            car = Car.objects.filter(
                id=car_id,
                data_providers__org_id=request.user.org
            ).first()
            if not car:
                logger.info(f"Автомобиль {car_id} не найден или не принадлежит организации {request.user.org.id}")
                return error_response(
                    "Автомобиль не найден или не принадлежит вашей организации",
                    status.HTTP_404_NOT_FOUND
                )
        except Car.DoesNotExist:
            logger.info(f"Автомобиль {car_id} не существует")
            return error_response("Автомобиль не найден", status.HTTP_404_NOT_FOUND)

        result = query_influxdb(
            car_id=car_id,
            metric=metric,
            period_from=period_from,
            period_due=period_due,
            agg_window=agg_window,
            agg_func=agg_func,
            org_id=request.user.org.id
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

        queryset = CarReport.objects.filter(
            car_id__data_providers__org_id=request.user.org,
            status=True
        )
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)

        daily_counts = (
            queryset
            .annotate(day=TruncDay('datetime'))
            .values('day')
            .annotate(value=Count('id'))
            .order_by('day')
        )

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

        queryset = CarReport.objects.filter(
            car_id__data_providers__org_id=request.user.org,
            status=True
        )
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

        queryset = CarReport.objects.filter(
            car_id__data_providers__org_id=request.user.org,
            status=True
        )
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)

        leaks_count = (
            queryset
            .values('car_id')
            .annotate(value=Count('id'))
            .order_by('car_id')
        )

        car_ids = [item['car_id'] for item in leaks_count]
        cars = Car.objects.filter(
            id__in=car_ids,
            data_providers__org_id=request.user.org
        )

        result = [
            CarLeaksSerializer({
                'id': str(car.id),
                'label': car.name,
                'value': next(item['value'] for item in leaks_count if item['car_id'] == car.id)
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

        queryset = CarReport.objects.filter(
            car_id__data_providers__org_id=request.user.org,
            status=True
        )
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)

        leaks_volume = (
            queryset
            .values('car_id')
            .annotate(value=Sum('volume'))
            .order_by('car_id')
        )

        car_ids = [item['car_id'] for item in leaks_volume]
        cars = Car.objects.filter(
            id__in=car_ids,
            data_providers__org_id=request.user.org
        )

        result = [
            CarLeaksSerializer({
                'id': str(car.id),
                'label': car.name,
                'value': next(item['value'] for item in leaks_volume if item['car_id'] == car.id)
            }).data
            for car in cars
        ]
        return success_response(result, status.HTTP_200_OK)


class CarActiveStatusAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**CAR_ACTIVE_STATUS_SCHEMA)
    def post(self, request):
        car_id = request.query_params.get('car_id')
        if not car_id:
            logger.error("Не указан параметр car_id")
            return error_response({"car_id": "Параметр car_id обязателен"}, status.HTTP_400_BAD_REQUEST)

        serializer = CarActiveStatusSerializer(data=request.data, context={'request': request, 'car_id': car_id})
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации данных: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        serializer.save()
        return success_response(
            {"car_id": car_id},
            status.HTTP_200_OK
        )


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
        is_save_bad_data = request.data.get('is_save_bad_data', False)  # Дефолт False

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

        try:
            if start_date:
                start_date = datetime.fromisoformat(start_date).replace(tzinfo=pytz.UTC)
            if end_date:
                end_date = datetime.fromisoformat(end_date).replace(tzinfo=pytz.UTC)
            if start_date and end_date and start_date > end_date:
                logger.error("start_date не может быть позже end_date")
                return error_response("start_date cannot be later than end_date", status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            logger.error(f"Неверный формат даты: {e}")
            return error_response(f"Invalid date format: {e}", status.HTTP_400_BAD_REQUEST)

        report_query = ReportQuery.objects.create(
            provider_id=provider,
            status="created",
            is_save_bad_data=is_save_bad_data
        )

        try:
            fetch_data_from_provider.delay(
                provider_name=provider_name,
                metadata=metadata,
                report_query_id=report_query.id,
                start_date=start_date,
                end_date=end_date
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
    permission_classes = [IsOrgMember]

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
                    f"Идентичный файл существует, но заявка не завершена успешно (статус: {existing_media.report_query_id.status}). Разрешаем повторную загрузку."
                )
        except Media.DoesNotExist:
            existing_media = None

        provider, _ = DataProvider.objects.get_or_create(
            name='csv',
            org_id_id=request.user.org.id,
            defaults={'metadata': {}}
        )
        report_query = ReportQuery.objects.create(
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

        if cars:
            valid_cars = Car.objects.filter(
                id__in=[car.id for car in cars],
                data_providers__org_id=request.user.org
            )
            if len(valid_cars) != len(cars):
                logger.error("Некоторые автомобили не принадлежат организации пользователя")
                return error_response(
                    "Один или несколько автомобилей не принадлежат вашей организации",
                    status.HTTP_404_NOT_FOUND
                )

        data_provider = DataProvider.objects.create(**validated_data)
        data_provider.org_id_id = request.user.org.id
        data_provider.save()
        if cars:
            data_provider.cars.set(valid_cars)
            logger.info(f"Автомобили {valid_cars} привязаны к провайдеру {data_provider.id}")

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
        return Car.objects.filter(
            data_providers__org_id=self.request.user.org.id
        ).annotate(Count(bad_data_count=Count('bad_data'))).order_by('id')


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

        car_exists = Car.objects.filter(
            id=car_id,
            data_providers__org_id=request.user.org
        ).exists()
        if not car_exists:
            logger.info(f"Автомобиль {car_id} не найден или не принадлежит организации {request.user.org.id}")
            return error_response(
                "Автомобиль не найден или не принадлежит вашей организации",
                status.HTTP_404_NOT_FOUND
            )

        queryset = self.get_queryset().filter(car_id=car_id)
        if period_from:
            queryset = queryset.filter(datetime__gte=period_from)
        if period_due:
            queryset = queryset.filter(datetime__lte=period_due)
        if volume_from is not None:
            queryset = queryset.filter(volume__gte=volume_from)
        if volume_to is not None:
            queryset = queryset.filter(volume__lte=volume_to)

        logger.info(f"Возвращены сливы для автомобиля {car_id}")
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


def api_docs_view(request):
    return render(request, 'api_docs.html', {
        'api_description_url': '/api/v1/swagger.json'
    })
