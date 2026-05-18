from rest_framework import serializers
from .models import (
    Organization,
    ParsingCarStats,
    ReportQuery,
    OrgUser,
    Driver,
    CarReport,
    CarConsumption,
    Car,
    DataProvider,
    CarBadData,
    ReportQueryDetails,
    SensorsKey,
    SensorsKeyLocalization,
    SensorsValues,
    Language,
    CarUnit, UserCarList, CarMileageReport, TelegramUser, CarFuelReport, APICalculationLog,
)


class OrganizationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "bot_token", "chat_id"]

class CarByGroupSensorsValuesOutputSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="car_id.id", read_only=True)
    name = serializers.CharField(source="car_id.name", read_only=True)
    description = serializers.CharField(source="car_id.description", read_only=True)
    is_active = serializers.BooleanField(source="car_id.is_active", read_only=True)
    is_tarrified = serializers.BooleanField(source="car_id.is_tarrified", read_only=True)

    key = serializers.CharField(source="key.key", read_only=True)

    class Meta:
        model = SensorsValues
        fields = [
            "id",
            "value",
            "key",
            "car_id",
            "name",
            "description",
            "is_active",
            "is_tarrified",
        ]

class CarOutputSerializer(serializers.ModelSerializer):
    sensors = serializers.SerializerMethodField()
    car_unit = serializers.SerializerMethodField()
    parsing_stats = serializers.SerializerMethodField()

    class Meta:
        model = Car
        fields = [
            "id",
            "id_in_provider_system",
            "name",
            "car_unit",
            "description",
            "engine_type",
            "input",
            "output",
            "created_at",
            "last_processed_date",
            "is_tarrified",
            "is_active",
            "sensors",
            "parsing_stats"
        ]

    def get_car_unit(self, obj):
        if hasattr(self.context, 'car_unit_info'):
            return self.context['car_unit_info']

        if obj.car_unit:
            return {
                'id': str(obj.car_unit.id),
                'name': obj.car_unit.name
            }
        return None

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
    def get_parsing_stats(self, obj):
        parsing_stats, status = ParsingCarStats.objects.get_or_create(car=obj, defaults={
            "car_id": obj.id
        })
        return {
            "is_parse_mileage": parsing_stats.is_parse_mileage,
            "is_parse_fuel": parsing_stats.is_parse_fuel,
            "is_parse_motohours": parsing_stats.is_parse_motohours
        }
        

class AutoDataOutputSerializer(serializers.ModelSerializer):
    car_unit = serializers.SerializerMethodField()
    fuel_sensor = serializers.CharField()

    class Meta:
        model = Car
        fields = [
            "id",
            "id_in_provider_system",
            "name",
            "car_unit",
            "description",
            "engine_type",
            "input",
            "output",
            "created_at",
            "last_processed_date",
            "is_tarrified",
            "is_active",
            "fuel_sensor",
            "grades"
        ]

    def get_car_unit(self, obj):
        if hasattr(self.context, 'car_unit_info'):
            return self.context['car_unit_info']

        if obj.car_unit:
            return obj.car_unit.name
            
        return None
    
    

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

class CarFuelReportSerializer(serializers.ModelSerializer):
    car_name = serializers.CharField(source='car_id.name', read_only=True)

    class Meta:
        model = CarFuelReport
        fields = [
            'id', 'car_id', 'car_name', 'start_moment',
            'end_moment', 'fuel_start', 'fuel_end', 'fuel_filled'
        ]

class CarUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = CarUnit
        fields = "__all__"

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


class DataProviderUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataProvider
        fields = ['name', 'metadata', 'cars']

    def validate_metadata(self, value):
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError("Metadata must be a valid JSON object.")
        return value

class ReportQueryDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQueryDetails
        fields = "__all__"

class ReportQueryOutputSerializer(serializers.ModelSerializer):
    provider_name = serializers.SerializerMethodField()
    report_query_details = ReportQueryDetailsSerializer(read_only=True)

    class Meta:
        model = ReportQuery
        fields = ["id", "status", "provider_name", "report_type", "created_at", "report_query_details",]

    def get_provider_name(self, obj):
        return obj.provider_id.name


class UserOutputSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    username = serializers.CharField()
    organization = OrganizationOutputSerializer(read_only=True, source="org")
    organization_tg_link = serializers.URLField()
    timezone = serializers.CharField(required=False)


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



class SensorsKeyOutputSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = SensorsKey
        fields = ["id", "key", "display_name"]


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

class TelegramUserRegistrationSerializer(serializers.Serializer):
    chat_id = serializers.CharField(max_length=100)
    username = serializers.CharField(max_length=255, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    organization_id = serializers.UUIDField()

    def validate_organization_id(self, value):
        try:
            Organization.objects.get(id=value)
        except Organization.DoesNotExist:
            raise serializers.ValidationError("Organization not found")
        return value

class TelegramUserOutputSerializer(serializers.ModelSerializer):
    organization = OrganizationOutputSerializer(read_only=True)

    class Meta:
        model = TelegramUser
        fields = ('id', 'chat_id', 'username', 'first_name', 'last_name', 'organization', 'created_at', 'is_active')

class LanguageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Language
        fields = ["id", "code", "name", "description"]



class DailyLeaksSerializer(serializers.Serializer):
    periodFrom = serializers.DateField(required=False, allow_null=True)
    periodDue = serializers.DateField(required=False, allow_null=True)


class CarLeaksSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    label = serializers.CharField()
    value = serializers.FloatField()


class CarMileageReportOutputSerializer(serializers.ModelSerializer):
    car_name = serializers.CharField(source='car_id.name', read_only=True)

    class Meta:
        model = CarMileageReport
        fields = [
            'id',
            'car_id',
            'car_name',
            'datetime',
            'mileage_start',
            'mileage_end',
            'fraud',
        ]
        read_only_fields = ['id']

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

class UserCarListSerializer(serializers.ModelSerializer):
    """Сериализатор для списков машин пользователя (без вложенных машин)"""

    class Meta:
        model = UserCarList
        fields = ['id', 'name', 'user']
        read_only_fields = ['id', 'user']


class UserCarListDetailSerializer(serializers.ModelSerializer):
    """Сериализатор для детального просмотра списка машин (с вложенными машинами)"""
    cars = CarOutputSerializer(many=True, read_only=True, source='car_set')

    class Meta:
        model = UserCarList
        fields = ['id', 'name', 'user', 'cars']
        read_only_fields = ['id', 'user', 'cars']


class UserCarListCreateUpdateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания и обновления списков машин"""
    car_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        help_text="Список ID машин для добавления в список"
    )

    class Meta:
        model = UserCarList
        fields = ['id', 'name', 'car_ids']
        read_only_fields = ['id']

    def create(self, validated_data):
        car_ids = validated_data.pop('car_ids', [])
        user = self.context['request'].user

        car_list = UserCarList.objects.create(
            name=validated_data['name'],
            user=user
        )

        if car_ids:
            cars = Car.objects.filter(
                id__in=car_ids,
                data_providers__org_id=user.org.id
            )
            cars.update(list_id=car_list)

        return car_list

    def update(self, instance, validated_data):
        car_ids = validated_data.pop('car_ids', None)

        if 'name' in validated_data:
            instance.name = validated_data['name']

        if car_ids is not None:
            Car.objects.filter(list_id=instance).update(list_id=None)

            if car_ids:
                cars = Car.objects.filter(
                    id__in=car_ids,
                    data_providers__org_id=instance.user.org.id
                )
                cars.update(list_id=instance)

        instance.save()
        return instance

class CarLeaksChartsRequestSerializer(serializers.Serializer):
    car_id = serializers.UUIDField(
    )
    days = serializers.IntegerField(
        required=False,
        default=365,
        min_value=1,
        max_value=365
    )
    leak_id = serializers.UUIDField(
        required=True,
    )

# API
class ParsingStatsSwitchSerializer(serializers.Serializer):
    car_id = serializers.UUIDField()
    parameter = serializers.ChoiceField(["mileage", "fuel", "motohours"])

# LOGS
class APICalculationLogOutputSerializer(serializers.ModelSerializer):
    car_name = serializers.CharField(source='car.name', read_only=True, default=None)

    class Meta:
        model = APICalculationLog
        fields = [
            'id',
            'view_name',
            'car',
            'car_name',
            'request_data',
            'response_data',
            'status_code',
            'created_at'
        ]