import os
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from core.services.notifications.tg_bot import logger

NULLABLE = {
    "blank": True,
    "null": True
}


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    bot_token = models.CharField(max_length=255, **NULLABLE)
    chat_id = models.CharField(max_length=255, **NULLABLE)

    class Meta:
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"
        ordering = ['name']

    def __str__(self):
        return self.name


class Language(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=5, default='ru')
    name = models.CharField(max_length=100, default='Русский')
    description = models.TextField(**NULLABLE)

    class Meta:
        verbose_name = "Language"
        verbose_name_plural = "Languages"
        ordering = ['code']

    def __str__(self):
        return self.name


class OrgUser(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    org = models.ForeignKey(Organization, on_delete=models.SET_NULL, **NULLABLE, related_name='users')
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
        ordering = ['username']

    def __str__(self):
        return self.username


class Car(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    id_in_provider_system = models.IntegerField(default=0)
    name = models.CharField(max_length=255)
    description = models.TextField()
    engine_type = models.FloatField(default=0.0)
    input = models.FloatField(**NULLABLE)
    output = models.FloatField(**NULLABLE)
    created_at = models.DateTimeField(default=timezone.now)
    last_processed_date = models.DateTimeField(**NULLABLE)
    is_tarrified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Car"
        verbose_name_plural = "Cars"
        ordering = ['name']

    def __str__(self):
        return self.name


class CarConsumption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='consumptions')
    winter_volume = models.FloatField(**NULLABLE)
    summer_volume = models.FloatField(**NULLABLE)
    speed_etalon = models.FloatField(default=60.0)
    max_fuel = models.FloatField(default=2000.0)
    valid_period = models.DateField(**NULLABLE)

    class Meta:
        verbose_name = "Car Consumption"
        verbose_name_plural = "Car Consumptions"
        ordering = ['car_id']

    def __str__(self):
        return f"{self.car_id.name} Consumption"


class DataProvider(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    metadata = models.JSONField(**NULLABLE)
    org_id = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='organization', **NULLABLE)
    cars = models.ManyToManyField(Car, related_name='data_providers', blank=True)

    class Meta:
        verbose_name = "Data Provider"
        verbose_name_plural = "Data Providers"
        ordering = ['name']

    def __str__(self):
        return self.name


class ReportQuery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=50, **NULLABLE)
    provider_id = models.ForeignKey(DataProvider, on_delete=models.CASCADE, related_name='report_queries')
    is_save_bad_data = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Report Query"
        verbose_name_plural = "Report Queries"
        ordering = ['-id']

    def __str__(self):
        return f"Report {self.id}"


class Media(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media_type = models.CharField(max_length=255, **NULLABLE)
    file = models.FileField(**NULLABLE, upload_to='')
    size = models.BigIntegerField(default=0, **NULLABLE)
    filename = models.CharField(max_length=255)
    type = models.CharField(max_length=255, **NULLABLE)
    report_query_id = models.OneToOneField(ReportQuery, on_delete=models.CASCADE, **NULLABLE, related_name='media')
    file_hash = models.CharField(max_length=64, unique=True, **NULLABLE)

    class Meta:
        verbose_name = "Media"
        verbose_name_plural = "Media Files"
        ordering = ['filename']

    def __str__(self):
        return self.filename

    def save(self, *args, **kwargs):
        from core.helpers.media_utils import calculate_file_hash
        if self.file:
            self.size = self.file.size
            self.file_hash = calculate_file_hash(self.file)
        super().save(*args, **kwargs)


class CarReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='reports')
    datetime = models.DateTimeField()
    volume = models.IntegerField()
    status = models.BooleanField()

    class Meta:
        verbose_name = "Car Report"
        verbose_name_plural = "Car Reports"
        ordering = ['-datetime']

    def __str__(self):
        return f"{self.car_id.name} - {self.datetime}"


class CarBadData(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='bad_data')
    reason = models.TextField()
    datetime = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Car Bad Data"
        verbose_name_plural = "Car Bad Data`s"
        ordering = ['-id']

    def __str__(self):
        return f"{self.car_id.name} - {self.datetime} - {self.reason}"


class Driver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fullname = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=255)
    car_id = models.ManyToManyField(Car, blank=True, related_name='drivers')

    class Meta:
        verbose_name = "Driver"
        verbose_name_plural = "Drivers"
        ordering = ['fullname']

    def __str__(self):
        return self.fullname


@receiver(post_delete, sender=Media)
def delete_media_file(sender, instance, **kwargs):
    if instance.file and os.path.isfile(instance.file.path):
        logger.info(f"Deleting file: {instance.file.path}")
        os.remove(instance.file.path)


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
    key = models.ForeignKey(SensorsKey, on_delete=models.CASCADE, related_name='values')
    value = models.CharField(max_length=255)
    car_id = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='values')

    class Meta:
        verbose_name = "SensorsValues"
        verbose_name_plural = "SensorsValues"
        ordering = ['key']

    def __str__(self):
        return f"{self.key} - {self.value}"


class SensorsKeyLocalization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.ForeignKey(SensorsKey, on_delete=models.CASCADE, related_name='locations')
    language = models.ForeignKey(Language, on_delete=models.CASCADE, related_name='locations')
    localization = models.CharField(max_length=255)

    class Meta:
        verbose_name = "SensorsKeyLocalization"
        verbose_name_plural = "SensorsKeyLocalizations"
        ordering = ['key']

    def __str__(self):
        return f"{self.key} - {self.language} - {self.localization}"
