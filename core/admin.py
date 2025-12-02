import os

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
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
    Media, ReportQuery, DataProvider, CarBadData, Language,
    SensorsKey, SensorsValues, SensorsKeyLocalization, ReportQueryDetails, UnitService
)
from core.helpers.widgets import UnfoldExportForm, UnfoldImportForm, UnfoldPeriodicTaskForm
from core.widgets.widgets import format_logs_display

admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)


class MediaInline(admin.TabularInline):
    model = Media
    extra = 0
    fields = ('filename', 'media_type', 'size', 'type', 'file_hash')
    readonly_fields = ('filename', 'media_type', 'size', 'type', 'file_hash')
    verbose_name = "Медиафайл"
    verbose_name_plural = "Медиафайлы"
    can_delete = True


class CarReportInline(admin.TabularInline):
    model = CarReport
    extra = 0
    fields = ('datetime', 'volume', 'status')
    readonly_fields = ('datetime', 'volume', 'status')
    verbose_name = "Отчет об автомобиле"
    verbose_name_plural = "Отчеты об автомобилях"
    can_delete = False


class SensorsValuesInline(admin.TabularInline):
    model = SensorsValues
    extra = 0
    fields = ('key', 'value')
    verbose_name = "Значение датчика"
    verbose_name_plural = "Значения датчиков"
    can_delete = True


class SensorsKeyLocalizationInline(admin.TabularInline):
    model = SensorsKeyLocalization
    extra = 0
    fields = ('language', 'localization')
    verbose_name = "Локализация"
    verbose_name_plural = "Локализации"
    can_delete = True


class CarConsumptionInline(admin.TabularInline):
    model = CarConsumption
    extra = 0
    fields = ('winter_volume', 'summer_volume', 'valid_period', 'max_fuel', 'speed_etalon')
    readonly_fields = ('winter_volume', 'summer_volume', 'valid_period')
    verbose_name = "Расход топлива"
    verbose_name_plural = "Расходы топлива"
    can_delete = True


class DriverCarInline(admin.TabularInline):
    model = Driver.car_id.through
    extra = 0
    verbose_name = "Автомобиль"
    verbose_name_plural = "Автомобили"
    can_delete = True
    fields = ('car',)
    autocomplete_fields = ['car']


class ReportQueryInline(admin.TabularInline):
    model = ReportQuery
    extra = 0
    fields = ('status', 'provider_id')
    readonly_fields = ('status', 'provider_id')
    verbose_name = "Запрос отчета"
    verbose_name_plural = "Запросы отчетов"
    can_delete = True


class OrgUserInline(admin.TabularInline):
    model = OrgUser
    extra = 0
    fields = ('username', 'is_active', 'email', 'active_language')
    readonly_fields = ('username', 'is_active', 'email', 'active_language')
    verbose_name = "Пользователь организации"
    verbose_name_plural = "Пользователи организации"
    can_delete = False


class CarInline(admin.TabularInline):
    model = DataProvider.cars.through
    extra = 0
    verbose_name = "Автомобиль"
    verbose_name_plural = "Автомобили"
    can_delete = True
    fields = ('car',)
    autocomplete_fields = ['car']


class ReportQueryDetailsInline(admin.TabularInline):
    model = ReportQueryDetails
    extra = 0
    fields = ('start_time', 'end_time', 'time_proceed_display', 'cars_proceed', 'cars_skipped', 'traceback_preview')
    readonly_fields = (
    'start_time', 'end_time', 'time_proceed_display', 'cars_proceed', 'cars_skipped', 'traceback_preview')
    verbose_name = "Детали выполнения"
    verbose_name_plural = "Детали выполнения"
    can_delete = False
    classes = ('collapse',)

    def time_proceed_display(self, obj):
        if obj.time_proceed:
            return str(obj.time_proceed)
        return "Не указано"

    time_proceed_display.short_description = "Время выполнения"

    def traceback_preview(self, obj):
        if obj.traceback:
            import json
            traceback_str = json.dumps(obj.traceback, ensure_ascii=False, indent=2)
            return mark_safe(
                f'<pre style="max-height: 200px; overflow: auto; background-color: #f8f8f8; padding: 10px; border: 1px solid #ddd;">{traceback_str}</pre>')
        return "Нет данных об ошибках"

    traceback_preview.short_description = "Детали ошибки"
    traceback_preview.allow_tags = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'bot_token_display', 'chat_id')
    list_filter = ('name',)
    search_fields = ('name', 'bot_token', 'chat_id')
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    inlines = [OrgUserInline]
    actions = ['export_selected']

    def bot_token_display(self, obj):
        return "****" + obj.bot_token[-4:] if obj.bot_token else "Не указан"

    bot_token_display.short_description = "Токен бота"


@admin.register(Language)
class LanguageAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'code', 'name', 'description')
    list_filter = ('code',)
    search_fields = ('code', 'name')
    ordering = ('code',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']


@admin.register(OrgUser)
class OrgUserAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
    'id', 'username', 'organization_display', 'email', 'active_language_display', 'is_active', 'last_login')
    list_filter = ('org', 'is_active', 'is_staff', 'active_language')
    search_fields = ('username', 'org__name', 'email')
    ordering = ('username',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected', 'activate_users', 'deactivate_users']

    def organization_display(self, obj):
        if obj.org:
            url = reverse("admin:core_organization_change", args=[obj.org.id])
            return mark_safe(f'<a href="{url}">{obj.org.name}</a>')
        return "Не указана"

    organization_display.short_description = "Организация"

    def active_language_display(self, obj):
        if obj.active_language:
            return obj.active_language.name
        return "Не указан"

    active_language_display.short_description = "Активный язык"

    def activate_users(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, "Выбранные пользователи активированы.")

    activate_users.short_description = "Активировать пользователей"

    def deactivate_users(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, "Выбранные пользователи деактивированы.")

    deactivate_users.short_description = "Деактивировать пользователей"


@admin.register(Car)
class CarAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'id', 'id_in_provider_system', 'name', 'description', 'engine_type', 'input', 'output',
        'is_tarrified', 'is_active', 'data_providers_display', 'created_at'
    )
    list_filter = ('engine_type', 'created_at', 'is_tarrified')
    search_fields = ('name', 'description', 'id_in_provider_system')
    ordering = ('name', 'is_active', 'is_tarrified')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    inlines = [CarReportInline, CarConsumptionInline, DriverCarInline, SensorsValuesInline]
    actions = ['export_selected', 'deactivate_selected', 'activate_selected']

    def data_providers_display(self, obj):
        providers = obj.data_providers.all()
        if not providers:
            return "Нет провайдеров"
        provider_links = [
            f'<a href="{reverse("admin:core_dataprovider_change", args=[p.id])}">{p.name}</a>'
            for p in providers[:3]
        ]
        result = ", ".join(provider_links) + ("..." if len(providers) > 3 else "")
        return mark_safe(result)

    data_providers_display.short_description = "Поставщики данных"

    @admin.action(description='Деактивировать выбранные машины')
    def deactivate_selected(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            f'Деактивировано {updated} машин(ы)',
            messages.SUCCESS
        )

    @admin.action(description='Активировать выбранные машины')
    def activate_selected(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(
            request,
            f'Активировано {updated} машин(ы)',
            messages.SUCCESS
        )


@admin.register(CarConsumption)
class CarConsumptionAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car_display', 'winter_volume', 'summer_volume', 'valid_period')
    list_filter = ('valid_period',)
    search_fields = ('car_id__name',)
    ordering = ('car_id__name', 'valid_period')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']

    def car_display(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return mark_safe(f'<a href="{url}">{obj.car_id.name}</a>')
        return "Не указан"

    car_display.short_description = "Автомобиль"


@admin.register(CarReport)
class CarReportAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car_display', 'datetime', 'volume', 'status')
    list_filter = ('datetime', 'status')
    search_fields = ('car_id__name',)
    ordering = ('-datetime',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected', 'mark_as_active', 'mark_as_inactive']

    def car_display(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return mark_safe(f'<a href="{url}">{obj.car_id.name}</a>')
        return "Не указан"

    car_display.short_description = "Автомобиль"

    def mark_as_active(self, request, queryset):
        queryset.update(status=True)
        self.message_user(request, "Выбранные отчеты отмечены как активные.")

    mark_as_active.short_description = "Отметить как активные"

    def mark_as_inactive(self, request, queryset):
        queryset.update(status=False)
        self.message_user(request, "Выбранные отчеты отмечены как неактивные.")

    mark_as_inactive.short_description = "Отметить как неактивные"


@admin.register(Driver)
class DriverAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'fullname', 'address', 'phone', 'cars_display')
    list_filter = ('fullname',)
    search_fields = ('fullname', 'phone', 'address')
    ordering = ('fullname',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    inlines = [DriverCarInline]
    actions = ['export_selected']

    def cars_display(self, obj):
        cars = obj.car_id.all()
        if not cars:
            return "Нет автомобилей"
        car_links = [
            f'<a href="{reverse("admin:core_car_change", args=[car.id])}">{car.name}</a>'
            for car in cars[:3]
        ]
        result = ", ".join(car_links) + ("..." if len(cars) > 3 else "")
        return mark_safe(result)

    cars_display.short_description = "Автомобили"


@admin.register(Media)
class MediaAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'filename', 'report_query_display', 'media_type', 'size', 'type', 'file_hash')
    list_filter = ('type',)
    search_fields = ('filename',)
    ordering = ('filename',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']

    def report_query_display(self, obj):
        if obj.report_query_id:
            url = reverse("admin:core_reportquery_change", args=[obj.report_query_id.id])
            return mark_safe(f'<a href="{url}">{obj.report_query_id}</a>')
        return "Не указан"

    report_query_display.short_description = "Запрос отчета"


@admin.register(ReportQuery)
class ReportQueryAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'provider_display', 'status')
    list_filter = ('status',)
    search_fields = ('provider_id__name',)
    ordering = ('-id',)
    inlines = [MediaInline]
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected', 'mark_as_completed', 'mark_as_error']

    def provider_display(self, obj):
        if obj.provider_id:
            url = reverse("admin:core_dataprovider_change", args=[obj.provider_id.id])
            return mark_safe(f'<a href="{url}">{obj.provider_id.name}</a>')
        return "Не указан"

    provider_display.short_description = "Поставщик данных"

    def mark_as_completed(self, request, queryset):
        queryset.update(status='completed')
        self.message_user(request, "Выбранные запросы отмечены как завершенные.")

    mark_as_completed.short_description = "Отметить как завершенные"

    def mark_as_error(self, request, queryset):
        queryset.update(status='error')
        self.message_user(request, "Выбранные запросы отмечены как с ошибкой.")

    mark_as_error.short_description = "Отметить как с ошибкой"


@admin.register(ReportQueryDetails)
class ReportQueryDetailsAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'report_query_display', 'start_time', 'end_time', 'time_proceed_display',
                    'cars_proceed', 'cars_skipped', 'has_traceback')
    list_filter = ('start_time', 'end_time')
    search_fields = ('report_query__id',)
    ordering = ('-start_time',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    readonly_fields = ('id', 'report_query_display', 'start_time', 'end_time', 'time_proceed',
                       'cars_proceed', 'cars_skipped', 'traceback_preview')
    actions = ['export_selected', 'clear_traceback']

    def report_query_display(self, obj):
        if obj.report_query:
            url = reverse("admin:core_reportquery_change", args=[obj.report_query.id])
            return mark_safe(f'<a href="{url}">{obj.report_query.id}</a>')
        return "Не указан"

    report_query_display.short_description = "Запрос отчета"

    def time_proceed_display(self, obj):
        if obj.time_proceed:
            return str(obj.time_proceed)
        return "Не указано"

    time_proceed_display.short_description = "Время выполнения"

    def has_traceback(self, obj):
        return bool(obj.traceback)

    has_traceback.short_description = "Есть ошибка"
    has_traceback.boolean = True

    def traceback_preview(self, obj):
        if obj.traceback:
            import json
            traceback_str = json.dumps(obj.traceback, ensure_ascii=False, indent=2)
            return mark_safe(f'<pre style="max-height: 300px; overflow: auto;">{traceback_str}</pre>')
        return "Нет данных об ошибках"

    traceback_preview.short_description = "Детали ошибки"

    def clear_traceback(self, request, queryset):
        updated = queryset.update(traceback=None)
        self.message_user(
            request,
            f'Traceback очищен для {updated} записей',
            messages.SUCCESS
        )

    clear_traceback.short_description = "Очистить traceback"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CarBadData)
class CarBadDataAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car_display', 'datetime', 'reason')
    list_filter = ('datetime',)
    search_fields = ('car_id__name', 'reason')
    ordering = ('-datetime',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']

    list_display_links = ('id', 'car_display')
    list_per_page = 20
    save_as = True
    save_on_top = True

    class Meta:
        verbose_name = "Ошибка автомобиля"
        verbose_name_plural = "Ошибки автомобилей"

    def car_display(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return mark_safe(f'<a href="{url}">{obj.car_id.name}</a>')
        return "Не указан"

    car_display.short_description = "Автомобиль"


@admin.register(DataProvider)
class DataProviderAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'org_display', 'cars_display')
    list_filter = ('name',)
    search_fields = ('name', 'cars__name')
    ordering = ('name',)
    inlines = [CarInline, ReportQueryInline]
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']

    def org_display(self, obj):
        if obj.org_id:
            url = reverse("admin:core_organization_change", args=[obj.org_id.id])
            return mark_safe(f'<a href="{url}">{obj.org_id.name}</a>')
        return "Не указана"

    org_display.short_description = "Организация"

    def cars_display(self, obj):
        cars = obj.cars.all()
        if not cars:
            return "Нет автомобилей"
        car_links = [
            f'<a href="{reverse("admin:core_car_change", args=[car.id])}">{car.name}</a>'
            for car in cars[:3]
        ]
        result = ", ".join(car_links) + ("..." if len(cars) > 3 else "")
        return mark_safe(result)

    cars_display.short_description = "Автомобили"


@admin.register(SensorsKey)
class SensorsKeyAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'key')
    search_fields = ('key',)
    ordering = ('key',)
    inlines = [SensorsKeyLocalizationInline, SensorsValuesInline]
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']


@admin.register(SensorsValues)
class SensorsValuesAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car_display', 'key_display', 'value')
    list_filter = ('key', 'car_id')
    search_fields = ('car_id__name', 'key__key', 'value')
    ordering = ('car_id__name', 'key__key')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']

    def car_display(self, obj):
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id.id])
            return mark_safe(f'<a href="{url}">{obj.car_id.name}</a>')
        return "Не указан"

    car_display.short_description = "Автомобиль"

    def key_display(self, obj):
        if obj.key:
            url = reverse("admin:core_sensorskey_change", args=[obj.key.id])
            return mark_safe(f'<a href="{url}">{obj.key.key}</a>')
        return "Не указан"

    key_display.short_description = "Ключ датчика"


@admin.register(SensorsKeyLocalization)
class SensorsKeyLocalizationAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'key_display', 'language_display', 'localization')
    list_filter = ('key', 'language')
    search_fields = ('key__key', 'language__name', 'localization')
    ordering = ('key__key', 'language__name')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    actions = ['export_selected']

    def key_display(self, obj):
        if obj.key:
            url = reverse("admin:core_sensorskey_change", args=[obj.key.id])
            return mark_safe(f'<a href="{url}">{obj.key.key}</a>')
        return "Не указан"

    key_display.short_description = "Ключ датчика"

    def language_display(self, obj):
        if obj.language:
            url = reverse("admin:core_language_change", args=[obj.language.id])
            return mark_safe(f'<a href="{url}">{obj.language.name}</a>')
        return "Не указан"

    language_display.short_description = "Язык"


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

##DEVOPS FEATURES

@admin.register(UnitService)
class UnitServiceAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'name',
        'service',
        'status_display',
        'autostart_display',
        'file_exists_display',
        'actions_display'
    )
    list_filter = ('is_active', 'auto_start')
    search_fields = ('name', 'service', 'description')
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm

    actions = [
        'install_services',
        'restart_services',
        'stop_services',
        'start_services',
        'reload_services',
        'enable_autostart',
        'disable_autostart',
        'uninstall_services'
    ]

    readonly_fields = (
        'status_display',
        'logs_display',
        'actions_preview',
        'file_info_display',
        'service_content_display'
    )

    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'service', 'service_filename', 'description', 'is_active')
        }),
        ('Настройки службы', {
            'fields': ('auto_start', 'restart_on_failure'),
            'classes': ('collapse',)
        }),
        ('Информация о файле', {
            'fields': ('file_info_display', 'service_content_display'),
        }),
        ('Управление', {
            'fields': ('status_display', 'actions_preview', 'logs_display'),
            'classes': ('wide',)
        }),
    )

    def status_display(self, obj):
        """Отображение статуса службы с цветом"""
        status = obj.status
        if status == 'active':
            color = 'green'
            icon = '🟢'
            text = 'Активна'
        elif status == 'inactive':
            color = 'gray'
            icon = '⚪'
            text = 'Неактивна'
        elif status == 'failed':
            color = 'red'
            icon = '🔴'
            text = 'Ошибка'
        else:
            color = 'orange'
            icon = '🟡'
            text = status

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            color, icon, text
        )

    status_display.short_description = "Статус"

    def autostart_display(self, obj):
        """Отображение статуса автозагрузки"""
        if obj.is_enabled:
            return format_html(
                '<span style="color: green; font-weight: bold;">✓ Включена</span>'
            )
        else:
            return format_html(
                '<span style="color: gray;">✗ Выключена</span>'
            )

    autostart_display.short_description = "Автозагрузка"

    def file_exists_display(self, obj):
        """Отображение статуса файла"""
        if obj.service_file_exists:
            return format_html(
                '<span style="color: green;">✓ {}</span>',
                obj.service_filename or obj.service
            )
        else:
            return format_html(
                '<span style="color: red;">✗ Файл не найден</span>'
            )

    file_exists_display.short_description = "Файл службы"

    def logs_display(self, obj):
        """Отображение логов службы"""
        logs = obj.logs
        return format_logs_display(logs)

    logs_display.short_description = "Логи и статус"

    def service_content_display(self, obj):
        """Отображение содержимого файла службы"""
        content = obj.service_content
        if content:
            # Подсветка синтаксиса (простая)
            highlighted = content.replace('[', '<strong style="color: #007acc;">[')
            highlighted = highlighted.replace(']', ']</strong>')
            highlighted = highlighted.replace('\n', '<br>')

            return format_html(
                '<div style="font-family: monospace; font-size: 12px; background: #f8f8f8; padding: 10px; border: 1px solid #ddd; border-radius: 5px; max-height: 300px; overflow-y: auto;">{}</div>',
                highlighted
            )
        return "Содержимое файла недоступно"

    service_content_display.short_description = "Содержимое файла службы"

    def file_info_display(self, obj):
        """Информация о файлах службы"""
        info = []

        if obj.service_file_exists:
            info.append(f"<strong>Файл в проекте:</strong> {obj.service_file_path}")
            info.append(f"<strong>Размер:</strong> {obj.service_file_path.stat().st_size} байт")
        else:
            info.append(f"<span style='color: red;'><strong>Файл не найден:</strong> {obj.service_file_path}</span>")

        info.append(f"<strong>Файл в systemd:</strong> {obj.systemd_file_path}")

        if os.path.exists(obj.systemd_file_path):
            info.append(f"<span style='color: green;'>✓ Установлен в systemd</span>")
        else:
            info.append(f"<span style='color: orange;'>⚠ Не установлен в systemd</span>")

        return format_html('<br>'.join(info))

    file_info_display.short_description = "Информация о файлах"

    def actions_display(self, obj):
        """Кнопки действий в списке"""
        install_url = reverse('admin:core_unitservice_install', args=[obj.pk])
        restart_url = reverse('admin:core_unitservice_restart', args=[obj.pk])
        stop_url = reverse('admin:core_unitservice_stop', args=[obj.pk])
        start_url = reverse('admin:core_unitservice_start', args=[obj.pk])

        return format_html('''
            <div style="display: flex; gap: 3px; flex-wrap: wrap;">
                <a href="{}" class="button" style="background: #9C27B0; color: white; padding: 2px 6px; border-radius: 3px; text-decoration: none; font-size: 11px;">Установить</a>
                <a href="{}" class="button" style="background: #4CAF50; color: white; padding: 2px 6px; border-radius: 3px; text-decoration: none; font-size: 11px;">Запуск</a>
                <a href="{}" class="button" style="background: #f44336; color: white; padding: 2px 6px; border-radius: 3px; text-decoration: none; font-size: 11px;">Стоп</a>
                <a href="{}" class="button" style="background: #2196F3; color: white; padding: 2px 6px; border-radius: 3px; text-decoration: none; font-size: 11px;">Рестарт</a>
            </div>
        ''', install_url, start_url, stop_url, restart_url)

    actions_display.short_description = "Действия"

    def actions_preview(self, obj):
        """Блок действий на странице редактирования"""
        install_url = reverse('admin:core_unitservice_install', args=[obj.pk])
        uninstall_url = reverse('admin:core_unitservice_uninstall', args=[obj.pk])
        restart_url = reverse('admin:core_unitservice_restart', args=[obj.pk])
        stop_url = reverse('admin:core_unitservice_stop', args=[obj.pk])
        start_url = reverse('admin:core_unitservice_start', args=[obj.pk])
        reload_url = reverse('admin:core_unitservice_reload', args=[obj.pk])
        enable_url = reverse('admin:core_unitservice_enable', args=[obj.pk])
        disable_url = reverse('admin:core_unitservice_disable', args=[obj.pk])

        return format_html('''
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 15px 0;">
                <a href="{}" class="button" style="background: #9C27B0; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">📥 Установить</a>
                <a href="{}" class="button" style="background: #607D8B; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">🗑 Удалить</a>
                <a href="{}" class="button" style="background: #4CAF50; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">▶ Запустить</a>
                <a href="{}" class="button" style="background: #f44336; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">⏹ Остановить</a>
                <a href="{}" class="button" style="background: #2196F3; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">🔄 Перезапустить</a>
                <a href="{}" class="button" style="background: #FF9800; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">📥 Обновить</a>
                <a href="{}" class="button" style="background: #8BC34A; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">✓ Вкл. автозагрузку</a>
                <a href="{}" class="button" style="background: #FF5722; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; text-align: center;">✗ Выкл. автозагрузку</a>
            </div>
            <p style="color: #666; font-size: 12px; margin-top: 5px;">
                Изменения вступают в силу немедленно. Логи обновятся через несколько секунд.
            </p>
        ''', install_url, uninstall_url, start_url, stop_url, restart_url, reload_url, enable_url, disable_url)

    actions_preview.short_description = "Быстрые действия"


    def install_service(self, request, pk):
        """Установка конкретной службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.install_service()

            if success:
                messages.success(request, f"Служба {unit.name} успешно установлена")
            else:
                messages.error(request, f"Ошибка при установке службы {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def uninstall_service(self, request, pk):
        """Удаление конкретной службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.uninstall_service()

            if success:
                messages.success(request, f"Служба {unit.name} успешно удалена")
            else:
                messages.error(request, f"Ошибка при удалении службы {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def restart_service(self, request, pk):
        """Перезапуск конкретной службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.restart()

            if success:
                messages.success(request, f"Служба {unit.name} успешно перезапущена")
            else:
                messages.error(request, f"Ошибка при перезапуске службы {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def stop_service(self, request, pk):
        """Остановка конкретной службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.stop()

            if success:
                messages.success(request, f"Служба {unit.name} успешно остановлена")
            else:
                messages.error(request, f"Ошибка при остановке службы {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def start_service(self, request, pk):
        """Запуск конкретной службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.start()

            if success:
                messages.success(request, f"Служба {unit.name} успешно запущена")
            else:
                messages.error(request, f"Ошибка при запуске службы {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def reload_service(self, request, pk):
        """Обновление конфигурации службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.reload()

            if success:
                messages.success(request, f"Конфигурация службы {unit.name} успешно обновлена")
            else:
                messages.error(request, f"Ошибка при обновлении службы {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def enable_service(self, request, pk):
        """Включение автозагрузки службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.enable_autostart()

            if success:
                messages.success(request, f"Автозагрузка службы {unit.name} включена")
            else:
                messages.error(request, f"Ошибка при включении автозагрузки {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def disable_service(self, request, pk):
        """Отключение автозагрузки службы"""
        try:
            unit = UnitService.objects.get(pk=pk)
            success, message = unit.disable_autostart()

            if success:
                messages.success(request, f"Автозагрузка службы {unit.name} отключена")
            else:
                messages.error(request, f"Ошибка при отключении автозагрузки {unit.name}: {message}")

        except UnitService.DoesNotExist:
            messages.error(request, "Служба не найдена")
        except Exception as e:
            messages.error(request, f"Ошибка: {str(e)}")

        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    # Массовые действия

    @admin.action(description="Установить выбранные службы")
    def install_services(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.install_service()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно установлено {success_count} служб")
        if error_count > 0:
            messages.error(request, f"Ошибка при установке {error_count} служб: {', '.join(errors[:3])}")

    @admin.action(description="Удалить выбранные службы")
    def uninstall_services(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.uninstall_service()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно удалено {success_count} служб")
        if error_count > 0:
            messages.error(request, f"Ошибка при удалении {error_count} служб: {', '.join(errors[:3])}")

    @admin.action(description="Перезапустить выбранные службы")
    def restart_services(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.restart()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно перезапущено {success_count} служб")
        if error_count > 0:
            messages.error(request, f"Ошибка при перезапуске {error_count} служб: {', '.join(errors[:3])}")

    @admin.action(description="Остановить выбранные службы")
    def stop_services(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.stop()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно остановлено {success_count} служб")
        if error_count > 0:
            messages.error(request, f"Ошибка при остановке {error_count} служб: {', '.join(errors[:3])}")

    @admin.action(description="Запустить выбранные службы")
    def start_services(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.start()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно запущено {success_count} служб")
        if error_count > 0:
            messages.error(request, f"Ошибка при запуске {error_count} служб: {', '.join(errors[:3])}")

    @admin.action(description="Обновить конфигурацию выбранных служб")
    def reload_services(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.reload()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно обновлено {success_count} служб")
        if error_count > 0:
            messages.error(request, f"Ошибка при обновлении {error_count} служб: {', '.join(errors[:3])}")

    @admin.action(description="Включить автозагрузку выбранных служб")
    def enable_autostart(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.enable_autostart()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно включена автозагрузка для {success_count} служб")
        if error_count > 0:
            messages.error(request,
                           f"Ошибка при включении автозагрузки для {error_count} служб: {', '.join(errors[:3])}")

    @admin.action(description="Отключить автозагрузку выбранных служб")
    def disable_autostart(self, request, queryset):
        success_count = 0
        error_count = 0
        errors = []

        for unit in queryset:
            try:
                success, message = unit.disable_autostart()
                if success:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{unit.name}: {message}")
            except Exception as e:
                error_count += 1
                errors.append(f"{unit.name}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Успешно отключена автозагрузка для {success_count} служб")
        if error_count > 0:
            messages.error(request,
                           f"Ошибка при отключении автозагрузки для {error_count} служб: {', '.join(errors[:3])}")

    # URL для отдельных действий
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
            path('<uuid:pk>/stop/', self.admin_site.admin_view(self.stop_service), name='core_unitservice_stop'),
            path('<uuid:pk>/start/', self.admin_site.admin_view(self.start_service), name='core_unitservice_start'),
            path('<uuid:pk>/reload/', self.admin_site.admin_view(self.reload_service), name='core_unitservice_reload'),
            path('<uuid:pk>/enable/', self.admin_site.admin_view(self.enable_service), name='core_unitservice_enable'),
            path('<uuid:pk>/disable/', self.admin_site.admin_view(self.disable_service),
                 name='core_unitservice_disable'),
        ]
        return custom_urls + urls