import datetime
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Organization, OrgUser, DataProvider, Car, CarFuelReport, CarReport


@pytest.fixture
def org(db):
    return Organization.objects.create(name=f"org-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def user(org):
    u = OrgUser.objects.create(username=f"u-{uuid.uuid4().hex[:8]}", org=org)
    u.set_password("pass12345")
    u.save()
    return u


@pytest.fixture
def provider(org):
    return DataProvider.objects.create(name=f"prov-{uuid.uuid4().hex[:8]}", org_id=org)


@pytest.fixture
def car(provider):
    c = Car.objects.create(name="а777аа 77", description="")
    provider.cars.add(c)
    return c


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_fuel_report_fuel_leaked_sums_leaks_inside_window(client, car):
    start = timezone.make_aware(datetime.datetime(2026, 6, 1, 0, 0))
    end = timezone.make_aware(datetime.datetime(2026, 6, 2, 0, 0))
    report = CarFuelReport.objects.create(car_id=car, start_moment=start, end_moment=end, fuel_start=100, fuel_end=40)

    CarReport.objects.create(car_id=car, datetime=start + datetime.timedelta(hours=3), volume=15, status=True)
    CarReport.objects.create(car_id=car, datetime=start + datetime.timedelta(hours=10), volume=25, status=True)
    # не считается: вне окна
    CarReport.objects.create(car_id=car, datetime=end + datetime.timedelta(hours=5), volume=100, status=True)
    # не считается: status=False
    CarReport.objects.create(car_id=car, datetime=start + datetime.timedelta(hours=4), volume=100, status=False)

    resp = client.get(reverse("fuel-report-list"), {"car_id": str(car.id)})
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()["results"]}
    assert rows[str(report.id)]["fuel_leaked"] == 40


@pytest.mark.django_db
def test_fuel_report_fuel_leaked_zero_when_no_leaks(client, car):
    start = timezone.make_aware(datetime.datetime(2026, 7, 1, 0, 0))
    end = timezone.make_aware(datetime.datetime(2026, 7, 2, 0, 0))
    report = CarFuelReport.objects.create(car_id=car, start_moment=start, end_moment=end)

    resp = client.get(reverse("fuel-report-list"), {"car_id": str(car.id)})
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()["results"]}
    assert rows[str(report.id)]["fuel_leaked"] == 0


@pytest.mark.django_db
def test_fuel_report_detail_has_fuel_leaked(client, car):
    start = timezone.make_aware(datetime.datetime(2026, 8, 1, 0, 0))
    end = timezone.make_aware(datetime.datetime(2026, 8, 2, 0, 0))
    report = CarFuelReport.objects.create(car_id=car, start_moment=start, end_moment=end)
    CarReport.objects.create(car_id=car, datetime=start + datetime.timedelta(hours=1), volume=12, status=True)

    resp = client.get(reverse("fuel-report-detail", args=[report.id]))
    assert resp.status_code == 200
    assert resp.json()["fuel_leaked"] == 12
