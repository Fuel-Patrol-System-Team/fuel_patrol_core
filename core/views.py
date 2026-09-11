import json
import uuid
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo
import polars as pl
from django.core.exceptions import ObjectDoesNotExist, ValidationError, PermissionDenied
from django.db.models import F, Q, Count, Prefetch, Subquery, OuterRef, Sum, IntegerField
from django.db.models.functions import Coalesce

import pandas
import polars
import pytz
from celery import group

from django.shortcuts import render

import django_filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg import openapi

from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView, RetrieveUpdateDestroyAPIView, \
    ListCreateAPIView, RetrieveUpdateAPIView

from rest_framework import status
import logging

from drf_yasg.utils import swagger_auto_schema
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from app import settings
from core.helpers.cars import filter_leaks_by_period, aggregate_daily_counts, \
    get_daily_leaks_sum, get_car_leaks_count, get_car_leaks_volume, update_car_active_status, check_car_exists, \
    filter_car_leaks
from core.services.providers.rpm_auto_calculation_service import RpmAutoCalculationService
from .helpers.agg import validate_agg
from .helpers.alert_subscription import check_telegram_user, get_or_create_subscription, patch_subscription
from .helpers.car_bad_data import get_bad_data_by_tag, get_bad_data_by_car, get_bad_data_calendar
from .helpers.car_move_stop import get_stops_mileage_report

from .helpers.car_request_helpers import CarRequestHelper
from .helpers.car_sensors_helpers import CarSensorsHelper
from .helpers.data_provider import validate_provider_cars, \
    create_data_provider
from .helpers.date import _parse_to_aware
from .helpers.ml_reasoning import update_ai_response, prepare_motohours_data, prepare_mileage_data, prepare_fuel_data

from .helpers.sensors_mapping import get_user_language_code, get_car_sensors_values, get_sensors_keys_with_localization
from .mixins.calculation_logs_mixin import APICalculationLoggingMixin
from .mixins.convert_utc_mixin import TimestampTimezoneConverterMixin
from .mixins.swagger_mixin import SwaggerSafeQuerysetMixin
from .models import CarMotohoursReport, ComputedData, Organization, ParsingCarStats, ReportQuery, OrgUser, Car, CarConsumption, CarReport, \
    Driver, DataProvider, \
    CarBadData, Language, CarUnit, SensorsValues, SensorsKeyLocalization, UserCarList, CarMileageReport, TelegramUser, \
    CarFuelReport, \
    APICalculationLog, CarModel, CarModelSpecification
from core.helpers.pagination import StandardResultsSetPagination
from core.helpers.rest import (
    CAR_LEAKS_CHARTS_SCHEMA, CAR_SENSOR_SWITCH_SCHEMA, CAR_SENSORS_GROUP_BY_PARTIAL_SCHEMA, FUELREPORT_REQUEST_SCHEMA,
    LEAKS_VOLUME_SCHEMA, LEAKS_COUNT_SCHEMA,
    DAILY_LEAKS_SUM_SCHEMA, DAILY_LEAKS_COUNT_SCHEMA,
    CAR_LEAKS_SCHEMA, DATA_PROVIDER_CREATE_SCHEMA, CAR_ACTIVE_STATUS_SCHEMA, MILEAGE_REQUEST_SCHEMA,
    MOTOHOURS_REQUEST_SCHEMA, PARSING_STATS_RPM_SCHEMA, PARSING_STATS_SWITCH_SCHEMA, VEHICLE_SYNC_SCHEMA,
    CAR_DATA_REQUEST_SCHEMA,
    BAD_DATA_SCHEMA, PARSE_RAW_DATA_SCHEMA,
    CAR_SENSORS_RAW_DATA_SCHEMA, TELEGRAM_REGISTER_SCHEMA, BAD_DATA_DASHBOARD_SCHEMA, ALERT_SUBSCRIPTION_PATCH_SCHEMA,
    ANALYSIS_SCHEMA, STOPS_MILEAGE_REQUEST_SCHEMA, STOPS_MILEAGE_RESPONSE_SCHEMA
)
from app.tasks import FuelReportService, sync_vehicles_task, parse_terminal_messages_task
from .serializers import (
    APICalculationRetrieveLogOutputSerializer, AutoDataOutputSerializer, CarByGroupSensorsValuesOutputSerializer,
    CarLeaksChartsRequestSerializer, CarMotohoursReportOutputSerializer, CarSensorsSwitchSerializer,
    ParsingStatsSwitchSerializer, ParsingStatsUpdateRpmSerializer, SensorsValuesOutputSerializer,
    UserRegistrationSerializer,
    OrganizationOutputSerializer,
    OrgUserOutputSerializer,
    CarOutputSerializer, CarSpecsUpdateSerializer, CarModelSerializer,
    CarModelSpecificationSerializer,
    CarConsumptionOutputSerializer, ReportQueryOutputSerializer,
    CarReportOutputSerializer, DriverOutputSerializer, UserOutputSerializer,
    DailyLeaksSerializer, DataProviderOutputSerializer, CarLeaksFilterSerializer,
    DataProviderSerializer, SensorsKeyOutputSerializer, LanguageSerializer,
    CarBadDataSerializer, CarUnitSerializer, UserCarListDetailSerializer, UserCarListCreateUpdateSerializer,
    UserCarListSerializer, CarMileageReportOutputSerializer, TelegramUserRegistrationSerializer,
    TelegramUserOutputSerializer, CarFuelReportSerializer, DataProviderUpdateSerializer,
    APICalculationLogOutputSerializer, BadDataQuerySerializer, CarBadDataFilterSerializer,
    AlertSubscriptionPatchSerializer, AnalysisRequestSerializer, StopsMileageRequestSerializer
)

from core.helpers.responses import error_response, user_registered_response, user_response, \
    success_response
from core.helpers.permissions import IsOrgMember
from core.demo_auth.permissions import IsNotDemoUser, IsDemoUser
from .services.ml_reasoning_service import MLReasoningService
from .services.providers.car_data_service import CarDataService
from .services.providers.mileage_calculation_service import MileageAlgorithms, MileageCalculationService
from .services.providers.motohours_calculation_service import MotohoursCalculationService

logger = logging.getLogger(__name__)

_ANON_GUARD = lambda self: (
    getattr(self, 'swagger_fake_view', False) or
    not getattr(self.request, 'user', None) or
    not self.request.user.is_authenticated
)


def _flatten_nested_for_csv(df: pl.DataFrame) -> pl.DataFrame:
    """Serialize all nested (List/Struct/Array/Object) columns to JSON strings so the DataFrame can be written as CSV."""
    nested_types = tuple(t for t in (pl.List, pl.Struct, pl.Array, pl.Object) if isinstance(t, type))
    if not nested_types:
        return df
    nested_exprs = []
    for col_name, dtype in zip(df.columns, df.dtypes):
        if isinstance(dtype, nested_types):
            nested_exprs.append(
                pl.col(col_name).map_elements(lambda x: json.dumps(x, default=str), return_dtype=pl.Utf8).alias(col_name)
            )
    if nested_exprs:
        df = df.with_columns(nested_exprs)
    return df


def _car_prefetch(language_code: str, prefix: str = ''):
    localization_prefetch = Prefetch(
        'key__locations',
        queryset=SensorsKeyLocalization.objects.select_related('language').filter(
            language__code=language_code
        ),
        to_attr='_prefetched_localized',
    )
    sensors_prefetch = Prefetch(
        f'{prefix}values',
        queryset=SensorsValues.objects.select_related('key').prefetch_related(
            localization_prefetch
        ),
    )
    return [sensors_prefetch, f'{prefix}parsingcar_stats']


def _get_language_code(user) -> str:
    return user.active_language.code if getattr(user, 'active_language', None) else 'ru'


def _fuel_leaked_annotation():
    leaked_sum = Subquery(
        CarReport.objects.filter(
            car_id=OuterRef('car_id'),
            status=True,
            datetime__gte=OuterRef('start_moment'),
            datetime__lte=OuterRef('end_moment'),
        ).values('car_id').annotate(total=Sum('volume')).values('total')[:1],
        output_field=IntegerField(),
    )
    return Coalesce(leaked_sum, 0)


class CarFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(field_name="name", lookup_expr="exact")
    model = django_filters.UUIDFilter(field_name="model_id")
    model_specs = django_filters.UUIDFilter(field_name="model_specs_id")
    model_label = django_filters.CharFilter(field_name="model__label", lookup_expr="icontains")
    model_specs_label = django_filters.CharFilter(field_name="model_specs__label", lookup_expr="icontains")
    fuel_type = django_filters.ChoiceFilter(choices=Car.FuelType.choices)
    engine_power = django_filters.NumberFilter(field_name="engine_power", lookup_expr="exact")
    engine_power_min = django_filters.NumberFilter(field_name="engine_power", lookup_expr="gte")
    engine_power_max = django_filters.NumberFilter(field_name="engine_power", lookup_expr="lte")

    class Meta:
        model = Car
        fields = [
            "name",
            "is_active",
            "is_tarrified",
            "model",
            "model_specs",
            "model_label",
            "model_specs_label",
            "fuel_type",
            "engine_power",
            "engine_power_min",
            "engine_power_max",
        ]


class DailyLeaksCountAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**DAILY_LEAKS_COUNT_SCHEMA)
    def get(self, request):
        serializer = DailyLeaksSerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        queryset = filter_leaks_by_period(request.user.org.id, data.get('periodFrom'), data.get('periodDue'))
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
        result = get_daily_leaks_sum(request.user.org.id, data.get('periodFrom'), data.get('periodDue'))
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
        result = get_car_leaks_count(request.user.org.id, data.get('periodFrom'), data.get('periodDue'))
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
        result = get_car_leaks_volume(request.user.org.id, data.get('periodFrom'), data.get('periodDue'))
        return success_response(result, status.HTTP_200_OK)


class CarActiveStatusAPIView(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(**CAR_ACTIVE_STATUS_SCHEMA)
    def post(self, request):
        car_id = request.query_params.get('car_id')
        result, error = update_car_active_status(car_id, request.data, request)
        if error:
            return error
        return success_response(result, status.HTTP_200_OK)


class UserInfoAPIView(APIView):
    permission_classes = [IsDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Получить информацию о текущем пользователе",
        operation_description="Возвращает данные авторизованного пользователя: имя, организацию, часовой пояс и ссылку на Telegram-бота.",
        responses={200: UserOutputSerializer(), 401: "Не авторизован"}
    )
    def get(self, request):
        user = request.user
        if not user:
            return error_response("Unauthorized", status.HTTP_401_UNAUTHORIZED)
        serializer = UserOutputSerializer({
            'id': user.id,
            'username': user.username,
            'organization': user.org.name,
            'organization_tg_link': f"https://t.me/{user.org.bot_username}?start={user.id}",
            'timezone': user.timezone,
        })
        return user_response(serializer.data, status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary="Обновить часовой пояс пользователя",
        operation_description="Позволяет изменить часовой пояс текущего пользователя. Принимает строку из списка `pytz.common_timezones`.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'timezone': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    example='Europe/Moscow',
                    description='Часовой пояс из списка pytz.common_timezones'
                )
            },
            required=['timezone']
        ),
        responses={200: UserOutputSerializer(), 400: "Неверный часовой пояс", 401: "Не авторизован"}
    )
    def patch(self, request):
        user = request.user
        if not user:
            return error_response("Unauthorized", status.HTTP_401_UNAUTHORIZED)
        new_timezone = request.data.get('timezone')
        if not new_timezone or new_timezone not in pytz.common_timezones:
            return error_response("Invalid timezone. Use one of pytz.common_timezones", status.HTTP_400_BAD_REQUEST)
        user.timezone = new_timezone
        user.save(update_fields=['timezone'])
        serializer = UserOutputSerializer({
            'id': user.id,
            'username': user.username,
            'organization': user.org.name,
            'organization_tg_link': (
                f"https://t.me/{user.org.bot_username}?start={user.id}"
                if user.org and user.org.bot_username
                else None
            ),
            'timezone': user.timezone,
        })
        return user_response(serializer.data, status.HTTP_200_OK)


class TimezoneListAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Список доступных часовых поясов",
        operation_description="Возвращает полный список поддерживаемых часовых поясов (pytz.common_timezones).",
        responses={
            200: openapi.Response(
                description="Список часовых поясов",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'timezones': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_STRING),
                            description='Список строк часовых поясов'
                        )
                    }
                )
            ),
            401: "Не авторизован"
        }
    )
    def get(self, request):
        return user_response({"timezones": pytz.common_timezones}, status.HTTP_200_OK)


class VehicleSyncAPIView(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Запустить синхронизацию транспортных средств",
        operation_description=(
            "Запускает асинхронную задачу синхронизации ТС от указанного провайдера данных. "
            "Создаёт ReportQuery для отслеживания статуса. "
            "Возвращает `task_id` для проверки прогресса."
        ),
        request_body=VEHICLE_SYNC_SCHEMA,
        responses={
            202: "Задача синхронизации запущена",
            400: "Не указан provider_name или пользователь не привязан к организации",
            404: "Провайдер с указанным именем не найден",
            503: "Ошибка запуска задачи Celery"
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
            return Response({"error": "Failed to start synchronization task"},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)


class CarDataRequestAPIView(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Запустить обработку данных по автомобилям",
        operation_description=(
            "Создаёт группу задач Celery для получения и обработки телематических данных "
            "по указанным автомобилям или подразделениям. "
            "Поддерживает режимы: конкретные car_ids, unit_ids, или parse_all=true для всего парка."
        ),
        request_body=CAR_DATA_REQUEST_SCHEMA,
        responses={
            202: "Задачи обработки запущены",
            400: "Ошибка валидации входных данных",
            404: "Провайдер или автомобили не найдены",
            503: "Ошибка запуска задач Celery"
        }
    )
    def post(self, request):
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
            return error_response("Unexpected error while fetching cars", status.HTTP_400_BAD_REQUEST)

        unit_info = {}
        if unit_ids:
            unit_info = CarRequestHelper.collect_unit_info(cars)

        task_group, report_query_ids, car_names, tasks_error = (
            CarRequestHelper.create_processing_tasks(cars, provider, start_date, end_date, is_save_bad_data)
        )
        if tasks_error:
            return self._handle_tasks_creation_error(report_query_ids, tasks_error, status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            job = group(task_group)
            result = job.apply_async()
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
            return self._handle_tasks_creation_error(report_query_ids, str(e), status.HTTP_503_SERVICE_UNAVAILABLE)

    def _handle_tasks_creation_error(self, report_query_ids, error_message, status_code):
        CarRequestHelper.handle_failed_tasks(report_query_ids, error_message)
        return error_response(f"Failed to start processing tasks: {error_message}", status_code)


class MileageCalculationAPIView(APICalculationLoggingMixin, APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Рассчитать пробег автомобиля за период",
        operation_description=(
                "Вычисляет пробег по телематическим данным за указанный период. "
                "Максимальный период — 60 дней. "
                "Поддерживает несколько алгоритмов расчёта (`alg`) и агрегацию (`agg`). "
                "Логирует результат в APICalculationLog."
        ),
        request_body=MILEAGE_REQUEST_SCHEMA,
        responses={
            200: "Результат расчёта пробега",
            400: "Неверные параметры или превышен период 60 дней",
            404: "Автомобиль не найден",
            500: "Ошибка расчёта"
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

        agg, agg_error = validate_agg(agg)
        if agg_error:
            return Response({"error": agg_error}, status=400)

        try:
            if not car_id:
                return Response({"error": "Параметр car_id обязателен"}, status=404)
            uuid.UUID(str(car_id))
        except (ValueError, TypeError):
            return Response({"error": "Автомобиль не найден (неверный формат ID)"}, status=404)

        user = request.user
        if user and user.is_authenticated and hasattr(user, 'timezone') and user.timezone in pytz.common_timezones:
            target_timezone = ZoneInfo(user.timezone)
        else:
            target_timezone = ZoneInfo("UTC")

        try:
            if start_date:
                start_date = _parse_to_aware(start_date, target_timezone).astimezone(pytz.utc)
            if end_date:
                end_date = _parse_to_aware(end_date, target_timezone).astimezone(pytz.utc)
            else:
                end_date = datetime.now(pytz.utc)
        except ValueError as e:
            return Response({"error": f"Неверный формат даты: {e}"}, status=400)
        except Exception as e:
            return Response({"error": f"Ошибка обработки дат: {e}"}, status=400)

        if not start_date:
            return Response({"error": "Параметр start_date обязателен"}, status=400)

        now = datetime.now(target_timezone)

        if (end_date - start_date).days > 60:
            return error_response("Превышен период в 60 дней", status.HTTP_400_BAD_REQUEST)
        if start_date > now + timedelta(hours=12):
            return error_response("Начало периода выше текущей даты", status.HTTP_400_BAD_REQUEST)
        if end_date > now + timedelta(days=1):
            end_date = now + timedelta(days=1)

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
        except (ObjectDoesNotExist, ValidationError):
            return Response({"error": "Автомобиль не найден в системе"}, status=404)

        return Response(result, status=status_code)


class FuelSpentCalculationService(APICalculationLoggingMixin, APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Рассчитать пробег автомобиля за период",
        operation_description=(
                "Вычисляет пробег по телематическим данным за указанный период. "
                "Максимальный период — 60 дней. "
                "Поддерживает несколько алгоритмов расчёта (`alg`) и агрегацию (`agg`). "
                "Логирует результат в APICalculationLog."
        ),
        request_body=FUELREPORT_REQUEST_SCHEMA,
        responses={
            200: "Результат расчёта пробега",
            400: "Неверные параметры или превышен период 60 дней",
            404: "Автомобиль не найден",
            500: "Ошибка расчёта"
        }
    )
    def post(self, request):
        car_id = request.data.get("car_id")
        agg = request.data.get("agg")
        force_chart = request.data.get("force_chart", False)
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")
        is_save_bad_data = request.data.get("is_save_bad_data", True)

        agg, agg_error = validate_agg(agg)
        if agg_error:
            return Response({"error": agg_error}, status=400)

        try:
            if not car_id:
                return Response({"error": "Параметр car_id обязателен"}, status=404)
            uuid.UUID(str(car_id))
        except (ValueError, TypeError):
            return Response({"error": "Автомобиль не найден (неверный формат ID)"}, status=404)

        user = request.user
        if user and user.is_authenticated and hasattr(user, 'timezone') and user.timezone in pytz.common_timezones:
            target_timezone = ZoneInfo(user.timezone)
        else:
            target_timezone = ZoneInfo("UTC")

        try:
            if start_date:
                start_date = _parse_to_aware(start_date, target_timezone).astimezone(pytz.utc)
            if end_date:
                end_date = _parse_to_aware(end_date, target_timezone).astimezone(pytz.utc)
            else:
                end_date = datetime.now(pytz.utc)
        except ValueError as e:
            return Response({"error": f"Неверный формат даты: {e}"}, status=400)
        except Exception as e:
            return Response({"error": f"Ошибка обработки дат: {e}"}, status=400)

        if not start_date:
            return Response({"error": "Параметр start_date обязателен"}, status=400)

        now = datetime.now(target_timezone)

        if (end_date - start_date).days > 60:
            return error_response("Превышен период в 60 дней", status.HTTP_400_BAD_REQUEST)
        if start_date > now + timedelta(hours=12):
            return error_response("Начало периода выше текущей даты", status.HTTP_400_BAD_REQUEST)
        if end_date > now + timedelta(days=1):
            end_date = now + timedelta(days=1)

        try:
            result, status_code = FuelReportService.calculate_fuelspent(
                car_id=car_id,
                agg=agg,
                start_date=start_date,
                end_date=end_date,
                is_save_bad_data=is_save_bad_data,
                force_chart=force_chart
            )

            leaks_sum, leaks_data = FuelReportService._get_leaks_data(car_id, start_date, end_date)
            result['leaks_sum'] = leaks_sum
            result['leaks_data'] = leaks_data

            if status_code == 400 and isinstance(result, dict) and "not exist" in str(result.get("error", "")).lower():
                status_code = 404
        except (ObjectDoesNotExist, ValidationError):
            return Response({"error": "Автомобиль не найден в системе"}, status=404)

        return Response(result, status=status_code)


class MotohoursCalculationAPIView(APICalculationLoggingMixin, APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Рассчитать моточасы автомобиля за период",
        operation_description=(
                "Вычисляет моточасы по данным датчиков зажигания/RPM за указанный период. "
                "Максимальный период — 60 дней. "
                "Логирует результат в APICalculationLog."
        ),
        request_body=MOTOHOURS_REQUEST_SCHEMA,
        responses={
            200: "Результат расчёта моточасов",
            400: "Неверные параметры или превышен период 60 дней",
            404: "Автомобиль не найден",
            500: "Ошибка расчёта"
        }
    )
    def post(self, request):
        car_id = request.data.get("car_id")
        agg = request.data.get("agg")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")
        is_save_bad_data = request.data.get("is_save_bad_data", True)

        agg, agg_error = validate_agg(agg)
        if agg_error:
            return Response({"error": agg_error}, status=400)

        try:
            if not car_id:
                return Response({"error": "Параметр car_id обязателен"}, status=404)
            uuid.UUID(str(car_id))
        except (ValueError, TypeError):
            return Response({"error": "Автомобиль не найден (неверный формат ID)"}, status=404)

        user = request.user
        if user and user.is_authenticated and hasattr(user, 'timezone') and user.timezone in pytz.common_timezones:
            target_timezone = ZoneInfo(user.timezone)
        else:
            target_timezone = ZoneInfo("UTC")

        try:
            if start_date:
                start_date = _parse_to_aware(start_date, target_timezone)
            if end_date:
                end_date = _parse_to_aware(end_date, target_timezone)
        except ValueError as e:
            return Response({"error": f"Неверный формат даты: {e}"}, status=400)

        if not start_date or not end_date:
            return Response({"error": "Параметры start_date и end_date обязательны"}, status=400)

        now = datetime.now(target_timezone)

        if (end_date - start_date).days > 60:
            return error_response("Превышен период в 60 дней", status.HTTP_400_BAD_REQUEST)
        if start_date > now + timedelta(hours=12):
            return error_response("Начало периода выше текущей даты", status.HTTP_400_BAD_REQUEST)
        if end_date > now + timedelta(days=1):
            end_date = now + timedelta(days=1)

        try:
            result, status_code = MotohoursCalculationService.calculate_motohours(
                car_id=car_id, agg=agg,
                start_date=start_date, end_date=end_date, is_save_bad_data=is_save_bad_data
            )
            if status_code == 400 and isinstance(result, dict) and "not exist" in str(result.get("error", "")).lower():
                status_code = 404
        except (ObjectDoesNotExist, ValidationError):
            return Response({"error": "Автомобиль не найден в системе"}, status=404)

        return Response(result, status=status_code)

class UserRegistrationAPIView(APIView):
    permission_classes = [IsNotDemoUser]

    @swagger_auto_schema(
        operation_summary="Зарегистрировать нового пользователя",
        operation_description="Создаёт нового пользователя в системе. Требует передачи данных организации.",
        responses={201: UserRegistrationSerializer(), 400: "Ошибка валидации", 404: "Организация не найдена"}
    )
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        return user_registered_response(user)


class DataProviderCreateAPIView(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(**DATA_PROVIDER_CREATE_SCHEMA)
    def post(self, request):
        serializer = DataProviderSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        validated_data = serializer.validated_data
        cars = validated_data.pop('cars', [])
        is_valid, valid_cars = validate_provider_cars(cars, request.user.org.id)
        if not is_valid:
            return valid_cars
        data_provider = create_data_provider(validated_data, request.user.org.id, valid_cars)
        output_serializer = DataProviderOutputSerializer(data_provider)
        return success_response(output_serializer.data, status.HTTP_201_CREATED)


class OrganizationListAPIView(ListAPIView):
    serializer_class = OrganizationOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name', 'bot_token', 'chat_id']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return Organization.objects.none()
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
        if _ANON_GUARD(self):
            return OrgUser.objects.none()
        return OrgUser.objects.filter(
            org_id=self.request.user.org_id
        ).select_related('org', 'active_language').order_by('id')


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
    filterset_class = CarFilter
    search_fields = ['name', 'description', 'car_unit__name', 'model__label', 'model_specs__label']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return Car.objects.none()
        user = self.request.user
        language_code = _get_language_code(user)
        return Car.objects.filter(
            data_providers__org_id=user.org.id
        ).select_related('car_unit', 'model', 'model_specs').prefetch_related(
            *_car_prefetch(language_code)
        ).distinct().order_by('id')


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
        if _ANON_GUARD(self):
            return SensorsValues.objects.none()
        key_value = self.request.query_params.get("key")
        if key_value == "motohours":
            result = SensorsValues.objects.select_related("car_id", 'key').filter(
                car_id__data_providers__org_id=self.request.user.org.id, is_active=True
            ).filter(Q(key__key="motohours") | Q(key__key="rpm") | Q(key__key="ign"))
        elif key_value == "fuel":
            result = SensorsValues.objects.select_related("car_id", 'key').filter(
                car_id__data_providers__org_id=self.request.user.org.id, is_active=True
            ).filter(Q(key__key="calc_sensors_fuel_level") | Q(key__key="fuel_consumpt"))
        else:
            result = SensorsValues.objects.select_related("car_id", 'key').filter(
                car_id__data_providers__org_id=self.request.user.org.id, key__key=key_value, is_active=True
            )
        return result.order_by("id")


class AutoDataListAPIView(SwaggerSafeQuerysetMixin, ListAPIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]
    serializer_class = AutoDataOutputSerializer

    def get_queryset(self):
        if _ANON_GUARD(self):
            return Car.objects.none()
        if self.request.user.is_staff:
            fuel_subquery = Subquery(
                SensorsValues.objects.filter(
                    car_id=OuterRef("pk"),
                    key__key="calc_sensors_fuel_level"
                ).values("value")[:1]
            )
            return Car.objects.annotate(fuel_sensor=fuel_subquery)
        return Car.objects.none()

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        cars = list(Car.objects.all())
        total_autos = None
        for car in cars:
            auto_df = CarDataService.prepare_auto_data(car, return_dict=True)
            if total_autos is None:
                total_autos = auto_df
            else:
                total_autos.extend(auto_df)
        df = pandas.DataFrame(total_autos)
        df.to_csv("/data/datasets/fuel/Cars-new.csv")
        return queryset


class CarMileageReportListAPIView(ListAPIView):
    serializer_class = CarMileageReportOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car_id', 'datetime']
    search_fields = ['car_id__name', 'fraud']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarMileageReport.objects.none()
        return CarMileageReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        ).select_related('car_id').order_by('-datetime')

class CarMotohoursReportListAPIView(ListAPIView):
    serializer_class = CarMotohoursReportOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backend = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["car_id", 'datetime']
    search_fields = ["car_id__name"]
    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarMotohoursReport.objects.none()
        return CarMotohoursReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        ).select_related("car_id").order_by('-datetime')


class CarMileageReportDetailAPIView(SwaggerSafeQuerysetMixin, RetrieveAPIView):
    serializer_class = CarMileageReportOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarMileageReport.objects.none()
        return CarMileageReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        ).select_related('car_id')


class ParsingStatsParsingSwitch(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(**PARSING_STATS_SWITCH_SCHEMA)
    def post(self, request):
        serializer = ParsingStatsSwitchSerializer(data=request.data)
        if not serializer.is_valid():
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


class ParsingStatsUpdateRpm(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(**PARSING_STATS_RPM_SCHEMA)
    def post(self, request):
        serializer = ParsingStatsUpdateRpmSerializer(data=request.data)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        rpm_idle = data.get("rpm_idle")
        car_id = data.get("car_id")
        try:
            target = ParsingCarStats.objects.filter(car_id=car_id)
        except BaseException:
            return error_response(f"Объект с id {car_id} не существует", status.HTTP_400_BAD_REQUEST)
        result = target.update(rpm_idle=rpm_idle)
        return success_response({"updated": result}, 200)


class CarDetailAPIView(SwaggerSafeQuerysetMixin, RetrieveUpdateAPIView):
    permission_classes = [IsDemoUser, IsOrgMember]
    lookup_field = 'pk'
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_serializer_class(self):
        if self.request and self.request.method == 'PATCH':
            return CarSpecsUpdateSerializer
        return CarOutputSerializer

    def get_queryset(self):
        if _ANON_GUARD(self):
            return Car.objects.none()
        language_code = _get_language_code(self.request.user)
        return Car.objects.filter(
            data_providers__org_id=self.request.user.org
        ).select_related('car_unit', 'model', 'model_specs').prefetch_related(
            *_car_prefetch(language_code)
        ).distinct()

    def get_object(self):
        if not hasattr(self, '_cached_object'):
            self._cached_object = super().get_object()
        return self._cached_object

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if _ANON_GUARD(self):
            return context
        user_language = getattr(self.request.user, 'active_language', None)
        context['language_code'] = user_language.code if user_language else 'ru'
        car = self.get_object()
        if car.car_unit:
            context['car_unit_info'] = {
                'id': str(car.car_unit.id),
                'name': car.car_unit.name
            }
        return context


class CarModelListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsDemoUser, IsAuthenticated]
    serializer_class = CarModelSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['label']
    search_fields = ['label']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarModel.objects.none()
        return CarModel.objects.all().order_by('label')


class CarModelDetailAPIView(SwaggerSafeQuerysetMixin, RetrieveUpdateAPIView):
    permission_classes = [IsDemoUser, IsAuthenticated]
    serializer_class = CarModelSerializer
    lookup_field = 'pk'
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarModel.objects.none()
        return CarModel.objects.all()


class CarModelSpecificationListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsDemoUser, IsAuthenticated]
    serializer_class = CarModelSpecificationSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['label']
    search_fields = ['label']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarModelSpecification.objects.none()
        return CarModelSpecification.objects.all().order_by('label')


class CarModelSpecificationDetailAPIView(SwaggerSafeQuerysetMixin, RetrieveUpdateAPIView):
    permission_classes = [IsDemoUser, IsAuthenticated]
    serializer_class = CarModelSpecificationSerializer
    lookup_field = 'pk'
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarModelSpecification.objects.none()
        return CarModelSpecification.objects.all()


class CarUnitListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarUnitSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarUnit.objects.none()
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
        if _ANON_GUARD(self):
            return CarConsumption.objects.none()
        user = self.request.user
        if user.org is None:
            return CarConsumption.objects.none()
        language_code = _get_language_code(user)
        return CarConsumption.objects.filter(
            car_id__data_providers__org_id=user.org
        ).select_related('car_id__car_unit', 'car_id__model', 'car_id__model_specs').prefetch_related(
            *_car_prefetch(language_code, prefix='car_id__')
        ).order_by('id')


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
        if _ANON_GUARD(self):
            return ReportQuery.objects.none()
        return ReportQuery.objects.filter(
            provider_id__org_id=self.request.user.org
        ).select_related('provider_id')


class ReportQueryDetailAPIView(SwaggerSafeQuerysetMixin, RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = ReportQueryOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        if _ANON_GUARD(self):
            return ReportQuery.objects.none()
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
        if _ANON_GUARD(self):
            return CarReport.objects.none()
        language_code = _get_language_code(self.request.user)
        return CarReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        ).select_related('car_id__car_unit', 'car_id__model', 'car_id__model_specs').prefetch_related(
            *_car_prefetch(language_code, prefix='car_id__')
        ).order_by('datetime')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(response.data, user_timezone)
        return response


class CarReportDetailAPIView(SwaggerSafeQuerysetMixin, TimestampTimezoneConverterMixin, RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarReport.objects.none()
        return CarReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org
        ).select_related('car_id__car_unit', 'car_id__model', 'car_id__model_specs')

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(response.data, user_timezone)
        return response


class CarFuelReportListAPIView(TimestampTimezoneConverterMixin, ListAPIView):
    serializer_class = CarFuelReportSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car_id', 'start_moment']
    search_fields = ['car_id__name', 'car_id__id_in_provider_system']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarFuelReport.objects.none()
        return CarFuelReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org.id
        ).select_related('car_id').annotate(
            fuel_leaked=_fuel_leaked_annotation()
        ).distinct().order_by('-start_moment')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(response.data, user_timezone)
        return response


class CarFuelReportDetailAPIView(SwaggerSafeQuerysetMixin, TimestampTimezoneConverterMixin, RetrieveAPIView):
    serializer_class = CarFuelReportSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarFuelReport.objects.none()
        return CarFuelReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org.id
        ).select_related('car_id').annotate(
            fuel_leaked=_fuel_leaked_annotation()
        ).distinct()

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        user_timezone = getattr(request.user, 'timezone', 'UTC')
        response.data = self.convert_timestamps_to_user_timezone(response.data, user_timezone)
        return response


class UserCarListListView(ListCreateAPIView):
    permission_classes = [IsDemoUser, IsOrgMember]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return UserCarList.objects.none()
        return UserCarList.objects.filter(user=self.request.user).order_by('name')

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return UserCarListSerializer
        return UserCarListCreateUpdateSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class UserCarListDetailView(SwaggerSafeQuerysetMixin, RetrieveUpdateDestroyAPIView):
    permission_classes = [IsDemoUser, IsAuthenticated]
    lookup_field = 'pk'
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return UserCarList.objects.none()
        language_code = _get_language_code(self.request.user)
        return UserCarList.objects.filter(user=self.request.user).prefetch_related(
            Prefetch(
                'car_set',
                queryset=Car.objects.select_related('car_unit').prefetch_related(
                    *_car_prefetch(language_code)
                )
            )
        )

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return UserCarListDetailSerializer
        return UserCarListCreateUpdateSerializer

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data
        stats = Car.objects.filter(list_id=instance).aggregate(
            car_count=Count('id'),
            active_cars=Count('id', filter=Q(is_active=True)),
            tarrified_cars=Count('id', filter=Q(is_tarrified=True)),
        )
        data['stats'] = stats
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
        if _ANON_GUARD(self):
            return Driver.objects.none()
        return Driver.objects.filter(
            driver_cars__car_id__data_providers__org_id=self.request.user.org
        ).prefetch_related('driver_cars__car_id', 'driver_cars__car_id__model', 'driver_cars__car_id__model_specs').order_by('id')


class DriverDetailAPIView(SwaggerSafeQuerysetMixin, RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverOutputSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        if _ANON_GUARD(self):
            return Driver.objects.none()
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
        if _ANON_GUARD(self):
            return DataProvider.objects.none()
        return DataProvider.objects.filter(org_id=self.request.user.org).order_by('id')


class DataProviderDetailAPIView(SwaggerSafeQuerysetMixin, RetrieveUpdateDestroyAPIView):
    permission_classes = [IsDemoUser, IsOrgMember]
    serializer_class = DataProviderUpdateSerializer
    lookup_field = 'pk'
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return DataProvider.objects.none()
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
        if _ANON_GUARD(self):
            return CarReport.objects.none()
        language_code = _get_language_code(self.request.user)
        return CarReport.objects.filter(
            car_id__data_providers__org_id=self.request.user.org,
            status=True
        ).select_related('car_id__car_unit', 'car_id__model', 'car_id__model_specs').prefetch_related(
            *_car_prefetch(language_code, prefix='car_id__')
        ).order_by('-datetime')


class SensorsKeyListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter]
    search_fields = ['key']
    serializer_class = SensorsKeyOutputSerializer

    def get_queryset(self):
        if _ANON_GUARD(self):
            return SensorsValues.objects.none()
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
        if _ANON_GUARD(self):
            return SensorsValues.objects.none()
        car_id = self.kwargs.get('car_id')
        language_code = get_user_language_code(self.request.user)
        search_query = self.request.query_params.get('search', None)
        return get_car_sensors_values(
            car_id=car_id,
            org_id=self.request.user.org.id,
            language_code=language_code,
            search_query=search_query
        )


class CarSensorsValuesTrue(ListAPIView):
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    filter_backends = [SearchFilter]
    search_fields = ["car_id"]
    serializer_class = SensorsValuesOutputSerializer

    def get_queryset(self):
        if _ANON_GUARD(self):
            return SensorsValues.objects.none()
        language_code = get_user_language_code(self.request.user)
        search_query = self.request.query_params.get('search', None)
        return SensorsValues.objects.none()


class CarSensorsSwitchView(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Переключить активный датчик автомобиля",
        operation_description=(
            "Переключает активный датчик (`is_active`) для указанного автомобиля и типа датчика. "
            "Если датчик не является мультидатчиком (`multi=False`), остальные датчики того же типа деактивируются. "
            "Для мультидатчиков состояние переключается независимо."
        ),
        **CAR_SENSOR_SWITCH_SCHEMA
    )
    def post(self, request):
        serializer = CarSensorsSwitchSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        try:
            vdata = serializer.validated_data
            car_id = vdata.get("car_id") or ""
            sensor_id = vdata.get("sensor_id") or ""
            key_name = vdata.get("key_name")
            SensorsValues.objects.filter(
                car_id=car_id, key__key=key_name, multi=False
            ).exclude(id=sensor_id).update(is_active=False)
            updated = SensorsValues.objects.filter(
                car_id=car_id, key__key=key_name, id=sensor_id
            ).update(is_active=~F("is_active"))
            if updated == 0:
                return error_response(f"Не найден сенсор с {key_name} для {car_id} и id {sensor_id}", 404)
            return success_response({"updated": updated}, 200)
        except BaseException as err:
            return error_response(f"Непредвиденная ошибка {err}", 500)


class CarBadDataAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    pagination_class = StandardResultsSetPagination
    serializer_class = CarBadDataSerializer

    @swagger_auto_schema(**BAD_DATA_SCHEMA)
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarBadData.objects.none()

        filter_serializer = CarBadDataFilterSerializer(data=self.request.query_params)
        if not filter_serializer.is_valid():
            logger.error(f"Ошибка фильтрации CarBadData: {filter_serializer.errors}")
            return CarBadData.objects.none()

        data = filter_serializer.validated_data
        org = self.request.user.org

        queryset = CarBadData.objects.filter(
            # car_id__data_providers__org_id=org.id
            report_query_id__provider_id__org_id=org.id
        ).select_related('car_id').distinct().order_by('-datetime')

        car_id = data.get('car_id')
        if car_id:
            try:
                car = Car.objects.get(id=car_id, data_providers__org_id=org.id)
                queryset = queryset.filter(car_id=car)
            except (Car.DoesNotExist, ValueError):
                return CarBadData.objects.none()

        if data.get('start_date'):
            queryset = queryset.filter(datetime__date__gte=data['start_date'])
        if data.get('end_date'):
            queryset = queryset.filter(datetime__date__lte=data['end_date'])
        if data.get('search'):
            queryset = queryset.filter(reason__icontains=data['search'])
        if data.get('severity'):
            queryset = queryset.filter(severity__in=data['severity'])
        if data.get('tags'):
            queryset = queryset.filter(tags__overlap=list(data['tags']))
        if data.get('category'):
            queryset = queryset.filter(category__in=data['category'])

        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
            car_id = request.query_params.get('car_id')
            if car_id and not queryset.exists():
                try:
                    Car.objects.get(id=car_id)
                    return Response(
                        {"error": "Автомобиль не принадлежит вашей организации"},
                        status=status.HTTP_404_NOT_FOUND
                    )
                except Car.DoesNotExist:
                    return Response({"error": "Автомобиль не найден"}, status=status.HTTP_404_NOT_FOUND)
                except ValueError:
                    return Response({"error": "Неверный формат UUID автомобиля"}, status=status.HTTP_400_BAD_REQUEST)
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.error(f"Ошибка получения CarBadData: {e}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CarBadDataDetailAPIView(RetrieveAPIView):
    # TODO: permission проблема при GET, поправить по человечески
    # permission_classes = [IsOrgMember]
    serializer_class = CarBadDataSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        if _ANON_GUARD(self):
            return CarBadData.objects.none()
        return CarBadData.objects.filter(
            car_id__data_providers__org_id=self.request.user.org.id
        ).select_related('car_id').distinct()

class CarCarDataPreparedAPiView(APIView):
    # TODO: admin only
    
    def post(self, request):
        cars = list(Car.objects.all().select_related("carprimary").prefetch_related("consumptions").filter(
            consumptions__isnull=False, carprimary__isnull=False
        ))
        result = None
        result_primary = None
        result_consumptions = None
        for car in cars:
            tmp = CarDataService.prepare_auto_data(car, return_dict=True)
            if result is None:
                
                result = tmp
            else:
                result.extend(tmp)
            if car.carprimary.primary:
                if result_primary is None:
                    result_primary = car.carprimary.primary
                else:
                    result_primary.extend(car.carprimary.primary)
            if car.consumptions.first():
                if result_consumptions is None:
                    result_consumptions = [car.consumptions.first().json_data]
                else:
                    result_consumptions.append(car.consumptions.first().json_data)
        result = pl.DataFrame(result)
        result_primary = pl.DataFrame(result_primary)
        result_consumptions = pl.DataFrame(result_consumptions)
        if isinstance(result, pl.DataFrame):
            result = _flatten_nested_for_csv(result)
            result.write_csv("/data/datasets/fuel/cars.csv")
        if isinstance(result_primary, pl.DataFrame):
            result_primary = _flatten_nested_for_csv(result_primary)
            result_primary.write_csv("/data/datasets/fuel/cars_primary.csv")
        if isinstance(result_consumptions, pl.DataFrame):
            result_consumptions = _flatten_nested_for_csv(result_consumptions)
            result_consumptions.write_csv("/data/datasets/fuel/cars_consumptions.csv")
        return success_response({"ok": True}, 200)

class CarBadDataDashboardAPIView(APIView):
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**BAD_DATA_DASHBOARD_SCHEMA)
    def get(self, request):
        serializer = BadDataQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            logger.error(f"Ошибка валидации параметров: {serializer.errors}")
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        org_id = request.user.org.id
        period_from = data.get("periodFrom")
        period_due = data.get("periodDue")
        category = data.get("category") or None
        tags = data.get("tags") or None
        dispatch = {
            BadDataQuerySerializer.TYPE_CALENDAR: get_bad_data_calendar,
            BadDataQuerySerializer.TYPE_CAR: get_bad_data_by_car,
            BadDataQuerySerializer.TYPE_TAG: get_bad_data_by_tag,
        }
        handler = dispatch[data["type"]]
        result = handler(org_id, period_from, period_due, category, tags)
        return success_response(result, status.HTTP_200_OK)


class StartTerminalMessagesParsingView(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

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
                    return Response({'error': 'start_date должен быть раньше end_date'},
                                    status=status.HTTP_400_BAD_REQUEST)
            except ValueError:
                return Response({'error': 'Неверный формат даты. Используйте YYYY-MM-DD'},
                                status=status.HTTP_400_BAD_REQUEST)

            provider = DataProvider.objects.filter(name=provider_name).first()

            if car_ids and not parse_all:
                try:
                    validated_car_ids = []
                    for car_id in car_ids:
                        validated_car_ids.append(UUID(car_id) if isinstance(car_id, str) else car_id)
                    car_ids = validated_car_ids
                except (ValueError, TypeError) as e:
                    return Response({'error': f'Неверный формат car_ids: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

            if parse_all:
                try:
                    car_ids = list(map(lambda car: car.id, list(provider.cars.all())))
                except (ValueError, TypeError) as e:
                    return Response({'error': f'Неверный формат car_ids: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

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
            return Response({'error': f'Ошибка запуска задачи: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LanguageListAPIView(ListAPIView):
    queryset = Language.objects.all()
    serializer_class = LanguageSerializer
    pagination_class = None


class CarLeaksChartsAPIView(TimestampTimezoneConverterMixin, APIView):
    permission_classes = [IsNotDemoUser, IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Получить данные для графика сливов топлива",
        operation_description=(
            "Возвращает три набора данных для построения аналитических графиков по сливам: "
            "`data_fast` — точки при движении (pos_s > 1), "
            "`data_slow` — точки в покое (pos_s < 1) с RPM или IGN данными, "
            "`data_bar` — агрегированные значения по месяцу и году с маркировкой сливов."
        ),
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

        start_date = (datetime.now() - timedelta(days)).astimezone(pytz.utc)
        start_date_month = (datetime.now() - timedelta(31)).astimezone(pytz.utc)

        computed_data_list = list(
            ComputedData.objects.filter(
                auto__id=car_id,
                timestamp__gte=start_date
            ).order_by('timestamp').values()
        )
        if not computed_data_list:
            return error_response("Нет данных для этой машины", status.HTTP_400_BAD_REQUEST)

        leaks = CarReport.objects.filter(car_id__id=car_id,datetime__gte=start_date, picked_by__in=["fpm_model"])
        leaks_df = polars.DataFrame(list(leaks.values()))

        df = polars.DataFrame(computed_data_list)
        tmp = df.select(["fpm", "pos_s", "timestamp", "z_values_fpm", "dtime", "ign_spread", "dtime_moving", "rpm_mean", "filtered", "sf_m", "spent_fuel", "leak_display"])
        tmp = tmp.rename({"z_values_fpm": "z_values"})
        if leaks_df.shape[0] > 0:
            tmp = tmp.with_columns(polars.col("timestamp").is_in(leaks_df["datetime"].unique()).alias("is_leak"))
        else:
            tmp = tmp.with_columns(pl.lit(False).alias("is_leak"))
        tmp = tmp.filter(pl.col("filtered").eq(False))
        # dtime, dtime_moving to minutes
        tmp = tmp.with_columns(pl.col("dtime").truediv(60), pl.col("dtime_moving").truediv(60))
        tmp = tmp.with_columns(
            pl.when(pl.col("timestamp").ge(start_date_month)).then(pl.lit("month")).otherwise(pl.lit("year")).alias("color"))
        tmp = tmp.with_columns(
            pl.when(pl.col("z_values").gt(3)).then(pl.lit("suspicious")).otherwise(pl.col("color")).alias("color")
        )
        tmp = tmp.with_columns(
            pl.when(pl.col("is_leak")).then(pl.lit("leak")).otherwise(pl.col("color")).alias("color"))

        data_fast = tmp.filter(pl.col("pos_s").gt(1)).select(["timestamp", "pos_s", "dtime_moving", "sf_m", "fpm", "color"])
        is_rpm = SensorsValues.objects.filter(key__key="rpm", car_id__id=car_id, is_active=True).count() > 0
        target_column = "rpm_mean" if is_rpm else "ign_spread"
        data_slow = tmp.filter(polars.col("pos_s").lt(1)).select(["timestamp", "pos_s", target_column, "fpm", "color"])
        data_agg = tmp.filter(polars.col("spent_fuel").gt(0))

        month = data_agg.group_by_dynamic(index_column="timestamp", every="1mo").agg([
            polars.mean("z_values"), polars.mean("fpm"), polars.first("spent_fuel"), pl.mean("sf_m"),
        ]).with_columns(polars.lit("month").alias("color"))
        year = data_agg.group_by_dynamic(index_column="timestamp", every="1y").agg([
            polars.mean("z_values"), polars.mean("fpm"), polars.first("spent_fuel"), pl.mean("sf_m")
        ]).with_columns(polars.lit("year").alias("color"))

        data_bar = [
            *tmp.filter(polars.col("is_leak")).select(["timestamp", "z_values", "fpm", "sf_m"]).with_columns(
                polars.lit("leaks").alias("color")).with_columns(
                polars.col("timestamp").dt.to_string("iso:strict")).to_dicts(),
            year.row(0, named=True),
            month.row(0, named=True)
        ]

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
    permission_classes = [IsNotDemoUser, IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Получить сырые данные датчиков для графиков",
        operation_description=(
            "Возвращает обработанные сырые телематические данные автомобиля за период. "
            "Режим (`mode`) определяет тип обработки: `mileage` — пробег, `motohours` — моточасы, "
            "`fuel` — уровень топлива. "
            "Параметр `agg` задаёт агрегацию временного ряда."
        ),
        request_body=CAR_SENSORS_RAW_DATA_SCHEMA['request_body'],
        responses=CAR_SENSORS_RAW_DATA_SCHEMA['responses']
    )
    def post(self, request):
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

            start_date, end_date, date_error = CarSensorsHelper.parse_and_validate_dates(start_date_str, end_date_str)
            if date_error:
                return error_response(date_error, status.HTTP_400_BAD_REQUEST)

            car, car_error = CarSensorsHelper.get_car_for_user(car_id, request.user)
            if car_error:
                return error_response(car_error, status.HTTP_404_NOT_FOUND)

            car_r = Car.objects.select_related('car_unit').get(id=car_id)
            provider = car_r.data_providers.first()
            result, parser, parse_error = CarSensorsHelper.parse_raw_data(car, provider, start_date, end_date, agg, mode)
            if parse_error:
                error_status = (
                    status.HTTP_400_BAD_REQUEST
                    if "Неверный формат" in parse_error
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                return error_response(parse_error, error_status)

            response_data = CarSensorsHelper.build_response_data(
                car_id=car_id, car_name=car.name, mode=mode,
                start_date_str=start_date_str, end_date_str=end_date_str,
                result=result, parser=parser
            )
            user_timezone = getattr(request.user, 'timezone', 'UTC')
            response_data = self.convert_timestamps_to_user_timezone(response_data, user_timezone)
            return success_response(response_data, status.HTTP_200_OK)
        except Exception as e:
            error_data, error_status = CarSensorsHelper.handle_general_exception(e)
            return error_response(error_data["error"], error_status)

    def _validate_request_params(self, car_id, start_date, end_date, mode):
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
                response.set_cookie(key='access_token', value=access_token, httponly=True,
                                    secure=not settings.DEBUG, samesite='Lax', max_age=60 * 60 * 24)
            if refresh_token:
                response.set_cookie(key='refresh_token', value=refresh_token, httponly=True,
                                    secure=not settings.DEBUG, samesite='Lax', max_age=60 * 60 * 24 * 7)
        return response


class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            access_token = response.data.get('access')
            if access_token:
                response.set_cookie(key='access_token', value=access_token, httponly=True,
                                    secure=not settings.DEBUG, samesite='Lax', max_age=60 * 60 * 24)
        return response


class TelegramRegisterAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    @swagger_auto_schema(
        **TELEGRAM_REGISTER_SCHEMA
    )
    def post(self, request):
        serializer = TelegramUserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(serializer.errors, 400)
        data = serializer.validated_data
        chat_id = data['chat_id']
        user_id = data['user_id']
        try:
            org_user = OrgUser.objects.get(id=user_id)
        except OrgUser.DoesNotExist:
            return error_response('User not found', 404)
        tg_user, created = TelegramUser.objects.update_or_create(
            chat_id=chat_id,
            defaults={
                'user': org_user,
                'username': data.get('username', '')[:255],
                'first_name': data.get('first_name', '')[:255],
                'last_name': data.get('last_name', '')[:255],
                'is_active': True,
            }
        )
        output_serializer = TelegramUserOutputSerializer(tg_user)
        return success_response(output_serializer.data, 201 if created else 200)


class APICalculationLogListAPIView(ListAPIView):
    serializer_class = APICalculationLogOutputSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car', 'status_code', 'view_name']
    search_fields = ['view_name', 'car__id', 'car__name', 'car__id_in_provider_system']

    def get_queryset(self):
        if _ANON_GUARD(self):
            return APICalculationLog.objects.none()
        return APICalculationLog.objects.filter(
            user=self.request.user
        ).select_related('car').order_by('-created_at')


class APICalculationLogRetrieveAPIView(RetrieveAPIView):
    serializer_class = APICalculationRetrieveLogOutputSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "pk"

    def get_queryset(self):
        if _ANON_GUARD(self):
            return APICalculationLog.objects.none()
        return APICalculationLog.objects.filter(
            user=self.request.user
        ).select_related('car').order_by('-created_at')


class AlertSubscriptionAPIView(APIView):
    permission_classes = [IsDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Получить настройки подписки на алерты",
        operation_description="Возвращает текущие настройки подписки пользователя на уведомления о сливах и аномалиях.",
        responses={200: AlertSubscriptionPatchSerializer()}
    )
    def get(self, request, *args, **kwargs):
        subscription = get_or_create_subscription(request.user)
        serializer = AlertSubscriptionPatchSerializer(subscription)
        return success_response(serializer.data, status.HTTP_200_OK)

    @swagger_auto_schema(
        **ALERT_SUBSCRIPTION_PATCH_SCHEMA
    )
    def patch(self, request, *args, **kwargs):
        try:
            check_telegram_user(request.user)
        except PermissionDenied as e:
            return error_response(str(e), status.HTTP_400_BAD_REQUEST) # для клиента
        subscription = get_or_create_subscription(request.user)
        serializer = AlertSubscriptionPatchSerializer(
            instance=subscription,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            logger.warning(
                f"Невалидные данные подписки: user={request.user.username}, errors={serializer.errors}"
            )
            return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)
        try:
            subscription = patch_subscription(
                user=request.user,
                validated_data=serializer.validated_data,
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения подписки: user={request.user.username}, error={e}")
            return error_response(str(e), status.HTTP_500_INTERNAL_SERVER_ERROR)
        serializer = AlertSubscriptionPatchSerializer(subscription)
        return success_response(serializer.data, status.HTTP_200_OK)



class MLReasoningView(APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(**ANALYSIS_SCHEMA)
    def post(self, request):
        service = MLReasoningService(request.data)
        result = service.process()

        if result.get('success'):
            return success_response(result['data'], status.HTTP_200_OK)
        else:
            return error_response(result.get('error', 'Unknown error'), status.HTTP_400_BAD_REQUEST)


class StopsMileageAPIView(APICalculationLoggingMixin, APIView):
    permission_classes = [IsNotDemoUser, IsOrgMember]

    @swagger_auto_schema(
        operation_summary="Получить список стоянок автомобиля с пробегом за период",
        operation_description=(
                "Запрашивает у провайдера данные об остановках (moveStop) и почасовом "
                "пробеге/одометре (mileageAndMotohours) за указанный период, сопоставляет их "
                "и возвращает список стоянок: дата/время, адрес, длительность и пробег "
                "(показание одометра) до начала стоянки. Максимальный период — 60 дней."
        ),
        request_body=STOPS_MILEAGE_REQUEST_SCHEMA,
        responses={
            200: STOPS_MILEAGE_RESPONSE_SCHEMA,
            400: "Неверные параметры или превышен период 60 дней",
            404: "Автомобиль не найден",
            500: "Ошибка расчёта",
        },
    )
    def post(self, request):
        serializer = StopsMileageRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result, status_code = get_stops_mileage_report(
            car_id=data["car_id"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            user=request.user,
            is_save_bad_data=data.get("is_save_bad_data", True),
        )

        return success_response(result, status_code)

def api_docs_view(request):
    return render(request, 'api_docs.html', {'api_description_url': '/api/v1/swagger.json'})