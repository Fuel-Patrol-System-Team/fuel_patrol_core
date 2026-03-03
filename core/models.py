import os
import subprocess
import uuid
from pathlib import Path

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

NULLABLE = {"blank": True, "null": True}


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    bot_token = models.CharField(max_length=255, **NULLABLE)
    chat_id = models.CharField(max_length=255, **NULLABLE)

    class Meta:
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Language(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=5, default="ru")
    name = models.CharField(max_length=100, default="Русский")
    description = models.TextField(**NULLABLE)

    class Meta:
        verbose_name = "Language"
        verbose_name_plural = "Languages"
        ordering = ["code"]

    def __str__(self):
        return self.name


class OrgUser(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    org = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, **NULLABLE, related_name="users"
    )
    password = models.CharField(max_length=128)
    email = models.EmailField(max_length=254, **NULLABLE)
    first_name = models.CharField(max_length=30, **NULLABLE)
    last_name = models.CharField(max_length=150, **NULLABLE)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    last_login = models.DateTimeField(**NULLABLE)
    date_joined = models.DateTimeField(auto_now_add=True)
    active_language = models.ForeignKey(Language, on_delete=models.SET_NULL, **NULLABLE)

    class Meta:
        verbose_name = "Org User"
        verbose_name_plural = "Org Users"
        ordering = ["username"]

    def __str__(self):
        return self.username

class CoreNotification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    target = models.ForeignKey(
        "OrgUser",
        on_delete=models.CASCADE,
        related_name="core_notifications"
    )

    type = models.CharField(max_length=50)

    message = models.TextField()

    url = models.CharField(
        max_length=256,
        blank=True,
        null=True
    )

    read_at = models.DateTimeField(
        blank=True,
        null=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        db_table = "core_notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["target", "read_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.target} - {self.type}"

class Car(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    id_in_provider_system = models.IntegerField(default=0)
    car_unit = models.ForeignKey("CarUnit", on_delete=models.SET_NULL, **NULLABLE)
    name = models.CharField(max_length=255)
    description = models.TextField()
    engine_type = models.FloatField(default=0.0)
    input = models.FloatField(**NULLABLE)
    output = models.FloatField(**NULLABLE)
    created_at = models.DateTimeField(default=timezone.now)
    last_processed_date = models.DateTimeField(**NULLABLE)
    is_tarrified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    list_id = models.ForeignKey("UserCarList", on_delete=models.SET_NULL, **NULLABLE)

    class Meta:
        verbose_name = "Car"
        verbose_name_plural = "Cars"
        ordering = ["name"]

    def __str__(self):
        return self.name



class CarUnit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Car Unit"
        verbose_name_plural = "Car Units"
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserCarList(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    user = models.ForeignKey(OrgUser, on_delete=models.SET_NULL, **NULLABLE)


class CarPrimary(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car = models.OneToOneField(Car, on_delete=models.SET_NULL, **NULLABLE)
    primary = models.JSONField(**NULLABLE)
    created_at = models.DateTimeField(default=timezone.now)


class CarConsumption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car_id = models.ForeignKey(
        Car, on_delete=models.CASCADE, related_name="consumptions"
    )
    winter_volume = models.FloatField(**NULLABLE)
    summer_volume = models.FloatField(**NULLABLE)
    speed_etalon = models.FloatField(default=60.0)
    max_fuel = models.FloatField(default=2000.0)
    valid_period = models.DateField(**NULLABLE)

    class Meta:
        verbose_name = "Car Consumption"
        verbose_name_plural = "Car Consumptions"
        ordering = ["car_id"]

    def __str__(self):
        return f"{self.car_id.name} Consumption"


class DataProvider(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    metadata = models.JSONField(**NULLABLE)
    org_id = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="organization", **NULLABLE
    )
    cars = models.ManyToManyField(Car, related_name="data_providers", blank=True)

    class Meta:
        verbose_name = "Data Provider"
        verbose_name_plural = "Data Providers"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ReportQuery(models.Model):
    class ReportType(models.TextChoices):
        VEHICLES = "vehicles", "Синхронизация транспортных средств"
        MILEAGE = "mileage", "Анализ пробега"
        MOTOHOURS = "motohours", "Анализ моточасов"
        LEAKS = "leaks", "Анализ утечек топлива"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=50, **NULLABLE)
    provider_id = models.ForeignKey(
        DataProvider, on_delete=models.CASCADE, related_name="report_queries"
    )
    is_save_bad_data = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    report_type = models.CharField(
        max_length=20, choices=ReportType.choices, default=ReportType.LEAKS
    )

    class Meta:
        verbose_name = "Report Query"
        verbose_name_plural = "Report Queries"
        ordering = ["-id"]

    def __str__(self):
        return f"Report {self.id}"


class ReportQueryDetails(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report_query = models.OneToOneField(
        ReportQuery, on_delete=models.CASCADE, related_name="report_query_details"
    )
    traceback = models.JSONField(**NULLABLE)
    result = models.JSONField(**NULLABLE)
    start_time = models.DateTimeField(**NULLABLE)
    end_time = models.DateTimeField(**NULLABLE)
    time_proceed = models.DurationField(**NULLABLE)
    cars_proceed = models.IntegerField(**NULLABLE)
    cars_skipped = models.IntegerField(**NULLABLE)

    class Meta:
        verbose_name = "Report Query Details"
        verbose_name_plural = "Report Query Details"
        ordering = ["-id"]

    def __str__(self):
        return f"Report {self.report_query} Details {self.id}"


class CarReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="reports")
    datetime = models.DateTimeField()
    speed = models.FloatField(null=True)
    volume = models.IntegerField()
    status = models.BooleanField()

    class Meta:
        verbose_name = "Car Report"
        verbose_name_plural = "Car Reports"
        ordering = ["-datetime"]

    def __str__(self):
        return f"{self.car_id.name} - {self.datetime}"

class CarMileageReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="mileage_reports")
    datetime = models.DateTimeField()
    mileage_start = models.FloatField(**NULLABLE)
    mileage_end = models.FloatField(**NULLABLE)
    fraud = models.FloatField(**NULLABLE)

    class Meta:
        verbose_name = "Car Mileage Report"
        verbose_name_plural = "Car Mileage Reports"
        ordering = ["-datetime"]

    def __str__(self):
        return f"{self.car_id.name} - {self.datetime}"

class CarBadData(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="bad_data")
    reason = models.TextField()
    datetime = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Car Bad Data"
        verbose_name_plural = "Car Bad Data`s"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.car_id.name} - {self.datetime} - {self.reason}"


class Driver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fullname = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=255)
    car_id = models.ManyToManyField(Car, blank=True, related_name="drivers")

    class Meta:
        verbose_name = "Driver"
        verbose_name_plural = "Drivers"
        ordering = ["fullname"]

    def __str__(self):
        return self.fullname


class SensorsKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=64)

    class Meta:
        verbose_name = "SensorsKey"
        verbose_name_plural = "SensorsKeys"

    def __str__(self):
        return self.key


class SensorsValues(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.ForeignKey(SensorsKey, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=255)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="values")

    class Meta:
        verbose_name = "SensorsValues"
        verbose_name_plural = "SensorsValues"
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} - {self.value}"


class SensorsKeyLocalization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.ForeignKey(
        SensorsKey, on_delete=models.CASCADE, related_name="locations"
    )
    language = models.ForeignKey(
        Language, on_delete=models.CASCADE, related_name="locations"
    )
    localization = models.CharField(max_length=255)

    class Meta:
        verbose_name = "SensorsKeyLocalization"
        verbose_name_plural = "SensorsKeyLocalizations"
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} - {self.language} - {self.localization}"


## DEVOPS FEATURES
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = PROJECT_ROOT / "services"


class UnitService(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, verbose_name="Название")
    service = models.CharField(
        max_length=100,
        verbose_name="Имя службы systemd",
        unique=True,
        help_text="Например: core-api, core-celery-worker",
    )
    description = models.TextField(verbose_name="Описание", **NULLABLE)
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    service_filename = models.CharField(
        max_length=255,
        verbose_name="Имя файла службы",
        help_text="Имя файла в папке services/ (например: core-api.service)",
        **NULLABLE,
    )
    auto_start = models.BooleanField(
        default=True,
        verbose_name="Автозагрузка",
        help_text="Автоматически запускать службу при загрузке системы",
    )
    restart_on_failure = models.BooleanField(
        default=True,
        verbose_name="Перезапуск при ошибке",
        help_text="Автоматически перезапускать службу при сбое",
    )

    class Meta:
        verbose_name = "Служба Systemd"
        verbose_name_plural = "Службы Systemd"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.service})"

    @property
    def service_file_path(self):
        """Путь к файлу службы в проекте"""
        if self.service_filename:
            return SERVICES_DIR / self.service_filename
        return SERVICES_DIR / f"{self.service}"

    @property
    def systemd_file_path(self):
        """Путь к файлу службы в systemd"""
        return f"/etc/systemd/system/{self.service}"

    @property
    def service_file_exists(self):
        """Проверка существования файла службы в проекте"""
        return self.service_file_path.exists()

    @property
    def service_content(self):
        """Содержимое файла службы"""
        try:
            if self.service_file_exists:
                return self.service_file_path.read_text()
        except Exception:
            pass
        return None

    @property
    def status(self):
        """Получение чистого статуса службы"""
        try:
            result = subprocess.run(
                f"systemctl is-active {self.service}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"

    @property
    def status_details(self):
        """Детальный статус службы (как в systemctl status)"""
        try:
            result = subprocess.run(
                f"systemctl status {self.service} --no-pager",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout if result.stdout else result.stderr
        except subprocess.TimeoutExpired:
            return "Таймаут при получении статуса"
        except Exception as e:
            return f"Ошибка: {str(e)}"

    @property
    def service_logs(self):
        """Получение последних логов службы (100 записей)"""
        try:
            result = subprocess.run(
                f"journalctl -u {self.service} -n 100 --no-pager",
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout if result.stdout else result.stderr
        except subprocess.TimeoutExpired:
            return "Таймаут при получении логов"
        except Exception as e:
            return f"Ошибка: {str(e)}"

    @property
    def is_running(self):
        """Проверка, запущена ли служба"""
        return self.status == "active"

    @property
    def is_enabled(self):
        """Проверка, включена ли автозагрузка"""
        try:
            result = subprocess.run(
                f"systemctl is-enabled {self.service}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.stdout.strip() == "enabled"
        except Exception:
            return False

    def clean(self):
        """Валидация"""
        super().clean()

        if not self.service.endswith(".service"):
            self.service = f"{self.service}.service"

    def install_service(self):
        """Установка службы в systemd"""
        if not self.service_file_exists:
            return False, f"Файл службы не найден: {self.service_file_path}"

        try:
            content = self.service_file_path.read_text()

            with open(self.systemd_file_path, "w") as f:
                f.write(content)

            subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=5)

            if self.auto_start:
                subprocess.run(
                    ["systemctl", "enable", self.service], check=True, timeout=5
                )

            return True, f"Служба установлена"

        except Exception as e:
            return False, f"Ошибка: {str(e)}"

    def uninstall_service(self):
        """Удаление службы из systemd"""
        try:
            subprocess.run(
                ["systemctl", "disable", self.service],
                capture_output=True,
                text=True,
                timeout=5,
            )

            subprocess.run(
                ["systemctl", "stop", self.service],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if os.path.exists(self.systemd_file_path):
                os.remove(self.systemd_file_path)

            subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=5)

            return True, f"Служба удалена"

        except Exception as e:
            return False, f"Ошибка: {str(e)}"

    def restart(self):
        """Перезапуск службы"""
        try:
            if not os.path.exists(self.systemd_file_path):
                success, message = self.install_service()
                if not success:
                    return False, f"Не удалось установить: {message}"

            result = subprocess.run(
                f"systemctl restart {self.service}",
                shell=True,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return True, "Служба перезапущена"
        except subprocess.CalledProcessError as e:
            return False, e.stderr or "Ошибка перезапуска"
        except Exception as e:
            return False, str(e)

    def stop(self):
        """Остановка службы"""
        try:
            result = subprocess.run(
                f"systemctl stop {self.service}",
                shell=True,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return True, "Служба остановлена"
        except subprocess.CalledProcessError as e:
            return False, e.stderr or "Ошибка остановки"
        except Exception as e:
            return False, str(e)

    def start(self):
        """Запуск службы"""
        try:
            if not os.path.exists(self.systemd_file_path):
                success, message = self.install_service()
                if not success:
                    return False, f"Не удалось установить: {message}"

            result = subprocess.run(
                f"systemctl start {self.service}",
                shell=True,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return True, "Служба запущена"
        except subprocess.CalledProcessError as e:
            return False, e.stderr or "Ошибка запуска"
        except Exception as e:
            return False, str(e)

    def reload(self):
        """Обновление конфигурации службы"""
        try:
            if self.service_file_exists:
                content = self.service_file_path.read_text()
                with open(self.systemd_file_path, "w") as f:
                    f.write(content)

            subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=5)

            if self.is_running:
                result = subprocess.run(
                    f"systemctl restart {self.service}",
                    shell=True,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                return True, "Конфигурация обновлена и служба перезапущена"
            else:
                return True, "Конфигурация обновлена"

        except Exception as e:
            return False, str(e)

    def enable_autostart(self):
        """Включение автозагрузки"""
        try:
            result = subprocess.run(
                f"systemctl enable {self.service}",
                shell=True,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            self.auto_start = True
            self.save()
            return True, "Автозагрузка включена"
        except Exception as e:
            return False, str(e)

    def disable_autostart(self):
        """Отключение автозагрузки"""
        try:
            result = subprocess.run(
                f"systemctl disable {self.service}",
                shell=True,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            self.auto_start = False
            self.save()
            return True, "Автозагрузка отключена"
        except Exception as e:
            return False, str(e)
