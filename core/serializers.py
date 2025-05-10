from rest_framework import serializers
from .models import Media, Organization, ReportQuery, OrgUser, Driver, CarReport, CarConsumption, Car, DriverCar, \
    DataProvider


class OrganizationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['id', 'name', 'bot_token', 'chat_id']


class CarOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Car
        fields = ['id', 'id_in_provider_system', 'name', 'description', 'engine_type', 'input', 'output', 'created_at']


class DriverOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = ['id', 'fullname', 'address', 'phone']


class CarReportOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarReport
        fields = ['id', 'car_id', 'datetime', 'volume', 'status']


class CarConsumptionOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarConsumption
        fields = ['id', 'car_id', 'winter_volume', 'summer_volume', 'valid_period']


class DriverCarOutputSerializer(serializers.ModelSerializer):
    driver_id = DriverOutputSerializer(read_only=True)
    car_id = CarOutputSerializer(read_only=True)

    class Meta:
        model = DriverCar
        fields = ['driver_id', 'car_id']



class DataProviderOutputSerializer(serializers.ModelSerializer):
    cars = CarOutputSerializer(many=True, read_only=True)

    class Meta:
        model = DataProvider
        fields = ['id', 'name', 'metadata', 'cars']


class MediaOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        fields = ['id', 'media_type', 'size', 'filename', 'type', 'file_hash']


class ReportQueryOutputSerializer(serializers.ModelSerializer):
    organization_id = OrganizationOutputSerializer(read_only=True)
    provider_id = DataProviderOutputSerializer(read_only=True)
    media = MediaOutputSerializer(read_only=True)
    cars = serializers.SerializerMethodField()
    car_reports = serializers.SerializerMethodField()
    car_consumptions = serializers.SerializerMethodField()
    driver_cars = serializers.SerializerMethodField()

    class Meta:
        model = ReportQuery
        fields = [
            'id', 'status', 'organization_id', 'provider_id', 'media',
            'cars', 'car_reports', 'car_consumptions', 'driver_cars'
        ]

    def get_cars(self, obj):
        cars = Car.objects.filter(data_providers=obj.provider_id)
        return CarOutputSerializer(cars, many=True).data

    def get_car_reports(self, obj):
        car_reports = CarReport.objects.filter(car_id__data_providers=obj.provider_id)
        return CarReportOutputSerializer(car_reports, many=True).data

    def get_car_consumptions(self, obj):
        car_consumptions = CarConsumption.objects.filter(car_id__data_providers=obj.provider_id)
        return CarConsumptionOutputSerializer(car_consumptions, many=True).data

    def get_driver_cars(self, obj):
        driver_cars = DriverCar.objects.filter(car_id__data_providers=obj.provider_id)
        return DriverCarOutputSerializer(driver_cars, many=True).data


class UserOutputSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    username = serializers.CharField()
    organization = OrganizationOutputSerializer(read_only=True, source='org')


class OrgUserOutputSerializer(serializers.ModelSerializer):
    org = OrganizationOutputSerializer(read_only=True)

    class Meta:
        model = OrgUser
        fields = ['id', 'username', 'org', 'email', 'first_name', 'last_name', 'is_active', 'is_staff', 'is_superuser',
                  'last_login', 'date_joined']


class AttachMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQuery
        fields = ['id', 'organization_id', 'provider_id', 'status']
        read_only_fields = ['id']

    def create(self, validated_data):
        report_query = ReportQuery.objects.create(**validated_data)
        return report_query


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    org_name = serializers.CharField(write_only=True)

    class Meta:
        model = OrgUser
        fields = ['id', 'username', 'password', 'org_name']
        read_only_fields = ['id']

    def create(self, validated_data):
        org_name = validated_data.pop('org_name')
        try:
            organization = Organization.objects.get(name=org_name)
        except Organization.DoesNotExist:
            raise serializers.ValidationError(f"Organization with name '{org_name}' not found.")
        user = OrgUser.objects.create_user(
            username=validated_data['username'],
            password=validated_data['password'],
            org=organization
        )
        return user


class CarMetricSerializer(serializers.Serializer):
    x = serializers.CharField(help_text="Дата")
    y = serializers.FloatField(help_text="Значение")
    metric = serializers.CharField(help_text="Тип метрики (fuel_level или speed)")


class CarMetricsQuerySerializer(serializers.Serializer):
    periodFrom = serializers.DateField(required=False, allow_null=True)
    periodDue = serializers.DateField(required=False, allow_null=True)
    car = serializers.UUIDField(required=True)
    agg = serializers.CharField(required=False, allow_null=True)
    func = serializers.ChoiceField(choices=['mean', 'median', 'sum'], default='mean')
    metric = serializers.ChoiceField(choices=['fuel_level', 'speed', 'both'], default='both')


class DailyLeaksSerializer(serializers.Serializer):
    periodFrom = serializers.DateField(required=False, allow_null=True)
    periodDue = serializers.DateField(required=False, allow_null=True)


class CarLeaksSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    label = serializers.CharField()
    value = serializers.FloatField()


class CarLeaksFilterSerializer(serializers.Serializer):
    car_id = serializers.UUIDField(required=True, help_text="ID автомобиля")
    periodFrom = serializers.DateField(required=False, allow_null=True, help_text="Начальная дата фильтрации")
    periodDue = serializers.DateField(required=False, allow_null=True, help_text="Конечная дата фильтрации")

class DataProviderSerializer(serializers.ModelSerializer):
    cars = serializers.PrimaryKeyRelatedField(
        queryset=Car.objects.all(),
        many=True,
        required=False,
        help_text="Список ID автомобилей, связанных с провайдером"
    )
    class Meta:
        model = DataProvider
        fields = ['name', 'metadata', 'cars']