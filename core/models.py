import uuid

from django.contrib.auth.models import User
from django.db import models

NULLABLE = {
    "blank": True,
    "null": True
}


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    bot_token = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class OrgUser(User):
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, null=NULLABLE)

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
