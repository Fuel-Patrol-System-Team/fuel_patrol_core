from django.db.models import Subquery, OuterRef, Value
from django.db.models.functions import Coalesce

from core.models import SensorsKeyLocalization, SensorsValues, Car, SensorsKey


def get_sensors_keys_with_localization(org_id, language_code=None, search_query=None):
    if language_code is None:
        language_code = 'ru'

    queryset = SensorsKey.objects.filter(
        values__car_id__data_providers__org_id=org_id
    ).distinct()

    if search_query:
        queryset = queryset.filter(key__icontains=search_query)

    queryset = queryset.annotate(
        display_name=Coalesce(
            Subquery(
                SensorsKeyLocalization.objects.filter(
                    key=OuterRef('pk'),
                    language__code=language_code
                ).values('localization')[:1]
            ),
            Value('')
        )
    )

    return queryset


def get_car_sensors_values(car_id, org_id, language_code=None, search_query=None):
    if language_code is None:
        language_code = 'ru'

    from django.shortcuts import get_object_or_404
    car = get_object_or_404(
        Car.objects.filter(data_providers__org_id=org_id),
        id=car_id
    )

    queryset = SensorsValues.objects.filter(car_id=car)

    if search_query:
        queryset = queryset.filter(key__key__icontains=search_query)

    queryset = queryset.annotate(
        key_display_name=Coalesce(
            Subquery(
                SensorsKeyLocalization.objects.filter(
                    key=OuterRef('key'),
                    language__code=language_code
                ).values('localization')[:1]
            ),
            Value('')
        )
    ).select_related('key', 'car_id')

    return queryset


def get_user_language_code(user):
    user_language = getattr(user, 'active_language', None)
    return user_language.code if user_language else 'ru'
