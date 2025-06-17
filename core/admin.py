import subprocess
from django.contrib import admin
from django.urls import reverse
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
    Media, ReportQuery, DataProvider
)
from core.widgets import UnfoldExportForm, UnfoldImportForm, UnfoldPeriodicTaskForm

admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)

# Inlines
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

class CarConsumptionInline(admin.TabularInline):
    model = CarConsumption
    extra = 0
    fields = ('winter_volume', 'summer_volume', 'valid_period')
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
    fields = ('username', 'is_active', 'email')
    readonly_fields = ('username', 'is_active', 'email')
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

# Admin Classes
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

@admin.register(OrgUser)
class OrgUserAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'username', 'organization_display', 'email', 'is_active', 'last_login')
    list_filter = ('org', 'is_active', 'is_staff')
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
        'id', 'name', 'description', 'engine_type', 'input', 'output',
        'is_tarrified', 'data_providers_display', 'created_at'
    )
    list_filter = ('engine_type', 'created_at', 'is_tarrified')
    search_fields = ('name', 'description')
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    inlines = [CarReportInline, CarConsumptionInline, DriverCarInline]
    actions = ['export_selected']

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
