from datetime import datetime

from celery import group

from django.db.models import Count
from django.shortcuts import render

from django_filters.rest_framework import DjangoFilterBackend

from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView, get_object_or_404
from rest_framework.parsers import MultiPartParser
from rest_framework import status
import logging

from drf_yasg.utils import swagger_auto_schema
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from app import settings
from core.helpers.cars import get_car_or_error, fetch_car_metrics, filter_leaks_by_period, aggregate_daily_counts, \
    get_daily_leaks_sum, get_car_leaks_count, get_car_leaks_volume, update_car_active_status, check_car_exists, \
    filter_car_leaks
from .helpers.data_provider import create_provider_data_request, validate_provider_request, validate_provider_cars, \
    create_data_provider
from .helpers.media import create_media_instance, validate_media_upload, process_media_task

from .helpers.sensors_mapping import get_user_language_code, get_car_sensors_values, get_sensors_keys_with_localization
from .models import Media, Organization, ReportQuery, OrgUser, Car, CarConsumption, CarReport, Driver, DataProvider, \
    CarBadData, Language
from core.helpers.pagination import StandardResultsSetPagination
from core.helpers.rest import (
    MEDIA_UPLOAD_SCHEMA, LEAKS_VOLUME_SCHEMA, LEAKS_COUNT_SCHEMA,
    DAILY_LEAKS_SUM_SCHEMA, DAILY_LEAKS_COUNT_SCHEMA, CAR_METRICS_SCHEMA, PROVIDER_DATA_REQUEST_SCHEMA,
    CAR_LEAKS_SCHEMA, DATA_PROVIDER_CREATE_SCHEMA, CAR_ACTIVE_STATUS_SCHEMA, MILEAGE_REQUEST_SCHEMA,
    MOTOHOURS_REQUEST_SCHEMA, VEHICLE_SYNC_SCHEMA, CAR_DATA_REQUEST_SCHEMA, BAD_DATA_SCHEMA
)
from app.tasks import sync_vehicles_task, process_single_car_data_task
from .serializers import (
    MileageTestSerializer, UserRegistrationSerializer, OrganizationOutputSerializer, OrgUserOutputSerializer,
    CarOutputSerializer,
    CarConsumptionOutputSerializer, ReportQueryOutputSerializer, MediaOutputSerializer,
    CarReportOutputSerializer, DriverOutputSerializer, UserOutputSerializer,
    CarMetricsQuerySerializer, DailyLeaksSerializer, DataProviderOutputSerializer, CarLeaksFilterSerializer,
    DataProviderSerializer, CarBadDataOutputSerializer, SensorsKeyOutputSerializer, LanguageSerializer,
    CarBadDataSerializer
)

from core.helpers.responses import error_response, user_registered_response, attach_media_response, user_response, \
    success_response
from core.helpers.permissions import IsOrgMember
from .services.data_providers.utils import provider_factory
from .services.providers.glonass.glonassoft_mileage_provider import GlonassSoftMileageProvider
from .services.providers.glonass.glonassoft_motohours_provider import GlonassSoftMotohoursProvider
from .services.providers.mileage_calculation_service import MileageCalculationService
from .services.providers.motohours_calculation_service import MotohoursCalculationService
from .services.providers.report_service import ReportService

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
            provider, start_date, end_date, is_save_bad_data
        )
        if error:
            return error

        return success_response({"report_query_id": report_query_id}, status.HTTP_201_CREATED)


class VehicleSyncAPIView(APIView):
    @swagger_auto_schema(
        operation_description="Запускает асинхронную синхронизацию транспортных средств с созданием отчета",
        request_body=VEHICLE_SYNC_SCHEMA,
        responses={
            202: "Задача синхронизации запущена",
            400: "Неверные данные",
            404: "Провайдер не найден"
        }
    )
    def post(self, request):
        provider_name = request.data.get('provider_name')

        if not provider_name:
            return Response({"error": "Provider name is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not request.user.org:
            return Response({"error": "User must be associated with an organization"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            provider = DataProvider.objects.get(name=provider_name, org_id=request.user.org)
        except DataProvider.DoesNotExist:
            return Response({"error": f"Provider {provider_name} not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            task = sync_vehicles_task.delay(
                provider_id=str(provider.id),
                organization_id=str(request.user.org.id)
            )

            return Response({
                "task_id": task.id,
                "message": "Синхронизация транспортных средств запущена с созданием отчета",
                "provider": provider_name,
                "report_created": True,
                "status_endpoint": f"/api/tasks/{task.id}/status/"
            }, status=status.HTTP_202_ACCEPTED)

        except Exception as e:
            logger.error(f"Ошибка запуска задачи синхронизации: {e}")
            return Response(
                {"error": "Failed to start synchronization task"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )


class CarDataRequestAPIView(APIView):
    @swagger_auto_schema(
        operation_description="Создаёт заявку на получение и обработку данных по конкретным машинам или по всем машинам провайдера",
        request_body=CAR_DATA_REQUEST_SCHEMA,
        responses={
            202: "Задачи обработки запущены",
            400: "Неверные данные",
            404: "Провайдер или машины не найдены"
        }
    )
    def post(self, request):
        provider_name = request.data.get('provider_name')
        car_ids = request.data.get('car_ids', [])
        parse_all = request.data.get('parse_all', False)
        start_date = request.data.get('start_date')
        end_date = request.data.get('end_date')
        is_save_bad_data = request.data.get('is_save_bad_data', False)

        if not provider_name:
            return error_response("Provider name is required", status.HTTP_400_BAD_REQUEST)

        if not start_date or not end_date:
            return error_response("Start date and end date are required", status.HTTP_400_BAD_REQUEST)

        if not parse_all and not car_ids:
            return error_response("Either car_ids list or parse_all=True is required", status.HTTP_400_BAD_REQUEST)

        if parse_all:
            car_ids = []
            logger.info(f"parse_all flag is True, will process all cars for provider {provider_name}")

        try:
            provider = DataProvider.objects.get(name=provider_name)
        except DataProvider.DoesNotExist:
            return error_response(f"Provider {provider_name} not found", status.HTTP_404_NOT_FOUND)

        try:
            if parse_all:
                cars = Car.objects.filter(provider=provider)
                if not cars.exists():
                    return error_response(
                        f"No cars found for provider {provider_name}",
                        status.HTTP_404_NOT_FOUND
                    )
                car_ids = [str(car.id) for car in cars]
            else:
                cars = Car.objects.filter(id__in=car_ids, provider=provider)
                found_car_ids = set(str(car.id) for car in cars)
                missing_car_ids = set(car_ids) - found_car_ids

                if missing_car_ids:
                    return error_response(
                        f"Some cars not found: {list(missing_car_ids)}",
                        status.HTTP_404_NOT_FOUND
                    )

        except Exception as e:
            logger.error(f"Error checking cars existence: {e}")
            return error_response("Error validating car IDs", status.HTTP_400_BAD_REQUEST)

        task_group = []
        report_query_ids = []
        car_names = {}

        try:
            for car in cars:
                report_query, report_details = ReportService.create_report(
                    provider_id=str(provider.id),
                    report_type=ReportQuery.ReportType.LEAKS,
                    is_save_bad_data=is_save_bad_data
                )

                task = process_single_car_data_task.s(
                    car_id=str(car.id),
                    provider_id=str(provider.id),
                    report_query_id=str(report_query.id),
                    start_date=start_date,
                    end_date=end_date
                )

                task_group.append(task)
                report_query_ids.append(str(report_query.id))
                car_names[str(car.id)] = car.name

            job = group(task_group)
            result = job.apply_async()

            return success_response({
                "task_group_id": result.id,
                "report_query_ids": report_query_ids,
                "total_cars": len(car_ids),
                "car_names": car_names,
                "message": f"Обработка данных для {len(car_ids)} машин запущена",
                "provider_name": provider_name,
                "parse_all_mode": parse_all,
                "status_endpoint": f"/api/tasks/{result.id}/status/",
                "individual_status_endpoint": "/api/tasks/{task_id}/status/"
            }, status.HTTP_202_ACCEPTED)

        except Exception as e:
            logger.error(f"Ошибка запуска задач обработки: {e}")

            for report_query_id in report_query_ids:
                try:
                    report_query = ReportQuery.objects.get(id=report_query_id)
                    ReportService.complete_report_error(
                        report_query, f"Failed to start processing task: {str(e)}"
                    )
                except Exception:
                    pass

            return error_response(
                "Failed to start processing tasks",
                status.HTTP_503_SERVICE_UNAVAILABLE
            )


class MileageCalculationAPIView(APIView):
    @swagger_auto_schema(
        operation_description="Расчет пробега для автомобиля за период с созданием отчета",
        request_body=MILEAGE_REQUEST_SCHEMA,
        responses={
            200: "Успешно",
            400: "Неверные данные",
            401: "Ошибка аутентификации",
            404: "Автомобиль не найден",
            500: "Ошибка обработки"
        }
    )
    def post(self, request):
        car_id = request.data.get("car_id")
        agg = request.data.get("agg")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")
        is_save_bad_data = request.data.get("is_save_bad_data", True)

        try:
            if start_date:
                start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            if end_date:
                end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except ValueError as e:
            return Response({"error": f"Неверный формат даты: {e}"}, status=400)

        result, status_code = MileageCalculationService.calculate_mileage(
            car_id=car_id,
            agg=agg,
            start_date=start_date,
            end_date=end_date,
            is_save_bad_data=is_save_bad_data
        )

        return Response(result, status=status_code)


class MotohoursCalculationAPIView(APIView):
    @swagger_auto_schema(
        operation_description="Расчет моточасов для автомобиля за период с созданием отчета",
        request_body=MOTOHOURS_REQUEST_SCHEMA,
        responses={
            200: "Успешно",
            400: "Неверные данные",
            401: "Ошибка аутентификации",
            404: "Автомобиль не найден",
            500: "Ошибка обработки"
        }
    )
    def post(self, request):
        car_id = request.data.get("car_id")
        agg = request.data.get("agg")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")
        is_save_bad_data = request.data.get("is_save_bad_data", True)

        from datetime import datetime
        try:
            if start_date:
                start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            if end_date:
                end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except ValueError as e:
            return Response({"error": f"Неверный формат даты: {e}"}, status=400)

        result, status_code = MotohoursCalculationService.calculate_motohours(
            car_id=car_id,
            agg=agg,
            start_date=start_date,
            end_date=end_date,
            is_save_bad_data=is_save_bad_data
        )

        return Response(result, status=status_code)


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

    def get_serializer_context(self):
        context = super().get_serializer_context()
        user_language = getattr(self.request.user, 'active_language', None)
        context['language_code'] = user_language.code if user_language else 'ru'
        return context


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


class SensorsKeyListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter]
    search_fields = ['key']
    serializer_class = SensorsKeyOutputSerializer

    def get_queryset(self):
        language_code = get_user_language_code(self.request.user)
        search_query = self.request.query_params.get('search', None)

        return get_sensors_keys_with_localization(
            org_id=self.request.user.org.id,
            language_code=language_code,
            search_query=search_query
        )


class CarSensorsValuesAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter]
    search_fields = ['key__key']
    serializer_class = SensorsKeyOutputSerializer

    def get_queryset(self):
        car_id = self.kwargs.get('car_id')
        language_code = get_user_language_code(self.request.user)
        search_query = self.request.query_params.get('search', None)

        return get_car_sensors_values(
            car_id=car_id,
            org_id=self.request.user.org.id,
            language_code=language_code,
            search_query=search_query
        )


class CarBadDataAPIView(ListAPIView):
    """
    API для получения проблемных данных автомобилей.
    Поддерживает фильтрацию через query parameters.
    """
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    serializer_class = CarBadDataSerializer

    @swagger_auto_schema(**BAD_DATA_SCHEMA)
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        """
        Получение и фильтрация queryset на основе query parameters
        """
        org = self.request.user.org

        queryset = CarBadData.objects.filter(
            car_id__data_providers__org_id=org.id
        ).distinct().order_by('-datetime')

        car_id = self.request.query_params.get('car_id')
        if car_id:
            try:
                car = Car.objects.get(
                    id=car_id,
                    data_providers__org_id=org.id
                )
                queryset = queryset.filter(car_id=car)
            except (Car.DoesNotExist, ValueError):
                return CarBadData.objects.none()

        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if start_date:
            queryset = queryset.filter(datetime__date__gte=start_date)
        if end_date:
            queryset = queryset.filter(datetime__date__lte=end_date)

        search_query = self.request.query_params.get('search')
        if search_query:
            queryset = queryset.filter(reason__icontains=search_query)

        return queryset

    def list(self, request, *args, **kwargs):
        """
        Переопределяем метод list для обработки ошибок
        """
        try:
            queryset = self.filter_queryset(self.get_queryset())

            car_id = self.request.query_params.get('car_id')
            if car_id and not queryset.exists():
                try:
                    Car.objects.get(id=car_id)
                    return Response(
                        {"error": "Автомобиль не принадлежит вашей организации"},
                        status=status.HTTP_404_NOT_FOUND
                    )
                except Car.DoesNotExist:
                    return Response(
                        {"error": "Автомобиль не найден"},
                        status=status.HTTP_404_NOT_FOUND
                    )
                except ValueError:
                    return Response(
                        {"error": "Неверный формат UUID автомобиля"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

class LanguageListAPIView(ListAPIView):
    """
    API для получения списка всех доступных языков
    """
    queryset = Language.objects.all()
    serializer_class = LanguageSerializer
    pagination_class = None


##TODO: YShipik - кастомные руты с куками для токенов
class CustomTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            access_token = response.data.get('access')
            refresh_token = response.data.get('refresh')

            if access_token:
                response.set_cookie(
                    key='access_token',
                    value=access_token,
                    httponly=True,
                    secure=not settings.DEBUG,
                    samesite='Lax',
                    max_age=60 * 60 * 24,
                )

            if refresh_token:
                response.set_cookie(
                    key='refresh_token',
                    value=refresh_token,
                    httponly=True,
                    secure=not settings.DEBUG,
                    samesite='Lax',
                    max_age=60 * 60 * 24 * 7,
                )

        return response


class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            access_token = response.data.get('access')

            if access_token:
                response.set_cookie(
                    key='access_token',
                    value=access_token,
                    httponly=True,
                    secure=not settings.DEBUG,
                    samesite='Lax',
                    max_age=60 * 60 * 24,
                )

        return response


def api_docs_view(request):
    return render(request, 'api_docs.html', {
        'api_description_url': '/api/v1/swagger.json'
    })
