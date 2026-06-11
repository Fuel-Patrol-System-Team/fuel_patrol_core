


from datetime import datetime

import pytest
import pytz
from app.tasks import calculate_leaks_cron_new, calculate_leaks_cron_one, internal_migrate_to_new_processing_system, parse_cars_computed_data_task
from core.models import DataProvider
from django.db.models import Count

@pytest.mark.django_db
def test_computed_task():
    dataprovider = DataProvider.objects.get(id="80710666-9ffc-4ed3-ad40-a3e789ab8796")
    
    internal_migrate_to_new_processing_system()
    parse_cars_computed_data_task(
        provider_id=dataprovider.id,
        is_save_bad_data=False,)

    assert True

@pytest.mark.django_db
def test_task_fuel():
    provider = DataProvider.objects.last()
    
    if provider is not None:
        car = provider.cars.exclude(grades__isnull=True).first()
        start_time =datetime(2026, 6, 1).astimezone(tz=pytz.UTC)
        end_time = datetime.now().astimezone(tz=pytz.UTC)
        task = calculate_leaks_cron_one.si("TEST-2", str("271d2f3f-4527-475f-a15a-809341bf8769"), False, False)
        result = task.apply_async()
        assert result.successful()

@pytest.mark.django_db
def test_task_fuel_new():
    provider = DataProvider.objects.annotate(car_count=Count("cars")).order_by("car_count").filter(car_count__gt=0).first()
    if provider is not None:
        task = calculate_leaks_cron_new.si(provider.name, True)
        result = task.apply_async()
        assert result.successful()
