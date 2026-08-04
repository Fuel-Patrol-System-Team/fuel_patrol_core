from uuid import UUID

from rest_framework import serializers
from .models import (
    CarMotohoursReport,
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
    CarUnit, UserCarList, CarMileageReport, TelegramUser, CarFuelReport, APICalculationLog, AlertSubscription,
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

        sensors_data = []
        for sensor_value in obj.values.select_related('key').all():
            prefetched = getattr(sensor_value.key, '_prefetched_localized', None)
            if prefetched is not None:
                display_name = prefetched[0].localization if prefetched else sensor_value.key.key
            else:
                try:
                    localization = SensorsKeyLocalization.objects.get(
                        key=sensor_value.key, language__code=language_code
                    )
                    display_name = localization.localization
                except SensorsKeyLocalization.DoesNotExist:
                    display_name = sensor_value.key.key

            sensors_data.append({
                "id": sensor_value.id,
                "display_name": display_name,
                "value": sensor_value.value,
                "key": sensor_value.key.key,
                "is_active": sensor_value.is_active
            })
        return sensors_data

    def get_parsing_stats(self, obj):
        try:
            stats = obj.parsingcar_stats
        except ParsingCarStats.DoesNotExist:
            stats, _ = ParsingCarStats.objects.get_or_create(car=obj, defaults={"car_id": obj.id})
        return {
            "is_parse_mileage": stats.is_parse_mileage,
            "is_parse_fuel": stats.is_parse_fuel,
            "is_parse_motohours": stats.is_parse_motohours,
            "rpm_idle": stats.rpm_idle,
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
        fields = ["id", "car_id", "speed", "datetime", "volume", "status", "picked_by"]


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
        fields = ["id", "status", "provider_name", "report_type", "created_at", "report_query_details"]

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
    key_display_name = serializers.CharField(read_only=True)
    value = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField()

    class Meta:
        model = SensorsKey
        fields = ["id", "key", "value", "key_display_name", "is_active"]

class SensorsValuesOutputSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    
    class Meta:
        model = SensorsValues
        fields = ["id", "key", "value", "display_name"]


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
    user_id = serializers.UUIDField()
    username = serializers.CharField(max_length=255, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=255, required=False, allow_blank=True)



class TelegramUserOutputSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = TelegramUser
        fields = (
            'id',
            'chat_id',
            'username',
            'first_name',
            'last_name',
            'user_username',
            'created_at',
            'is_active',
        )

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

class CarSensorsSwitchSerializer(serializers.Serializer):
    car_id = serializers.UUIDField()
    key_name= serializers.CharField()
    sensor_id = serializers.UUIDField()


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
class CarMotohoursReportOutputSerializer(serializers.ModelSerializer):
    car_name = serializers.CharField(source='car_id.name', read_only=True)

    class Meta:
        model = CarMotohoursReport
        fields = [
            'id',
            'car_id',
            'car_name',
            'datetime',
            'motohours_start',
            'motohours_end',
            'motohours',
            'motohours_fraud',
        ]
        read_only_fields = ['id']


class CarLeaksFilterSerializer(serializers.Serializer):
    car_id = serializers.UUIDField(required=True, help_text="ID автомобиля")
    periodFrom = serializers.DateTimeField(required=False, allow_null=True)
    periodDue = serializers.DateTimeField(required=False, allow_null=True)
    volume_from = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    volume_to = serializers.IntegerField(required=False, allow_null=True, min_value=0)

    def validate(self, data):
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
    )

    class Meta:
        model = DataProvider
        fields = ["name", "metadata", "cars"]


class CarActiveStatusSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=True)

    def validate(self, data):
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
            "event_date",
            "reason",
            "tags",
            "category",
            "severity",
            "datetime",
        ]
        read_only_fields = fields


class CarBadDataFilterSerializer(serializers.Serializer):
    car_id = serializers.UUIDField(required=False, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)
    search = serializers.CharField(required=False, allow_blank=True)
    severity = serializers.MultipleChoiceField(
        choices=CarBadData.Severity.choices,
        required=False,
    )
    tags = serializers.MultipleChoiceField(
        choices=CarBadData.Tag.choices,
        required=False,
    )
    category = serializers.MultipleChoiceField(
        choices=CarBadData.Category.choices,
        required=False,
    )

class BadDataQuerySerializer(serializers.Serializer):
    TYPE_CALENDAR = "calendar"
    TYPE_CAR = "car"
    TYPE_TAG = "tag"

    TYPE_CHOICES = [
        (TYPE_CALENDAR, "Календарь"),
        (TYPE_CAR, "По автомобилям"),
        (TYPE_TAG, "По тегам"),
    ]

    type = serializers.ChoiceField(choices=TYPE_CHOICES)
    periodFrom = serializers.DateField(required=False, allow_null=True)
    periodDue = serializers.DateField(required=False, allow_null=True)
    category = serializers.ChoiceField(
        choices=CarBadData.Category.choices,
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    tags = serializers.MultipleChoiceField(
        choices=CarBadData.Tag.choices,
        required=False,
    )

class UserCarListSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserCarList
        fields = ['id', 'name', 'user']
        read_only_fields = ['id', 'user']


class UserCarListDetailSerializer(serializers.ModelSerializer):
    cars = CarOutputSerializer(many=True, read_only=True, source='car_set')

    class Meta:
        model = UserCarList
        fields = ['id', 'name', 'user', 'cars']
        read_only_fields = ['id', 'user', 'cars']


class UserCarListCreateUpdateSerializer(serializers.ModelSerializer):
    car_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
    )

    class Meta:
        model = UserCarList
        fields = ['id', 'name', 'car_ids']
        read_only_fields = ['id']

    def create(self, validated_data):
        car_ids = validated_data.pop('car_ids', [])
        user = self.context['request'].user
        car_list = UserCarList.objects.create(name=validated_data['name'], user=user)
        if car_ids:
            Car.objects.filter(
                id__in=car_ids,
                data_providers__org_id=user.org.id
            ).update(list_id=car_list)
        return car_list

    def update(self, instance, validated_data):
        car_ids = validated_data.pop('car_ids', None)
        if 'name' in validated_data:
            instance.name = validated_data['name']
        if car_ids is not None:
            Car.objects.filter(list_id=instance).update(list_id=None)
            if car_ids:
                Car.objects.filter(
                    id__in=car_ids,
                    data_providers__org_id=instance.user.org.id
                ).update(list_id=instance)
        instance.save()
        return instance


class CarLeaksChartsRequestSerializer(serializers.Serializer):
    car_id = serializers.UUIDField()
    days = serializers.IntegerField(required=False, default=365, min_value=1, max_value=365)
    leak_id = serializers.UUIDField(required=True)


class ParsingStatsSwitchSerializer(serializers.Serializer):
    car_id = serializers.UUIDField()
    parameter = serializers.ChoiceField(["mileage", "fuel", "motohours"])

class ParsingStatsUpdateRpmSerializer(serializers.Serializer):
    car_id = serializers.UUIDField()
    rpm_idle = serializers.IntegerField()
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
            'status_code',
            'created_at'
        ]
class APICalculationRetrieveLogOutputSerializer(serializers.ModelSerializer):
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

class AlertSubscriptionPatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertSubscription
        fields = [
            'alert_types',
            'bad_data_tags',
            'bad_data_min_severity',
            'min_fraud_km',
            'min_leak_liters',
            'notify_hour',
            'is_active',
        ]
        extra_kwargs = {
            'alert_types':         {'required': False},
            'bad_data_tags':       {'required': False},
            'bad_data_min_severity': {'required': False},
            'min_fraud_km':        {'required': False},
            'min_leak_liters':     {'required': False},
            'notify_hour':         {'required': False},
            'is_active':           {'required': False},
        }

    def validate_alert_types(self, value):
        valid = {c[0] for c in AlertSubscription.AlertType.choices}
        invalid = set(value) - valid
        if invalid:
            raise serializers.ValidationError(
                f"Недопустимые типы алертов: {', '.join(invalid)}. "
                f"Допустимые значения: {', '.join(valid)}"
            )
        return value

    def validate_bad_data_tags(self, value):
        valid = {c[0] for c in CarBadData.Tag.choices}
        invalid = set(value) - valid
        if invalid:
            raise serializers.ValidationError(
                f"Недопустимые теги: {', '.join(invalid)}. "
                f"Допустимые значения: {', '.join(valid)}"
            )
        return value

    def validate_bad_data_min_severity(self, value):
        valid = {c[0] for c in CarBadData.Severity.choices}
        if value not in valid:
            raise serializers.ValidationError(
                f"Недопустимый уровень серьёзности: {value}. "
                f"Допустимые значения: {', '.join(valid)}"
            )
        return value

    def validate_notify_hour(self, value):
        if not (0 <= value <= 23):
            raise serializers.ValidationError(
                f"Час отправки должен быть от 0 до 23, получено: {value}"
            )
        return value

    def validate_min_fraud_km(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Минимальная накрутка не может быть отрицательной."
            )
        return value

    def validate_min_leak_liters(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Минимальный объём слива не может быть отрицательным."
            )
        return value

    def validate(self, attrs):
        instance = self.instance
        alert_types = attrs.get(
            'alert_types',
            instance.alert_types if instance else []
        )

        errors = {}

        if AlertSubscription.AlertType.BAD_DATA in alert_types:
            severity = attrs.get(
                'bad_data_min_severity',
                instance.bad_data_min_severity if instance else None
            )
            if not severity:
                errors['bad_data_min_severity'] = (
                    "Обязательно при выборе типа bad_data: "
                    "укажите минимальный уровень серьёзности."
                )

        if AlertSubscription.AlertType.LEAKS in alert_types:
            min_leak = attrs.get(
                'min_leak_liters',
                instance.min_leak_liters if instance else None
            )
            if min_leak is None:
                errors['min_leak_liters'] = (
                    "Обязательно при выборе типа leaks: "
                    "укажите минимальный объём слива (л). "
                    "Для отключения фильтра передайте 0."
                )

        if AlertSubscription.AlertType.FRAUDS in alert_types:
            min_fraud = attrs.get(
                'min_fraud_km',
                instance.min_fraud_km if instance else None
            )
            if min_fraud is None:
                errors['min_fraud_km'] = (
                    "Обязательно при выборе типа frauds: "
                    "укажите минимальную накрутку пробега (км). "
                    "Для отключения фильтра передайте 0."
                )

        if errors:
            raise serializers.ValidationError(errors)

        return attrs


class FuelAnalysisSerializer(serializers.Serializer):
    service = serializers.CharField(default='fuel')
    car_report_id = serializers.UUIDField()

    def validate_service(self, value):
        if value != 'fuel':
            raise serializers.ValidationError("Service must be 'fuel'")
        return value

class MileageAnalysisSerializer(serializers.Serializer):
    service = serializers.CharField(default='mileage')
    data = serializers.ListField(child=serializers.DictField())

    def validate_service(self, value):
        if value != 'mileage':
            raise serializers.ValidationError("Service must be 'mileage'")
        return value

class MotohoursAnalysisSerializer(serializers.Serializer):
    service = serializers.CharField(default='motohours')
    data = serializers.ListField(child=serializers.DictField())

    def validate_service(self, value):
        if value != 'motohours':
            raise serializers.ValidationError("Service must be 'motohours'")
        return value

class AnalysisRequestSerializer(serializers.Serializer):
    service = serializers.ChoiceField(choices=['fuel', 'mileage', 'motohours'])

    def validate(self, attrs):
        service = attrs.get('service')
        if service == 'fuel':
            if 'car_report_id' not in self.initial_data:
                raise serializers.ValidationError({"car_report_id": "This field is required for fuel service."})
            try:
                UUID(self.initial_data['car_report_id'])
            except ValueError:
                raise serializers.ValidationError({"car_report_id": "Invalid UUID format."})
        elif service in ('mileage', 'motohours'):
            if 'data' not in self.initial_data:
                raise serializers.ValidationError({"data": "This field is required for mileage/motohours service."})
            if not isinstance(self.initial_data['data'], list):
                raise serializers.ValidationError({"data": "Must be a list of objects."})
        return attrs


class StopsMileageRequestSerializer(serializers.Serializer):
    car_id = serializers.CharField()
    start_date = serializers.DateTimeField()
    end_date = serializers.DateTimeField()
    is_save_bad_data = serializers.BooleanField(required=False, default=True)


class StopMileageRecordSerializer(serializers.Serializer):
    stop_start = serializers.DateTimeField()
    stop_end = serializers.DateTimeField()
    address = serializers.CharField(allow_blank=True)
    duration_seconds = serializers.IntegerField()
    mileage_before_stop = serializers.FloatField(allow_null=True)
    mileage = serializers.FloatField(allow_null=True)

class StopsMileageResultSerializer(serializers.Serializer):
    stops = StopMileageRecordSerializer(many=True)


class StopsMileageResponseSerializer(serializers.Serializer):
    result = StopsMileageResultSerializer()