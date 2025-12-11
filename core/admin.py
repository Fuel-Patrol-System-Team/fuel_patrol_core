import os

##TODO: NRyabcev: Сделай норм, а не вот эта хуета

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
    SensorsKey, SensorsValues, SensorsKeyLocalization, ReportQueryDetails, UnitService, CarUnit, UserCarList, CarPrimary
)
from core.helpers.widgets import UnfoldExportForm, UnfoldImportForm, UnfoldPeriodicTaskForm

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
    fields = ('datetime', 'speed', 'volume', 'status')
    readonly_fields = ('datetime', 'speed', 'volume', 'status')
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
    list_display = ('id', 'car_display', 'speed', 'datetime', 'volume', 'status')
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


@admin.register(UserCarList)
class UserCarListAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'id',
        'name',
        'user_display',
        'cars_count_display',
        'created_at_display'
    )
    list_filter = ('name', 'user')
    search_fields = ('name', 'user__username')
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm

    fields = ('name', 'user')

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        # Добавляем аннотацию количества машин
        from django.db.models import Count
        return queryset.annotate(
            cars_count=Count('car', distinct=True)
        )

    def user_display(self, obj):
        """Отображение пользователя со ссылкой"""
        if obj.user:
            url = reverse("admin:core_orguser_change", args=[obj.user.id])
            return mark_safe(f'<a href="{url}">{obj.user.username}</a>')
        return "Не указан"

    user_display.short_description = "Пользователь"
    user_display.admin_order_field = 'user__username'

    def cars_count_display(self, obj):
        """Отображение количества машин со ссылкой на фильтр"""
        count = obj.car_set.count() if hasattr(obj, 'car_set') else 0
        if count > 0:
            url = (
                    reverse('admin:core_car_changelist')
                    + f'?list_id__id__exact={obj.id}'
            )
            return mark_safe(f'<a href="{url}">{count} машина(ы)</a>')
        return "0 машин"

    cars_count_display.short_description = "Количество машин"
    cars_count_display.admin_order_field = 'cars_count'

    def created_at_display(self, obj):
        """Отображение даты создания первой машины в списке"""
        if hasattr(obj, 'car_set') and obj.car_set.exists():
            first_car = obj.car_set.earliest('created_at')
            return first_car.created_at.strftime('%Y-%m-%d %H:%M')
        return "Нет машин в списке"

    created_at_display.short_description = "Первая машина добавлена"

    actions = [
        'export_selected',
        'delete_empty_lists'
    ]

    @admin.action(description='🗑️ Удалить пустые списки машин')
    def delete_empty_lists(self, request, queryset):
        """Удаление списков машин, в которых нет машин"""
        empty_lists = []
        for car_list in queryset:
            if not car_list.car_set.exists():
                empty_lists.append(car_list.name)
                car_list.delete()

        if empty_lists:
            self.message_user(
                request,
                f'Удалено {len(empty_lists)} пустых списков: {", ".join(empty_lists[:5])}'
                + ("..." if len(empty_lists) > 5 else ""),
                messages.SUCCESS
            )
        else:
            self.message_user(
                request,
                'Пустые списки не найдены',
                messages.INFO
            )

    def get_inlines(self, request, obj=None):
        """Показываем inline только при редактировании существующего объекта"""
        if obj:
            return [UserCarListCarInline]
        return []


class UserCarListCarInline(admin.TabularInline):
    """Inline для отображения машин в списке"""
    model = Car
    extra = 0
    fields = (
        'name',
        'id_in_provider_system',
        'car_unit_display',
        'is_active_display',
        'is_tarrified_display',
        'created_at'
    )
    readonly_fields = (
        'name',
        'id_in_provider_system',
        'car_unit_display',
        'is_active_display',
        'is_tarrified_display',
        'created_at'
    )
    verbose_name = "Машина в списке"
    verbose_name_plural = "Машины в списке"
    can_delete = False
    show_change_link = True


    def car_unit_display(self, obj):
        """Отображение юнита машины"""
        if obj.car_unit:
            return obj.car_unit.name
        return "Не указан"

    car_unit_display.short_description = "Подразделение"

    def is_active_display(self, obj):
        """Иконка активности"""
        if obj.is_active:
            return format_html('<span style="color: #00ff00;">● Активна</span>')
        return format_html('<span style="color: #cccccc;">○ Неактивна</span>')

    is_active_display.short_description = "Статус"

    def is_tarrified_display(self, obj):
        """Иконка тарирования"""
        if obj.is_tarrified:
            return format_html('<span style="color: #00ff00;">✓ Тарирована</span>')
        return format_html('<span style="color: #cccccc;">✗ Не тарирована</span>')

    is_tarrified_display.short_description = "Тарирование"

    def has_add_permission(self, request, obj):
        """Запрещаем добавление через inline"""
        return False


@admin.register(CarPrimary)
class CarPrimaryAdmin(ImportExportMixin, ModelAdmin):
    list_display = (
        'id',
        'car_display',
        'created_at',
        'data_preview',
        'has_data_display'
    )
    list_filter = ('created_at',)
    search_fields = ('car__name', 'car__id_in_provider_system')
    ordering = ('-created_at',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm
    readonly_fields = ('id', 'car_display', 'created_at', 'data_preview_full')

    fieldsets = (
        ('Основная информация', {
            'fields': ('id', 'car_display', 'created_at')
        }),
        ('JSON данные', {
            'fields': ('primary',),
            'classes': ('collapse',)
        }),
        ('Предпросмотр данных', {
            'fields': ('data_preview_full',),
            'classes': ('wide',)
        }),
    )

    def car_display(self, obj):
        """Отображение машины со ссылкой"""
        if obj.car:
            url = reverse("admin:core_car_change", args=[obj.car.id])
            return mark_safe(f'<a href="{url}">{obj.car.name}</a>')
        return "Не указана"

    car_display.short_description = "Автомобиль"
    car_display.admin_order_field = 'car__name'

    def data_preview(self, obj):
        """Краткий предпросмотр JSON данных в списке"""
        if obj.primary:
            import json
            data_str = json.dumps(obj.primary, ensure_ascii=False)
            if len(data_str) > 100:
                return data_str[:97] + '...'
            return data_str
        return "Нет данных"

    data_preview.short_description = "Данные (предпросмотр)"

    def data_preview_full(self, obj):
        """Полный предпросмотр JSON данных на странице редактирования"""
        if obj.primary:
            import json
            data_str = json.dumps(obj.primary, ensure_ascii=False, indent=2)
            return format_html(
                '<pre style="font-family: monospace; font-size: 12px; '
                'background-color: #000000; color: #00ff00; padding: 15px; '
                'border-radius: 3px; max-height: 500px; overflow-y: auto; '
                'line-height: 1.3; white-space: pre;">{}</pre>',
                data_str
            )
        return "Нет данных"

    data_preview_full.short_description = "Данные (полный просмотр)"

    def has_data_display(self, obj):
        """Иконка наличия данных"""
        if obj.primary:
            return format_html('<span style="color: #00ff00;">✓ Есть данные</span>')
        return format_html('<span style="color: #cccccc;">✗ Нет данных</span>')

    has_data_display.short_description = "Наличие данных"
    has_data_display.boolean = True

    actions = [
        'export_selected',
        'delete_empty_primary_data'
    ]

    @admin.action(description='🗑️ Удалить записи без данных')
    def delete_empty_primary_data(self, request, queryset):
        """Удаление записей с пустыми JSON данными"""
        empty_records = queryset.filter(primary__isnull=True) | queryset.filter(primary={})
        count = empty_records.count()

        if count > 0:
            empty_records.delete()
            self.message_user(
                request,
                f'Удалено {count} записей без данных',
                messages.SUCCESS
            )
        else:
            self.message_user(
                request,
                'Записей без данных не найдено',
                messages.INFO
            )

    def has_add_permission(self, request):
        """Запрещаем добавление через админку (данные создаются автоматически)"""
        return False

    def has_change_permission(self, request, obj=None):
        """Разрешаем редактирование только поля primary"""
        return True

    def get_readonly_fields(self, request, obj=None):
        """Делаем все поля кроме primary readonly"""
        if obj:
            return ['id', 'car_display', 'created_at', 'data_preview_full']
        return self.readonly_fields


@admin.register(CarUnit)
class CarUnitAdmin(ImportExportMixin, ModelAdmin):
    """Обновленный класс для CarUnit"""
    list_display = (
        'id',
        'name',
        'cars_count_display',
        'active_cars_display',
        'created_at_display',
        'updated_at_display'
    )
    list_filter = ('name',)
    search_fields = ('name',)
    ordering = ('name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm

    fields = ('name', 'description_display')

    readonly_fields = ('description_display',)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        # Добавляем аннотации для оптимизации
        from django.db.models import Count, Q
        return queryset.annotate(
            total_cars=Count('car', distinct=True),
            active_cars=Count('car', filter=Q(car__is_active=True), distinct=True)
        )

    def cars_count_display(self, obj):
        """Отображение общего количества машин со ссылкой"""
        count = obj.total_cars if hasattr(obj, 'total_cars') else obj.car_set.count()
        if count > 0:
            url = (
                    reverse('admin:core_car_changelist')
                    + f'?car_unit__id__exact={obj.id}'
            )
            return mark_safe(f'<a href="{url}">{count} машина(ы)</a>')
        return "0 машин"

    cars_count_display.short_description = "Всего машин"
    cars_count_display.admin_order_field = 'total_cars'

    def active_cars_display(self, obj):
        """Отображение количества активных машин"""
        count = obj.active_cars if hasattr(obj, 'active_cars') else obj.car_set.filter(is_active=True).count()
        if count > 0:
            url = (
                    reverse('admin:core_car_changelist')
                    + f'?car_unit__id__exact={obj.id}&is_active__exact=1'
            )
            return mark_safe(f'<a href="{url}" style="color: #00ff00;">{count} активных</a>')
        return "0 активных"

    active_cars_display.short_description = "Активных машин"
    active_cars_display.admin_order_field = 'active_cars'

    def created_at_display(self, obj):
        """Дата добавления первой машины"""
        if hasattr(obj, 'car_set') and obj.car_set.exists():
            first_car = obj.car_set.earliest('created_at')
            return first_car.created_at.strftime('%Y-%m-%d')
        return "Нет данных"

    created_at_display.short_description = "Первая машина"

    def updated_at_display(self, obj):
        """Дата последней обработки"""
        if hasattr(obj, 'car_set') and obj.car_set.exists():
            last_car = obj.car_set.order_by('-last_processed_date').first()
            if last_car and last_car.last_processed_date:
                return last_car.last_processed_date.strftime('%Y-%m-%d %H:%M')
        return "Нет данных"

    updated_at_display.short_description = "Последняя обработка"

    def description_display(self, obj):
        """Описание с статистикой"""
        total = obj.total_cars if hasattr(obj, 'total_cars') else obj.car_set.count()
        active = obj.active_cars if hasattr(obj, 'active_cars') else obj.car_set.filter(is_active=True).count()

        stats = f"""
        <div style="padding: 10px; background-color: #f5f5f5; border-radius: 4px;">
            <strong>Статистика подразделения:</strong><br>
            • Всего машин: {total}<br>
            • Активных: {active}<br>
            • Неактивных: {total - active}
        </div>
        """
        return mark_safe(stats)

    description_display.short_description = "Статистика"

    actions = [
        'export_selected',
        'merge_car_units',
        'delete_empty_units'
    ]

    @admin.action(description='🔀 Объединить выбранные подразделения')
    def merge_car_units(self, request, queryset):
        """Объединение нескольких подразделений в одно"""
        if queryset.count() < 2:
            self.message_user(
                request,
                'Для объединения нужно выбрать минимум 2 подразделения',
                messages.WARNING
            )
            return

        # Выбираем основное подразделение (первое по алфавиту)
        main_unit = queryset.order_by('name').first()
        other_units = queryset.exclude(id=main_unit.id)

        # Переносим все машины в основное подразделение
        moved_count = 0
        for unit in other_units:
            cars_to_move = unit.car_set.all()
            moved_count += cars_to_move.count()
            cars_to_move.update(car_unit=main_unit)

        # Удаляем пустые подразделения
        other_units.delete()

        self.message_user(
            request,
            f'Объединено {queryset.count()} подразделений в "{main_unit.name}". '
            f'Перемещено {moved_count} машин.',
            messages.SUCCESS
        )

    @admin.action(description='🗑️ Удалить пустые подразделения')
    def delete_empty_units(self, request, queryset):
        """Удаление подразделений без машин"""
        empty_units = []
        for unit in queryset:
            if not unit.car_set.exists():
                empty_units.append(unit.name)
                unit.delete()

        if empty_units:
            self.message_user(
                request,
                f'Удалено {len(empty_units)} пустых подразделений: {", ".join(empty_units[:5])}'
                + ("..." if len(empty_units) > 5 else ""),
                messages.SUCCESS
            )
        else:
            self.message_user(
                request,
                'Пустых подразделений не найдено',
                messages.INFO
            )

    def get_inlines(self, request, obj=None):
        """Показываем inline с машинами при редактировании"""
        if obj:
            return [CarUnitCarInline]
        return []


class CarUnitCarInline(admin.TabularInline):
    """Inline для отображения машин в подразделении"""
    model = Car
    extra = 0
    fields = (
        'name',
        'id_in_provider_system',
        'is_active_display',
        'is_tarrified_display',
        'last_processed_date',
        'user_car_list_display'
    )
    readonly_fields = (
        'name',
        'id_in_provider_system',
        'is_active_display',
        'is_tarrified_display',
        'last_processed_date',
        'user_car_list_display'
    )
    verbose_name = "Машина в подразделении"
    verbose_name_plural = "Машины в подразделении"
    can_delete = False
    show_change_link = True

    def get_queryset(self, request):
        """Только машины принадлежащие этому подразделению"""
        qs = super().get_queryset(request)
        return qs.filter(car_unit=self.parent_object.id)

    def is_active_display(self, obj):
        """Иконка активности"""
        if obj.is_active:
            return format_html('<span style="color: #00ff00;">● Активна</span>')
        return format_html('<span style="color: #cccccc;">○ Неактивна</span>')

    is_active_display.short_description = "Статус"

    def is_tarrified_display(self, obj):
        """Иконка тарирования"""
        if obj.is_tarrified:
            return format_html('<span style="color: #00ff00;">✓ Тарирована</span>')
        return format_html('<span style="color: #cccccc;">✗ Не тарирована</span>')

    is_tarrified_display.short_description = "Тарирование"

    def user_car_list_display(self, obj):
        """Отображение списка пользователя"""
        if obj.list_id:
            url = reverse("admin:core_usercarlist_change", args=[obj.list_id.id])
            return mark_safe(f'<a href="{url}">{obj.list_id.name}</a>')
        return "Не в списке"

    user_car_list_display.short_description = "Список пользователя"

    def has_add_permission(self, request, obj):
        """Запрещаем добавление через inline"""
        return False


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
    list_filter = ('is_active', 'auto_start', 'restart_on_failure')
    search_fields = ('name', 'service', 'description')
    ordering = ('name',)
    list_per_page = 25
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
        'status_details_display',
        'service_logs_display',
        'service_content_display',
        'actions_block',
        'file_info_display'
    )

    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'service', 'service_filename', 'description', 'is_active')
        }),
        ('Настройки службы', {
            'fields': ('auto_start', 'restart_on_failure'),
            'classes': ('collapse',)
        }),
        ('Файл службы', {
            'fields': ('file_info_display', 'service_content_display'),
        }),
        ('Статус службы', {
            'fields': ('status_display', 'status_details_display'),
        }),
        ('Логи службы (последние 100 записей)', {
            'fields': ('service_logs_display',),
            'classes': ('collapse',)
        }),
        ('Управление', {
            'fields': ('actions_block',),
        }),
    )

    def status_display(self, obj):
        """Отображение статуса службы"""
        status = obj.status
        if status == 'active':
            return format_html(
                '<span style="color: #00ff00; font-weight: bold;">● Активна</span>'
            )
        elif status == 'inactive':
            return format_html(
                '<span style="color: #cccccc;">○ Неактивна</span>'
            )
        elif status == 'failed':
            return format_html(
                '<span style="color: #ff0000;">✗ Ошибка</span>'
            )
        else:
            return format_html(
                f'<span style="color: #ff9900;">? {status}</span>'
            )

    status_display.short_description = "Статус"

    def status_details_display(self, obj):
        """Чистый текстовый вывод статуса службы"""
        details = obj.status_details
        if details:
            return format_html(
                '<pre style="font-family: monospace; font-size: 12px; '
                'background-color: #000000; color: #00ff00; padding: 10px; '
                'border-radius: 3px; max-height: 300px; overflow-y: auto; '
                'line-height: 1.3; white-space: pre;">{}</pre>',
                details
            )
        return "Статус недоступен"

    status_details_display.short_description = "Детали статуса (systemctl status)"

    def service_logs_display(self, obj):
        """Чистый текстовый вывод логов службы"""
        logs = obj.service_logs
        if logs:
            return format_html(
                '<pre style="font-family: monospace; font-size: 11px; '
                'background-color: #000000; color: #00ff00; padding: 10px; '
                'border-radius: 3px; max-height: 500px; overflow-y: auto; '
                'line-height: 1.2; white-space: pre;">{}</pre>',
                logs
            )
        return "Логи недоступны"

    service_logs_display.short_description = "Логи службы"

    def service_content_display(self, obj):
        """Чистый текстовый вывод содержимого файла службы"""
        content = obj.service_content
        if content:
            return format_html(
                '<pre style="font-family: monospace; font-size: 12px; '
                'background-color: #000000; color: #00ff00; padding: 10px; '
                'border-radius: 3px; max-height: 400px; overflow-y: auto; '
                'line-height: 1.3; white-space: pre;">{}</pre>',
                content
            )
        return "Файл не найден"

    service_content_display.short_description = "Содержимое файла службы"

    def autostart_display(self, obj):
        """Простое отображение статуса автозагрузки"""
        if obj.is_enabled:
            return format_html(
                '<span style="color: #00ff00;">● Вкл.</span>'
            )
        else:
            return format_html(
                '<span style="color: #cccccc;">○ Выкл.</span>'
            )

    autostart_display.short_description = "Автозагрузка"

    def file_exists_display(self, obj):
        """Простое отображение статуса файла"""
        if obj.service_file_exists:
            return format_html(
                '<span style="color: #00ff00;">✓ Файл</span>'
            )
        else:
            return format_html(
                '<span style="color: #ff0000;">✗ Файл</span>'
            )

    file_exists_display.short_description = "Файл"

    def file_info_display(self, obj):
        """Простая информация о файлах службы"""
        info = []

        if obj.service_file_exists:
            size = obj.service_file_path.stat().st_size
            info.append(
                f"<strong>Файл в проекте:</strong><br><code style='color: #00ff00;'>{obj.service_file_path}</code><br>Размер: {size} байт")
        else:
            info.append(
                f"<span style='color: #ff0000;'><strong>Файл не найден:</strong><br><code>{obj.service_file_path}</code></span>")

        info.append(
            f"<br><strong>Файл в systemd:</strong><br><code style='color: #00ff00;'>{obj.systemd_file_path}</code>")

        if os.path.exists(obj.systemd_file_path):
            info.append("<span style='color: #00ff00;'>● Установлен</span>")
        else:
            info.append("<span style='color: #ff9900;'>○ Не установлен</span>")

        return format_html('<br>'.join(info))

    file_info_display.short_description = "Информация о файлах"

    def actions_display(self, obj):
        """Кнопки действий в списке"""
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
                f'<a href="{url}" '
                f'style="display: inline-block; background: {color}; color: white; '
                f'padding: 3px 6px; margin: 0 1px; border-radius: 2px; '
                f'text-decoration: none; font-size: 11px; font-weight: bold; '
                f'min-width: 20px; text-align: center;">{label}</a>'
            )

        return format_html(''.join(buttons))

    actions_display.short_description = "Действия"

    def actions_block(self, obj):
        """Блок действий на странице редактирования"""
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
                f'<a href="{url}" '
                f'style="display: block; background: {color}; color: white; '
                f'padding: 10px; border-radius: 4px; text-decoration: none; '
                f'text-align: center; margin-bottom: 8px; font-weight: bold; '
                f'transition: opacity 0.2s;" '
                f'onmouseover="this.style.opacity=\'0.8\'" '
                f'onmouseout="this.style.opacity=\'1\'">{title}</a>'
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
        """Получить службу или вернуть 404"""
        try:
            return UnitService.objects.get(pk=pk)
        except UnitService.DoesNotExist:
            messages.error(self.request, "Служба не найдена") if self.request else None
            return None

    def install_service(self, request, pk):
        """Установка конкретной службы"""
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
        """Удаление конкретной службы"""
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
        """Перезапуск конкретной службы"""
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
        """Остановка конкретной службы"""
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
        """Запуск конкретной службы"""
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
        """Обновление конфигурации службы"""
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
        """Включение автозагрузки службы"""
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
        """Отключение автозагрузки службы"""
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
        results = []
        for unit in queryset:
            success, message = unit.install_service()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Установлено {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при установке: {failed_list}")

    @admin.action(description="🗑 Удалить выбранные службы")
    def uninstall_services(self, request, queryset):
        results = []
        for unit in queryset:
            success, message = unit.uninstall_service()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Удалено {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при удалении: {failed_list}")

    @admin.action(description="🔄 Перезапустить выбранные службы")
    def restart_services(self, request, queryset):
        results = []
        for unit in queryset:
            success, message = unit.restart()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Перезапущено {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при перезапуске: {failed_list}")

    @admin.action(description="⏹ Остановить выбранные службы")
    def stop_services(self, request, queryset):
        results = []
        for unit in queryset:
            success, message = unit.stop()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Остановлено {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при остановке: {failed_list}")

    @admin.action(description="▶ Запустить выбранные службы")
    def start_services(self, request, queryset):
        results = []
        for unit in queryset:
            success, message = unit.start()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Запущено {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при запуске: {failed_list}")

    @admin.action(description="📥 Обновить конфигурацию выбранных служб")
    def reload_services(self, request, queryset):
        results = []
        for unit in queryset:
            success, message = unit.reload()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Обновлено {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при обновлении: {failed_list}")

    @admin.action(description="✓ Включить автозагрузку выбранных служб")
    def enable_autostart(self, request, queryset):
        results = []
        for unit in queryset:
            success, message = unit.enable_autostart()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Включена автозагрузка для {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при включении автозагрузки: {failed_list}")

    @admin.action(description="✗ Отключить автозагрузку выбранных служб")
    def disable_autostart(self, request, queryset):
        results = []
        for unit in queryset:
            success, message = unit.disable_autostart()
            results.append((unit.name, success, message))

        success_count = sum(1 for _, success, _ in results if success)
        failed = [(name, msg) for name, success, msg in results if not success]

        if success_count:
            messages.success(request, f"Отключена автозагрузка для {success_count} служб")
        if failed:
            failed_list = ", ".join([f"{name}" for name, _ in failed[:3]])
            if len(failed) > 3:
                failed_list += f" и ещё {len(failed) - 3}"
            messages.error(request, f"Ошибка при отключении автозагрузки: {failed_list}")

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


