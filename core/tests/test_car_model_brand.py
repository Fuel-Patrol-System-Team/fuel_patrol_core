import uuid

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import (
    Organization,
    OrgUser,
    DataProvider,
    Car,
    CarModel,
    CarModelSpecification,
)


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
def brand(db):
    return CarModel.objects.create(label="KAMAZ")


@pytest.fixture
def spec(db):
    return CarModelSpecification.objects.create(label="5490")


@pytest.fixture
def car(provider):
    c = Car.objects.create(name="а001аа 77", description="")
    provider.cars.add(c)
    return c


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_car_detail_exposes_nested_model_and_brand(client, car, brand, spec):
    car.model = brand
    car.model_specs = spec
    car.engine_power = 400
    car.fuel_type = Car.FuelType.DIESEL
    car.save()

    resp = client.get(reverse("car-detail", args=[car.id]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == {"id": str(brand.id), "label": "KAMAZ"}
    assert data["model_specs"] == {"id": str(spec.id), "label": "5490"}
    assert data["engine_power"] == 400
    assert data["fuel_type"] == "Дизель"


@pytest.mark.django_db
def test_car_patch_sets_model_brand_and_scalars(client, car, brand, spec):
    resp = client.patch(
        reverse("car-detail", args=[car.id]),
        {
            "model": str(brand.id),
            "model_specs": str(spec.id),
            "engine_power": 300,
            "fuel_type": "Бензин",
        },
        format="json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"]["label"] == "KAMAZ"
    assert body["model_specs"]["label"] == "5490"
    assert body["engine_power"] == 300
    assert body["fuel_type"] == "Бензин"

    car.refresh_from_db()
    assert car.model_id == brand.id
    assert car.model_specs_id == spec.id
    assert car.engine_power == 300
    assert car.fuel_type == "Бензин"


@pytest.mark.django_db
def test_car_patch_rejects_unknown_fuel_type(client, car):
    resp = client.patch(
        reverse("car-detail", args=[car.id]),
        {"fuel_type": "Уран"},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_car_patch_can_clear_model(client, car, brand):
    car.model = brand
    car.save()
    resp = client.patch(
        reverse("car-detail", args=[car.id]),
        {"model": None},
        format="json",
    )
    assert resp.status_code == 200
    car.refresh_from_db()
    assert car.model_id is None


@pytest.mark.django_db
def test_car_list_filter_by_model_label(client, provider, brand):
    matched = Car.objects.create(name="match", description="", model=brand)
    other = Car.objects.create(name="other", description="")
    provider.cars.add(matched, other)

    resp = client.get(reverse("car-list"), {"model_label": "kam"})
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["results"]}
    assert str(matched.id) in ids
    assert str(other.id) not in ids


@pytest.mark.django_db
def test_car_list_filter_by_fuel_type(client, provider):
    diesel = Car.objects.create(name="d", description="", fuel_type=Car.FuelType.DIESEL)
    petrol = Car.objects.create(name="p", description="", fuel_type=Car.FuelType.PETROL)
    provider.cars.add(diesel, petrol)

    resp = client.get(reverse("car-list"), {"fuel_type": "Дизель"})
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["results"]}
    assert str(diesel.id) in ids
    assert str(petrol.id) not in ids


@pytest.mark.django_db
def test_car_model_list_create(client):
    create = client.post(reverse("car-model-list"), {"label": "MAN"}, format="json")
    assert create.status_code == 201
    new_id = create.json()["id"]
    assert CarModel.objects.filter(id=new_id, label="MAN").exists()

    listing = client.get(reverse("car-model-list"), {"search": "MAN"})
    assert listing.status_code == 200
    assert any(row["label"] == "MAN" for row in listing.json()["results"])


@pytest.mark.django_db
def test_car_model_patch_label(client, brand):
    resp = client.patch(
        reverse("car-model-detail", args=[brand.id]),
        {"label": "KAMAZ-renamed"},
        format="json",
    )
    assert resp.status_code == 200
    brand.refresh_from_db()
    assert brand.label == "KAMAZ-renamed"


@pytest.mark.django_db
def test_car_model_specs_list_create(client):
    create = client.post(reverse("car-model-specs-list"), {"label": "TGX 18.440"}, format="json")
    assert create.status_code == 201
    assert CarModelSpecification.objects.filter(label="TGX 18.440").exists()
