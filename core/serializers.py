from rest_framework import serializers
from .models import (
    Media,
    Organization,
    ReportQuery,
    OrgUser,
    Driver,
    CarReport,
    CarConsumption,
    Car,
    DataProvider,
    CarBadData,
    SensorsKey,
    SensorsKeyLocalization,
    SensorsValues,
    Language,
)


class OrganizationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "bot_token", "chat_id"]


class CarOutputSerializer(serializers.ModelSerializer):
    sensors = serializers.SerializerMethodField()

    class Meta:
        model = Car
        fields = [
            "id",
            "id_in_provider_system",
            "name",
            "description",
            "engine_type",
            "input",
            "output",
            "created_at",
            "last_processed_date",
            "is_tarrified",
            "is_active",
            "sensors",  # добавляем поле с датчиками
        ]

    def get_sensors(self, obj):
        language_code = self.context.get("language_code", "ru")

        sensors_values = SensorsValues.objects.filter(car_id=obj)

        sensors_data = []
        for sensor_value in sensors_values:
            try:
                localization = SensorsKeyLocalization.objects.get(
                    key=sensor_value.key, language__code=language_code
                )
                display_name = localization.localization
            except SensorsKeyLocalization.DoesNotExist:
                display_name = sensor_value.key.key

            sensors_data.append(
                {
                    "display_name": display_name,
                    "value": sensor_value.value,
                    "key": sensor_value.key.key,
                }
            )

        return sensors_data


class DriverOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(many=True, read_only=True)

    class Meta:
        model = Driver
        fields = ["id", "fullname", "address", "phone", "car_id"]


class CarReportOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarReport
        fields = ["id", "car_id", "speed", "datetime", "volume", "status"]


class CarConsumptionOutputSerializer(serializers.ModelSerializer):
    car_id = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarConsumption
        fields = [
            "id",
            "car_id",
            "winter_volume",
            "summer_volume",
            "valid_period",
            "speed_etalon",
            "max_fuel",
        ]


class DataProviderOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataProvider
        fields = ["id", "name", "metadata"]


class MediaOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        fields = ["id", "media_type", "size", "filename", "type", "file_hash"]


class ReportQueryOutputSerializer(serializers.ModelSerializer):
    provider_name = serializers.SerializerMethodField()

    class Meta:
        model = ReportQuery
        fields = ["id", "status", "provider_name", "created_at"]

    def get_provider_name(self, obj):
        return obj.provider_id.name


class UserOutputSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    username = serializers.CharField()
    organization = OrganizationOutputSerializer(read_only=True, source="org")


class OrgUserOutputSerializer(serializers.ModelSerializer):
    org = OrganizationOutputSerializer(read_only=True)

    class Meta:
        model = OrgUser
        fields = [
            "id",
            "username",
            "org",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
        ]


class CarBadDataOutputSerializer(serializers.ModelSerializer):
    car_name = serializers.CharField(source="car_id.name", read_only=True)

    class Meta:
        model = CarBadData
        fields = ["id", "car_name", "reason", "datetime"]


class SensorsKeyOutputSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = SensorsKey
        fields = ["id", "key", "display_name"]


class AttachMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQuery
        fields = ["id", "provider_id", "status"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        report_query = ReportQuery.objects.create(**validated_data)
        return report_query


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    org_name = serializers.CharField(write_only=True)
    language_code = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = OrgUser
        fields = ["id", "username", "password", "org_name", "language_code"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        org_name = validated_data.pop("org_name")
        language_code = validated_data.pop("language_code", "ru")

        try:
            organization = Organization.objects.get(name=org_name)
        except Organization.DoesNotExist:
            raise serializers.ValidationError(
                f"Organization with name '{org_name}' not found."
            )

        try:
            language = Language.objects.get(code=language_code)
        except Language.DoesNotExist:
            language = Language.objects.get(code="ru")

        user = OrgUser.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
            org=organization,
            active_language=language,
        )
        return user


class LanguageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Language
        fields = ["id", "code", "name", "description"]


class CarMetricSerializer(serializers.Serializer):
    x = serializers.CharField(help_text="Дата")
    y = serializers.FloatField(help_text="Значение")
    metric = serializers.CharField(help_text="Тип метрики (fuel_level или speed)")


class CarMetricsQuerySerializer(serializers.Serializer):
    periodFrom = serializers.DateField(required=False, allow_null=True)
    periodDue = serializers.DateField(required=False, allow_null=True)
    car = serializers.UUIDField(required=True)
    agg = serializers.CharField(required=False, allow_null=True)
    func = serializers.ChoiceField(choices=["mean", "median", "sum"], default="mean")
    metric = serializers.ChoiceField(
        choices=["fuel_level", "speed", "both"], default="both"
    )


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
        required=False,
        allow_null=True,
        min_value=0,
        help_text="Минимальный объём слива",
    )
    volume_to = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text="Максимальный объём слива",
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

        if (
            volume_from is not None
            and volume_to is not None
            and volume_from > volume_to
        ):
            raise serializers.ValidationError(
                {"volume_from": "Минимальный объём не может быть больше максимального."}
            )

        return data


class DataProviderSerializer(serializers.ModelSerializer):
    cars = serializers.PrimaryKeyRelatedField(
        queryset=Car.objects.all(),
        many=True,
        required=False,
        help_text="Список ID автомобилей, связанных с провайдером",
    )

    class Meta:
        model = DataProvider
        fields = ["name", "metadata", "cars"]


class CarActiveStatusSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(
        required=True, help_text="Статус активности автомобиля"
    )

    def validate(self, data):
        """
        Проверка существования автомобиля и его принадлежности организации.
        """
        car_id = self.context.get("car_id")
        user = self.context["request"].user

        if not car_id:
            raise serializers.ValidationError({"car_id": "Параметр car_id обязателен"})

        try:
            car = Car.objects.get(id=car_id, data_providers__org_id=user.org)
        except Car.DoesNotExist:
            raise serializers.ValidationError(
                {"car_id": "Автомобиль не найден или не принадлежит вашей организации"}
            )

        data["car"] = car
        return data

    def save(self):
        """
        Обновление статуса is_active автомобиля.
        """
        car = self.validated_data["car"]
        is_active = self.validated_data["is_active"]
        car.is_active = is_active
        car.save()
        return car


class MileageTestSerializer(serializers.Serializer):
    car_id = serializers.UUIDField(required=True, help_text="Id автомобиля")
    start_date = serializers.DateTimeField(required=True, help_text="Начало промежутка")
    end_date = serializers.DateTimeField(required=True, help_text="Конец промежутка")
    agg = serializers.IntegerField(required=False, help_text="Агрегация (в минутах)")


class CarBadDataSerializer(serializers.ModelSerializer):
    car_name = serializers.CharField(source="car_id.name", read_only=True)
    car_id_display = serializers.UUIDField(source="car_id.id", read_only=True)

    class Meta:
        model = CarBadData
        fields = [
            "id",
            "car_id",
            "car_id_display",
            "car_name",
            "reason",
            "datetime",
        ]
        read_only_fields = fields
