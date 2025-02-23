# core/rest.py
from drf_yasg import openapi

BASE_RESPONSE_SCHEMA = {
    "error": openapi.Schema(type=openapi.TYPE_STRING, description="Описание ошибки.")
}

BASE_DATA_RESPONSE_SCHEMA = {
    "data": openapi.Schema(type=openapi.TYPE_OBJECT, description="Данные ответа.")
}

MEDIA_UPLOAD_SCHEMA = {
    "tags": ["Media"],
    "operation_description": "Загружает медиафайл.",
    "manual_parameters": [
        openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Файл для загрузки."),
        openapi.Parameter("type", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Тип файла."),
    ],
    "responses": {
        201: "Успешно",
        400: "Некорректный запрос",
    }
}

ATTACH_MEDIA_SCHEMA = {
    "tags": ["Media"],
    "operation_description": "Прикрепляет медиафайлы к организации. Максимум 3 файла.",
    "request_body": openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "media_uuids": openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_UUID),
                description="Список UUID медиафайлов (от 1 до 3)."
            )
        },
        required=["media_uuids"]
    ),
    "responses": {
        201: "Успешно",
        400: "Некорректный запрос",
        404: "Организация или медиафайл не найдены",
    }
}

LIST_RESPONSE_SCHEMA = {
    "responses": {
        200: "Успешно",
        403: "Доступ запрещён",
        500: "Ошибка сервера",
    }
}

DETAIL_RESPONSE_SCHEMA = {
    "responses": {
        200: "Успешно",
        403: "Доступ запрещён",
        404: "Объект не найден",
        500: "Ошибка сервера",
    }
}