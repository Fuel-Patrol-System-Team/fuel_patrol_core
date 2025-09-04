import pytz
from datetime import datetime
from celery.exceptions import CeleryError
from core.helpers import *
from core.models import ReportQuery, DataProvider, Car
from app.tasks import fetch_data_from_provider


def validate_provider_request(provider_name, start_date, end_date, organization):
    if not provider_name:
        logger.error("Имя провайдера не указано")
        return False, error_response("Provider name is required", status.HTTP_400_BAD_REQUEST)

    if not organization:
        logger.error("Организация не найдена для пользователя")
        return False, error_response("User must be associated with an organization", status.HTTP_400_BAD_REQUEST)

    try:
        provider = DataProvider.objects.get(name=provider_name)
    except DataProvider.DoesNotExist:
        logger.error(f"Провайдер {provider_name} не найден")
        return False, error_response(f"Provider {provider_name} not found", status.HTTP_400_BAD_REQUEST)

    metadata = provider.metadata or {}
    if not metadata:
        logger.error(f"Метаданные провайдера {provider_name} отсутствуют")
        return False, error_response(f"Provider {provider_name} metadata is required", status.HTTP_400_BAD_REQUEST)

    try:
        if start_date:
            start_date = datetime.fromisoformat(start_date).replace(tzinfo=pytz.UTC)
        if end_date:
            end_date = datetime.fromisoformat(end_date).replace(tzinfo=pytz.UTC)
        if start_date and end_date and start_date > end_date:
            logger.error("start_date не может быть позже end_date")
            return False, error_response("start_date cannot be later than end_date", status.HTTP_400_BAD_REQUEST)
    except ValueError as e:
        logger.error(f"Неверный формат даты: {e}")
        return False, error_response(f"Invalid date format: {e}", status.HTTP_400_BAD_REQUEST)

    return True, (provider, metadata, start_date, end_date)


def create_provider_data_request(provider, start_date, end_date, is_save_bad_data):
    report_query = ReportQuery.objects.create(
        provider_id=provider,
        status="created",
        is_save_bad_data=is_save_bad_data
    )
    try:
        fetch_data_from_provider.delay(
            report_query_id=report_query.id,
            start_date=start_date,
            end_date=end_date
        )
        logger.info(f"Заявка на получение данных от провайдера {provider.name} создана: {report_query.id}")
        return report_query.id, None
    except CeleryError as e:
        logger.error(f"Ошибка Celery при запуске задачи: {e}")
        report_query.status = "error"
        report_query.save()
        return None, error_response(f"Failed to launch provider data task: {e}", status.HTTP_503_SERVICE_UNAVAILABLE)


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
