from django.contrib import admin
from django.urls import reverse
from unfold.admin import ModelAdmin
from import_export.admin import ImportExportMixin
from django.utils.safestring import mark_safe

from core.models import (
    Organization, OrgUser, Car, CarReport, CarConsumption, Driver,
    Media, ReportQuery, DataProvider, DriverCar
)

from django_celery_beat.models import (
    ClockedSchedule, CrontabSchedule, IntervalSchedule, PeriodicTask, SolarSchedule
)
from django_celery_beat.admin import (
    ClockedScheduleAdmin as BaseClockedScheduleAdmin,
    CrontabScheduleAdmin as BaseCrontabScheduleAdmin,
    PeriodicTaskAdmin as BasePeriodicTaskAdmin,
)

from core.widgets import UnfoldExportForm, UnfoldImportForm, UnfoldPeriodicTaskForm

admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)

class MediaInline(admin.StackedInline):
    model = Media
    extra = 0
    fields = ('filename', 'media_type', 'size', 'type', 'report_query_id')
    readonly_fields = ('filename', 'media_type', 'size', 'type', 'report_query_display')
    verbose_name = "Медиафайл"
    verbose_name_plural = "Медиафайлы"

    def report_query_display(self, obj):
        return str(obj.report_query_id) if obj.report_query_id else "Не указан"
    report_query_display.short_description = "Запрос отчёта"

@admin.register(Organization)
class OrganizationAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'bot_token_display', 'chat_id')
    list_filter = ('name',)
    search_fields = ('name', 'bot_token', 'chat_id')
    ordering = ('name',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Организация"
    verbose_name_plural = "Организации"
    actions = ['export_selected']

    def bot_token_display(self, obj):
        return "****" + obj.bot_token[-4:] if obj.bot_token else "Не указан"
    bot_token_display.short_description = "Токен бота"

@admin.register(OrgUser)
class OrgUserAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'username', 'organization_display', 'is_active')
    list_filter = ('org_id', 'is_active', 'is_staff')
    search_fields = ('username', 'org_id__name', 'email')
    ordering = ('username',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Пользователь организации"
    verbose_name_plural = "Пользователи организаций"
    actions = ['export_selected', 'activate_users', 'deactivate_users']

    def organization_display(self, obj):
        return str(obj.org_id) if obj.org_id else "Не указана"
    organization_display.short_description = "Организация"

    def activate_users(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, "Выбранные пользователи активированы.")

    def deactivate_users(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, "Выбранные пользователи деактивированы.")

    activate_users.short_description = "Активировать пользователей"
    deactivate_users.short_description = "Деактивировать пользователей"

@admin.register(Car)
class CarAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'description', 'engine_type', 'data_providers_display')
    list_filter = ('engine_type',)
    search_fields = ('name', 'description', 'id')
    ordering = ('name', 'engine_type')
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Автомобиль"
    verbose_name_plural = "Автомобили"
    actions = ['export_selected']

    def data_providers_display(self, obj):
        providers = obj.data_providers.all()
        if not providers:
            return "Нет провайдеров"
        provider_links = [
            f'<a href="{reverse("admin:core_dataprovider_change", args=[provider.id])}">{provider.name}</a>'
            for provider in providers[:3]
        ]
        result = ", ".join(provider_links) + ("..." if len(providers) > 3 else "")
        return mark_safe(result)
    data_providers_display.short_description = "Поставщики данных"

@admin.register(CarConsumption)
class CarConsumptionAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car_display', 'winter_volume', 'summer_volume', 'valid_period')
    list_filter = ('car_id', 'valid_period')
    search_fields = ('car_id__name',)
    ordering = ('car_id__name', 'valid_period')
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Расход топлива"
    verbose_name_plural = "Расходы топлива"
    actions = ['export_selected']

    def car_display(self, obj):
        return str(obj.car_id) if obj.car_id else "Не указан"
    car_display.short_description = "Автомобиль"

@admin.register(CarReport)
class CarReportAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car_display', 'datetime', 'volume', 'status')
    list_filter = ('car_id', 'datetime', 'status')
    ordering = ('-datetime',)
    search_fields = ('car_id__name',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Отчёт об автомобиле"
    verbose_name_plural = "Отчёты об автомобилях"
    actions = ['export_selected']

    def car_display(self, obj):
        return str(obj.car_id) if obj.car_id else "Не указан"
    car_display.short_description = "Автомобиль"

@admin.register(Driver)
class DriverAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'fullname', 'address', 'phone', 'cars_display')
    list_filter = ('fullname',)
    search_fields = ('fullname', 'phone', 'address')
    ordering = ('fullname',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Водитель"
    verbose_name_plural = "Водители"
    actions = ['export_selected']

    def cars_display(self, obj):
        cars = Car.objects.filter(driver_cars__driver_id=obj)
        return ", ".join(str(car) for car in cars[:3]) + ("..." if len(cars) > 3 else "")
    cars_display.short_description = "Автомобили"

@admin.register(DriverCar)
class DriverCarAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('driver_id', 'car_id')
    list_filter = ('driver_id', 'car_id')
    search_fields = ('driver_id__fullname', 'car_id__name')
    ordering = ('driver_id',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Назначение водителя-автомобиля"
    verbose_name_plural = "Назначения водителей-автомобилей"
    actions = ['export_selected']

@admin.register(Media)
class MediaAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'filename', 'report_query_display', 'media_type', 'size', 'type')
    list_filter = ('type', 'report_query_id__organization_id')
    search_fields = ('filename', 'report_query_id__organization_id__name')
    ordering = ('filename',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Медиафайл"
    verbose_name_plural = "Медиафайлы"
    actions = ['export_selected']

    def report_query_display(self, obj):
        return str(obj.report_query_id) if obj.report_query_id else "Не указан"
    report_query_display.short_description = "Запрос отчёта"

@admin.register(ReportQuery)
class ReportQueryAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'organization_display', 'provider_display', 'status')
    list_filter = ('status', 'organization_id')
    search_fields = ('organization_id__name', 'provider_id__name')
    ordering = ('-id',)
    list_per_page = 25
    inlines = [MediaInline]
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Запрос отчёта"
    verbose_name_plural = "Запросы отчётов"
    actions = ['export_selected', 'mark_as_completed', 'mark_as_error']

    def organization_display(self, obj):
        return str(obj.organization_id) if obj.organization_id else "Не указана"
    organization_display.short_description = "Организация"

    def provider_display(self, obj):
        return str(obj.provider_id) if obj.provider_id else "Не указан"
    provider_display.short_description = "Поставщик данных"

    def mark_as_completed(self, request, queryset):
        queryset.update(status='completed')
        self.message_user(request, "Выбранные запросы отмечены как завершённые.")

    def mark_as_error(self, request, queryset):
        queryset.update(status='error')
        self.message_user(request, "Выбранные запросы отмечены как с ошибкой.")

    mark_as_completed.short_description = "Отметить как завершённые"
    mark_as_error.short_description = "Отметить как с ошибкой"

@admin.register(DataProvider)
class DataProviderAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'cars_display')
    list_filter = ('name',)
    search_fields = ('name', 'cars__name')
    ordering = ('name',)
    list_per_page = 25
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    verbose_name = "Поставщик данных"
    verbose_name_plural = "Поставщики данных"
    actions = ['export_selected']

    def cars_display(self, obj):
        providers = getattr(obj, 'cars', None)
        if not providers:
            return "Нет автомобилей"
        cars = providers.all()
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
    ordering = ('-enabled', 'name')
    list_display = ('name', 'task', 'enabled', 'last_run_at', 'total_run_count')
    list_filter = ('enabled', 'task')
    search_fields = ('name', 'task')
    verbose_name = "Периодическая задача"
    verbose_name_plural = "Периодические задачи"

@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(ModelAdmin):
    list_display = ('every', 'period')
    search_fields = ('every',)
    verbose_name = "Интервальный график"
    verbose_name_plural = "Интервальные графики"

@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin):
    list_display = ('minute', 'hour', 'day_of_month', 'month_of_year', 'day_of_week')
    search_fields = ('minute', 'hour')
    verbose_name = "График по Cron"
    verbose_name_plural = "Графики по Cron"

@admin.register(SolarSchedule)
class SolarScheduleAdmin(ModelAdmin):
    list_display = ('event', 'latitude', 'longitude')
    search_fields = ('event',)
    verbose_name = "Солнечный график"
    verbose_name_plural = "Солнечные графики"

@admin.register(ClockedSchedule)
class ClockedScheduleAdmin(BaseClockedScheduleAdmin, ModelAdmin):
    list_display = ('clocked_time',)
    search_fields = ('clocked_time',)
    verbose_name = "График по времени"
    verbose_name_plural = "Графики по времени"