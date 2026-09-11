import logging
from drf_yasg.generators import OpenAPISchemaGenerator

logger = logging.getLogger(__name__)

# ============================================================
# Публичный whitelist
# ============================================================
PUBLIC_PATHS_WHITELIST = {
    "/organizations",
    "/organizations/{id}",
    "/org-users",
    "/org-users/{id}",
    "/cars",
    "/cars/{id}",
    "/cars/car-units",
    "/cars/bad-data",
    "/cars/bad-data/{id}",
    "/cars/bySensorGroup",
    "/cars/models",
    "/cars/models/{id}",
    "/cars/model-specs",
    "/cars/model-specs/{id}",
    "/car-consumptions",
    "/car-consumptions/{id}",
    "/car-reports",
    "/car-reports/{id}",
    "/car-fuel-reports",
    "/car-fuel-reports/{id}",
    "/car-reports-mileage",
    "/car-reports-mileage/{id}",
    "/drivers",
    "/drivers/{id}",
    "/dataprovider",
    "/dataprovider/{id}",
    "/leaks/count",
    "/leaks/volume",
    "/leaks/daily-sum",
    "/leaks/daily-count",
    "/leaks/history",
    "/report-queries",
    "/report-queries/{id}",
    "/sensors/keys",
    "/user-car-lists",
    "/user-car-lists/{id}",
    "/user/info",
    "/timezones",
    "/languages",
    "/alerts/subscription",
    "/dashboard/bad-data",
}

ALWAYS_PRIVATE_PATHS = {
    "/parsing/mileage",
    "/parsing/motohours",
    "/parsing/logs",
    "/parsing/logs/{id}",
    "/parsing/cars",
    "/parsing/terminal-messages",
    "/parsing/parse-terminal-messages",
    "/parsing/car-sensors-raw-data-charts",
    "/parsing-stats/switch",
    "/parsing-stats/rpm",
    "/staff/auto-data",
    "/token",
    "/token/refresh",
    "/client/token",
    "/client/token/refresh",
    "/register",
    "/register/telegram",
    "/car-active-status",
    "/charts/leaks",
    "/dataprovider/create",
}

PRIVATE_METHODS = {"put", "post", "delete", "patch"}

TAG_PUBLIC = "✅ Public"
TAG_PRIVATE = "🔒 Private"

PRIVATE_TAG_DESCRIPTIONS = {
    "parsing": "Эндпоинты запуска и управления парсингом данных. Требуют прав staff.",
    "auth": "Авторизация и регистрация пользователей.",
    "staff": "Служебные эндпоинты только для сотрудников.",
    "mutations": "Операции изменения данных (POST/PUT/PATCH/DELETE). Недоступны по demo-токену.",
}


def _normalize_path(path: str) -> str:
    return path.rstrip("/")


def _is_private_path(normalized: str) -> bool:
    return normalized in ALWAYS_PRIVATE_PATHS


def _is_public_path(normalized: str) -> bool:
    return normalized in PUBLIC_PATHS_WHITELIST


def _get_private_subtag(path: str) -> str:
    """Определяет подгруппу приватного эндпоинта для красивой группировки."""
    if "parsing" in path:
        return "🔒 Private — Парсинг"
    if "token" in path or "register" in path:
        return "🔒 Private — Авторизация"
    if "staff" in path:
        return "🔒 Private — Staff"
    return "🔒 Private — Мутации"


def _annotate_operation(operation: dict, tag: str, note: str = None):
    """Добавляет тег и описание к операции."""
    existing_tags = operation.get("tags", [])
    operation["tags"] = [tag]

    if note:
        existing_desc = operation.get("description", "")
        if existing_desc:
            operation["description"] = f"{existing_desc}\n\n> ⚠️ {note}"
        else:
            operation["description"] = f"> ⚠️ {note}"

    return operation


class PublicSchemaGenerator(OpenAPISchemaGenerator):
    """Только GET-эндпоинты из whitelist. Для /api/v1/swagger/"""

    def get_schema(self, request=None, public=False):
        schema = super().get_schema(request=request, public=public)

        if schema is None or 'paths' not in schema:
            return schema

        paths = schema['paths']
        paths_to_delete = []

        for path, path_item in list(paths.items()):
            normalized = _normalize_path(path)

            if normalized in ALWAYS_PRIVATE_PATHS:
                paths_to_delete.append(path)
                continue

            if normalized not in PUBLIC_PATHS_WHITELIST:
                paths_to_delete.append(path)
                continue

            for method in list(path_item.keys()):
                if method.lower() in PRIVATE_METHODS:
                    del path_item[method]

            remaining = [m for m in path_item.keys()
                         if m.lower() not in ('parameters', 'summary', 'description')]
            if not remaining:
                paths_to_delete.append(path)

        for path in paths_to_delete:
            del paths[path]

        logger.warning(
            f"[PublicSchemaGenerator] Публичных путей: {len(paths)}\n"
            + "\n".join(sorted(paths.keys()))
        )

        return schema


class PrivateSchemaGenerator(OpenAPISchemaGenerator):
    def get_schema(self, request=None, public=False):
        return super().get_schema(request=request, public=True)


class SpotlightSchemaGenerator(OpenAPISchemaGenerator):
    """
    Полная схема для публичного Spotlight.
    Все эндпоинты присутствуют, но приватные помечены тегом 🔒 Private
    с описанием что они недоступны по demo-токену.
    """

    def get_schema(self, request=None, public=False):
        schema = super().get_schema(request=request, public=public)

        if schema is None or 'paths' not in schema:
            return schema

        paths = schema['paths']

        for path, path_item in list(paths.items()):
            normalized = _normalize_path(path)
            is_private_path = _is_private_path(normalized)
            is_public_path = _is_public_path(normalized)

            for method, operation in list(path_item.items()):
                if method.lower() in ('parameters',):
                    continue
                if not isinstance(operation, dict):
                    continue

                method_lower = method.lower()
                is_mutating = method_lower in PRIVATE_METHODS

                if is_private_path:
                    subtag = _get_private_subtag(normalized)
                    _annotate_operation(
                        operation,
                        tag=subtag,
                        note="Этот эндпоинт недоступен по demo-токену. "
                             "Требует авторизации staff-пользователя."
                    )
                elif is_public_path and is_mutating:
                    _annotate_operation(
                        operation,
                        tag="🔒 Private — Мутации",
                        note="Операция изменения данных. "
                             "Demo-токен даёт только чтение (GET). "
                             "Для записи нужен полноценный JWT."
                    )
                else:
                    _annotate_operation(operation, tag=TAG_PUBLIC)

        schema['tags'] = [
            {
                "name": TAG_PUBLIC,
                "description": (
                    "Публичные эндпоинты. Доступны по demo-токену.\n\n"
                    "**Авторизация**: нажмите Authorize и введите `Bearer <DEMO_ACCESS_TOKEN>`."
                )
            },
            {
                "name": "🔒 Private — Парсинг",
                "description": (
                    "Запуск и управление парсингом данных от провайдеров.\n\n"
                    "Требуют полноценного JWT staff-пользователя."
                )
            },
            {
                "name": "🔒 Private — Мутации",
                "description": (
                    "Операции создания, изменения и удаления данных.\n\n"
                    "Demo-токен возвращает `403 Forbidden` на эти методы."
                )
            },
            {
                "name": "🔒 Private — Авторизация",
                "description": "Получение токенов и регистрация пользователей."
            },
            {
                "name": "🔒 Private — Staff",
                "description": "Служебные эндпоинты только для сотрудников."
            },
        ]

        return schema