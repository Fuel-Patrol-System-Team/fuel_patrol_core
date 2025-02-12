from drf_yasg import openapi

MEDIA_UPLOAD_SCHEMA = {
    "method": 'POST',
    "tags": ['Media'],
    "operation_description": "Upload a media file.",
    "manual_parameters": [
        openapi.Parameter(
            name='file',
            in_=openapi.IN_FORM,
            type=openapi.TYPE_FILE,
            required=True,
            description="File to upload"
        ),
        openapi.Parameter(
            name='type',
            in_=openapi.IN_FORM,
            type=openapi.TYPE_STRING,
            required=False,
            description="Type of the file (optional)"
        ),
    ],
    "responses": {
        200: openapi.Response('Success', openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'id': openapi.Schema(type=openapi.TYPE_STRING, description='File ID (UUID)'),
            }
        )),
        400: "No file uploaded"
    }
}

ATTACH_MEDIA_SCHEMA = {
    "method": 'POST',
    "tags": ['Media'],
    "operation_description": "Прикрепляет медиафайлы к организации. Максимум 3 файла.",
    "request_body": openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'media_uuids': openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_UUID),
                description="Список UUID медиафайлов (от 1 до 3)."
            )
        },
        required=['media_uuids']
    ),
    "responses": {
        200: openapi.Response(
            description="Медиафайлы успешно прикреплены.",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'id': openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="ID созданной заявки."
                    ),
                }
            )
        ),
        400: openapi.Response(
            description="Некорректный запрос.",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'error': openapi.Schema(type=openapi.TYPE_STRING, description="Описание ошибки.")
                }
            )
        ),
        404: openapi.Response(
            description="Организация или медиафайл не найдены.",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'error': openapi.Schema(type=openapi.TYPE_STRING, description="Описание ошибки.")
                }
            )
        )
    }
}
