import datetime
import pytest
import polars

from app.tasks import ReportService
from core.models import Car, CarBadData, DataProvider, ReportQuery


@pytest.mark.django_db
def test_leaks_saving():
    df = polars.DataFrame([
        {
            'auto': '271d2f3f-4527-475f-a15a-809341bf8769',
            'timestamp': datetime.datetime(2025, 6, 9, 5, 0),
            'pos_s': 33.36676,
            'spent_fuel': 18.909729,
            'pos_s_max': 91.0,
            'spent_fuel_2': -35.866577,
            'spent_fuel_standing': -8.790894,
            'f1': 577.8065,
            'f2': 569.0156,
            'spent_fuel_rolling': -0.17935932,
            'dtime': 1186,
            'travel': 15.43082852906651,
            'count': 156,
            'amtr': 0,
            'jumps': 0,
            'rpm_mean': 0,
            'fd': 96,
            'fuel_level_nan': 11,
            'no_sat_data': 0,
            'load': 852.5231,
            'ign_max': 33.0,
            'ign': 152.0,
            'fuel_first': 585.16956,
            'fuel_last': 569.0156,
            'ptime': 19.76666666666666,
            'refuel': 0.0,
            'spent_fuel_m': -20.816711,
            'pos_s_m': 39.658707,
            'dtime_m': 1037,
            'spent_per_100': -122.54513079635781,
            'f1_diff': 0.0,
            'bad_flags': 0,
            'is_bad_data': False,
            'decrease_ratio': 0.6153846153846154,
            'norma_rasx': 3.3368979,
            'norma_rasx_per_travel': 3.3368979,
            'leak': 15.57283110390625,
            'leak_factor': 12,
            'untariffed': False,
            'load_ratio': 0.7188221511712163,
            'is_leak': True,
            '': 41,
            'id_in_provider_system': 475921,
            'car_unit_id': 'bf82b1fb-3efb-4ee6-b90b-ca2e4ce44df6',
            'name': 'MAN Т 343 НУ 193',
            'description': 'ИП Чрагян Ашот Айкович, Грузовой тягач седельный, Тягачи',
            'sl_tip_dvigat': 0.0,
            'input': 4096.0,
            'output': 600.0,
            'grades': '{"grades": [{"input": 1.0, "output": 10.0}, {"input": 4095.0, "output": 600.0}, {"input": 4096.0, "output": 600.0}]}',
            'created_at': '2026-03-11 07:42:02.495127+00:00',
            'last_processed_date': '2026-03-13 11:34:01.306090+00:00',
            'is_tarrified': True,
            'is_active': True,
            'list_id_id': None,
            'fuel_sensor': 'parameters.fuel8',
            'max_fuel': 599.8535,
            'norma_rasx_summer': 3.3368979,
            'norma_rasx_winter': 3.6705875,
            'norma_mean': 3.3368979,
            'norma_std': 5.7207313,
            'speed_etalon': 51.373333,
            'norma_rpm_mean': 0.0,
            'norma_rpm_std': 0.0,
            'norma_rpm_max': 0.0,
            'norma_fpm_mean': 0.0,
            'norma_fpm_std': 0.0,
            'period': '2027-03-12T10:39:24.743078',
            'is_special_car': True,
            'max_fuel_right': 4095.0,
            'rpm_max': 0.0,
            'rpm_std': None,
            'ign_functional': 1.0,
            'norm_speed': 65.0,
            'norm_speed_2': 85.0,
            'ign_working': True,
            'sp_factor': 0.5827194903444157,
            'is_special_car_right': True,
            'is_leak_sigma': True,
            'z_values': 2.722174891155306,
            'sat_coverage': 1.0,
            'low_speed': False}
    ])
    result = ReportService.save_car_reports_batch(df)

    pass

@pytest.mark.django_db
def test_adding_bad_data_from_list():
    car = Car.objects.first()
    bad_data_list = [
        {
            "event_date": datetime.datetime.now(),
            "message": f"Одинаковый уровень топлива, несмотря на пройденное расстояние {car.name}",
            "tags": [CarBadData.Tag.SENSOR,],
            "category": CarBadData.Category.MAINTENANCE,
            "severity": CarBadData.Severity.WARNING,
        }
    ]
    provider =DataProvider.objects.first()
    report_query, _ = ReportService.create_report(str(provider.id), "leaks")
    
    report = ReportService.create_bad_data_record_from_list(car, bad_data_list, report_query )



    