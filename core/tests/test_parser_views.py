import datetime

from django.db.models import Q
import pytest
import polars as pl
from rest_framework.test import APIClient
from django.http import HttpResponse
from django.urls import reverse

from core.models import OrgUser
from core.services.providers.car_sensors_raw_parser import CarSensorsRawParser


@pytest.mark.django_db
def test_parser_charts_view():
    client = APIClient()
    user = OrgUser.objects.filter(~Q(org=None)).first()
    client.force_authenticate(user)
    print(reverse("car-sensors-raw-data"))

    response: HttpResponse = client.post(
        reverse("car-sensors-raw-data"),
        {
            "car_id": "87c04b05-4571-4eab-9c81-b91b9d8eb413",
            "start_date": datetime.date(2025, 7, 14),
            "end_date": datetime.date(2025, 7, 16),
            "mode": "mileage",
        },
        format="json"
    ) # type: ignore
    response.content
    assert response.status_code == 200
    

