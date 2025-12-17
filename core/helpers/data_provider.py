from core.helpers import *
from core.models import DataProvider, Car

def validate_provider_cars(cars, org_id):
    if cars:
        valid_cars = Car.objects.filter(
            id__in=[car.id for car in cars],
            data_providers__org_id=org_id
        )
        if len(valid_cars) != len(cars):
            logger.error("Некоторые автомобили не принадлежат организации пользователя")
            return False, error_response(
                "Один или несколько автомобилей не принадлежат вашей организации",
                status.HTTP_404_NOT_FOUND
            )
        return True, valid_cars
    return True, []


def create_data_provider(validated_data, org_id, cars):
    data_provider = DataProvider.objects.create(**validated_data)
    data_provider.org_id_id = org_id
    data_provider.save()
    if cars:
        data_provider.cars.set(cars)
        logger.info(f"Автомобили {cars} привязаны к провайдеру {data_provider.id}")
    return data_provider
