from core.models import Car, CarReport
from django.db.models import Count
from django.db.models.functions import TruncDay
from django.db.models import Sum
from core.serializers import CarLeaksSerializer
from core.serializers import CarActiveStatusSerializer

from core.helpers import *

def filter_leaks_by_period(org_id, period_from=None, period_due=None):
    queryset = CarReport.objects.filter(
        car_id__data_providers__org_id=org_id,
        status=True
    )
    if period_from:
        queryset = queryset.filter(datetime__gte=period_from)
    if period_due:
        queryset = queryset.filter(datetime__lte=period_due)
    return queryset


def aggregate_daily_counts(queryset):
    daily_counts = (
        queryset
        .annotate(day=TruncDay('datetime'))
        .values('day')
        .annotate(value=Count('id'))
        .order_by('day')
    )
    return [{"value": item['value'], "day": item['day'].strftime('%Y-%m-%d')} for item in daily_counts]


def get_daily_leaks_sum(org_id, period_from=None, period_due=None):
    queryset = CarReport.objects.filter(
        car_id__data_providers__org_id=org_id,
        status=True
    )
    if period_from:
        queryset = queryset.filter(datetime__gte=period_from)
    if period_due:
        queryset = queryset.filter(datetime__lte=period_due)

    daily_sums = (
        queryset
        .annotate(day=TruncDay('datetime'))
        .values('day')
        .annotate(value=Sum('volume'))
        .order_by('day')
    )

    return [{"value": item['value'], "day": item['day'].strftime('%Y-%m-%d')} for item in daily_sums]


def get_car_leaks_count(org_id, period_from=None, period_due=None):
    queryset = CarReport.objects.filter(
        car_id__data_providers__org_id=org_id,
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
        data_providers__org_id=org_id
    )

    return [
        CarLeaksSerializer({
            'id': str(car.id),
            'label': car.name,
            'value': next(item['value'] for item in leaks_count if item['car_id'] == car.id)
        }).data
        for car in cars
    ]


def get_car_leaks_volume(org_id, period_from=None, period_due=None):
    queryset = CarReport.objects.filter(
        car_id__data_providers__org_id=org_id,
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
        data_providers__org_id=org_id
    )

    return [
        CarLeaksSerializer({
            'id': str(car.id),
            'label': car.name,
            'value': next(item['value'] for item in leaks_volume if item['car_id'] == car.id)
        }).data
        for car in cars
    ]


def update_car_active_status(car_id, request_data, request):
    if not car_id:
        logger.error("Не указан параметр car_id")
        return None, error_response({"car_id": "Параметр car_id обязателен"}, status.HTTP_400_BAD_REQUEST)

    serializer = CarActiveStatusSerializer(data=request_data, context={'request': request, 'car_id': car_id})
    if not serializer.is_valid():
        logger.error(f"Ошибка валидации данных: {serializer.errors}")
        return None, error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)

    serializer.save()
    return {"car_id": car_id}, None


def check_car_exists(car_id, org_id):
    car_exists = Car.objects.filter(
        id=car_id,
        data_providers__org_id=org_id
    ).exists()
    if not car_exists:
        logger.info(f"Автомобиль {car_id} не найден или не принадлежит организации {org_id}")
        return False, error_response(
            "Автомобиль не найден или не принадлежит вашей организации",
            status.HTTP_404_NOT_FOUND
        )
    return True, None


def filter_car_leaks(queryset, car_id, period_from, period_due, volume_from, volume_to):
    queryset = queryset.filter(car_id=car_id)
    if period_from:
        queryset = queryset.filter(datetime__gte=period_from)
    if period_due:
        queryset = queryset.filter(datetime__lte=period_due)
    if volume_from is not None:
        queryset = queryset.filter(volume__gte=volume_from)
    if volume_to is not None:
        queryset = queryset.filter(volume__lte=volume_to)
    logger.info(f"Возвращены сливы для автомобиля {car_id}")
    return queryset
