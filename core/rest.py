from drf_yasg import openapi

MEDIA_UPLOAD_SCHEMA = {
    "tags": ["Media"],
    "operation_description": "Загружает медиафайл.",
    "manual_parameters": [
        openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True,
                          description="Файл для загрузки."),
        openapi.Parameter("type", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Тип файла."),
    ],
    "responses": {
        201: "Успешно",
        400: "Некорректный запрос",
    }
}

LEAKS_COUNT_SCHEMA = {
    "tags": ["Leaks"],
    "operation_description": "Получает количество сливов по автомобилям",
    "manual_parameters": [
        openapi.Parameter('periodFrom', openapi.IN_QUERY,
                          description="Начало периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
        openapi.Parameter('periodDue', openapi.IN_QUERY,
                          description="Конец периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
    ],
    "responses": {
        200: openapi.Response('Успешный ответ',
                              openapi.Schema(
                                  type=openapi.TYPE_ARRAY,
                                  items=openapi.Schema(
                                      type=openapi.TYPE_OBJECT,
                                      properties={
                                          'id': openapi.Schema(type=openapi.TYPE_STRING),
                                          'label': openapi.Schema(type=openapi.TYPE_STRING),
                                          'value': openapi.Schema(type=openapi.TYPE_INTEGER),
                                      }
                                  )
                              )
                              ),
        400: 'Неверный формат даты',
        500: 'Внутренняя ошибка сервера'
    }
}

LEAKS_VOLUME_SCHEMA = {
    "tags": ["Leaks"],
    "operation_description": "Получает объем сливов по автомобилям",
    "manual_parameters": [
        openapi.Parameter('periodFrom', openapi.IN_QUERY,
                          description="Начало периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
        openapi.Parameter('periodDue', openapi.IN_QUERY,
                          description="Конец периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
    ],
    "responses": {
        200: openapi.Response('Успешный ответ',
                              openapi.Schema(
                                  type=openapi.TYPE_ARRAY,
                                  items=openapi.Schema(
                                      type=openapi.TYPE_OBJECT,
                                      properties={
                                          'id': openapi.Schema(type=openapi.TYPE_STRING),
                                          'label': openapi.Schema(type=openapi.TYPE_STRING),
                                          'value': openapi.Schema(type=openapi.TYPE_INTEGER),
                                      }
                                  )
                              )
                              ),
        400: 'Неверный формат даты',
        500: 'Внутренняя ошибка сервера'
    }
}

DAILY_LEAKS_SUM_SCHEMA = {
    "tags": ["Leaks"],
    "operation_description": "Получает сумму сливов по дням за указанный период или за всё время",
    "manual_parameters": [
        openapi.Parameter('periodFrom', openapi.IN_QUERY,
                          description="Начало периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
        openapi.Parameter('periodDue', openapi.IN_QUERY,
                          description="Конец периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
    ],
    "responses": {
        200: openapi.Response('Успешный ответ',
                              openapi.Schema(
                                  type=openapi.TYPE_ARRAY,
                                  items=openapi.Schema(
                                      type=openapi.TYPE_OBJECT,
                                      properties={
                                          'value': openapi.Schema(type=openapi.TYPE_INTEGER,
                                                                  description="Сумма сливов"),
                                          'day': openapi.Schema(type=openapi.TYPE_STRING,
                                                                description="Дата в формате YYYY-MM-DD")
                                      }
                                  )
                              )
                              ),
        400: 'Неверный формат даты',
        500: 'Внутренняя ошибка сервера'
    }
}

DAILY_LEAKS_COUNT_SCHEMA = {
    "tags": ["Leaks"],
    "operation_description": "Получает количество сливов по дням за указанный период или за всё время",
    "manual_parameters": [
        openapi.Parameter('periodFrom', openapi.IN_QUERY,
                          description="Начало периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
        openapi.Parameter('periodDue', openapi.IN_QUERY,
                          description="Конец периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
    ],
    "responses": {
        200: openapi.Response('Успешный ответ',
                              openapi.Schema(
                                  type=openapi.TYPE_ARRAY,
                                  items=openapi.Schema(
                                      type=openapi.TYPE_OBJECT,
                                      properties={
                                          'value': openapi.Schema(type=openapi.TYPE_INTEGER,
                                                                  description="Количество сливов"),
                                          'day': openapi.Schema(type=openapi.TYPE_STRING,
                                                                description="Дата в формате YYYY-MM-DD")
                                      }
                                  )
                              )
                              ),
        400: 'Неверный формат даты',
        500: 'Внутренняя ошибка сервера'
    }
}
