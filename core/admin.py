import json
import os
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from import_export.admin import ImportExportMixin
from django_celery_beat.models import (
    ClockedSchedule, CrontabSchedule, IntervalSchedule, PeriodicTask, SolarSchedule
)
from django_celery_beat.admin import (
    ClockedScheduleAdmin as BaseClockedScheduleAdmin,
    CrontabScheduleAdmin as BaseCrontabScheduleAdmin,
    PeriodicTaskAdmin as BasePeriodicTaskAdmin,
)
from core.models import (
    Organization, OrgUser, Car, CarReport, CarConsumption, Driver,
    ReportQuery, DataProvider, CarBadData, Language,
    SensorsKey, SensorsValues, SensorsKeyLocalization, ReportQueryDetails,
    UnitService, CarUnit, UserCarList, CarPrimary, CarMileageReport,
    TelegramUser, CarFuelReport, CoreNotification, ParsingCarStats, ComputedData, AlertSubscription, Alert,
    APICalculationLog, CarMotohoursReport,
)
from core.helpers.widgets import UnfoldExportForm, UnfoldImportForm, UnfoldPeriodicTaskForm
from django.utils import timezone

admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)


# ─────────────────────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────────────────────
def _bool_icon(value, true_label="Да", false_label="Нет"):
    if value:
        return format_html('<span style="color:#22c55e;font-weight:600;">● {}</span>', true_label)
    return format_html('<span style="color:#94a3b8;">○ {}</span>', false_label)


def _link(url, label):
    return mark_safe(f'<a href="{url}">{label}</a>')


def _format_number(value, suffix=""):
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f} {suffix}".replace(",", " ")
    except (ValueError, TypeError):
        return str(value)


def _json_preview(data, max_length=100):
    if data:
        import json
        s = json.dumps(data, ensure_ascii=False)
        return s[:max_length] + '…' if len(s) > max_length else s
    return "—"


def _json_full(data):
    if data:
        import json
        s = json.dumps(data, ensure_ascii=False, indent=2)
        return format_html(
            '<pre style="font-family:monospace;font-size:12px;background:#000;color:#0f0;'
            'padding:15px;border-radius:3px;max-height:500px;overflow-y:auto;'
            'line-height:1.3;white-space:pre;">{}</pre>', s
        )
    return "Нет данных"


# ─────────────────────────────────────────────────────────────
# Инлайны
# ─────────────────────────────────────────────────────────────
class CarReportInline(TabularInline):
    model = CarReport
    extra = 0
    fields = ('datetime', 'datetime_end', 'speed', 'volume', 'status', 'picked_by', 'ai_response_status')
    readonly_fields = ('datetime', 'datetime_end', 'speed', 'volume', 'status', 'picked_by', 'ai_response_status')
    verbose_name = "Отчёт об утечке"
    verbose_name_plural = "Отчёты об утечках"
    can_delete = False
    max_num = 20
    classes = ('collapse',)
    show_change_link = True


class TelegramUserInline(TabularInline):
    model = TelegramUser
    extra = 0
    readonly_fields = ('chat_id', 'username', 'first_name', 'last_name', 'created_at', 'is_active')
    fields = ('chat_id', 'username', 'first_name', 'last_name', 'is_active', 'created_at')
    verbose_name = "Telegram-аккаунт"
    verbose_name_plural = "Telegram-аккаунт"
    can_delete = True
    max_num = 1


class CarFuelReportInline(TabularInline):
    model = CarFuelReport
    extra = 0
    fields = ('start_moment', 'end_moment', 'fuel_start', 'fuel_end', 'fuel_filled')
    readonly_fields = ('start_moment', 'end_moment', 'fuel_start', 'fuel_end', 'fuel_filled')
    verbose_name = "Отчёт по топливу"
    verbose_name_plural = "Отчёты по топливу"
    can_delete = False
    show_change_link = True
    classes = ('collapse',)


class SensorsValuesInline(TabularInline):
    model = SensorsValues
    extra = 0
    fields = ('key', 'value', 'is_system_pick', 'is_active', 'multi', 'multi_type', 'grades', 'metadata')
    verbose_name = "Значение датчика"
    verbose_name_plural = "Значения датчиков"
    can_delete = True
    classes = ('collapse',)


class SensorsKeyLocalizationInline(TabularInline):
    model = SensorsKeyLocalization
    extra = 0
    fields = ('language', 'localization')
    verbose_name = "Локализация"
    verbose_name_plural = "Локализации"
    can_delete = True


class CarConsumptionInline(TabularInline):
    model = CarConsumption
    extra = 0
    fields = ('winter_volume', 'summer_volume', 'valid_period', 'max_fuel', 'speed_etalon', 'json_data')
    verbose_name = "Расход топлива"
    verbose_name_plural = "Расходы топлива"
    can_delete = True
    classes = ('collapse',)


class DriverCarInline(TabularInline):
    model = Driver.car_id.through
    extra = 0
    verbose_name = "Автомобиль"
    verbose_name_plural = "Автомобили водителя"
    can_delete = True
    fields = ('car',)
    autocomplete_fields = ['car']


class ReportQueryInline(TabularInline):
    model = ReportQuery
    extra = 0
    fields = ('status', 'report_type', 'created_at')
    readonly_fields = ('status', 'report_type', 'created_at')
    verbose_name = "Запрос отчёта"
    verbose_name_plural = "Запросы отчётов"
    can_delete = False
    classes = ('collapse',)
    show_change_link = True


class CarBadDataInline(admin.TabularInline):
    model = CarBadData
    extra = 0
    fields = ('datetime', 'severity', 'category', 'tags_list', 'reason_short')
    readonly_fields = ('datetime', 'severity', 'category', 'tags_list', 'reason_short')
    can_delete = False
    show_change_link = True
    max_num = 5

    def tags_list(self, obj):
        if obj.tags:
            return ", ".join(obj.tags)
        return "—"

    tags_list.short_description = "Теги"

    def reason_short(self, obj):
        return obj.reason[:100] + '…' if len(obj.reason) > 100 else obj.reason

    reason_short.short_description = "Причина"


class OrgUserInline(TabularInline):
    model = OrgUser
    extra = 0
    fields = ('username', 'email', 'is_active', 'active_language', 'last_login')
    readonly_fields = ('username', 'email', 'is_active', 'active_language', 'last_login')
    verbose_name = "Пользователь"
    verbose_name_plural = "Пользователи организации"
    can_delete = False
    show_change_link = True


class CarInline(TabularInline):
    model = DataProvider.cars.through
    extra = 0
    verbose_name = "Автомобиль"
    verbose_name_plural = "Автомобили поставщика"
    can_delete = True
    fields = ('car',)
    autocomplete_fields = ['car']


class CarMileageReportInline(admin.TabularInline):
    model = CarMileageReport
    extra = 0
    fields = ('datetime', 'mileage_start', 'mileage_end', 'travel', 'fraud', 'ign_miss', 'travel_fraud_jumps',
              'ai_response_status')
    readonly_fields = ('datetime', 'mileage_start', 'mileage_end', 'travel', 'fraud', 'ign_miss', 'travel_fraud_jumps',
                       'ai_response_status')
    verbose_name = "Отчет по пробегу"
    verbose_name_plural = "Отчеты по пробегу"
    can_delete = False
    show_change_link = True
    classes = ('collapse',)


class CarMotohoursReportInline(admin.TabularInline):
    model = CarMotohoursReport
    extra = 0
    fields = (
        'datetime', 'motohours_start', 'motohours_end', 'motohours',
        'motohours_fraud', 'motohours_fraud_by_sensor', 'motohours_idle',
        'motohours_active', 'rpm_same_cases', 'rpm_same_cases_time',
        'unefficient_cases', 'unefficient_time', 'sensor', 'sensor_check',
        'ai_response_status'
    )
    readonly_fields = (
        'datetime', 'motohours_start', 'motohours_end', 'motohours',
        'motohours_fraud', 'motohours_fraud_by_sensor', 'motohours_idle',
        'motohours_active', 'rpm_same_cases', 'rpm_same_cases_time',
        'unefficient_cases', 'unefficient_time', 'sensor', 'sensor_check',
        'ai_response_status'
    )
    verbose_name = "Отчет по моточасам"
    verbose_name_plural = "Отчеты по моточасам"
    can_delete = False
    show_change_link = True
    classes = ('collapse',)


class ReportQueryDetailsInline(TabularInline):
    model = ReportQueryDetails
    extra = 0
    fields = (
        'start_time', 'end_time', 'time_proceed_display',
        'cars_proceed', 'cars_skipped', 'traceback_preview', 'result_preview'
    )
    readonly_fields = (
        'start_time', 'end_time', 'time_proceed_display',
        'cars_proceed', 'cars_skipped', 'traceback_preview', 'result_preview'
    )
    verbose_name = "Детали выполнения"
    verbose_name_plural = "Детали выполнения"
    can_delete = False
    classes = ('collapse',)

    def time_proceed_display(self, obj):
        return str(obj.time_proceed) if obj.time_proceed else "—"

    time_proceed_display.short_description = "Время"

    def traceback_preview(self, obj):
        if obj.traceback:
            return _json_full(obj.traceback)
        return "—"

    traceback_preview.short_description = "Ошибка"

    def result_preview(self, obj):
        if obj.result:
            return _json_preview(obj.result, 200)
        return "—"

    result_preview.short_description = "Результат"


class CoreNotificationInline(TabularInline):
    model = CoreNotification
    extra = 0
    fields = ('type', 'message', 'url', 'read_at', 'created_at')
    readonly_fields = ('type', 'message', 'url', 'read_at', 'created_at')
    verbose_name = "Уведомление"
    verbose_name_plural = "Уведомления пользователя"
    can_delete = False
    show_change_link = True
    classes = ('collapse',)


class ParsingCarStatsInline(TabularInline):
    model = ParsingCarStats
    extra = 0
    fields = (
        'norms_last_processed', 'fuel_last_processed', 'leaks_last_processed',
        'primary_last_processed', 'mileage_last_processed', 'computed_last_processed',
        'motohours_last_processed', 'preffered_period_days', 'rpm_idle',
        'is_parse_mileage', 'is_parse_motohours', 'is_parse_fuel',
    )
    verbose_name = "Статистика парсинга"
    verbose_name_plural = "Статистика парсинга"
    can_delete = False
    classes = ('collapse',)


# ─────────────────────────────────────────────────────────────
# Organization
# ─────────────────────────────────────────────────────────────
@admin.register(Organization)
class OrganizationAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('name', 'users_count_display', 'providers_count_display', 'has_bot_display', 'chat_id')
    search_fields = ('name', 'chat_id')
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    save_on_top = True
    list_per_page = 25
    inlines = [OrgUserInline]
    actions = ['export_selected']
    fieldsets = (
        ('Основное', {
            'fields': ('name',)
        }),
        ('Telegram-уведомления', {
            'fields': ('bot_token', 'chat_id', 'bot_username'),
            'classes': ('collapse',),
            'description': 'Токен бота и chat_id для отправки уведомлений.',
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            users_count=Count('users', distinct=True),
            providers_count=Count('organization', distinct=True),
        )

    def users_count_display(self, obj):
        count = getattr(obj, 'users_count', 0)
        url = reverse('admin:core_orguser_changelist') + f'?org__id__exact={obj.id}'
        return _link(url, f'{count} польз.') if count else '0'

    users_count_display.short_description = "Пользователи"
    users_count_display.admin_order_field = 'users_count'

    def providers_count_display(self, obj):
        count = getattr(obj, 'providers_count', 0)
        url = reverse('admin:core_dataprovider_changelist') + f'?org_id__id__exact={obj.id}'
        return _link(url, f'{count} пост.') if count else '0'

    providers_count_display.short_description = "Поставщики"
    providers_count_display.admin_order_field = 'providers_count'

    def has_bot_display(self, obj):
        return _bool_icon(bool(obj.bot_token), "Есть токен", "Нет токена")

    has_bot_display.short_description = "Telegram-бот"


@admin.register(TelegramUser)
class TelegramUserAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'chat_id',
        'username',
        'user_display',
        'created_at',
        'is_active',
    )
    list_filter = ('is_active', 'created_at', 'user__org')
    search_fields = ('chat_id', 'username', 'first_name', 'last_name', 'user__username')
    ordering = ('-created_at',)
    list_per_page = 25
    date_hierarchy = 'created_at'
    actions = ['export_selected', 'activate_users', 'deactivate_users']

    fieldsets = (
        ('Привязка', {
            'fields': ('user', 'chat_id')
        }),
        ('Данные Telegram', {
            'fields': ('username', 'first_name', 'last_name')
        }),
        ('Статус', {
            'fields': ('is_active', 'created_at')
        }),
    )
    readonly_fields = ('created_at',)

    def user_display(self, obj):
        if obj.user:
            url = reverse("admin:core_orguser_change", args=[obj.user.id])
            return _link(url, obj.user.username)
        return "—"

    user_display.short_description = "Пользователь"
    user_display.admin_order_field = 'user__username'

    @admin.action(description='✅ Активировать выбранных пользователей')
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'Активировано {updated} пользователей.')

    @admin.action(description='🚫 Деактивировать выбранных пользователей')
    def deactivate_users(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'Деактивировано {updated} пользователей.')


# ─────────────────────────────────────────────────────────────
# Language
# ─────────────────────────────────────────────────────────────
@admin.register(Language)
class LanguageAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('code', 'name', 'description', 'users_count_display')
    search_fields = ('code', 'name')
    ordering = ('code',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 25
    actions = ['export_selected']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(users_count=Count('orguser', distinct=True))

    def users_count_display(self, obj):
        count = getattr(obj, 'users_count', 0)
        return count if count else '—'

    users_count_display.short_description = "Пользователей"
    users_count_display.admin_order_field = 'users_count'


# ─────────────────────────────────────────────────────────────
# OrgUser
# ─────────────────────────────────────────────────────────────
@admin.register(OrgUser)
class OrgUserAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'username', 'org_display', 'email',
        'active_language_display', 'is_active_display', 'is_staff_display',
        'notifications_count_display', 'last_login',
    )
    list_filter = ('is_active', 'is_staff', 'active_language', 'org')
    search_fields = ('username', 'email', 'org__name')
    ordering = ('username',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    date_hierarchy = 'date_joined'
    save_on_top = True
    actions = ['export_selected', 'activate_users', 'deactivate_users']
    inlines = [CoreNotificationInline, TelegramUserInline]
    fieldsets = (
        ('Учётная запись', {'fields': ('username', 'password', 'email')}),
        ('Персональные данные', {'fields': ('first_name', 'last_name'), 'classes': ('collapse',)}),
        ('Организация', {'fields': ('org', 'active_language', 'timezone')}),
        ('Права доступа', {'fields': ('is_active', 'is_staff', 'is_superuser'), 'classes': ('collapse',)}),
        ('Даты', {'fields': ('last_login', 'date_joined'), 'classes': ('collapse',)}),
    )
    readonly_fields = ('last_login', 'date_joined')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            notifications_count=Count('core_notifications', distinct=True),
        )

    def org_display(self, obj):
        if obj.org:
            url = reverse("admin:core_organization_change", args=[obj.org.id])
            return _link(url, obj.org.name)
        return "—"

    org_display.short_description = "Организация"
    org_display.admin_order_field = 'org__name'

    def active_language_display(self, obj):
        return obj.active_language.name if obj.active_language else "—"

    active_language_display.short_description = "Язык"

    def is_active_display(self, obj):
        return _bool_icon(obj.is_active, "Активен", "Отключён")

    is_active_display.short_description = "Активен"
    is_active_display.admin_order_field = 'is_active'

    def is_staff_display(self, obj):
        return _bool_icon(obj.is_staff, "Стафф", "")

    is_staff_display.short_description = "Стафф"

    def notifications_count_display(self, obj):
        count = getattr(obj, 'notifications_count', 0)
        if count:
            url = reverse('admin:core_corenotification_changelist') + f'?target__id__exact={obj.id}'
            return _link(url, f'{count} увед.')
        return '0'

    notifications_count_display.short_description = "Уведомления"
    notifications_count_display.admin_order_field = 'notifications_count'

    @admin.action(description="✅ Активировать пользователей")
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Активировано {updated} пользователей.")

    @admin.action(description="🚫 Деактивировать пользователей")
    def deactivate_users(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Деактивировано {updated} пользователей.")


# ─────────────────────────────────────────────────────────────
# CoreNotification
# ─────────────────────────────────────────────────────────────
@admin.register(CoreNotification)
class CoreNotificationAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'target_display', 'type', 'message_short',
        'is_read_display', 'created_at', 'url_display',
    )
    list_filter = ('type', 'created_at', 'read_at')
    search_fields = ('target__username', 'type', 'message')
    ordering = ('-created_at',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 40
    date_hierarchy = 'created_at'
    show_full_result_count = False
    actions = ['export_selected', 'mark_as_read', 'mark_as_unread']
    fieldsets = (
        ('Основное', {'fields': ('target', 'type', 'message', 'url')}),
        ('Статус', {'fields': ('read_at', 'created_at')}),
    )
    readonly_fields = ('created_at',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('target')

    def target_display(self, obj):
        if obj.target:
            url = reverse("admin:core_orguser_change", args=[obj.target.id])
            return _link(url, obj.target.username)
        return "—"

    target_display.short_description = "Пользователь"
    target_display.admin_order_field = 'target__username'

    def message_short(self, obj):
        return obj.message[:80] + '…' if len(obj.message) > 80 else obj.message

    message_short.short_description = "Сообщение"

    def is_read_display(self, obj):
        return _bool_icon(bool(obj.read_at), "Прочитано", "Не прочитано")

    is_read_display.short_description = "Прочитано"
    is_read_display.admin_order_field = 'read_at'

    def url_display(self, obj):
        if obj.url:
            return mark_safe(
                f'<a href="{obj.url}" target="_blank">'
                f'{obj.url[:50]}{"…" if len(obj.url) > 50 else ""}</a>'
            )
        return "—"

    url_display.short_description = "Ссылка"

    @admin.action(description="✅ Отметить как прочитанные")
    def mark_as_read(self, request, queryset):

        updated = queryset.filter(read_at__isnull=True).update(read_at=timezone.now())
        self.message_user(request, f"Отмечено прочитанными: {updated}.")

    @admin.action(description="🔄 Отметить как непрочитанные")
    def mark_as_unread(self, request, queryset):
        updated = queryset.update(read_at=None)
        self.message_user(request, f"Отмечено непрочитанными: {updated}.")


# ─────────────────────────────────────────────────────────────
# Car
# ─────────────────────────────────────────────────────────────
@admin.register(Car)
class CarAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'id', 'name', 'id_in_provider_system', 'car_unit_display',
        'is_active_display', 'is_tarrified_display',
        'data_providers_display', 'last_processed_date',
    )
    list_filter = ('is_active', 'is_tarrified', 'engine_type', 'car_unit')
    search_fields = ('name', 'description', 'id_in_provider_system')
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    date_hierarchy = 'created_at'
    save_on_top = True
    inlines = [
        CarConsumptionInline, SensorsValuesInline, CarReportInline,
        DriverCarInline, CarFuelReportInline, ParsingCarStatsInline,
        CarMileageReportInline, CarMotohoursReportInline,
    ]
    actions = ['export_selected', 'activate_selected', 'deactivate_selected']
    fieldsets = (
        ('Основное', {'fields': ('name', 'description', 'id_in_provider_system', 'car_unit', 'list_id')}),
        ('Технические параметры', {'fields': ('engine_type', 'input', 'output', 'grades')}),
        ('Статус', {'fields': ('is_active', 'is_tarrified')}),
        ('Даты', {'fields': ('created_at', 'last_processed_date'), 'classes': ('collapse',)}),
    )
    readonly_fields = ('created_at',)

    def car_unit_display(self, obj):
        if obj.car_unit:
            url = reverse("admin:core_carunit_change", args=[obj.car_unit.id])
            return _link(url, obj.car_unit.name)
        return "—"

    car_unit_display.short_description = "Подразделение"
    car_unit_display.admin_order_field = 'car_unit__name'

    def is_active_display(self, obj):
        return _bool_icon(obj.is_active, "Активна", "Откл.")

    is_active_display.short_description = "Активна"
    is_active_display.admin_order_field = 'is_active'

    def is_tarrified_display(self, obj):
        return _bool_icon(obj.is_tarrified, "Тарир.", "Нет")

    is_tarrified_display.short_description = "Тарирована"
    is_tarrified_display.admin_order_field = 'is_tarrified'

    def data_providers_display(self, obj):
        providers = obj.data_providers.all()
        if not providers:
            return "—"
        links = [
            f'<a href="{reverse("admin:core_dataprovider_change", args=[p.id])}">{p.name}</a>'
            for p in providers[:3]
        ]
        return mark_safe(", ".join(links) + ("…" if len(providers) > 3 else ""))

    data_providers_display.short_description = "Поставщики"

    @admin.action(description='✅ Активировать выбранные машины')
    def activate_selected(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'Активировано {updated} машин(ы).', messages.SUCCESS)

    @admin.action(description='🚫 Деактивировать выбранные машины')
    def deactivate_selected(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'Деактивировано {updated} машин(ы).', messages.SUCCESS)


# ─────────────────────────────────────────────────────────────
# CarUnit
# ─────────────────────────────────────────────────────────────
class CarUnitCarInline(TabularInline):
    model = Car
    extra = 0
    fields = ('name', 'id_in_provider_system', 'is_active', 'is_tarrified', 'last_processed_date')
    readonly_fields = ('name', 'id_in_provider_system', 'is_active', 'is_tarrified', 'last_processed_date')
    verbose_name = "Машина"
    verbose_name_plural = "Машины подразделения"
    can_delete = False
    show_change_link = True


@admin.register(CarUnit)
class CarUnitAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('name', 'cars_count_display', 'active_cars_display')
    search_fields = ('name',)
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    fields = ('name',)
    actions = ['export_selected', 'merge_car_units', 'delete_empty_units']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            total_cars=Count('car', distinct=True),
            active_cars=Count('car', filter=Q(car__is_active=True), distinct=True),
        )

    def cars_count_display(self, obj):
        count = getattr(obj, 'total_cars', 0)
        if count:
            url = reverse('admin:core_car_changelist') + f'?car_unit__id__exact={obj.id}'
            return _link(url, f'{count} машин(ы)')
        return '0'

    cars_count_display.short_description = "Всего машин"
    cars_count_display.admin_order_field = 'total_cars'

    def active_cars_display(self, obj):
        count = getattr(obj, 'active_cars', 0)
        if count:
            url = reverse('admin:core_car_changelist') + f'?car_unit__id__exact={obj.id}&is_active__exact=1'
            return format_html('<a href="{}" style="color:#22c55e;">{} активных</a>', url, count)
        return format_html('<span style="color:#94a3b8;">0 активных</span>')

    active_cars_display.short_description = "Активных"
    active_cars_display.admin_order_field = 'active_cars'

    def get_inlines(self, request, obj=None):
        return [CarUnitCarInline] if obj else []

    @admin.action(description='🔀 Объединить выбранные подразделения')
    def merge_car_units(self, request, queryset):
        if queryset.count() < 2:
            self.message_user(request, 'Выберите минимум 2 подразделения.', messages.WARNING)
            return
        main_unit = queryset.order_by('name').first()
        other_units = queryset.exclude(id=main_unit.id)
        moved_count = sum(u.car_set.count() for u in other_units)
        for unit in other_units:
            unit.car_set.all().update(car_unit=main_unit)
        deleted_count = other_units.count()
        other_units.delete()
        self.message_user(
            request,
            f'Объединено в «{main_unit.name}»: удалено {deleted_count} подразделений, перемещено {moved_count} машин.',
            messages.SUCCESS,
        )

    @admin.action(description='🗑️ Удалить пустые подразделения')
    def delete_empty_units(self, request, queryset):
        deleted = [u.name for u in queryset if not u.car_set.exists()]
        queryset.filter(car__isnull=True).delete()
        if deleted:
            self.message_user(
                request,
                f'Удалено {len(deleted)}: {", ".join(deleted[:5])}{"…" if len(deleted) > 5 else ""}',
                messages.SUCCESS,
            )
        else:
            self.message_user(request, 'Пустых подразделений не найдено.', messages.INFO)


# ─────────────────────────────────────────────────────────────
# ParsingCarStats
# ─────────────────────────────────────────────────────────────
@admin.register(ParsingCarStats)
class ParsingCarStatsAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'car_display',
        'preffered_period_days',
        'is_parse_fuel_display',
        'is_parse_mileage_display',
        'is_parse_motohours_display',
        'fuel_last_processed',
        'mileage_last_processed',
        'leaks_last_processed',
        'norms_last_processed',
        'primary_last_processed',
        'computed_last_processed',
        'motohours_last_processed',
    )
    list_filter = ('is_parse_fuel', 'is_parse_mileage', 'is_parse_motohours')
    search_fields = ('car__name', 'car__id_in_provider_system')
    ordering = ('-id',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    show_full_result_count = False
    fieldsets = (
        ('Автомобиль', {'fields': ('car',)}),
        ('Настройки', {
            'fields': ('preffered_period_days', 'rpm_idle', 'is_parse_fuel', 'is_parse_mileage', 'is_parse_motohours'),
        }),
        ('Даты последней обработки', {
            'fields': (
                'fuel_last_processed', 'mileage_last_processed', 'leaks_last_processed',
                'norms_last_processed', 'primary_last_processed', 'computed_last_processed',
                'motohours_last_processed',
            ),
            'classes': ('collapse',),
        }),
    )
    actions = ['export_selected', 'reset_all_dates', 'enable_fuel_parsing', 'disable_fuel_parsing']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('car')

    def car_display(self, obj):
        if obj.car:
            url = reverse("admin:core_car_change", args=[obj.car.id])
            return _link(url, obj.car.name)
        return "—"

    car_display.short_description = "Автомобиль"
    car_display.admin_order_field = 'car__name'

    def is_parse_fuel_display(self, obj):
        return _bool_icon(obj.is_parse_fuel, "Топливо", "")

    is_parse_fuel_display.short_description = "Топливо"
    is_parse_fuel_display.admin_order_field = 'is_parse_fuel'

    def is_parse_mileage_display(self, obj):
        return _bool_icon(obj.is_parse_mileage, "Пробег", "")

    is_parse_mileage_display.short_description = "Пробег"
    is_parse_mileage_display.admin_order_field = 'is_parse_mileage'

    def is_parse_motohours_display(self, obj):
        return _bool_icon(obj.is_parse_motohours, "Моточ.", "")

    is_parse_motohours_display.short_description = "Моточ."
    is_parse_motohours_display.admin_order_field = 'is_parse_motohours'

    @admin.action(description='🔄 Сбросить все даты обработки')
    def reset_all_dates(self, request, queryset):
        updated = queryset.update(
            fuel_last_processed=None,
            mileage_last_processed=None,
            leaks_last_processed=None,
            norms_last_processed=None,
            primary_last_processed=None,
            computed_last_processed=None,
            motohours_last_processed=None,
        )
        self.message_user(request, f'Даты сброшены для {updated} записей.', messages.SUCCESS)

    @admin.action(description='✅ Включить парсинг топлива')
    def enable_fuel_parsing(self, request, queryset):
        updated = queryset.update(is_parse_fuel=True)
        self.message_user(request, f'Парсинг топлива включён для {updated} машин.', messages.SUCCESS)

    @admin.action(description='🚫 Отключить парсинг топлива')
    def disable_fuel_parsing(self, request, queryset):
        updated = queryset.update(is_parse_fuel=False)
        self.message_user(request, f'Парсинг топлива отключён для {updated} машин.', messages.SUCCESS)


# ─────────────────────────────────────────────────────────────
# ComputedData
# ─────────────────────────────────────────────────────────────
@admin.register(ComputedData)
class ComputedDataAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'id', 'auto_display', 'timestamp',
        'pos_s', 'spent_fuel', 'spent_fuel_boundary', 'z_values',
        'rpm_mean', 'fpm', 'dtime',
        'fuel_first', 'fuel_last', 'es', 'no_sat_data', 'count',
        'norma_rasx_per_travel'
    )
    list_filter = ('auto', 'timestamp')
    search_fields = ('auto__name', 'auto__id_in_provider_system')
    ordering = ('-timestamp',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 50
    date_hierarchy = 'timestamp'
    show_full_result_count = False
    actions = ['export_selected', 'delete_selected_records']
    fieldsets = (
        ('Автомобиль', {'fields': ('auto', 'timestamp')}),
        ('Показатели движения', {'fields': ('pos_s', 'dtime', 'rpm_mean', 'ign_spread')}),
        ('Топливо', {'fields': ('spent_fuel', 'spent_fuel_boundary', 'fpm', 'fuel_first', 'fuel_last')}),
        ('Прочее', {'fields': ('z_values', 'es', 'no_sat_data', 'count', 'norma_rasx_per_travel')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('auto')

    def auto_display(self, obj):
        if obj.auto:
            url = reverse("admin:core_car_change", args=[obj.auto.id])
            return _link(url, obj.auto.name)
        return "—"

    auto_display.short_description = "Автомобиль"
    auto_display.admin_order_field = 'auto__name'

    @admin.action(description='🗑️ Удалить выбранные записи')
    def delete_selected_records(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f'Удалено {count} записей.', messages.SUCCESS)


# ─────────────────────────────────────────────────────────────
# CarReport (Leaks)
# ─────────────────────────────────────────────────────────────
@admin.register(CarReport)
class CarReportAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'car_display', 'datetime', 'datetime_end', 'speed', 'volume',
        'status_display', 'picked_by', 'ai_response_status_display'
    )
    list_filter = ('status', 'picked_by', 'ai_response_status', 'datetime')
    search_fields = ('car_id__name', 'car_id__id_in_provider_system')
    ordering = ('-datetime',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 40
    date_hierarchy = 'datetime'
    show_full_result_count = False
    actions = ['export_selected', 'mark_as_active', 'mark_as_inactive']
    fieldsets = (
        ('Основное', {'fields': ('car_id', 'datetime', 'datetime_end', 'created_at')}),
        ('Параметры', {'fields': ('speed', 'volume', 'status', 'picked_by')}),
        ('AI', {'fields': ('ai_response', 'ai_response_status'), 'classes': ('collapse',)}),
    )
    readonly_fields = ('created_at',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('car_id')

    def car_display(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return _link(url, obj.car_id.name)
        return "—"

    car_display.short_description = "Автомобиль"
    car_display.admin_order_field = 'car_id__name'

    def status_display(self, obj):
        return _bool_icon(obj.status, "Активен", "Неактивен")

    status_display.short_description = "Статус"
    status_display.admin_order_field = 'status'

    def ai_response_status_display(self, obj):
        if obj.ai_response_status:
            colors = {
                'ok': '#22c55e',
                'error': '#ef4444',
            }
            color = colors.get(obj.ai_response_status, '#94a3b8')
            return format_html('<span style="color:{};font-weight:600;">{}</span>', color, obj.ai_response_status)
        return "—"

    ai_response_status_display.short_description = "AI статус"
    ai_response_status_display.admin_order_field = 'ai_response_status'

    @admin.action(description="✅ Отметить как активные")
    def mark_as_active(self, request, queryset):
        queryset.update(status=True)
        self.message_user(request, "Отчёты отмечены как активные.")

    @admin.action(description="🚫 Отметить как неактивные")
    def mark_as_inactive(self, request, queryset):
        queryset.update(status=False)
        self.message_user(request, "Отчёты отмечены как неактивные.")


# ─────────────────────────────────────────────────────────────
# CarFuelReport
# ─────────────────────────────────────────────────────────────
@admin.register(CarFuelReport)
class CarFuelReportAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'car_info', 'start_moment', 'end_moment',
        'fuel_start_display', 'fuel_end_display', 'fuel_filled_display',
    )
    list_filter = ('start_moment', 'car_id__name')
    search_fields = ('car_id__name', 'car_id__id_in_provider_system')
    ordering = ('-start_moment',)
    list_per_page = 30
    date_hierarchy = 'start_moment'
    autocomplete_fields = ['car_id']
    fieldsets = (
        ('Основная информация', {'fields': ('car_id', ('start_moment', 'end_moment'))}),
        ('Показания топлива (л)', {'fields': ('fuel_start', 'fuel_end', 'fuel_filled')}),
    )

    def car_info(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return format_html('<a href="{}">{}</a>', url, obj.car_id.name)
        return "—"

    car_info.short_description = "Автомобиль"
    car_info.admin_order_field = 'car_id__name'

    def _format_fuel(self, value):
        if value is None:
            return format_html('<span style="color: #94a3b8;">—</span>')
        return f"{value:.2f} л"

    def fuel_start_display(self, obj):
        return self._format_fuel(obj.fuel_start)

    fuel_start_display.short_description = "Бак (нач)"

    def fuel_end_display(self, obj):
        return self._format_fuel(obj.fuel_end)

    fuel_end_display.short_description = "Бак (кон)"

    def fuel_filled_display(self, obj):
        if obj.fuel_filled and obj.fuel_filled > 0:
            return format_html(
                '<span style="color: #22c55e; font-weight: bold;">+ {}</span>',
                self._format_fuel(obj.fuel_filled),
            )
        return self._format_fuel(obj.fuel_filled)

    fuel_filled_display.short_description = "Заправлено"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('car_id')

    def get_readonly_fields(self, request, obj=None):
        return ('car_id', 'start_moment', 'end_moment') if obj else ()


# ─────────────────────────────────────────────────────────────
# CarPrimary
# ─────────────────────────────────────────────────────────────
@admin.register(CarPrimary)
class CarPrimaryAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('car_display', 'created_at', 'has_data_display', 'data_preview')
    list_filter = ('created_at',)
    search_fields = ('car__name', 'car__id_in_provider_system')
    ordering = ('-created_at',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    date_hierarchy = 'created_at'
    actions = ['export_selected', 'delete_empty_primary_data']
    fieldsets = (
        ('Основная информация', {'fields': ('id', 'car', 'created_at')}),
        ('JSON данные', {'fields': ('primary', 'data_preview_full'), 'classes': ('collapse',)}),
    )

    def car_display(self, obj):
        if obj.car:
            url = reverse("admin:core_car_change", args=[obj.car.id])
            return _link(url, obj.car.name)
        return "—"

    car_display.short_description = "Автомобиль"
    car_display.admin_order_field = 'car__name'

    def has_data_display(self, obj):
        return _bool_icon(bool(obj.primary), "Есть данные", "Нет данных")

    has_data_display.short_description = "Данные"
    has_data_display.boolean = True

    def data_preview(self, obj):
        return _json_preview(obj.primary, 100)

    data_preview.short_description = "Данные (предпросмотр)"

    def data_preview_full(self, obj):
        return _json_full(obj.primary)

    data_preview_full.short_description = "Данные (полный просмотр)"

    @admin.action(description='🗑️ Удалить записи без данных')
    def delete_empty_primary_data(self, request, queryset):
        empty = queryset.filter(primary__isnull=True)
        count = empty.count()
        if count:
            empty.delete()
            self.message_user(request, f'Удалено {count} записей без данных.', messages.SUCCESS)
        else:
            self.message_user(request, 'Записей без данных не найдено.', messages.INFO)


# ─────────────────────────────────────────────────────────────
# CarMileageReport
# ─────────────────────────────────────────────────────────────
@admin.register(CarMileageReport)
class CarMileageReportAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'car_info', 'datetime',
        'mileage_start', 'mileage_end', 'travel',
        'fraud_status', 'ign_miss', 'travel_fraud_jumps',
        'ai_response_status_display'
    )
    list_filter = ('datetime', 'car_id__name', 'ai_response_status')
    search_fields = ('car_id__name', 'car_id__id_in_provider_system')
    ordering = ('-datetime',)
    list_per_page = 30
    date_hierarchy = 'datetime'
    autocomplete_fields = ['car_id']
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected', 'clear_fraud_flags']
    fieldsets = (
        ('Основная информация', {'fields': ('car_id', 'datetime')}),
        ('Показания пробега', {
            'fields': ('mileage_start', 'mileage_end', 'travel'),
            'description': 'Пробег в километрах',
        }),
        ('Аномалии', {'fields': ('fraud', 'ign_miss', 'travel_fraud_jumps'), 'classes': ('collapse',)}),
        ('AI', {'fields': ('ai_response', 'ai_response_status'), 'classes': ('collapse',)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('car_id')

    def car_info(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            car_name = obj.car_id.name
            if obj.car_id.id_in_provider_system:
                car_name += f" (ID: {obj.car_id.id_in_provider_system})"
            return format_html('<a href="{}">{}</a>', url, car_name)
        return "—"

    car_info.short_description = "Автомобиль"
    car_info.admin_order_field = 'car_id__name'

    def fraud_status(self, obj):
        if obj.fraud is not None:
            if obj.fraud > 0:
                return format_html(
                    '<span style="color: #ef4444; font-weight: bold;">⚠️ {}</span>',
                    _format_number(obj.fraud, "км"),
                )
            return format_html('<span style="color: #22c55e;">✓ {}</span>', _format_number(obj.fraud, "км"))
        return format_html('<span style="color: #94a3b8;">—</span>')

    fraud_status.short_description = "Аномалия"
    fraud_status.admin_order_field = 'fraud'

    def ai_response_status_display(self, obj):
        if obj.ai_response_status:
            colors = {
                'ok': '#22c55e',
                'error': '#ef4444',
            }
            color = colors.get(obj.ai_response_status, '#94a3b8')
            return format_html('<span style="color:{};font-weight:600;">{}</span>', color, obj.ai_response_status)
        return "—"

    ai_response_status_display.short_description = "AI статус"
    ai_response_status_display.admin_order_field = 'ai_response_status'

    @admin.action(description='Сбросить флаги аномалий')
    def clear_fraud_flags(self, request, queryset):
        updated = queryset.update(fraud=None)
        self.message_user(request, f'Сброшены флаги аномалий для {updated} записей.', messages.SUCCESS)

    def get_readonly_fields(self, request, obj=None):
        return ('car_id', 'datetime') if obj else ()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# ─────────────────────────────────────────────────────────────
# CarMotohoursReport
# ─────────────────────────────────────────────────────────────
@admin.register(CarMotohoursReport)
class CarMotohoursReportAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'car_info', 'datetime',
        'motohours_start', 'motohours_end', 'motohours',
        'motohours_fraud_display', 'motohours_idle', 'motohours_active',
        'sensor', 'sensor_check', 'ai_response_status_display'
    )
    list_filter = ('datetime', 'car_id__name', 'sensor', 'sensor_check', 'ai_response_status')
    search_fields = ('car_id__name', 'car_id__id_in_provider_system')
    ordering = ('-datetime',)
    list_per_page = 30
    date_hierarchy = 'datetime'
    autocomplete_fields = ['car_id']
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']
    fieldsets = (
        ('Основная информация', {'fields': ('car_id', 'datetime')}),
        ('Показания моточасов', {
            'fields': ('motohours_start', 'motohours_end', 'motohours', 'motohours_idle', 'motohours_active'),
            'description': 'Моточасы',
        }),
        ('Аномалии', {
            'fields': (
                'motohours_fraud', 'motohours_fraud_by_sensor',
                'rpm_same_cases', 'rpm_same_cases_time',
                'unefficient_cases', 'unefficient_time'
            ),
            'classes': ('collapse',),
        }),
        ('Датчики', {'fields': ('sensor', 'sensor_check'), 'classes': ('collapse',)}),
        ('AI', {'fields': ('ai_response', 'ai_response_status'), 'classes': ('collapse',)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('car_id')

    def car_info(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return format_html('<a href="{}">{}</a>', url, obj.car_id.name)
        return "—"

    car_info.short_description = "Автомобиль"
    car_info.admin_order_field = 'car_id__name'

    def motohours_fraud_display(self, obj):
        if obj.motohours_fraud is not None and obj.motohours_fraud > 0:
            return format_html(
                '<span style="color: #ef4444; font-weight: bold;">⚠️ {}</span>',
                _format_number(obj.motohours_fraud, "ч"),
            )
        return _format_number(obj.motohours_fraud, "ч")

    motohours_fraud_display.short_description = "Накрутка"
    motohours_fraud_display.admin_order_field = 'motohours_fraud'

    def ai_response_status_display(self, obj):
        if obj.ai_response_status:
            colors = {
                'ok': '#22c55e',
                'error': '#ef4444',
            }
            color = colors.get(obj.ai_response_status, '#94a3b8')
            return format_html('<span style="color:{};font-weight:600;">{}</span>', color, obj.ai_response_status)
        return "—"

    ai_response_status_display.short_description = "AI статус"
    ai_response_status_display.admin_order_field = 'ai_response_status'

    def get_readonly_fields(self, request, obj=None):
        return ('car_id', 'datetime') if obj else ()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# ─────────────────────────────────────────────────────────────
# UserCarList
# ─────────────────────────────────────────────────────────────
class UserCarListCarInline(TabularInline):
    model = Car
    extra = 0
    fields = ('name', 'id_in_provider_system', 'car_unit', 'is_active', 'is_tarrified')
    readonly_fields = ('name', 'id_in_provider_system', 'car_unit', 'is_active', 'is_tarrified')
    verbose_name = "Машина"
    verbose_name_plural = "Машины в списке"
    can_delete = False
    show_change_link = True


@admin.register(UserCarList)
class UserCarListAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('name', 'user_display', 'cars_count_display')
    list_filter = ('user',)
    search_fields = ('name', 'user__username')
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    fields = ('name', 'user')
    actions = ['export_selected', 'delete_empty_lists']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(cars_count=Count('car', distinct=True))

    def user_display(self, obj):
        if obj.user:
            url = reverse("admin:core_orguser_change", args=[obj.user.id])
            return _link(url, obj.user.username)
        return "—"

    user_display.short_description = "Пользователь"
    user_display.admin_order_field = 'user__username'

    def cars_count_display(self, obj):
        count = getattr(obj, 'cars_count', 0)
        if count:
            url = reverse('admin:core_car_changelist') + f'?list_id__id__exact={obj.id}'
            return _link(url, f'{count} машин(ы)')
        return '0'

    cars_count_display.short_description = "Машин в списке"
    cars_count_display.admin_order_field = 'cars_count'

    def get_inlines(self, request, obj=None):
        return [UserCarListCarInline] if obj else []

    @admin.action(description='🗑️ Удалить пустые списки')
    def delete_empty_lists(self, request, queryset):
        deleted = []
        for lst in queryset:
            if not lst.car_set.exists():
                deleted.append(lst.name)
                lst.delete()
        if deleted:
            self.message_user(
                request,
                f'Удалено {len(deleted)}: {", ".join(deleted[:5])}{"…" if len(deleted) > 5 else ""}',
                messages.SUCCESS,
            )
        else:
            self.message_user(request, 'Пустых списков не найдено.', messages.INFO)


# ─────────────────────────────────────────────────────────────
# CarConsumption
# ─────────────────────────────────────────────────────────────
@admin.register(CarConsumption)
class CarConsumptionAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('car_display', 'winter_volume', 'summer_volume', 'speed_etalon', 'max_fuel', 'valid_period')
    list_filter = ('valid_period',)
    search_fields = ('car_id__name',)
    ordering = ('car_id__name', 'valid_period')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    date_hierarchy = 'valid_period'
    actions = ['export_selected']
    fieldsets = (
        ('Основное', {'fields': ('car_id', 'valid_period')}),
        ('Расход', {'fields': ('winter_volume', 'summer_volume')}),
        ('Дополнительно', {'fields': ('speed_etalon', 'max_fuel', 'json_data'), 'classes': ('collapse',)}),
    )

    def car_display(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return _link(url, obj.car_id.name)
        return "—"

    car_display.short_description = "Автомобиль"
    car_display.admin_order_field = 'car_id__name'


# ─────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────
@admin.register(Driver)
class DriverAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('fullname', 'phone', 'address', 'cars_display')
    search_fields = ('fullname', 'phone', 'address')
    ordering = ('fullname',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    inlines = [DriverCarInline]
    actions = ['export_selected']
    fieldsets = (
        ('Персональные данные', {'fields': ('fullname', 'phone', 'address')}),
    )

    def cars_display(self, obj):
        cars = obj.car_id.all()
        if not cars:
            return "—"
        links = [
            f'<a href="{reverse("admin:core_car_change", args=[c.id])}">{c.name}</a>'
            for c in cars[:3]
        ]
        return mark_safe(", ".join(links) + ("…" if len(cars) > 3 else ""))

    cars_display.short_description = "Автомобили"


# ─────────────────────────────────────────────────────────────
# ReportQuery
# ─────────────────────────────────────────────────────────────
@admin.register(ReportQuery)
class ReportQueryAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'provider_display', 'report_type', 'status_display', 'is_save_bad_data', 'created_at')
    list_filter = ('status', 'report_type', 'is_save_bad_data')
    search_fields = ('provider_id__name',)
    ordering = ('-created_at',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    date_hierarchy = 'created_at'
    inlines = [ReportQueryDetailsInline]
    actions = ['export_selected', 'mark_as_completed', 'mark_as_error']
    fieldsets = (
        ('Основное', {'fields': ('provider_id', 'report_type', 'is_save_bad_data')}),
        ('Статус', {'fields': ('status', 'created_at')}),
    )
    readonly_fields = ('created_at',)

    def provider_display(self, obj):
        if obj.provider_id:
            url = reverse("admin:core_dataprovider_change", args=[obj.provider_id.id])
            return _link(url, obj.provider_id.name)
        return "—"

    provider_display.short_description = "Поставщик"
    provider_display.admin_order_field = 'provider_id__name'

    def status_display(self, obj):
        status = obj.status or "—"
        colors = {
            'completed': '#22c55e',
            'error': '#ef4444',
            'pending': '#f59e0b',
            'running': '#3b82f6',
        }
        color = colors.get(status, '#94a3b8')
        return format_html('<span style="color:{};font-weight:600;">● {}</span>', color, status)

    status_display.short_description = "Статус"
    status_display.admin_order_field = 'status'

    @admin.action(description="✅ Отметить как завершённые")
    def mark_as_completed(self, request, queryset):
        queryset.update(status='completed')
        self.message_user(request, "Запросы отмечены как завершённые.")

    @admin.action(description="❌ Отметить как ошибку")
    def mark_as_error(self, request, queryset):
        queryset.update(status='error')
        self.message_user(request, "Запросы отмечены как ошибка.")


# ─────────────────────────────────────────────────────────────
# ReportQueryDetails
# ─────────────────────────────────────────────────────────────
@admin.register(ReportQueryDetails)
class ReportQueryDetailsAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'report_query_display', 'start_time', 'end_time',
        'time_proceed_display', 'cars_proceed', 'cars_skipped', 'has_traceback_display'
    )
    list_filter = ('start_time',)
    search_fields = ('report_query__id',)
    ordering = ('-start_time',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    date_hierarchy = 'start_time'
    show_full_result_count = False
    actions = ['export_selected', 'clear_traceback']
    fieldsets = (
        ('Запрос', {'fields': ('id', 'report_query')}),
        ('Время выполнения', {'fields': ('start_time', 'end_time', 'time_proceed')}),
        ('Результат', {'fields': ('cars_proceed', 'cars_skipped')}),
        ('Данные', {'fields': ('result_preview',), 'classes': ('collapse',)}),
        ('Ошибки', {'fields': ('traceback_preview',), 'classes': ('collapse',)}),
    )

    def report_query_display(self, obj):
        if obj.report_query:
            url = reverse("admin:core_reportquery_change", args=[obj.report_query.id])
            short_id = str(obj.report_query.id)[:8] + '…'
            return _link(url, short_id)
        return "—"

    report_query_display.short_description = "Запрос"

    def time_proceed_display(self, obj):
        return str(obj.time_proceed) if obj.time_proceed else "—"

    time_proceed_display.short_description = "Время"

    def has_traceback_display(self, obj):
        return _bool_icon(bool(obj.traceback), "Есть ошибка", "")

    has_traceback_display.short_description = "Ошибка"
    has_traceback_display.boolean = True

    def traceback_preview(self, obj):
        if obj.traceback:
            return _json_full(obj.traceback)
        return "Нет данных об ошибках"

    traceback_preview.short_description = "Детали ошибки"

    def result_preview(self, obj):
        if obj.result:
            return _json_full(obj.result)
        return "Нет данных о результате"

    result_preview.short_description = "Результат"

    @admin.action(description="🗑 Очистить traceback")
    def clear_traceback(self, request, queryset):
        updated = queryset.update(traceback=None)
        self.message_user(request, f'Traceback очищен для {updated} записей.', messages.SUCCESS)


# ─────────────────────────────────────────────────────────────
# CarBadData
# ─────────────────────────────────────────────────────────────
@admin.register(CarBadData)
class CarBadDataAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'car_link', 'datetime', 'severity_badge', 'category_badge',
        'tags_display', 'reason_short', 'has_report'
    )
    list_filter = (
        'severity', 'category', 'datetime',
        ('tags', admin.AllValuesFieldListFilter),
        ('report_query', admin.EmptyFieldListFilter),
    )
    search_fields = ('car_id__name', 'car_id__id_in_provider_system', 'reason', 'tags')
    ordering = ('-datetime',)
    readonly_fields = ('id', 'created_at_display')
    list_per_page = 40
    date_hierarchy = 'datetime'
    show_full_result_count = True
    list_select_related = ('car_id', 'report_query')
    actions = ['export_selected', 'set_critical', 'set_warning', 'set_info']
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm

    fieldsets = (
        ('Информация об ошибке', {
            'fields': ('car_id', 'datetime', 'severity', 'category', 'tags')
        }),
        ('Детали', {
            'fields': ('reason', 'description', 'report_query')
        }),
        ('Системная информация', {
            'fields': ('id', 'event_date', 'created_at_display'),
            'classes': ('collapse',)
        }),
    )

    def car_link(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return format_html(
                '<a href="{}" style="font-weight: 500;">{} <span style="color: #666;">({})</span></a>',
                url,
                obj.car_id.name,
                obj.car_id.id_in_provider_system or '—'
            )
        return "—"

    car_link.short_description = "Автомобиль"
    car_link.admin_order_field = 'car_id__name'

    def severity_badge(self, obj):
        colors = {
            'info': '#6c757d',
            'warning': '#ffc107',
            'error': '#dc3545',
            'critical': '#9b1d2c',
        }
        color = colors.get(obj.severity, '#6c757d')
        icons = {
            'info': 'ℹ️',
            'warning': '⚠️',
            'error': '❌',
            'critical': '🔥',
        }
        icon = icons.get(obj.severity, '📌')
        return format_html(
            '<span style="display: inline-block; padding: 3px 8px; background: {}20; '
            'color: {}; border-radius: 4px; font-size: 12px; font-weight: 500;">'
            '{} {}</span>',
            color, color, icon, obj.get_severity_display()
        )

    severity_badge.short_description = "Важность"
    severity_badge.admin_order_field = 'severity'

    def category_badge(self, obj):
        colors = {
            'no_data': '#17a2b8',
            'provider_error': '#fd7e14',
            'calculation': '#e83e8c',
            'sync': '#6f42c1',
            'data_quality': '#28a745',
            'auth': '#495057',
            'unknown': '#adb5bd',
        }
        color = colors.get(obj.category, '#adb5bd')
        return format_html(
            '<span style="padding: 3px 8px; background: {}20; color: {}; '
            'border-radius: 4px; font-size: 12px;">{}</span>',
            color, color, obj.get_category_display()
        )

    category_badge.short_description = "Категория"
    category_badge.admin_order_field = 'category'

    def tags_display(self, obj):
        if not obj.tags:
            return "—"
        tags_html = []
        tag_colors = {
            'mileage': '#007bff',
            'leaks': '#17a2b8',
            'fuel': '#28a745',
            'motohours': '#ffc107',
            'server': '#dc3545',
            'provider': '#fd7e14',
            'malfunction': '#e83e8c',
            'alert': '#ffc107',
            'fault': '#dc3545',
        }
        for tag in obj.tags[:5]:
            color = tag_colors.get(tag, '#6c757d')
            tag_display = dict(CarBadData.Tag.choices).get(tag, tag)
            tags_html.append(
                format_html(
                    '<span style="display: inline-block; margin: 2px; padding: 2px 6px; '
                    'background: {}20; color: {}; border-radius: 3px; font-size: 11px;">{}</span>',
                    color, color, tag_display
                )
            )
        if len(obj.tags) > 5:
            tags_html.append(format_html('<span>…+{}</span>', len(obj.tags) - 5))
        return mark_safe(' '.join(str(t) for t in tags_html))

    tags_display.short_description = "Теги"

    def reason_short(self, obj):
        if len(obj.reason) > 60:
            return obj.reason[:60] + '…'
        return obj.reason

    reason_short.short_description = "Причина"

    def has_report(self, obj):
        if obj.report_query:
            url = reverse("admin:core_reportquery_change", args=[obj.report_query.id])
            return format_html('<a href="{}">📄 Report #{}</a>', url, obj.report_query.id)
        return "—"

    has_report.short_description = "Report Query"

    def created_at_display(self, obj):
        return obj.datetime.strftime("%Y-%m-%d %H:%M:%S")

    created_at_display.short_description = "Время создания"

    @admin.action(description='🔴 Отметить как CRITICAL')
    def set_critical(self, request, queryset):
        updated = queryset.update(severity=CarBadData.Severity.CRITICAL)
        self.message_user(request, f'Отмечено как CRITICAL: {updated} записей.', messages.SUCCESS)

    @admin.action(description='🟡 Отметить как WARNING')
    def set_warning(self, request, queryset):
        updated = queryset.update(severity=CarBadData.Severity.WARNING)
        self.message_user(request, f'Отмечено как WARNING: {updated} записей.', messages.SUCCESS)

    @admin.action(description='🔵 Отметить как INFO')
    def set_info(self, request, queryset):
        updated = queryset.update(severity=CarBadData.Severity.INFO)
        self.message_user(request, f'Отмечено как INFO: {updated} записей.', messages.SUCCESS)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


# ─────────────────────────────────────────────────────────────
# DataProvider
# ─────────────────────────────────────────────────────────────
@admin.register(DataProvider)
class DataProviderAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'org_display', 'cars_count_display', 'queries_count_display')
    list_filter = ('org_id',)
    search_fields = ('name', 'cars__name')
    ordering = ('name',)
    inlines = [CarInline, ReportQueryInline]
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 25
    save_on_top = True
    actions = ['export_selected']
    fieldsets = (
        ('Основное', {'fields': ('name', 'org_id')}),
        ('Метаданные', {'fields': ('metadata',), 'classes': ('collapse',)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            cars_count=Count('cars', distinct=True),
            queries_count=Count('report_queries', distinct=True),
        )

    def org_display(self, obj):
        if obj.org_id:
            url = reverse("admin:core_organization_change", args=[obj.org_id.id])
            return _link(url, obj.org_id.name)
        return "—"

    org_display.short_description = "Организация"
    org_display.admin_order_field = 'org_id__name'

    def cars_count_display(self, obj):
        count = getattr(obj, 'cars_count', 0)
        if count:
            url = reverse('admin:core_car_changelist') + f'?data_providers__id__exact={obj.id}'
            return _link(url, f'{count} машин(ы)')
        return '0'

    cars_count_display.short_description = "Машин"
    cars_count_display.admin_order_field = 'cars_count'

    def queries_count_display(self, obj):
        count = getattr(obj, 'queries_count', 0)
        if count:
            url = reverse('admin:core_reportquery_changelist') + f'?provider_id__id__exact={obj.id}'
            return _link(url, f'{count} запросов')
        return '0'

    queries_count_display.short_description = "Запросы"
    queries_count_display.admin_order_field = 'queries_count'


# ─────────────────────────────────────────────────────────────
# Sensors
# ─────────────────────────────────────────────────────────────
@admin.register(SensorsKey)
class SensorsKeyAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('key', 'values_count_display', 'localizations_count_display')
    search_fields = ('key',)
    ordering = ('key',)
    inlines = [SensorsKeyLocalizationInline, SensorsValuesInline]
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 30
    actions = ['export_selected']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            values_count=Count('values', distinct=True),
            localizations_count=Count('locations', distinct=True),
        )

    def values_count_display(self, obj):
        return getattr(obj, 'values_count', 0)

    values_count_display.short_description = "Значений"
    values_count_display.admin_order_field = 'values_count'

    def localizations_count_display(self, obj):
        return getattr(obj, 'localizations_count', 0)

    localizations_count_display.short_description = "Локализаций"
    localizations_count_display.admin_order_field = 'localizations_count'


@admin.register(SensorsValues)
class SensorsValuesAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'car_display', 'key_display', 'value',
        'is_system_pick_display', 'is_active_display',
        'multi_type_display', 'created_at'
    )
    list_filter = ('key', 'is_active', 'is_system_pick', 'multi_type')
    search_fields = ('car_id__name', 'key__key', 'value')
    ordering = ('car_id__name', 'key__key')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 40
    show_full_result_count = False
    actions = ['export_selected']
    fieldsets = (
        ('Основное', {'fields': ('car_id', 'key', 'value')}),
        ('Настройки', {'fields': ('is_system_pick', 'is_active', 'multi', 'multi_type')}),
        ('Дополнительно', {'fields': ('grades', 'metadata', 'created_at'), 'classes': ('collapse',)}),
    )
    readonly_fields = ('created_at',)

    def car_display(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return _link(url, obj.car_id.name)
        return "—"

    car_display.short_description = "Автомобиль"

    def key_display(self, obj):
        if obj.key:
            url = reverse("admin:core_sensorskey_change", args=[obj.key.id])
            return _link(url, obj.key.key)
        return "—"

    key_display.short_description = "Ключ"

    def is_active_display(self, obj):
        return _bool_icon(obj.is_active, "Активен", "Откл.")

    is_active_display.short_description = "Активен"
    is_active_display.admin_order_field = 'is_active'

    def is_system_pick_display(self, obj):
        return _bool_icon(obj.is_system_pick, "Системный", "")

    is_system_pick_display.short_description = "Системный"
    is_system_pick_display.admin_order_field = 'is_system_pick'

    def multi_type_display(self, obj):
        if obj.multi_type:
            return obj.get_multi_type_display()
        return "—"

    multi_type_display.short_description = "Тип"


@admin.register(SensorsKeyLocalization)
class SensorsKeyLocalizationAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('key_display', 'language_display', 'localization')
    list_filter = ('language',)
    search_fields = ('key__key', 'language__name', 'localization')
    ordering = ('key__key', 'language__name')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    list_per_page = 40
    actions = ['export_selected']

    def key_display(self, obj):
        if obj.key:
            url = reverse("admin:core_sensorskey_change", args=[obj.key.id])
            return _link(url, obj.key.key)
        return "—"

    key_display.short_description = "Ключ"

    def language_display(self, obj):
        if obj.language:
            url = reverse("admin:core_language_change", args=[obj.language.id])
            return _link(url, obj.language.name)
        return "—"

    language_display.short_description = "Язык"


# ─────────────────────────────────────────────────────────────
# ALERTS
# ─────────────────────────────────────────────────────────────
@admin.register(AlertSubscription)
class AlertSubscriptionAdmin(ModelAdmin):
    list_display = [
        'user',
        'alert_types',
        'bad_data_min_severity',
        'min_leak_liters',
        'min_fraud_km',
        'notify_hour',
        'is_active',
        'updated_at',
    ]
    list_filter = ['is_active', 'bad_data_min_severity', 'notify_hour']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['updated_at']

    fieldsets = (
        ("Пользователь", {
            "fields": ("user", "is_active"),
        }),
        ("Типы уведомлений", {
            "fields": ("alert_types",),
        }),
        ("Настройки Bad Data", {
            "fields": ("bad_data_tags", "bad_data_min_severity"),
            "classes": ("collapse",),
        }),
        ("Настройки сливов", {
            "fields": ("min_leak_liters",),
            "classes": ("collapse",),
        }),
        ("Настройки накруток", {
            "fields": ("min_fraud_km",),
            "classes": ("collapse",),
        }),
        ("Расписание", {
            "fields": ("notify_hour",),
        }),
        ("Служебное", {
            "fields": ("updated_at",),
            "classes": ("collapse",),
        }),
    )


@admin.register(Alert)
class AlertAdmin(ModelAdmin):
    list_display = [
        'car',
        'organization',
        'alert_type',
        'event_datetime',
        'is_sent',
        'sent_at',
        'created_at',
    ]
    list_filter = [
        'alert_type',
        'is_sent',
        'organization',
        'event_datetime',
    ]
    search_fields = ['car__name', 'organization__name']
    date_hierarchy = 'event_datetime'

    fieldsets = (
        ("Основное", {
            "fields": ("organization", "car", "alert_type", "event_datetime"),
        }),
        ("Источник", {
            "fields": (
                "source_car_report",
                "source_mileage_report",
                "source_motohours_report",
                "source_bad_data",
            ),
            "classes": ("collapse",),
        }),
        ("Payload", {
            "fields": ("payload",),
            "classes": ("collapse",),
        }),
        ("Telegram", {
            "fields": ("is_sent", "sent_at"),
        }),
        ("Служебное", {
            "fields": ("created_at",),
            "classes": ("collapse",),
        }),
    )


# ─────────────────────────────────────────────────────────────
# LOGS
# ─────────────────────────────────────────────────────────────
@admin.register(APICalculationLog)
class APICalculationLogAdmin(ModelAdmin):
    list_display = (
        'created_at', 'view_name', 'user', 'car', 'status_display'
    )
    list_filter = ('view_name', 'status_code', 'created_at')
    search_fields = (
        'view_name', 'user__username', 'user__email', 'car__name', 'car__id_in_provider_system')
    readonly_fields = (
        'created_at', 'view_name', 'user', 'car',
        'status_display', 'request_content_display', 'response_content_display'
    )
    fieldsets = (
        ('Основная информация', {
            'fields': ('created_at', 'view_name', 'user', 'car', 'status_display'),
        }),
        ('Данные запроса и ответа', {
            'fields': ('request_content_display', 'response_content_display'),
        }),
    )

    def status_display(self, obj):
        status = obj.status_code
        if status and 200 <= status < 300:
            return format_html(f'<span style="color: #22c55e; font-weight: bold;">{status} OK</span>')
        elif status and status >= 400:
            return format_html(f'<span style="color: #ef4444; font-weight: bold;">{status} Error</span>')
        return format_html(f'<span style="color: #f59e0b;">{status}</span>')

    status_display.short_description = "Статус"

    def request_content_display(self, obj):
        return _json_full(obj.request_data)

    request_content_display.short_description = "Тело запроса (Request Data)"

    def response_content_display(self, obj):
        return _json_full(obj.response_data)

    response_content_display.short_description = "Тело ответа (Response Data)"


# ─────────────────────────────────────────────────────────────
# Celery Beat
# ─────────────────────────────────────────────────────────────
@admin.register(PeriodicTask)
class PeriodicTaskAdmin(BasePeriodicTaskAdmin, ModelAdmin):
    form = UnfoldPeriodicTaskForm
    list_display = ('name', 'task', 'enabled', 'last_run_at', 'total_run_count')
    list_filter = ('enabled', 'task')
    search_fields = ('name', 'task')
    ordering = ('-enabled', 'name')


@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(ModelAdmin):
    list_display = ('every', 'period')
    search_fields = ('every',)


@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin):
    list_display = ('minute', 'hour', 'day_of_month', 'month_of_year', 'day_of_week')
    search_fields = ('minute', 'hour')


@admin.register(SolarSchedule)
class SolarScheduleAdmin(ModelAdmin):
    list_display = ('event', 'latitude', 'longitude')
    search_fields = ('event',)


@admin.register(ClockedSchedule)
class ClockedScheduleAdmin(BaseClockedScheduleAdmin, ModelAdmin):
    list_display = ('clocked_time',)
    search_fields = ('clocked_time',)


# ─────────────────────────────────────────────────────────────
# UnitService
# ─────────────────────────────────────────────────────────────
@admin.register(UnitService)
class UnitServiceAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'name', 'service', 'status_display',
        'autostart_display', 'file_exists_display', 'actions_display',
    )
    list_filter = ('is_active', 'auto_start', 'restart_on_failure')
    search_fields = ('name', 'service', 'description')
    ordering = ('name',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = [
        'install_services', 'restart_services', 'stop_services', 'start_services',
        'reload_services', 'enable_autostart', 'disable_autostart', 'uninstall_services',
    ]
    readonly_fields = (
        'status_display', 'status_details_display', 'service_logs_display',
        'service_content_display', 'actions_block', 'file_info_display',
    )
    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'service', 'service_filename', 'description', 'is_active'),
        }),
        ('Настройки службы', {
            'fields': ('auto_start', 'restart_on_failure'),
            'classes': ('collapse',),
        }),
        ('Файл службы', {'fields': ('file_info_display', 'service_content_display')}),
        ('Статус службы', {'fields': ('status_display', 'status_details_display')}),
        ('Логи службы (последние 100 записей)', {
            'fields': ('service_logs_display',),
            'classes': ('collapse',),
        }),
        ('Управление', {'fields': ('actions_block',)}),
    )

    def status_display(self, obj):
        status = obj.status
        if status == 'active':
            return format_html('<span style="color: #00ff00; font-weight: bold;">● Активна</span>')
        elif status == 'inactive':
            return format_html('<span style="color: #cccccc;">○ Неактивна</span>')
        elif status == 'failed':
            return format_html('<span style="color: #ff0000;">✗ Ошибка</span>')
        return format_html(f'<span style="color: #ff9900;">? {status}</span>')

    status_display.short_description = "Статус"

    def status_details_display(self, obj):
        details = obj.status_details
        if details:
            return format_html(
                '<pre style="font-family: monospace; font-size: 12px; background-color: #000000; '
                'color: #00ff00; padding: 10px; border-radius: 3px; max-height: 300px; '
                'overflow-y: auto; line-height: 1.3; white-space: pre;">{}</pre>', details
            )
        return "Статус недоступен"

    status_details_display.short_description = "Детали статуса (systemctl status)"

    def service_logs_display(self, obj):
        logs = obj.service_logs
        if logs:
            return format_html(
                '<pre style="font-family: monospace; font-size: 11px; background-color: #000000; '
                'color: #00ff00; padding: 10px; border-radius: 3px; max-height: 500px; '
                'overflow-y: auto; line-height: 1.2; white-space: pre;">{}</pre>', logs
            )
        return "Логи недоступны"

    service_logs_display.short_description = "Логи службы"

    def service_content_display(self, obj):
        content = obj.service_content
        if content:
            return format_html(
                '<pre style="font-family: monospace; font-size: 12px; background-color: #000000; '
                'color: #00ff00; padding: 10px; border-radius: 3px; max-height: 400px; '
                'overflow-y: auto; line-height: 1.3; white-space: pre;">{}</pre>', content
            )
        return "Файл не найден"

    service_content_display.short_description = "Содержимое файла службы"

    def autostart_display(self, obj):
        if obj.is_enabled:
            return format_html('<span style="color: #00ff00;">● Вкл.</span>')
        return format_html('<span style="color: #cccccc;">○ Выкл.</span>')

    autostart_display.short_description = "Автозагрузка"

    def file_exists_display(self, obj):
        if obj.service_file_exists:
            return format_html('<span style="color: #00ff00;">✓ Файл</span>')
        return format_html('<span style="color: #ff0000;">✗ Файл</span>')

    file_exists_display.short_description = "Файл"

    def file_info_display(self, obj):
        info = []
        if obj.service_file_exists:
            size = obj.service_file_path.stat().st_size
            info.append(
                f"<strong>Файл в проекте:</strong><br>"
                f"<code style='color: #00ff00;'>{obj.service_file_path}</code><br>Размер: {size} байт"
            )
        else:
            info.append(
                f"<span style='color: #ff0000;'><strong>Файл не найден:</strong><br>"
                f"<code>{obj.service_file_path}</code></span>"
            )
        info.append(
            f"<br><strong>Файл в systemd:</strong><br>"
            f"<code style='color: #00ff00;'>{obj.systemd_file_path}</code>"
        )
        if os.path.exists(obj.systemd_file_path):
            info.append("<span style='color: #00ff00;'>● Установлен</span>")
        else:
            info.append("<span style='color: #ff9900;'>○ Не установлен</span>")
        return format_html('<br>'.join(info))

    file_info_display.short_description = "Информация о файлах"

    def actions_display(self, obj):
        actions = [
            ('install', 'Уст.', '#9C27B0'),
            ('start', '▶', '#4CAF50'),
            ('stop', '⏹', '#F44336'),
            ('restart', '↻', '#2196F3'),
        ]
        buttons = []
        for action, label, color in actions:
            url = reverse(f'admin:core_unitservice_{action}', args=[obj.pk])
            buttons.append(
                f'<a href="{url}" style="display: inline-block; background: {color}; color: white; '
                f'padding: 3px 6px; margin: 0 1px; border-radius: 2px; text-decoration: none; '
                f'font-size: 11px; font-weight: bold; min-width: 20px; text-align: center;">{label}</a>'
            )
        return format_html(''.join(buttons))

    actions_display.short_description = "Действия"

    def actions_block(self, obj):
        actions = [
            ('install', '📥 Установить', '#9C27B0'),
            ('uninstall', '🗑 Удалить', '#607D8B'),
            ('start', '▶ Запустить', '#4CAF50'),
            ('stop', '⏹ Остановить', '#F44336'),
            ('restart', '🔄 Перезапустить', '#2196F3'),
            ('reload', '📥 Обновить конфиг', '#FF9800'),
            ('enable', '✓ Вкл. автозагрузку', '#8BC34A'),
            ('disable', '✗ Выкл. автозагрузку', '#FF5722'),
        ]
        buttons_html = []
        for action, title, color in actions:
            url = reverse(f'admin:core_unitservice_{action}', args=[obj.pk])
            buttons_html.append(
                f'<a href="{url}" style="display: block; background: {color}; color: white; '
                f'padding: 10px; border-radius: 4px; text-decoration: none; text-align: center; '
                f'margin-bottom: 8px; font-weight: bold;" '
                f'onmouseover="this.style.opacity=\'0.8\'" onmouseout="this.style.opacity=\'1\'">{title}</a>'
            )
        grid_html = f'''
            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; margin: 15px 0;">
                {''.join(buttons_html)}
            </div>
            <div style="padding: 10px; background: #222; color: #0f0; border-radius: 3px; font-size: 12px; font-family: monospace;">
                $ Действия выполняются немедленно<br>
                $ Используйте "Обновить конфиг" после изменения файла<br>
                $ Логи обновятся через несколько секунд
            </div>
        '''
        return mark_safe(grid_html)

    actions_block.short_description = "Управление службой"

    def _get_unit_service_or_404(self, pk):
        try:
            return UnitService.objects.get(pk=pk)
        except UnitService.DoesNotExist:
            return None

    def install_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.install_service()
        if success:
            messages.success(request, f"Служба '{unit.name}' установлена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def uninstall_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.uninstall_service()
        if success:
            messages.success(request, f"Служба '{unit.name}' удалена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def restart_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.restart()
        if success:
            messages.success(request, f"Служба '{unit.name}' перезапущена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def stop_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.stop()
        if success:
            messages.success(request, f"Служба '{unit.name}' остановлена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def start_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.start()
        if success:
            messages.success(request, f"Служба '{unit.name}' запущена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def reload_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.reload()
        if success:
            messages.success(request, f"Конфигурация службы '{unit.name}' обновлена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def enable_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.enable_autostart()
        if success:
            messages.success(request, f"Автозагрузка службы '{unit.name}' включена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def disable_service(self, request, pk):
        unit = self._get_unit_service_or_404(pk)
        if not unit:
            return redirect(request.META.get('HTTP_REFERER', 'admin:index'))
        success, message = unit.disable_autostart()
        if success:
            messages.success(request, f"Автозагрузка службы '{unit.name}' отключена")
        else:
            messages.error(request, f"Ошибка: {message}")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    @admin.action(description="📥 Установить выбранные службы")
    def install_services(self, request, queryset):
        results = [(u.name, *u.install_service()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Установлено {success_count} служб")
        if failed:
            self._report_failures(request, failed, "установке")

    @admin.action(description="🗑 Удалить выбранные службы")
    def uninstall_services(self, request, queryset):
        results = [(u.name, *u.uninstall_service()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Удалено {success_count} служб")
        if failed:
            self._report_failures(request, failed, "удалении")

    @admin.action(description="🔄 Перезапустить выбранные службы")
    def restart_services(self, request, queryset):
        results = [(u.name, *u.restart()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Перезапущено {success_count} служб")
        if failed:
            self._report_failures(request, failed, "перезапуске")

    @admin.action(description="⏹ Остановить выбранные службы")
    def stop_services(self, request, queryset):
        results = [(u.name, *u.stop()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Остановлено {success_count} служб")
        if failed:
            self._report_failures(request, failed, "остановке")

    @admin.action(description="▶ Запустить выбранные службы")
    def start_services(self, request, queryset):
        results = [(u.name, *u.start()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Запущено {success_count} служб")
        if failed:
            self._report_failures(request, failed, "запуске")

    @admin.action(description="📥 Обновить конфигурацию выбранных служб")
    def reload_services(self, request, queryset):
        results = [(u.name, *u.reload()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Обновлено {success_count} служб")
        if failed:
            self._report_failures(request, failed, "обновлении")

    @admin.action(description="✓ Включить автозагрузку выбранных служб")
    def enable_autostart(self, request, queryset):
        results = [(u.name, *u.enable_autostart()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Включена автозагрузка для {success_count} служб")
        if failed:
            self._report_failures(request, failed, "включении автозагрузки")

    @admin.action(description="✗ Отключить автозагрузку выбранных служб")
    def disable_autostart(self, request, queryset):
        results = [(u.name, *u.disable_autostart()) for u in queryset]
        success_count = sum(1 for _, s, _ in results if s)
        failed = [(n, m) for n, s, m in results if not s]
        if success_count:
            messages.success(request, f"Отключена автозагрузка для {success_count} служб")
        if failed:
            self._report_failures(request, failed, "отключении автозагрузки")

    def _report_failures(self, request, failed, action_name):
        names = ", ".join(n for n, _ in failed[:3])
        suffix = f" и ещё {len(failed) - 3}" if len(failed) > 3 else ""
        messages.error(request, f"Ошибка при {action_name}: {names}{suffix}")

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('<uuid:pk>/install/', self.admin_site.admin_view(self.install_service),
                 name='core_unitservice_install'),
            path('<uuid:pk>/uninstall/', self.admin_site.admin_view(self.uninstall_service),
                 name='core_unitservice_uninstall'),
            path('<uuid:pk>/restart/', self.admin_site.admin_view(self.restart_service),
                 name='core_unitservice_restart'),
            path('<uuid:pk>/stop/', self.admin_site.admin_view(self.stop_service),
                 name='core_unitservice_stop'),
            path('<uuid:pk>/start/', self.admin_site.admin_view(self.start_service),
                 name='core_unitservice_start'),
            path('<uuid:pk>/reload/', self.admin_site.admin_view(self.reload_service),
                 name='core_unitservice_reload'),
            path('<uuid:pk>/enable/', self.admin_site.admin_view(self.enable_service),
                 name='core_unitservice_enable'),
            path('<uuid:pk>/disable/', self.admin_site.admin_view(self.disable_service),
                 name='core_unitservice_disable'),
        ]
        return custom_urls + urls
