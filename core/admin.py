from django.contrib import admin

from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import UnfoldAdminTextInputWidget, UnfoldAdminSelectWidget

from core.models import Organization, OrgUser, Car, CarReport, CarConsumption, Driver
from import_export.admin import ImportExportMixin

from django_celery_beat.models import (
    ClockedSchedule,
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    SolarSchedule,
)

from django_celery_beat.admin import ClockedScheduleAdmin as BaseClockedScheduleAdmin
from django_celery_beat.admin import CrontabScheduleAdmin as BaseCrontabScheduleAdmin
from django_celery_beat.admin import PeriodicTaskAdmin as BasePeriodicTaskAdmin
from django_celery_beat.admin import PeriodicTaskForm, TaskSelectWidget

admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)


@admin.register(Organization)
class OrganizationAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('name', 'bot_token')


@admin.register(OrgUser)
class OrgUserAdmin(ImportExportMixin, ModelAdmin):
    list_display = ('username', 'org')


@admin.register(Car)
class CarAdmin(ImportExportMixin, admin.ModelAdmin):
    list_display = ('name', 'description', 'organization')


@admin.register(CarConsumption)
class CarConsumptionAdmin(ImportExportMixin, admin.ModelAdmin):
    list_display = ('car', 'winter_volume', 'summer_volume', 'valid_period')


@admin.register(CarReport)
class CarReportAdmin(ImportExportMixin, admin.ModelAdmin):
    list_display = ('car', 'datetime', 'volume', 'status')


@admin.register(Driver)
class DriverAdmin(ImportExportMixin, admin.ModelAdmin):
    list_display = ('fullname', 'address', 'phone')


class UnfoldTaskSelectWidget(UnfoldAdminSelectWidget, TaskSelectWidget): pass


class UnfoldPeriodicTaskForm(PeriodicTaskForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task"].widget = UnfoldAdminTextInputWidget()
        self.fields["regtask"].widget = UnfoldTaskSelectWidget()


@admin.register(PeriodicTask)
class PeriodicTaskAdmin(BasePeriodicTaskAdmin, ModelAdmin):
    form = UnfoldPeriodicTaskForm
    ordering = ('-enabled', 'name')


@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(ModelAdmin): pass


@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin): pass


@admin.register(SolarSchedule)
class SolarScheduleAdmin(ModelAdmin): pass


@admin.register(ClockedSchedule)
class ClockedScheduleAdmin(BaseClockedScheduleAdmin, ModelAdmin): pass
