from rest_framework.exceptions import ValidationError
from rest_framework import serializers
from .models import Media, Organization, ReportQuery, OrgUser, Driver, CarReport, CarConsumption, Car

##TODO: Возможно сериализатор на вход один, другой на выход и переписать связанное.

class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = '__all__'


class OrgUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrgUser
        fields = '__all__'


class CarSerializer(serializers.ModelSerializer):
    class Meta:
        model = Car
        fields = '__all__'


class CarConsumptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CarConsumption
        fields = '__all__'


class ReportQuerySerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQuery
        fields = '__all__'


class MediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        fields = '__all__'


class CarReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = CarReport
        fields = '__all__'


class DriverSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = '__all__'


class AttachMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQuery
        fields = ['id','organization', 'media']
        read_only_fields = ['id']

    def create(self, validated_data):
        report_query = ReportQuery.objects.create(**validated_data)
        return report_query


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    org_name = serializers.CharField(write_only=True)

    class Meta:
        model = OrgUser
        fields = ['id','username', 'password', 'org_name']
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


