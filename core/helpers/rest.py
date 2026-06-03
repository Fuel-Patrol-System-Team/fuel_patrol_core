from drf_yasg import openapi

from core.models import CarBadData
from core.serializers import CarReportOutputSerializer, DataProviderSerializer, \
    CarActiveStatusSerializer, TelegramUserOutputSerializer

CAR_DATA_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "provider_name": openapi.Schema(
            type=openapi.TYPE_STRING,
            description="Имя провайдера (например, 'glonasssoft')",
            example="glonasssoft"
        ),
        "car_ids": openapi.Schema(
            type=openapi.TYPE_ARRAY,
            items=openapi.Schema(type=openapi.TYPE_STRING),
            description="Список ID машин для обработки. Можно не передавать, если parse_all=True",
            example=["car-uuid-1", "car-uuid-2"]
        ),
        "unit_ids": openapi.Schema(
            type=openapi.TYPE_ARRAY,
            items=openapi.Schema(type=openapi.TYPE_STRING),
            description="Список ID unit",
            example=["unit-uuid-1", "unit-uuid-2"]
        ),
        "parse_all": openapi.Schema(
            type=openapi.TYPE_BOOLEAN,
            description="Если True, обрабатывает все машины провайдера (car_ids игнорируются). По умолчанию False",
            example=False,
            default=False
        ),

        "start_date": openapi.Schema(
            type=openapi.TYPE_STRING,
            format=openapi.FORMAT_DATETIME,
            description="Начальная дата для запроса данных (ISO формат)",
            example="2025-01-01T00:00:00Z"
        ),
        "end_date": openapi.Schema(
            type=openapi.TYPE_STRING,
            format=openapi.FORMAT_DATETIME,
            description="Конечная дата для запроса данных (ISO формат)",
            example="2025-06-13T23:59:59Z"
        ),
        "is_save_bad_data": openapi.Schema(
            type=openapi.TYPE_BOOLEAN,
            description="Сохранять ли некорректные данные в CarBadData (по умолчанию False)",
            example=False,
            default=False
        )
    },
    required=["provider_name", "start_date", "end_date"]
)

BAD_DATA_DASHBOARD_SCHEMA = {
    "operation_summary": "Дашборд ошибок (CarBadData)",
    "operation_description": (
        "Возвращает статистику ошибок по уровням severity (warning/error/critical).\n\n"
        "**type=calendar** — по дням для тепловой карты\n"
        "**type=car** — по автомобилям\n"
        "**type=tag** — по тегам\n\n"
        "Фильтрация по category: `calculation`, `no_data`, `provider_error`, `sync`, "
        "`data_quality`, `auth`, `unknown`. Без category — все категории."
    ),
    "manual_parameters": [
        openapi.Parameter("type", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                          enum=["calendar", "car", "tag"], required=True),
        openapi.Parameter("periodFrom", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                          format=openapi.FORMAT_DATE, required=False),
        openapi.Parameter("periodDue", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                          format=openapi.FORMAT_DATE, required=False),
        openapi.Parameter("category", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                          enum=["calculation", "no_data", "provider_error",
                                "sync", "data_quality", "auth", "unknown"],
                          required=False),
        openapi.Parameter(
            "tags",
            openapi.IN_QUERY,
            type=openapi.TYPE_ARRAY,
            items=openapi.Items(
                type=openapi.TYPE_STRING,
                enum=[t[0] for t in CarBadData.Tag.choices],
            ),
            collection_format="multi",
            required=False,
            description="Фильтр по тегам (можно передать несколько)",
        ),
    ],
    "responses": {
        200: openapi.Response("OK"),
        400: openapi.Response("Ошибка валидации"),
    },
}

BAD_DATA_SCHEMA = {
    "operation_summary": "Список ошибок CarBadData",
    "manual_parameters": [
        openapi.Parameter("car_id", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
        openapi.Parameter("start_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                          format=openapi.FORMAT_DATE, required=False),
        openapi.Parameter("end_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                          format=openapi.FORMAT_DATE, required=False),
        openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
        openapi.Parameter(
            "severity", openapi.IN_QUERY,
            type=openapi.TYPE_ARRAY,
            items=openapi.Items(type=openapi.TYPE_STRING,
                                enum=[s[0] for s in CarBadData.Severity.choices]),
            collection_format="multi",
            required=False,
        ),
        openapi.Parameter(
            "tags", openapi.IN_QUERY,
            type=openapi.TYPE_ARRAY,
            items=openapi.Items(type=openapi.TYPE_STRING,
                                enum=[t[0] for t in CarBadData.Tag.choices]),
            collection_format="multi",
            required=False,
        ),
        openapi.Parameter(
            "category", openapi.IN_QUERY,
            type=openapi.TYPE_ARRAY,
            items=openapi.Items(type=openapi.TYPE_STRING,
                                enum=[c[0] for c in CarBadData.Category.choices]),
            collection_format="multi",
            required=False,
        ),
    ],
    "responses": {
        200: openapi.Response("OK"),
        400: openapi.Response("Ошибка валидации"),
        404: openapi.Response("Не найдено"),
    },
}

VEHICLE_SYNC_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "provider_name": openapi.Schema(
            type=openapi.TYPE_STRING,
            description="Имя провайдера (например, 'glonasssoft')",
            example="glonasssoft"
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

MILEAGE_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        'car_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID автомобиля'),
        'start_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME,
                                     description='Дата начала в ISO формате'),
        'end_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME,
                                   description='Дата конца в ISO формате'),
        'agg': openapi.Schema(type=openapi.TYPE_NUMBER, format=openapi.FORMAT_INT32, description="Агрегация в минутах")
    },
    required=['car_id', 'start_date', 'end_date']
)

MOTOHOURS_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        'car_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID автомобиля'),
        'start_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME,
                                     description='Дата начала в ISO формате'),
        'end_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME,
                                   description='Дата конца в ISO формате'),
        'agg': openapi.Schema(type=openapi.TYPE_NUMBER, format=openapi.FORMAT_INT32, description="Агрегация в минутах")
    },
    required=['car_id', 'start_date', 'end_date']
)

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

PARSING_STATS_SWITCH_SCHEMA = {
    "tags": ["cars"],
    "operation_description": "Переключает статус парсинга выбранного параметра автомобиля (mileage, fuel, motohours).",
    "request_body": openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=["car_id", "parameter"],
        properties={
            "car_id": openapi.Schema(
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                description="ID автомобиля (UUID)",
                example="550e8400-e29b-41d4-a716-446655440000",
            ),
            "parameter": openapi.Schema(
                type=openapi.TYPE_STRING,
                description="Параметр для переключения",
                enum=["mileage", "fuel", "motohours"],
                example="mileage",
            ),
        },
    ),
    "responses": {
        200: openapi.Response(
            description="Успешный ответ",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "message": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Сообщение об успешном обновлении",
                        example="Параметр успешно обновлён",
                    )
                },
            ),
        ),
        400: openapi.Response(description="Неверные параметры запроса"),
        404: openapi.Response(description="Автомобиль не найден"),
    },
}

PARSING_STATS_RPM_SCHEMA = {
    "tags": ["cars"],
    "operation_description": "Обновляет параметры RPM (rpm_idle) для выбранного автомобиля.",
    "request_body": openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=["car_id", "rpm_idle"],
        properties={
            "car_id": openapi.Schema(
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                description="ID автомобиля (UUID)",
                example="550e8400-e29b-41d4-a716-446655440000",
            ),
            "rpm_idle": openapi.Schema(
                type=openapi.TYPE_INTEGER,
                description="Обороты холостого хода (холостой ход)",
                minimum=0,
                example=800,
            ),
            
        },
    ),
    "responses": {
        200: openapi.Response(
            description="Успешный ответ",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "message": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Сообщение об успешном обновлении",
                        example="Параметры RPM успешно обновлены",
                    )
                },
            ),
        ),
        400: openapi.Response(description="Неверные параметры запроса (например, отрицательные значения RPM)"),
        404: openapi.Response(description="Автомобиль не найден"),
    },
}



CAR_SENSORS_GROUP_BY_PARTIAL_SCHEMA = openapi.Parameter(
    name="key",
    in_=openapi.IN_QUERY,
    description="Sensor key used to filter cars",
    type=openapi.TYPE_STRING,
    required=True,
)


PARSE_RAW_DATA_SCHEMA = {
    'request_body': openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['provider_name', 'start_date', 'end_date'],
        properties={
            'provider_name': openapi.Schema(
                type=openapi.TYPE_STRING,
                description='Название провайдера'
            ),
            'start_date': openapi.Schema(
                type=openapi.TYPE_STRING,
                format='date',
                description='Дата начала в формате YYYY-MM-DD'
            ),
            'end_date': openapi.Schema(
                type=openapi.TYPE_STRING,
                format='date',
                description='Дата окончания в формате YYYY-MM-DD'
            ),
            'is_raw_data': openapi.Schema(
                type=openapi.TYPE_BOOLEAN,
                default=True,
                description='True - данные без маппинга, False - с маппингом сенсоров'
            ),
            'parse_all': openapi.Schema(
                type=openapi.TYPE_BOOLEAN,
                default=False,
                description='True - парсинг всех активных машин (is_active=True)'
            ),
            'car_ids': openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format='uuid'
                ),
                description='Список UUID машин для парсинга (используется если parse_all=False)'
            )
        }
    ),
    'responses': {
        202: openapi.Response(
            description='Задача запущена',
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'status': openapi.Schema(type=openapi.TYPE_STRING),
                    'message': openapi.Schema(type=openapi.TYPE_STRING),
                    'task_id': openapi.Schema(type=openapi.TYPE_STRING),
                    'provider_name': openapi.Schema(type=openapi.TYPE_STRING),
                    'start_date': openapi.Schema(type=openapi.TYPE_STRING),
                    'end_date': openapi.Schema(type=openapi.TYPE_STRING),
                    'is_raw_data': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    'parse_all': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    'car_ids': openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_STRING)
                    ),
                    'mode': openapi.Schema(type=openapi.TYPE_STRING),
                    'car_count': openapi.Schema(type=openapi.TYPE_INTEGER),
                }
            )
        ),
        400: openapi.Response(
            description='Неверные параметры запроса',
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'error': openapi.Schema(type=openapi.TYPE_STRING),
                }
            )
        )
    }
}

CAR_SENSORS_RAW_DATA_SCHEMA = {
    'request_body': openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['car_id', 'start_date', 'end_date'],
        properties={
            'car_id': openapi.Schema(
                type=openapi.TYPE_STRING,
                format='uuid',
                description='ID машины (UUID)'
            ),
            'start_date': openapi.Schema(
                type=openapi.TYPE_STRING,
                format='date',
                description='Дата начала в формате YYYY-MM-DD'
            ),
            'end_date': openapi.Schema(
                type=openapi.TYPE_STRING,
                format='date',
                description='Дата окончания в формате YYYY-MM-DD'
            ),
            'mode': openapi.Schema(
                type=openapi.TYPE_STRING,
                enum=['mileage', 'fuel_charts', 'motohours'],
                default='mileage',
                description='Режим парсинга: mileage (пробег), fuel (топливо), motohours (моточасы)'
            )
        }
    ),
    'responses': {
        200: openapi.Response(
            description='Данные успешно получены',
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'success': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    'car_id': openapi.Schema(type=openapi.TYPE_STRING, format='uuid'),
                    'car_name': openapi.Schema(type=openapi.TYPE_STRING),
                    'mode': openapi.Schema(type=openapi.TYPE_STRING),
                    'mode_name': openapi.Schema(type=openapi.TYPE_STRING),
                    'start_date': openapi.Schema(type=openapi.TYPE_STRING),
                    'end_date': openapi.Schema(type=openapi.TYPE_STRING),
                    'result': openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_OBJECT)
                    ),
                    'statistics': openapi.Schema(type=openapi.TYPE_OBJECT),
                    'fields': openapi.Schema(type=openapi.TYPE_OBJECT)
                }
            )
        ),
        400: openapi.Response(
            description='Неверные параметры запроса',
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'success': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    'message': openapi.Schema(type=openapi.TYPE_STRING),
                    'errors': openapi.Schema(type=openapi.TYPE_OBJECT)
                }
            )
        )
    }
}

CAR_LEAKS_CHARTS_SCHEMA = {
    'request_body': openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['car_id', 'days', 'leak_id'],
        properties={
            'car_id': openapi.Schema(
                type=openapi.TYPE_STRING,
                format='uuid',
                description='ID машины (UUID)'
            ),
            'days': openapi.Schema(
                type=openapi.TYPE_INTEGER,
                description='Количество дней для выборки данных'
            ),
            'leak_id': openapi.Schema(
                type=openapi.TYPE_STRING,
                format='uuid',
                description=''
            )
        }
    ),
    'responses': {
        200: openapi.Response(
            description='Данные успешно получены',
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'data': openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_OBJECT),
                        description='Основные данные'
                    ),
                    'histo_data': openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_OBJECT),
                        description='Исторические данные'
                    ),
                }
            )
        ),
        400: openapi.Response(
            description='Неверные параметры запроса',
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'message': openapi.Schema(type=openapi.TYPE_STRING),
                    'errors': openapi.Schema(type=openapi.TYPE_OBJECT)
                }
            )
        )
    }
}

TELEGRAM_REGISTER_SCHEMA = {
    'operation_description': "Регистрация/обновление пользователя Telegram",
    'request_body': openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['chat_id', 'organization_id'],
        properties={
            'chat_id': openapi.Schema(type=openapi.TYPE_STRING, description='Telegram chat ID'),
            'username': openapi.Schema(type=openapi.TYPE_STRING, description='Telegram username (без @)', default=''),
            'first_name': openapi.Schema(type=openapi.TYPE_STRING, description='Имя', default=''),
            'last_name': openapi.Schema(type=openapi.TYPE_STRING, description='Фамилия', default=''),
            'organization_id': openapi.Schema(type=openapi.TYPE_STRING, format='uuid', description='UUID организации'),
        }
    ),
    'responses': {
        201: openapi.Response('Пользователь создан', TelegramUserOutputSerializer),
        200: openapi.Response('Пользователь обновлен', TelegramUserOutputSerializer),
        400: 'Bad Request',
        401: 'Unauthorized (неверный API-ключ)',
        404: 'Organization not found',
    }
}
