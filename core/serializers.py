from rest_framework import serializers
from .models import Media, Organization, ReportQuery


class AttachMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportQuery
        fields = ['organization', 'media']

    def create(self, validated_data):
        report_query = ReportQuery.objects.create(**validated_data)
        return report_query