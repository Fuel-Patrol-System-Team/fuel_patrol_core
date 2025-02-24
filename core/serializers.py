# core/serializers.py
from rest_framework.exceptions import ValidationError
from rest_framework import serializers
from .models import Media, Organization, ReportQuery, OrgUser, Driver, CarReport, CarConsumption, Car


class UserOutputSerializer(serializers.Serializer):
    class Meta:
        model= OrgUser
        fields = ('id', 'username', 'organization')

class OrganizationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = '__all__'


class MediaOutputSerializer(serializers.ModelSerializer):
    report_query = serializers.PrimaryKeyRelatedField(queryset=ReportQuery.objects.all(), required=False)

    class Meta:
        model = Media
        fields = '__all__'


class ReportQueryOutputSerializer(serializers.ModelSerializer):
    organization = OrganizationOutputSerializer(read_only=True)
    media = MediaOutputSerializer(many=True, source='media_set', read_only=True)

    class Meta:
        model = ReportQuery
        fields = '__all__'


class CarOutputSerializer(serializers.ModelSerializer):
    organization = OrganizationOutputSerializer(read_only=True)

    class Meta:
        model = Car
        fields = '__all__'


class CarConsumptionOutputSerializer(serializers.ModelSerializer):
    car = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarConsumption
        fields = '__all__'


class CarReportOutputSerializer(serializers.ModelSerializer):
    car = CarOutputSerializer(read_only=True)

    class Meta:
        model = CarReport
        fields = '__all__'


class DriverOutputSerializer(serializers.ModelSerializer):
    car = CarOutputSerializer(many=True, read_only=True)

    class Meta:
        model = Driver
        fields = '__all__'


class OrgUserOutputSerializer(serializers.ModelSerializer):
    org = OrganizationOutputSerializer(read_only=True)

    class Meta:
        model = OrgUser
        fields = '__all__'


class AttachMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQuery
        fields = ['id', 'organization', 'media']
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
            raise ValidationError(f"Organization with name '{org_name}' not found.")
        user = OrgUser.objects.create_user(
            username=validated_data['username'],
            password=validated_data['password'],
            org=organization
        )
        return user
