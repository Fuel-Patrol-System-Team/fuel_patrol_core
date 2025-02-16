import os
import uuid
from email.policy import default

from django.contrib.auth.models import User, AbstractUser
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

NULLABLE = {
    "blank": True,
    "null": True
}


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    bot_token = models.CharField(max_length=255, **NULLABLE)
    chat_id = models.CharField(max_length=255, **NULLABLE)

    def __str__(self):
        return self.name


class OrgUser(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True)

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='orguser_groups',
        blank=True
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='orguser_permissions',
        blank=True
    )

    def __str__(self):
        return self.username


class Car(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField()
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


class CarConsumption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car = models.ForeignKey(Car, on_delete=models.CASCADE)
    winter_volume = models.FloatField(**NULLABLE)
    summer_volume = models.FloatField(**NULLABLE)
    valid_period = models.DateField(**NULLABLE)

    def __str__(self):
        return self.car.name


QUERY_STATUS = [
    ("pending", "pending"),
    ("completed", "completed"),
    ("error", "error")
]


class ReportQuery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    status = models.CharField(max_length=50, choices=QUERY_STATUS, **NULLABLE)
    flux_parsed = models.BooleanField(default=False)

    def __str__(self):
        return self.organization.name


MEDIA_TYPE = [
    ("norm", "norm"),
    ("raw", "raw"),
    ("auto", "auto")
]


class Media(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media_type = models.CharField(max_length=50, **NULLABLE)
    file = models.FileField(**NULLABLE)
    size = models.IntegerField(default=0, **NULLABLE)
    filename = models.CharField(max_length=255)
    type = models.CharField(max_length=50, **NULLABLE, choices=MEDIA_TYPE)
    report_query = models.ForeignKey(ReportQuery, **NULLABLE, on_delete=models.CASCADE)

    def __str__(self):
        return self.filename

    def save(self, *args, **kwargs):
        if self.file:
            self.size = self.file.size
        super().save(*args, **kwargs)


class CarReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car = models.ForeignKey(Car, on_delete=models.CASCADE)
    datetime = models.DateTimeField()
    volume = models.IntegerField()
    status = models.BooleanField()

    def __str__(self):
        return self.car.name


class Driver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car = models.ManyToManyField(Car)
    fullname = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=255)

    def __str__(self):
        return self.fullname


@receiver(post_delete, sender=Media)
def delete_media_file(sender, instance, **kwargs):
    if instance.file:
        if os.path.isfile(instance.file.path):
            os.remove(instance.file.path)
