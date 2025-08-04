from core.helpers import *

import uuid
from celery.exceptions import CeleryError
from mimetypes import guess_type as mimetypes_guess_type

from core.helpers.media_utils import calculate_file_hash, get_upload_path
from core.models import Media, DataProvider, ReportQuery
from app.tasks import parse_merged_data, process_raw_data_task





def validate_media_upload(uploaded_file, file_type):
    if not uploaded_file:
        logger.error("Файл не загружен")
        return False, error_response("No file uploaded", status.HTTP_400_BAD_REQUEST)

    if file_type not in ["raw", "auto"]:
        logger.error(f"Неверный тип файла: {file_type}")
        return False, error_response("Invalid file type", status.HTTP_400_BAD_REQUEST)
    return True, None


def create_media_instance(uploaded_file, file_type, org_id):
    file_id = uuid.uuid4()
    media_type, _ = mimetypes_guess_type(uploaded_file.name)
    file_extension = uploaded_file.name.split('.')[-1].lower()
    file_hash = calculate_file_hash(uploaded_file)
    new_filename = f"{file_id}.{file_extension}"
    file_size = uploaded_file.size

    try:
        existing_media = Media.objects.get(
            filename=uploaded_file.name,
            size=file_size,
            type=file_type,
            file_hash=file_hash
        )
        if existing_media.report_query_id.status == "completed":
            logger.info(f"Идентичный файл уже существует и заявка завершена успешно: {existing_media.id}")
            return None, error_response("Такой файл уже был загружен и обработан", status.HTTP_400_BAD_REQUEST)
        else:
            logger.info(
                f"Идентичный файл существует, но заявка не завершена успешно (статус: {existing_media.report_query_id.status}). Разрешаем повторную загрузку."
            )
    except Media.DoesNotExist:
        existing_media = None

    provider, _ = DataProvider.objects.get_or_create(
        name='csv',
        org_id_id=org_id,
        defaults={'metadata': {}}
    )
    report_query = ReportQuery.objects.create(
        provider_id=provider,
        status="created"
    )

    media = Media(
        id=file_id,
        media_type=media_type,
        size=file_size,
        filename=uploaded_file.name,
        type=file_type,
        report_query_id=report_query,
        file_hash=file_hash
    )
    upload_path = get_upload_path(new_filename)
    media.file.save(upload_path, uploaded_file)
    media.save()
    report_query.save()

    return media, report_query


def process_media_task(report_query_id, file_type):
    try:
        if file_type == "auto":
            parse_merged_data.delay(report_query_id)
        elif file_type == "raw":
            process_raw_data_task.delay(report_query_id)
        logger.info(f"Медиафайл успешно загружен: {report_query_id}")
        return True, None
    except CeleryError as e:
        logger.error(f"Ошибка Celery при запуске задачи: {e}")
        return False, error_response(f"Failed to launch processing task: {e}", status.HTTP_503_SERVICE_UNAVAILABLE)
