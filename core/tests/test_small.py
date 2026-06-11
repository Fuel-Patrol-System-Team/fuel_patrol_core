

from datetime import datetime

import pytest
import pytz

from app.tasks import CarDataService, GlonassGeneralProvider
from core.helpers.fuel import tarify_car_by_sensor
from core.models import Car, DataProvider
from django.db.models import Q


@pytest.mark.django_db
def test_tarify_small():
    provider = DataProvider.objects.last()
    if provider is not None:
        car = provider.cars.exclude(grades__isnull=True).first()
        start_time =datetime(2026, 6, 1).astimezone(tz=pytz.UTC)
        end_time = datetime.now().astimezone(tz=pytz.UTC)
        parser = GlonassGeneralProvider([car], None, provider, start_time, end_time, "fuel")
        status, data = parser.parse_raw_data("fuel", True, car, start_time, end_time)
        car_data = CarDataService.prepare_auto_data(car, True)[-1]
        if status:
            tarify_car_by_sensor(data, car_data)

    