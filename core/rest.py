from drf_yasg import openapi

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
