import datetime
import polars
import pytest

from app.tasks import ComputedDataService
from core.models import Car, DataProvider
from core.services.providers.car_data_service import CarDataService
from core.services.providers.filtering_service import FilteringService
from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.leaks_service import LeaksService
from core.services.providers.norms_service import NormsService
from core.services.providers.report_service import ReportService


@pytest.mark.django_db
def test_parser_compute_single():

    
    provider = DataProvider.objects.first()
    car = provider.cars.get(id="43b8552e-fb05-4ee0-b8a6-78f6af69d8dd")
    start_time = datetime.datetime(2025, 10, 1)
    end_time = datetime.datetime(2025, 12, 21)

    parser = GlonassGeneralProvider(None, car, provider, start_time, end_time, "fuel")
    status, raw_df = parser.parse_raw_data("fuel", return_df=True)
    # raw_df = data_provider.get_car_data(start_dt, end_dt)
    
    auto_df = CarDataService.prepare_auto_data(car)

    primary_df = CarDataService.calculate_primary_single(raw_df, auto_df)
    assert isinstance(primary_df, polars.DataFrame)
    assert primary_df.shape[0] > 0
    norms_df = NormsService.calculate_norms_single(raw_df, primary_df, auto_df)
    assert isinstance(norms_df, polars.DataFrame)
    assert norms_df.shape[0] > 0
    leaks_service = LeaksService()
    leaks_result, intermediate_df = None, None
    if norms_df is not None and not norms_df.is_empty():
        leaks_result, intermediate_df = leaks_service.compute_leaks(
            auto_df=auto_df,
            data_df=raw_df,
            primary_df=primary_df,
            norma_df=norms_df,
            is_save_bad_data=False,
            is_filter_bad_data=True,
        )
    assert isinstance(leaks_result, polars.DataFrame)
    assert leaks_result.shape[0] > 0
    assert status == True
    filtering_service = FilteringService()
    filtering_result = filtering_service.apply_filters(leaks_result)
    assert isinstance(filtering_result, polars.DataFrame)
    assert filtering_result.shape[0] > 0
    
@pytest.mark.django_db
def test_parser_computed_data_single():
    provider = DataProvider.objects.last()
    car = provider.cars.get(id="2354530f-d7a9-4acc-980b-653fd202c370")
    start_time = datetime.datetime(2025, 10, 1)
    end_time = datetime.datetime(2025, 12, 21)

    parser = GlonassGeneralProvider(None, car, provider, start_time, end_time, "fuel")
    status, raw_df = parser.parse_raw_data("fuel", return_df=True)
    # raw_df = data_provider.get_car_data(start_dt, end_dt)
    
    auto_df = CarDataService.prepare_auto_data(car)

    primary_df = CarDataService.calculate_primary_single(raw_df, auto_df)
    assert isinstance(primary_df, polars.DataFrame)
    assert primary_df.shape[0] > 0
    norms_df = NormsService.calculate_norms_single(raw_df, primary_df, auto_df)
    assert isinstance(norms_df, polars.DataFrame)
    assert norms_df.shape[0] > 0
    leaks_service = LeaksService()
    leaks_result, intermediate_df = None, None
    if norms_df is not None and not norms_df.is_empty():
        leaks_result, intermediate_df = leaks_service.compute_leaks(
            auto_df=auto_df,
            data_df=raw_df,
            primary_df=primary_df,
            norma_df=norms_df,
            is_save_bad_data=False,
            is_filter_bad_data=True,
        )
    assert isinstance(leaks_result, polars.DataFrame)
    assert leaks_result.shape[0] > 0
    assert status == True
    computed_data_service = ComputedDataService()
    computed_data_amount, _ = computed_data_service.save_preprocessed_data(leaks_result)
    assert computed_data_amount > 0