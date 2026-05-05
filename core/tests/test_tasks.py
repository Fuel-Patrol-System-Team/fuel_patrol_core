


import pytest
from app.tasks import internal_migrate_to_new_processing_system, parse_cars_computed_data_task
from core.models import DataProvider

@pytest.mark.django_db
def test_computed_task():
    dataprovider = DataProvider.objects.get(id="80710666-9ffc-4ed3-ad40-a3e789ab8796")
    
    internal_migrate_to_new_processing_system()
    parse_cars_computed_data_task(
        provider_id=dataprovider.id,
        is_save_bad_data=False,)

    assert True