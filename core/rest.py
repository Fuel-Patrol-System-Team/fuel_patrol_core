from drf_yasg import openapi

from core.serializers import CarMetricSerializer, CarReportOutputSerializer, DataProviderSerializer, \
    CarActiveStatusSerializer

PROVIDER_DATA_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "provider_name": openapi.Schema(
            type=openapi.TYPE_STRING,
            description="Имя провайдера (например, 'glonasssoft')",
            example="glonasssoft"
        ),
        "start_date": openapi.Schema(
            type=openapi.TYPE_STRING,
            format=openapi.FORMAT_DATETIME,
            description="Начальная дата для запроса данных (ISO формат, необязательно)",
            example="2025-01-01T00:00:00Z"
        ),
        "end_date": openapi.Schema(
            type=openapi.TYPE_STRING,
            format=openapi.FORMAT_DATETIME,
            description="Конечная дата для запроса данных (ISO формат, необязательно)",
            example="2025-06-13T23:59:59Z"
        ),
        "is_save_bad_data": openapi.Schema(
            type=openapi.TYPE_BOOLEAN,
            description="Сохранять ли некорректные данные в CarBadData (по умолчанию False)",
            example=False,
            default=False
        )
    },
    required=["provider_name"]
)

CAR_LEAKS_SCHEMA = {
    'operation_description': (
        "Получение всех сливов по указанному автомобилю организации с опциональной "
        "фильтрацией по датам и объёму слива."
    ),
    'manual_parameters': [
        openapi.Parameter(
            name='car_id',
            in_=openapi.IN_QUERY,
            description="ID автомобиля (UUID)",
            type=openapi.TYPE_STRING,
            required=True
        ),
        openapi.Parameter(
            name='periodFrom',
            in_=openapi.IN_QUERY,
            description="Начальная дата и время в формате ISO 8601 (например, 2025-01-01T00:00:00)",
            type=openapi.TYPE_STRING,
            format=openapi.FORMAT_DATETIME,
            required=False
        ),
        openapi.Parameter(
            name='periodDue',
            in_=openapi.IN_QUERY,
            description="Конечная дата и время в формате ISO 8601 (например, 2025-12-31T23:59:59)",
            type=openapi.TYPE_STRING,
            format=openapi.FORMAT_DATETIME,
            required=False
        ),
        openapi.Parameter(
            name='volume_from',
            in_=openapi.IN_QUERY,
            description="Минимальный объём слива",
            type=openapi.TYPE_INTEGER,
            required=False
        ),
        openapi.Parameter(
            name='volume_to',
            in_=openapi.IN_QUERY,
            description="Максимальный объём слива",
            type=openapi.TYPE_INTEGER,
            required=False
        ),
    ],
    'responses': {
        200: CarReportOutputSerializer(many=True),
        400: "Bad Request: Неверные параметры запроса",
        404: "Car not found or not associated with organization"
    }
}
DATA_PROVIDER_CREATE_SCHEMA = {
    'operation_description': 'Создаёт нового провайдера данных.',
    'request_body': openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['name'],
        properties={
            'name': openapi.Schema(type=openapi.TYPE_STRING, description='Имя провайдера'),
            'metadata': openapi.Schema(type=openapi.TYPE_OBJECT, description='Метаданные провайдера (опционально)',
                                       nullable=True),
            'cars': openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(type=openapi.TYPE_STRING, format='uuid'),
                description='Список ID автомобилей (опционально)'
            ),
        }
    ),
    'responses': {
        201: DataProviderSerializer,
        400: openapi.Response(description='Неверные данные'),
        403: openapi.Response(description='Доступ запрещён'),
        404: openapi.Response(description='Автомобили не найдены или не принадлежат организации'),
    }
}

MEDIA_UPLOAD_SCHEMA = {
    "tags": ["media"],
    "operation_description": "Загружает медиафайл.",
    "manual_parameters": [
        openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True,
                          description="Файл для загрузки."),
        openapi.Parameter("type", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Тип файла.")
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

CAR_ACTIVE_STATUS_SCHEMA = {
    "tags": ["cars"],
    "operation_description": "Обновляет статус активности автомобиля по его ID.",
    "manual_parameters": [
        openapi.Parameter(
            name='car_id',
            in_=openapi.IN_QUERY,
            description="ID автомобиля (UUID)",
            type=openapi.TYPE_STRING,
            required=True
        ),
    ],
    "request_body": CarActiveStatusSerializer,
    "responses": {
        200: openapi.Response(
            description="Успешный ответ",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'message': openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Сообщение об успешном обновлении",
                        example="Статус активности автомобиля успешно обновлён"
                    )
                }
            )
        ),
        400: openapi.Response(description="Неверные параметры запроса"),
        404: openapi.Response(description="Автомобиль не найден или не принадлежит организации"),
    }
}
