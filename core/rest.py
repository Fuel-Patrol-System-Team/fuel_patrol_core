# core/rest.py
from drf_yasg import openapi
from .serializers import (
    MediaSerializer, ReportQuerySerializer
)

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
        openapi.Parameter(
            name="file",
            in_=openapi.IN_FORM,
            type=openapi.TYPE_FILE,
            required=True,
            description="Файл для загрузки."
        ),
        openapi.Parameter(
            name="type",
            in_=openapi.IN_FORM,
            type=openapi.TYPE_STRING,
            required=False,
            description="Тип файла (опционально)."
        ),
    ],
    "responses": {
        201: openapi.Response("Успешно", MediaSerializer),
        400: openapi.Response("Некорректный запрос", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties=BASE_RESPONSE_SCHEMA
        ))
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
        201: openapi.Response("Медиафайлы успешно прикреплены.", ReportQuerySerializer),
        400: openapi.Response("Некорректный запрос", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties=BASE_RESPONSE_SCHEMA
        )),
        404: openapi.Response("Организация или медиафайл не найдены", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties=BASE_RESPONSE_SCHEMA
        ))
    }
}

LIST_RESPONSE_SCHEMA = {
    "responses": {
        200: openapi.Response("Успешно", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "data": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Schema(type=openapi.TYPE_OBJECT)
                )
            }
        )),
        500: openapi.Response("Ошибка сервера", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties=BASE_RESPONSE_SCHEMA
        ))
    }
}

DETAIL_RESPONSE_SCHEMA = {
    "responses": {
        200: openapi.Response("Успешно", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties=BASE_DATA_RESPONSE_SCHEMA
        )),
        404: openapi.Response("Объект не найден", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties=BASE_RESPONSE_SCHEMA
        )),
        500: openapi.Response("Ошибка сервера", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties=BASE_RESPONSE_SCHEMA
        ))
    }
}
