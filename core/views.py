import json
import uuid
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo
import polars as pl
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import F

import pandas
import polars
import pytz
from celery import group

from django.db.models import Q, Count
from django.shortcuts import render

from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg import openapi

from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView, RetrieveUpdateDestroyAPIView, \
    ListCreateAPIView

from rest_framework import status
import logging

from drf_yasg.utils import swagger_auto_schema
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from app import settings
from core.helpers.cars import filter_leaks_by_period, aggregate_daily_counts, \
    get_daily_leaks_sum, get_car_leaks_count, get_car_leaks_volume, update_car_active_status, check_car_exists, \
    filter_car_leaks

from .helpers.car_request_helpers import CarRequestHelper
from .helpers.car_sensors_helpers import CarSensorsHelper
from .helpers.data_provider import validate_provider_cars, \
    create_data_provider

from .helpers.sensors_mapping import get_user_language_code, get_car_sensors_values, get_sensors_keys_with_localization
from .mixins.calculation_logs_mixin import APICalculationLoggingMixin
from .mixins.convert_utc_mixin import TimestampTimezoneConverterMixin
from .models import ComputedData, Organization, ParsingCarStats, ReportQuery, OrgUser, Car, CarConsumption, CarReport, \
    Driver, DataProvider, \
    CarBadData, Language, CarUnit, SensorsValues, UserCarList, CarMileageReport, TelegramUser, CarFuelReport, \
    APICalculationLog
from core.helpers.pagination import StandardResultsSetPagination
from core.helpers.rest import (
    CAR_LEAKS_CHARTS_SCHEMA, CAR_SENSORS_GROUP_BY_PARTIAL_SCHEMA, LEAKS_VOLUME_SCHEMA, LEAKS_COUNT_SCHEMA,
    DAILY_LEAKS_SUM_SCHEMA, DAILY_LEAKS_COUNT_SCHEMA,
    CAR_LEAKS_SCHEMA, DATA_PROVIDER_CREATE_SCHEMA, CAR_ACTIVE_STATUS_SCHEMA, MILEAGE_REQUEST_SCHEMA,
    MOTOHOURS_REQUEST_SCHEMA, PARSING_STATS_SWITCH_SCHEMA, VEHICLE_SYNC_SCHEMA, CAR_DATA_REQUEST_SCHEMA,
    BAD_DATA_SCHEMA, PARSE_RAW_DATA_SCHEMA,
    CAR_SENSORS_RAW_DATA_SCHEMA, TELEGRAM_REGISTER_SCHEMA
)
from app.tasks import sync_vehicles_task, parse_terminal_messages_task
from .serializers import (
    AutoDataOutputSerializer, CarByGroupSensorsValuesOutputSerializer, CarLeaksChartsRequestSerializer,
    ParsingStatsSwitchSerializer, UserRegistrationSerializer,
    OrganizationOutputSerializer,
    OrgUserOutputSerializer,
    CarOutputSerializer,
    CarConsumptionOutputSerializer, ReportQueryOutputSerializer,
    CarReportOutputSerializer, DriverOutputSerializer, UserOutputSerializer,
    DailyLeaksSerializer, DataProviderOutputSerializer, CarLeaksFilterSerializer,
    DataProviderSerializer, SensorsKeyOutputSerializer, LanguageSerializer,
    CarBadDataSerializer, CarUnitSerializer, UserCarListDetailSerializer, UserCarListCreateUpdateSerializer,
    UserCarListSerializer, CarMileageReportOutputSerializer, TelegramUserRegistrationSerializer,
    TelegramUserOutputSerializer, CarFuelReportSerializer, DataProviderUpdateSerializer,
    APICalculationLogOutputSerializer
)

from core.helpers.responses import error_response, user_registered_response, user_response, \
    success_response
from core.helpers.permissions import IsOrgMember
from .services.providers.mileage_calculation_service import MileageAlgorithms, MileageCalculationService
from .services.providers.motohours_calculation_service import MotohoursCalculationService

logger = logging.getLogger(__name__)


##TODO: Декомпозиция объемных views
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
            'organization': user.org.name,
            'organization_tg_link': f"https://t.me/{user.org.bot_username}?start={user.org.id}",
            'timezone': user.timezone,
        })
        return user_response(serializer.data, status.HTTP_200_OK)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'timezone': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='UTC-зона (например: Europe/Moscow, Asia/Yekaterinburg)',
                    example='Europe/Moscow'
                )
            },
            required=['timezone']
        ),
        responses={
            200: UserOutputSerializer(),
            400: "Bad Request",
            401: "Unauthorized"
        }
    )
    def patch(self, request):
        user = request.user
        if not user:
            logger.error("Пользователь не авторизован")
            return error_response("Unauthorized", status.HTTP_401_UNAUTHORIZED)

        new_timezone = request.data.get('timezone')

        if not new_timezone or new_timezone not in pytz.common_timezones:
            logger.error(f"Некорректный часовой пояс: {new_timezone}")
            return error_response(
                "Invalid timezone. Use one of pytz.common_timezones",
                status.HTTP_400_BAD_REQUEST
            )

        user.timezone = new_timezone
        user.save(update_fields=['timezone'])

        serializer = UserOutputSerializer({
            'id': user.id,
            'username': user.username,
            'organization': user.org.name,
            'organization_tg_link': f"https://t.me/{user.org.bot_username}?start={user.org.id}",
            'timezone': user.timezone,
        })

        return user_response(serializer.data, status.HTTP_200_OK)


class TimezoneListAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Список всех доступных UTC-зон",
        operation_description="Возвращает список всех часовых поясов из pytz.common_timezones для заполнения выпадающего списка на клиенте.",
        responses={
            200: openapi.Response(
                description="Успешный ответ",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'timezones': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_STRING),
                            description="Список строк с названиями зон (например: Europe/Moscow)",
                            example=["UTC", "Europe/Moscow", "Asia/Yekaterinburg", "America/New_York"]
                        )
                    }
                )
            ),
            401: "Unauthorized"
        }
    )
    def get(self, request):
        return user_response(
            {
                "timezones": pytz.common_timezones
            },
            status.HTTP_200_OK
        )


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
    """
    API для создания заявок на получение и обработку данных по автомобилям.
    Поддерживает три режима: все машины, по ID машин, по ID юнитов.
    """

    @swagger_auto_schema(
        operation_description=(
                "Создаёт заявку на получение и обработку данных "
                "по конкретным машинам, по юнитам или по всем машинам провайдера"
        ),
        request_body=CAR_DATA_REQUEST_SCHEMA,
        responses={
            202: "Задачи обработки запущены",
            400: "Неверные данные",
            404: "Провайдер, машины или юниты не найдены"
        }
    )
    def post(self, request):
        """Обработка POST-запроса на создание задач обработки данных."""
        data = request.data
        provider_name = data.get('provider_name')
        car_ids = data.get('car_ids', [])
        unit_ids = data.get('unit_ids', [])
        parse_all = data.get('parse_all', False)
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        is_save_bad_data = data.get('is_save_bad_data', False)

        is_valid, validation_error = CarRequestHelper.validate_input_data(
            provider_name, parse_all, car_ids, unit_ids, start_date, end_date
        )
        if not is_valid:
            return error_response(validation_error, status.HTTP_400_BAD_REQUEST)

        provider, provider_error = CarRequestHelper.get_provider(provider_name)
        if provider_error:
            return error_response(provider_error, status.HTTP_404_NOT_FOUND)

        cars, car_ids_list, mode_info, cars_error = CarRequestHelper.get_cars_by_mode(
            provider, parse_all, car_ids, unit_ids
        )
        if cars_error:
            return error_response(cars_error, status.HTTP_404_NOT_FOUND)

        if cars is None or not cars.exists():
            logger.error("Cars queryset is None or empty unexpectedly")
            return error_response(
                "Unexpected error while fetching cars",
                status.HTTP_400_BAD_REQUEST
            )

        unit_info = {}
        if unit_ids:
            unit_info = CarRequestHelper.collect_unit_info(cars)

        task_group, report_query_ids, car_names, tasks_error = (
            CarRequestHelper.create_processing_tasks(
                cars, provider, start_date, end_date, is_save_bad_data
            )
        )

        if tasks_error:
            return self._handle_tasks_creation_error(
                report_query_ids, tasks_error, status.HTTP_503_SERVICE_UNAVAILABLE
            )

        try:
            job = group(task_group)
            result = job.apply_async()
            logger.info(f"Successfully started task group {result.id} for {len(cars)} cars")

            response_data = CarRequestHelper.create_response_data(
                task_group_id=result.id,
                report_query_ids=report_query_ids,
                car_ids=car_ids_list,
                car_names=car_names,
                provider_name=provider_name,
                mode_info=mode_info,
                parse_all=parse_all,
                unit_ids=unit_ids,
                unit_info=unit_info
            )

            return success_response(response_data, status.HTTP_202_ACCEPTED)

        except Exception as e:
            logger.error(f"Ошибка запуска задач обработки: {e}", exc_info=True)
            return self._handle_tasks_creation_error(
                report_query_ids, str(e), status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def _handle_tasks_creation_error(self, report_query_ids, error_message, status_code):
        """Обработка ошибок создания задач."""
        CarRequestHelper.handle_failed_tasks(report_query_ids, error_message)
        return error_response(
            f"Failed to start processing tasks: {error_message}",
            status_code
        )


class MileageCalculationAPIView(APICalculationLoggingMixin, APIView):
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
        force_chart = request.data.get("force_chart", False)
        alg = MileageAlgorithms.__members__.get(request.data.get("alg", ""),
                                                MileageAlgorithms.compute)
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")
        is_save_bad_data = request.data.get("is_save_bad_data", True)

        try:
            if not car_id:
                return Response({"error": "Параметр car_id обязателен"}, status=404)
            uuid.UUID(str(car_id))
        except (ValueError, TypeError):
            return Response({"error": "Автомобиль не найден (неверный формат ID)"}, status=404)

        try:
            user = request.user
            if user and user.is_authenticated and hasattr(user, 'timezone') and user.timezone in pytz.common_timezones:
                target_timezone = ZoneInfo(user.timezone)
            else:
                target_timezone = ZoneInfo("UTC")

            if start_date:
                start_date = datetime.fromisoformat(start_date).astimezone(tz=target_timezone).astimezone(pytz.utc)
            if end_date:
                end_date = datetime.fromisoformat(end_date).astimezone(tz=target_timezone).astimezone(pytz.utc)
            else:
                end_date = datetime.now()
        except ValueError as e:
            logger.error(f"Неверный формат даты: {e}")
            return Response({"error": f"Неверный формат даты: {e}"}, status=400)
        except Exception as e:
            logger.error(f"Ошибка обработки дат: {e}")
            return Response({"error": f"Ошибка обработки дат: {e}"}, status=400)

        if start_date and (end_date - start_date).days > 60:
            return error_response("Превышен период в 60 дней", status.HTTP_400_BAD_REQUEST)

        try:
            result, status_code = MileageCalculationService.calculate_mileage(
                car_id=car_id,
                agg=agg,
                alg=alg,
                start_date=start_date,
                end_date=end_date,
                is_save_bad_data=is_save_bad_data,
                force_chart=force_chart
            )

            if status_code == 400 and isinstance(result, dict) and "not exist" in str(result.get("error", "")).lower():
                status_code = 404

        except (ObjectDoesNotExist, ValidationError, Http404):
            return Response({"error": "Автомобиль не найден в системе"}, status=404)

        return Response(result, status=status_code)


class MotohoursCalculationAPIView(APICalculationLoggingMixin, APIView):
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

        try:
            if not car_id:
                return Response({"error": "Параметр car_id обязателен"}, status=404)
            uuid.UUID(str(car_id))
        except (ValueError, TypeError):
            return Response({"error": "Автомобиль не найден (неверный формат ID)"}, status=404)

        try:
            if start_date:
                start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            if end_date:
                end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except ValueError as e:
            return Response({"error": f"Неверный формат даты: {e}"}, status=400)

        if start_date and end_date and (end_date - start_date).days > 60:
            return error_response("Превышен период в 60 дней", status.HTTP_400_BAD_REQUEST)

        try:
            result, status_code = MotohoursCalculationService.calculate_motohours(
                car_id=car_id,
                agg=agg,
                start_date=start_date,
                end_date=end_date,
                is_save_bad_data=is_save_bad_data
            )

            if status_code == 400 and isinstance(result, dict) and "not exist" in str(result.get("error", "")).lower():
                status_code = 404

        except (ObjectDoesNotExist, ValidationError, Http404):
            return Response({"error": "Автомобиль не найден в системе"}, status=404)

        return Response(result, status=status_code)


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
    search_fields = ['name', 'description', 'car_unit__name']

    def get_queryset(self):
        result = Car.objects.filter(
            data_providers__org_id=self.request.user.org.id
        ).annotate(bad_data_count=Count('bad_data')).order_by('id')
        return result


class CarListBySensorGroupAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarByGroupSensorsValuesOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    search_fields = ["car_id__name"]

    @swagger_auto_schema(manual_parameters=[CAR_SENSORS_GROUP_BY_PARTIAL_SCHEMA])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        key_value = self.request.query_params.get("key")
        if key_value == "motohours":
            result = SensorsValues.objects.select_related("car_id", 'key').filter(
                car_id__data_providers__org_id=self.request.user.org.id
            ).filter(Q(key__key="motohours") | Q(key__key="ign"))
        else:
            result = SensorsValues.objects.select_related("car_id", 'key').filter(
                car_id__data_providers__org_id=self.request.user.org.id, key__key=key_value
            )
        return result.annotate(
            bad_data_count=Count("car_id__bad_data")
        ).order_by("id")


class AutoDataListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    serializer_class = AutoDataOutputSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]

    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        if self.request.user.is_staff:
            fuel_subquery = Subquery(
                SensorsValues.objects.filter(
                    car_id=OuterRef("pk"),
                    key__key="calc_sensors_fuel_level"
                ).values("value")[:1]
            )
            cars = Car.objects.annotate(
                fuel_sensor=fuel_subquery
            )
            return cars
        return []


import csv
from django.http import StreamingHttpResponse, Http404
from django.db.models import OuterRef, Subquery
from rest_framework.generics import ListAPIView


class AutoDataListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]

    def get_queryset(self):
        if self.request.user.is_staff:
            fuel_subquery = Subquery(
                SensorsValues.objects.filter(
                    car_id=OuterRef("pk"),
                    key__key="calc_sensors_fuel_level"
                ).values("value")[:1]
            )

            return Car.objects.annotate(
                fuel_sensor=fuel_subquery
            )

        return Car.objects.none()

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        cars = list(Car.objects.all())
        total_autos = None
        for car in cars:
            auto_df = CarDataService.prepare_auto_data(car, return_dict=True)
            if total_autos is None:
                total_autos= auto_df
            else:
                total_autos.extend(auto_df)
                

        df = pandas.DataFrame(total_autos,)
        df.to_csv("/data/datasets/fuel/Cars-new.csv")

        return queryset


class CarMileageReportListAPIView(ListAPIView):
    serializer_class = CarMileageReportOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car_id', 'datetime']
    search_fields = ['car_id__name', 'fraud']

    def get_queryset(self):
        return CarMileageReport.objects.filter(
            car_id__data_providers__org_id__users=self.request.user
        ).select_related('car_id').order_by('-datetime')


class CarMileageReportDetailAPIView(RetrieveAPIView):
    serializer_class = CarMileageReportOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return CarMileageReport.objects.filter(
            car_id__data_providers__org_id__users=self.request.user
        ).select_related('car_id')


class ParsingStatsParsingSwitch(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**PARSING_STATS_SWITCH_SCHEMA)
    def post(self, request):

        serializer = ParsingStatsSwitchSerializer(data=request.data)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        parameter = data.get("parameter")
        true_parameter = f"is_parse_{parameter}"
        car_id = data.get("car_id")
        try:
            target = ParsingCarStats.objects.filter(car_id=car_id)
        except BaseException:
            return error_response(f"Объект с id {car_id} не существует", status.HTTP_400_BAD_REQUEST)
        result = target.update(**{true_parameter: ~F(true_parameter)})
        return success_response({"updated": result}, 200)


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

        car_id = self.kwargs.get('pk')
        try:
            car = Car.objects.get(id=car_id)
            if car.car_unit:
                context['car_unit_info'] = {
                    'id': str(car.car_unit.id),
                    'name': car.car_unit.name
                }
        except Car.DoesNotExist:
            pass

        return context


class CarUnitListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarUnitSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name']

    def get_queryset(self):
        return CarUnit.objects.filter(
            car__data_providers__org_id=self.request.user.org.id
        ).distinct().order_by('name')


class CarConsumptionListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarConsumptionOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car_id', 'valid_period']
    search_fields = ['car_id__name', 'valid_period']

    def get_queryset(self):
        user = self.request.user
        if user.org is None:
            return CarConsumption.objects.none()
        result = CarConsumption.objects.filter(
            car_id__data_providers__org_id__users=self.request.user
        ).select_related('car_id').order_by('id')
        return result


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
        ).select_related('provider_id', 'report_query_details')


class CarReportListAPIView(TimestampTimezoneConverterMixin, ListAPIView):
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

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)

        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(
            response.data, user_timezone
        )

        return response


class CarReportDetailAPIView(TimestampTimezoneConverterMixin, RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return CarReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        )

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)

        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(
            response.data, user_timezone
        )

        return response


class CarFuelReportListAPIView(TimestampTimezoneConverterMixin, ListAPIView):
    serializer_class = CarFuelReportSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car_id', 'start_moment']
    search_fields = ['car_id__name', 'car_id__id_in_provider_system']

    def get_queryset(self):
        return CarFuelReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org.id
        ).select_related('car_id').distinct().order_by('-start_moment')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)

        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(
            response.data, user_timezone
        )

        return response


class CarFuelReportDetailAPIView(TimestampTimezoneConverterMixin, RetrieveAPIView):
    serializer_class = CarFuelReportSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return CarFuelReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org.id
        ).select_related('car_id').distinct()

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)

        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(
            response.data, user_timezone
        )

        return response


class UserCarListListView(ListCreateAPIView):
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name']

    def get_queryset(self):
        return UserCarList.objects.filter(user=self.request.user).order_by('name')

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return UserCarListSerializer
        return UserCarListCreateUpdateSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class UserCarListDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        return UserCarList.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return UserCarListDetailSerializer
        return UserCarListCreateUpdateSerializer

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        data = serializer.data
        car_count = Car.objects.filter(list_id=instance).count()
        data['stats'] = {
            'car_count': car_count,
            'active_cars': Car.objects.filter(list_id=instance, is_active=True).count(),
            'tarrified_cars': Car.objects.filter(list_id=instance, is_tarrified=True).count()
        }

        return Response(data)

    def perform_destroy(self, instance):
        Car.objects.filter(list_id=instance).update(list_id=None)

        instance.delete()


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


class DataProviderDetailAPIView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DataProviderUpdateSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return DataProvider.objects.filter(
            org_id=self.request.user.org.id
        ).prefetch_related('cars')

    def perform_destroy(self, instance):
        super().perform_destroy(instance)


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
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    serializer_class = CarBadDataSerializer

    @swagger_auto_schema(**BAD_DATA_SCHEMA)
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
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


class StartTerminalMessagesParsingView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**PARSE_RAW_DATA_SCHEMA)
    def post(self, request):
        try:
            provider_name = request.data.get('provider_name')
            start_date_str = request.data.get('start_date')
            end_date_str = request.data.get('end_date')
            is_raw_data = request.data.get('is_raw_data', True)
            parse_all = request.data.get('parse_all', False)
            car_ids = request.data.get('car_ids', [])

            if not all([provider_name, start_date_str, end_date_str]):
                return Response(
                    {'error': 'Требуются параметры: provider_name, start_date, end_date'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

                if start_date >= end_date:
                    return Response(
                        {'error': 'start_date должен быть раньше end_date'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            except ValueError:
                return Response(
                    {'error': 'Неверный формат даты. Используйте YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            provider = DataProvider.objects.filter(name=provider_name).first()

            if car_ids and not parse_all:
                try:
                    validated_car_ids = []
                    for car_id in car_ids:
                        if isinstance(car_id, str):
                            validated_car_ids.append(UUID(car_id))
                        else:
                            validated_car_ids.append(car_id)
                    car_ids = validated_car_ids
                except (ValueError, TypeError) as e:
                    return Response(
                        {'error': f'Неверный формат car_ids: {str(e)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            if parse_all:
                try:
                    car_ids = list(map(lambda car: car.id, list(provider.cars.all())))

                except (ValueError, TypeError) as e:
                    return Response(
                        {'error': f'Неверный формат car_ids: {str(e)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            task = parse_terminal_messages_task.delay(
                provider_name=provider.name,
                start_date_str=start_date_str,
                end_date_str=end_date_str,
                mode="raw" if is_raw_data else "raw_mapped",
                car_ids=car_ids
            )

            mode = "parse_all" if parse_all else ("specific_cars" if car_ids else "all_cars")

            return Response({
                'status': 'success',
                'message': 'Задача парсинга terminalMessages запущена',
                'task_id': task.id,
                'provider_name': provider_name,
                'start_date': start_date_str,
                'end_date': end_date_str,
                'is_raw_data': is_raw_data,
                'parse_all': parse_all,
                'car_ids': car_ids,
                'mode': mode,
                'car_count': len(car_ids) if car_ids else None
            }, status=status.HTTP_202_ACCEPTED)

        except Exception as e:
            return Response(
                {'error': f'Ошибка запуска задачи: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class LanguageListAPIView(ListAPIView):
    queryset = Language.objects.all()
    serializer_class = LanguageSerializer
    pagination_class = None


class CarLeaksChartsAPIView(TimestampTimezoneConverterMixin, APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Получение данных о графике для слива",
        operation_description="""
            Получение данных о графиках для слива
            
            ***Ограничения***
            - Максимальный период: 365 дней
        """,
        request_body=CAR_LEAKS_CHARTS_SCHEMA["request_body"],
        responses=CAR_LEAKS_CHARTS_SCHEMA["responses"],
    )
    def post(self, request):
        serializer = CarLeaksChartsRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        car_id = data["car_id"]
        days = data["days"]
        leak_id = data.get("leak_id")

        start_date = (datetime.now() - timedelta(days)).astimezone(pytz.utc)
        start_date_month = (datetime.now() - timedelta(31)).astimezone(pytz.utc)
        computed_data = ComputedData.objects.filter(
            auto__id=car_id,
            timestamp__gte=start_date
        ).order_by('timestamp')
        if len(computed_data) == 0:
            return error_response("Нет данных для этой машины", status.HTTP_400_BAD_REQUEST)
        leaks = CarReport.objects.filter(car_id__id=car_id, datetime__gte=start_date)
        leaks_df = polars.DataFrame(list(leaks.values()))
        # fpm, pos_s
        df = polars.DataFrame(list(computed_data.values()))
        tmp = df.select(["fpm", "pos_s", "timestamp", "z_values", "ign_spread", "rpm_mean", "spent_fuel"])
        tmp = tmp.with_columns(polars.col("timestamp").is_in(leaks_df["datetime"].unique()).alias("is_leak"))
        tmp = tmp.filter(polars.col("spent_fuel").gt(0))
        tmp = tmp.with_columns(
            pl.when(pl.col("timestamp").ge(start_date_month)).then(pl.lit("month")).otherwise(pl.lit("year")).alias(
                "color"))
        tmp = tmp.with_columns(
            pl.when(pl.col("is_leak")).then(pl.lit("leak")).otherwise(pl.col("color")).alias("color"))

        data_fast = tmp.filter(polars.col("pos_s").gt(1)).select(["timestamp", "pos_s", "spent_fuel", "fpm", "color"])
        is_rpm = SensorsValues.objects.filter(key__key="rpm", car_id__id=car_id).count() > 0
        target_column = "rpm_mean" if is_rpm else "ign_spread"
        data_slow = tmp.filter(polars.col("pos_s").lt(1)).select(["timestamp", "pos_s", target_column, "fpm", "color"])
        data_agg = tmp.filter(
            polars.col("spent_fuel").gt(0)
        )
        month = data_agg.group_by_dynamic(
            index_column="timestamp",
            every="1mo"
        ).agg([
            polars.mean("z_values"),
            polars.mean("fpm"),
            polars.first("spent_fuel"),
        ]).with_columns(polars.lit("month").alias("color"))
        year = data_agg.group_by_dynamic(
            index_column="timestamp",
            every="1y"
        ).agg([
            polars.mean("z_values"),
            polars.mean("fpm"),
            polars.first("spent_fuel"),
        ]).with_columns(polars.lit("year").alias("color"))
        data_bar = [
            *tmp.filter(polars.col("is_leak")).select(["timestamp", "z_values", "fpm", "spent_fuel"]).with_columns(
                polars.lit("leaks").alias("color")).with_columns(
                polars.col("timestamp").dt.to_string("iso:strict")).to_dicts(),
            year.row(0, named=True),
            month.row(0, named=True)
        ]

        print(data_slow.columns)
        print(data_fast.columns)
        logger.info(f"{len(data_slow)} - {len(data_fast)} - {len(data_bar)}")
        response_data = {
            "data_slow": data_slow.with_columns(
                polars.col("timestamp").dt.to_string("iso:strict").alias("timestamp")).to_dicts(),
            "slow_type": "rpm" if is_rpm else "ign",
            "data_fast": data_fast.with_columns(
                polars.col("timestamp").dt.to_string("iso:strict").alias("timestamp")).to_dicts(),
            "data_bar": data_bar,
        }

        return success_response(response_data, status.HTTP_200_OK)


class CarSensorsRawDataAPIView(TimestampTimezoneConverterMixin, APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Получение сырых данных по машине для графиков",
        operation_description="""
            Получение сырых данных по машине для построения графиков.

            **Режимы работы:**
            1. **mileage** - данные о пробеге: timestamp, mileage, pos_s, ign, rpm
            2. **fuel** - данные о топливе: timestamp, calc_sensors_fuel_level, pos_s, rpm
            3. **motohours** - данные о моточасах: timestamp, motohours, pos_s, rpm, ign

            **Ограничения:**
            - Максимальный период: 90 дней
            - Rate limit: 1 запрос в секунду к API провайдера
            """,
        request_body=CAR_SENSORS_RAW_DATA_SCHEMA['request_body'],
        responses=CAR_SENSORS_RAW_DATA_SCHEMA['responses']
    )
    def post(self, request):
        DAYS = 30
        try:
            data = request.data
            car_id = data.get('car_id')
            start_date_str = data.get('start_date')
            end_date_str = data.get('end_date')
            agg = data.get("agg")
            mode = data.get('mode', 'mileage')

            validation_rsp = self._validate_request_params(car_id, start_date_str, end_date_str, mode)
            if validation_rsp:
                return validation_rsp

            start_date, end_date, date_error = CarSensorsHelper.parse_and_validate_dates(
                start_date_str, end_date_str
            )
            if date_error:
                return error_response(date_error, status.HTTP_400_BAD_REQUEST)

            car, car_error = CarSensorsHelper.get_car_for_user(car_id, request.user)

            if car_error:
                return error_response(car_error, status.HTTP_404_NOT_FOUND)
            car_r = Car.objects.select_related('car_unit').get(id=car_id)

            provider = car_r.data_providers.first()
            result, parser, parse_error = CarSensorsHelper.parse_raw_data(
                car, provider, start_date, end_date, agg, mode
            )
            if parse_error:
                error_status = (
                    status.HTTP_400_BAD_REQUEST
                    if "Неверный формат" in parse_error
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                return error_response(parse_error, error_status)

            response_data = CarSensorsHelper.build_response_data(
                car_id=car_id,
                car_name=car.name,
                mode=mode,
                start_date_str=start_date_str,
                end_date_str=end_date_str,
                result=result,
                parser=parser
            )

            user_timezone = getattr(request.user, 'timezone', 'UTC')
            response_data = self.convert_timestamps_to_user_timezone(response_data, user_timezone)

            return success_response(response_data, status.HTTP_200_OK)

        except Exception as e:
            error_data, error_status = CarSensorsHelper.handle_general_exception(e)
            return error_response(error_data["error"], error_status)

    def _validate_request_params(self, car_id: str, start_date: str, end_date: str, mode: str):
        is_valid, required_error = CarSensorsHelper.validate_required_params(car_id, start_date, end_date)
        if not is_valid:
            return error_response(required_error, status.HTTP_400_BAD_REQUEST)

        is_valid_mode, mode_error = CarSensorsHelper.validate_mode(mode)
        if not is_valid_mode:
            return error_response(mode_error, status.HTTP_400_BAD_REQUEST)

        return None


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


class TelegramRegisterAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    @swagger_auto_schema(**TELEGRAM_REGISTER_SCHEMA)
    def post(self, request):
        serializer = TelegramUserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(serializer.errors, status=400)

        data = serializer.validated_data
        chat_id = data['chat_id']
        organization_id = data['organization_id']

        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return error_response('Organization not found', status=404)

        user, created = TelegramUser.objects.update_or_create(
            chat_id=chat_id,
            defaults={
                'organization': organization,
                'username': data.get('username', '')[:255],
                'first_name': data.get('first_name', '')[:255],
                'last_name': data.get('last_name', '')[:255],
                'is_active': True,
            }
        )

        output_serializer = TelegramUserOutputSerializer(user)
        status_code = 201 if created else 200
        return success_response(output_serializer.data, status_code)


##LOGS
class APICalculationLogListAPIView(ListAPIView):
    serializer_class = APICalculationLogOutputSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter]

    filterset_fields = ['car', 'status_code', 'view_name']

    search_fields = ['view_name', 'car__id', 'car__name', 'car__id_in_provider_system']

    def get_queryset(self):
        return APICalculationLog.objects.filter(
            user=self.request.user
        ).select_related('car').order_by('-created_at')


def api_docs_view(request):
    return render(request, 'api_docs.html', {
        'api_description_url': '/api/v1/swagger.json'
    })
