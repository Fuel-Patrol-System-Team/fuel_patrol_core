import os
import uuid

from django.contrib.auth.models import User, AbstractUser
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from core.services.media.utils import calculate_file_hash

NULLABLE = {
    "blank": True,
    "null": True
}


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    name = models.CharField(max_length=100, verbose_name="Название")
    bot_token = models.CharField(max_length=255, **NULLABLE, verbose_name="Токен бота")
    chat_id = models.CharField(max_length=255, **NULLABLE, verbose_name="ID чата")

    class Meta:
        verbose_name = "Организация"
        verbose_name_plural = "Организации"
        ordering = ['name']
        indexes = [
            models.Index(fields=['name'], name='idx_organization_name'),
        ]

    def __str__(self):
        return self.name



class OrgUser(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, verbose_name="Организация")

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='orguser_groups',
        blank=True,
        verbose_name="Группы"
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='orguser_permissions',
        blank=True,
        verbose_name="Разрешения"
    )

    class Meta:
        verbose_name = "Пользователь организации"
        verbose_name_plural = "Пользователи организаций"
        ordering = ['username']
        indexes = [
            models.Index(fields=['username', 'org'], name='idx_orguser_username_org'),
        ]

    def __str__(self):
        return str(self.id)


class Car(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    name = models.CharField(max_length=100, verbose_name="Название")
    description = models.TextField(verbose_name="Описание")
    engine_type = models.FloatField(default=0.0)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, verbose_name="Организация")

    class Meta:
        verbose_name = "Автомобиль"
        verbose_name_plural = "Автомобили"
        ordering = ['name']
        indexes = [
            models.Index(fields=['name', 'organization'], name='idx_car_name_org'),
            models.Index(fields=['id'], name='idx_car_id'),
        ]

    def __str__(self):
        return self.name


class CarConsumption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    car = models.ForeignKey(Car, on_delete=models.CASCADE, verbose_name="Автомобиль")
    winter_volume = models.FloatField(**NULLABLE, verbose_name="Зимний расход")
    summer_volume = models.FloatField(**NULLABLE, verbose_name="Летний расход")
    valid_period = models.DateField(**NULLABLE, verbose_name="Период действия")

    class Meta:
        verbose_name = "Расход топлива"
        verbose_name_plural = "Расходы топлива"
        ordering = ['car__name', 'valid_period']
        indexes = [
            models.Index(fields=['car', 'valid_period'], name='idx_carconsumption_car_period'),
        ]

    def __str__(self):
        return self.car.name


QUERY_STATUS = [
    ("pending", "В обработке"),
    ("completed", "Завершено"),
    ("error", "Ошибка")
]

class ReportQuery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, verbose_name="Организация")
    status = models.CharField(max_length=50, choices=QUERY_STATUS, **NULLABLE, verbose_name="Статус")


    class Meta:
        verbose_name = "Запрос отчёта"
        verbose_name_plural = "Запросы отчётов"
        ordering = ['-id']
        indexes = [
            models.Index(fields=['organization', 'status'], name='idx_reportquery_org_status'),
        ]

    def __str__(self):
        return self.organization.name



MEDIA_TYPE = [
    ("raw", "Сырые данные"),
    ("auto_data", "Данные об автомобилях")
]

class Media(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    media_type = models.CharField(max_length=256, **NULLABLE, verbose_name="Тип медиа")
    file = models.FileField(**NULLABLE, upload_to='media/', verbose_name="Файл")
    size = models.IntegerField(default=0, **NULLABLE, verbose_name="Размер")
    filename = models.CharField(max_length=255, verbose_name="Имя файла")
    type = models.CharField(max_length=50, **NULLABLE, choices=MEDIA_TYPE, verbose_name="Тип файла")
    report_query = models.OneToOneField(ReportQuery, on_delete=models.CASCADE, **NULLABLE, verbose_name="Запрос отчёта")
    file_hash = models.CharField(max_length=64, unique=True, **NULLABLE, verbose_name="Хэш файла")

    class Meta:
        verbose_name = "Медиафайл"
        verbose_name_plural = "Медиафайлы"
        ordering = ['filename']
        indexes = [
            models.Index(fields=['type', 'report_query'], name='idx_media_type_query'),
        ]

    def __str__(self):
        return self.filename

    def save(self, *args, **kwargs):
        if self.file:
            self.size = self.file.size
            self.file_hash = calculate_file_hash(self.file)
        super().save(*args, **kwargs)


class CarReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    car = models.ForeignKey(Car, on_delete=models.CASCADE, verbose_name="Автомобиль")
    datetime = models.DateTimeField(verbose_name="Дата и время")
    volume = models.IntegerField(verbose_name="Объём")
    status = models.BooleanField(verbose_name="Статус утечки")

    class Meta:
        verbose_name = "Отчёт об автомобиле"
        verbose_name_plural = "Отчёты об автомобилях"
        ordering = ['-datetime']
        indexes = [
            models.Index(fields=['car', 'datetime'], name='idx_carreport_car_datetime'),
        ]

    def __str__(self):
        return self.car.name



class Driver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор")
    car = models.ManyToManyField(Car, verbose_name="Автомобили")
    fullname = models.CharField(max_length=255, verbose_name="ФИО")
    address = models.CharField(max_length=255, verbose_name="Адрес")
    phone = models.CharField(max_length=255, verbose_name="Телефон")

    class Meta:
        verbose_name = "Водитель"
        verbose_name_plural = "Водители"
        ordering = ['fullname']
        indexes = [
            models.Index(fields=['fullname', 'phone'], name='idx_driver_fullname_phone'),
        ]

    def __str__(self):
        return self.fullname


@receiver(post_delete, sender=Media)
def delete_media_file(sender, instance, **kwargs):
    """
    Удаляет файл медиа после удаления записи из базы данных.
    """
    if instance.file and os.path.isfile(instance.file.path):
        os.remove(instance.file.path)