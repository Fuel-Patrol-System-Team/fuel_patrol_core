from django.contrib import admin
from unfold.admin import ModelAdmin
from import_export.admin import ImportExportMixin

from core.models import (
    Organization, OrgUser, Car, CarReport, CarConsumption, Driver,
    Media, ReportQuery
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


class MediaInline(admin.StackedInline):  # ТЕСТОВАЯ ХУЙНЯ (можно Tabular/Stacked Inline)
    model = Media
    extra = 0
    fields = ('filename', 'media_type', 'size', 'type')
    readonly_fields = ('filename', 'media_type', 'size', 'type')


@admin.register(Organization)
class OrganizationAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'bot_token','chat_id')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(OrgUser)
class OrgUserAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'username', 'org','password')
    list_filter = ('org',)
    search_fields = ('username', 'org__name')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(Car)
class CarAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'name', 'description', 'organization')
    list_filter = ('organization',)
    search_fields = ('name', 'organization__name')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(CarConsumption)
class CarConsumptionAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car', 'winter_volume', 'summer_volume', 'valid_period')
    list_filter = ('valid_period',)
    search_fields = ('car__name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(CarReport)
class CarReportAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'car', 'datetime', 'volume', 'status')
    list_filter = ('datetime', 'status')
    search_fields = ('car__name',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(Driver)
class DriverAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'fullname', 'address', 'phone')
    search_fields = ('fullname', 'phone')
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(Media)
class MediaAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'filename', 'report_query', 'media_type', 'size', 'type')
    list_filter = ('type',)
    search_fields = ('filename',)
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(ReportQuery)
class ReportQueryAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('id', 'organization', 'status','flux_parsed')
    list_filter = ('status', 'organization')
    search_fields = ('organization__name', 'media__filename')
    inlines = [MediaInline]
    export_form_class = UnfoldExportForm
    import_form_class = UnfoldImportForm


@admin.register(PeriodicTask)
class PeriodicTaskAdmin(BasePeriodicTaskAdmin, ModelAdmin):
    form = UnfoldPeriodicTaskForm
    ordering = ('-enabled', 'name')


@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(ModelAdmin):
    pass


@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin):
    pass


@admin.register(SolarSchedule)
class SolarScheduleAdmin(ModelAdmin):
    pass


@admin.register(ClockedSchedule)
class ClockedScheduleAdmin(BaseClockedScheduleAdmin, ModelAdmin):
    pass
