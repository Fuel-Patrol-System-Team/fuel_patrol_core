from django.conf import settings
from django.urls import re_path, path
from django.http import HttpResponse
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework.authentication import BasicAuthentication, SessionAuthentication

from core.swagger_config.schema_generators import (
    PublicSchemaGenerator,
    PrivateSchemaGenerator,
    SpotlightSchemaGenerator,
)
from core.swagger_config.swagger_views import IsStaffOrBasicAuth


def _get_demo_token():
    token = getattr(settings, 'DEMO_ACCESS_TOKEN', '')
    if token:
        return f"**Demo-токен для тестирования** (только чтение):\n\n`{token}`"
    return "Demo-токен: установите `DEMO_ACCESS_TOKEN` в `.env`"


# ============================================================
# Публичная схема — только GET (для /api/v1/swagger/)
# ============================================================
public_schema_view = get_schema_view(
    openapi.Info(
        title="FuelPatrol API — Public",
        default_version="v1",
        description=(
            "Публичная документация API FuelPatrol.\n\n"
            "Доступны только операции чтения (GET).\n\n"
            "Для тестирования нажмите **Authorize** и введите `Bearer <token>`:\n\n"
            f"{_get_demo_token()}"
        ),
        contact=openapi.Contact(email="support@fuelpatrol.ru"),
    ),
    generator_class=PublicSchemaGenerator,
    public=True,
    permission_classes=[permissions.AllowAny],
)

# ============================================================
# Spotlight схема — полная с тегами (для /api/v1/docs/)
# ============================================================
spotlight_schema_view = get_schema_view(
    openapi.Info(
        title="FuelPatrol API",
        default_version="v1",
        description=(
            "Документация API системы учёта топлива FuelPatrol.\n\n"
            "## Авторизация\n\n"
            "Используйте demo-токен для работы с публичными эндпоинтами (метка ✅ Public).\n\n"
            f"{_get_demo_token()}\n\n"
            "## Разграничение доступа\n\n"
            "| Метка | Описание |\n"
            "|-------|----------|\n"
            "| ✅ Public | Доступен по demo-токену (только GET) |\n"
            "| 🔒 Private — Мутации | Требует полноценный JWT, demo-токен вернёт 403 |\n"
            "| 🔒 Private — Парсинг | Только для staff, запуск обработки данных |\n"
            "| 🔒 Private — Авторизация | Получение токенов и регистрация |\n"
            "| 🔒 Private — Staff | Служебные эндпоинты |\n\n"
            "## Базовый URL\n\n"
            "`/api/v1/fuel/`"
        ),
        contact=openapi.Contact(email="support@fuelpatrol.ru"),
    ),
    generator_class=SpotlightSchemaGenerator,
    public=True,
    permission_classes=[permissions.AllowAny],
)

# ============================================================
# Приватная схема — полная (для /api/v1/docs/private/)
# ============================================================
private_schema_view = get_schema_view(
    openapi.Info(
        title="FuelPatrol API — Full (Private)",
        default_version="v1",
        description=(
            "Полная документация API FuelPatrol.\n\n"
            "⚠️ Только для внутреннего использования.\n\n"
            "Включает: мутации, parsing, staff-эндпоинты.\n\n"
            "Доступ: залогиньтесь в `/admin` как staff, затем откройте эту страницу."
        ),
        contact=openapi.Contact(email="support@fuelpatrol.ru"),
    ),
    generator_class=PrivateSchemaGenerator,
    public=False,
    permission_classes=[IsStaffOrBasicAuth],
    authentication_classes=[BasicAuthentication, SessionAuthentication],
)


# ============================================================
# Spotlight — публичный UI (полная схема с тегами)
# ============================================================
def public_spotlight_view(request):
    demo_token = getattr(settings, 'DEMO_ACCESS_TOKEN', '')

    token_bar = ''
    if demo_token:
        token_bar = f"""
  <div id="token-bar">
    <span>🔑 Demo-токен для ✅ Public эндпоинтов:</span>
    <code id="token-val">{demo_token}</code>
    <button id="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('token-val').innerText);this.innerText='✓ Скопировано';setTimeout(()=>this.innerText='Копировать',2000)">
      Копировать
    </button>
    <span id="hint">Нажмите Authorize в Spotlight и введите: Bearer &lt;токен&gt;</span>
  </div>"""

    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
  <title>FuelPatrol API — Документация</title>
  <script src="https://unpkg.com/@stoplight/elements/web-components.min.js"></script>
  <link rel="stylesheet" href="https://unpkg.com/@stoplight/elements/styles.min.css">
  <style>
    html, body {{
      height: 100%;
      margin: 0;
      padding: 0;
    }}
    #token-bar {{
      background: #1a1a2e;
      color: #e0e0e0;
      padding: 8px 20px;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}
    #token-bar code {{
      background: #2d2d4e;
      padding: 2px 8px;
      border-radius: 4px;
      color: #7ec8e3;
      font-size: 12px;
      word-break: break-all;
      max-width: 400px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      display: inline-block;
    }}
    #token-bar span {{ opacity: 0.85; }}
    #hint {{ font-size: 11px; opacity: 0.55; }}
    #copy-btn {{
      background: #4a90d9;
      color: white;
      border: none;
      padding: 4px 12px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 12px;
      white-space: nowrap;
      flex-shrink: 0;
    }}
    #copy-btn:hover {{ background: #357abd; }}
    elements-api {{
      display: block;
      height: {'calc(100vh - 42px)' if demo_token else '100vh'};
    }}
  </style>
</head>
<body>
  {token_bar}
  <elements-api
    apiDescriptionUrl="/api/v1/docs/swagger.json"
    router="hash"
    layout="sidebar"
    tryItCredentialsPolicy="same-origin"
  />
</body>
</html>"""
    return HttpResponse(html, content_type="text/html")


# ============================================================
# URL patterns
# ============================================================
docs_urlpatterns = [

    # ---------- Публичный Spotlight (полная схема с тегами) ----------
    path(
        "api/v1/docs/",
        public_spotlight_view,
        name="public-spotlight",
    ),
    # JSON схема для Spotlight (SpotlightSchemaGenerator — полная с тегами)
    re_path(
        r"^api/v1/docs/swagger(?P<format>\.json|\.yaml)$",
        spotlight_schema_view.without_ui(cache_timeout=0),
        name="spotlight-schema-json",
    ),

    # ---------- Публичный Swagger UI (только GET) ----------
    re_path(
        r"^api/v1/swagger/swagger(?P<format>\.json|\.yaml)$",
        public_schema_view.without_ui(cache_timeout=0),
        name="public-swagger-schema-json",
    ),
    re_path(
        r"^api/v1/swagger/$",
        public_schema_view.with_ui("swagger", cache_timeout=0),
        name="public-swagger-ui",
    ),
    re_path(
        r"^api/v1/swagger/redoc/$",
        public_schema_view.with_ui("redoc", cache_timeout=0),
        name="public-redoc",
    ),

    # ---------- Приватный Swagger (только staff) ----------
    re_path(
        r"^api/v1/docs/private/swagger(?P<format>\.json|\.yaml)$",
        private_schema_view.without_ui(cache_timeout=0),
        name="private-schema-json",
    ),
    re_path(
        r"^api/v1/docs/private/$",
        private_schema_view.with_ui("swagger", cache_timeout=0),
        name="private-swagger-ui",
    ),
    re_path(
        r"^api/v1/docs/private/redoc/$",
        private_schema_view.with_ui("redoc", cache_timeout=0),
        name="private-redoc",
    ),
]