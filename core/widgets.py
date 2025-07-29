from django_celery_beat.admin import TaskSelectWidget, PeriodicTaskForm

from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from import_export.formats.base_formats import CSV, JSON, XLSX, XLS
from import_export.forms import ImportForm, ExportForm
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminFileFieldWidget

CUSTOM_FORMATS = [CSV, JSON, XLSX, XLS]


class UnfoldImportForm(ImportForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["import_file"].widget = UnfoldAdminFileFieldWidget()

        # Создаём кортежи (значение, метка)
        format_choices = [(str(i), f.__name__) for i, f in enumerate(CUSTOM_FORMATS)]
        self.fields["format"].widget = UnfoldAdminSelectWidget(choices=format_choices)


class UnfoldExportForm(ExportForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        format_choices = [(str(i), f.__name__) for i, f in enumerate(CUSTOM_FORMATS)]
        self.fields["format"].widget = UnfoldAdminSelectWidget(choices=format_choices)


class UnfoldTaskSelectWidget(UnfoldAdminSelectWidget, TaskSelectWidget):
    pass


class UnfoldPeriodicTaskForm(PeriodicTaskForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task"].widget = UnfoldAdminTextInputWidget()
        self.fields["regtask"].widget = UnfoldTaskSelectWidget()
