from drf_yasg import openapi

from core.serializers import CarMetricSerializer

MEDIA_UPLOAD_SCHEMA = {
    "tags": ["media"],
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
    "tags": ["leaks"],
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
    "tags": ["leaks"],
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
    "tags": ["leaks"],
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
    "tags": ["leaks"],
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

CAR_METRICS_SCHEMA = {
    "tags": ["Metrics"],
    "operation_description": "Получает метрики автомобиля (уровень топлива и/или скорость) из InfluxDB",
    "manual_parameters": [
        openapi.Parameter('periodFrom', openapi.IN_QUERY,
                          description="Начало периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
        openapi.Parameter('periodDue', openapi.IN_QUERY,
                          description="Конец периода (YYYY-MM-DD)",
                          type=openapi.TYPE_STRING),
        openapi.Parameter('car', openapi.IN_QUERY,
                          description="ID автомобиля",
                          type=openapi.TYPE_STRING,
                          required=True),
        openapi.Parameter('agg', openapi.IN_QUERY,
                          description="Окно агрегации (например, 1h, 1d)",
                          type=openapi.TYPE_STRING),
        openapi.Parameter('func', openapi.IN_QUERY,
                          description="Функция агрегации (mean, median, sum)",
                          type=openapi.TYPE_STRING,
                          enum=['mean', 'median', 'sum']),
        openapi.Parameter('metric', openapi.IN_QUERY,
                          description="Тип метрики (fuel_level, speed, both)",
                          type=openapi.TYPE_STRING,
                          enum=['fuel_level', 'speed', 'both'],
                          default='both')
    ],
    "responses": {
        200: openapi.Response(
            description="Успешный ответ",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'data': openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                'x': openapi.Schema(type=openapi.TYPE_STRING,
                                                    description="Дата в формате YYYY-MM-DD HH:MM:SS"),
                                'y': openapi.Schema(type=openapi.TYPE_NUMBER,
                                                    description="Значение метрики (уровень топлива или скорость)"),
                                'metric': openapi.Schema(type=openapi.TYPE_STRING, description="Тип метрики",
                                                         enum=['fuel_level', 'speed'])
                            }
                        ),
                        description="Массив метрик автомобиля"
                    )
                }
            )
        ),
        400: 'Неверный формат параметров',
        404: 'Автомобиль или данные не найдены',
        500: 'Внутренняя ошибка сервера'
    }
}
