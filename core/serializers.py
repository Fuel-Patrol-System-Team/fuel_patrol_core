from rest_framework import serializers
from .models import Media, Organization, ReportQuery, OrgUser, Driver, CarReport, CarConsumption, Car, DataProvider, \
    CarBadData, SensorsMapping


class OrganizationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['id', 'name', 'bot_token', 'chat_id']


class CarOutputSerializer(serializers.ModelSerializer):
    bad_data_count = serializers.IntegerField(read_only=True)
    class Meta:
        model = Car
        fields = ['id', 'id_in_provider_system', 'name', 'description', 'engine_type', 'input', 'output',
                  'is_tarrified', 'is_active', 'created_at', 'bad_data_count']


class DriverOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(many=True, read_only=True)

    class Meta:
        model = Driver
        fields = ['id', 'fullname', 'address', 'phone', 'car_id']


class CarReportOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarReport
        fields = ['id', 'car_id', 'datetime', 'volume', 'status']


class CarConsumptionOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarConsumption
        fields = ['id', 'car_id', 'winter_volume', 'summer_volume', 'valid_period', 'speed_etalon', 'max_fuel']


class DataProviderOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataProvider
        fields = ['id', 'name', 'metadata']


class MediaOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        fields = ['id', 'media_type', 'size', 'filename', 'type', 'file_hash']


class ReportQueryOutputSerializer(serializers.ModelSerializer):
    provider_name = serializers.SerializerMethodField()

    class Meta:
        model = ReportQuery
        fields = [
            'id', 'status', 'provider_name'
        ]

    def get_provider_name(self, obj):
        return obj.provider_id.name


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


class CarBadDataOutputSerializer(serializers.ModelSerializer):
    car_name = serializers.CharField(source='car_id.name', read_only=True)

    class Meta:
        model = CarBadData
        fields = ['id', 'car_name', 'reason', 'datetime']

class SensorsMappingOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = SensorsMapping
        fields = ['id', 'label', 'value', 'car_id']


class AttachMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQuery
        fields = ['id', 'provider_id', 'status']
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
    periodFrom = serializers.DateTimeField(
        required=False, allow_null=True, help_text="Начальная дата и время фильтрации"
    )
    periodDue = serializers.DateTimeField(
        required=False, allow_null=True, help_text="Конечная дата и время фильтрации"
    )
    volume_from = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, help_text="Минимальный объём слива"
    )
    volume_to = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, help_text="Максимальный объём слива"
    )

    def validate(self, data):
        """
        Проверка корректности диапазона дат и объёмов.
        """
        period_from = data.get("periodFrom")
        period_due = data.get("periodDue")
        volume_from = data.get("volume_from")
        volume_to = data.get("volume_to")

        if period_from and period_due and period_from > period_due:
            raise serializers.ValidationError(
                {"periodFrom": "Начальная дата не может быть позже конечной даты."}
            )

        if volume_from is not None and volume_to is not None and volume_from > volume_to:
            raise serializers.ValidationError(
                {"volume_from": "Минимальный объём не может быть больше максимального."}
            )

        return data


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

class CarActiveStatusSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=True, help_text="Статус активности автомобиля")

    def validate(self, data):
        """
        Проверка существования автомобиля и его принадлежности организации.
        """
        car_id = self.context.get('car_id')
        user = self.context['request'].user

        if not car_id:
            raise serializers.ValidationError({"car_id": "Параметр car_id обязателен"})

        try:
            car = Car.objects.get(
                id=car_id,
                data_providers__org_id=user.org
            )
        except Car.DoesNotExist:
            raise serializers.ValidationError(
                {"car_id": "Автомобиль не найден или не принадлежит вашей организации"}
            )

        data['car'] = car
        return data

    def save(self):
        """
        Обновление статуса is_active автомобиля.
        """
        car = self.validated_data['car']
        is_active = self.validated_data['is_active']
        car.is_active = is_active
        car.save()
        return car