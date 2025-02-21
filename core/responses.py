# core/responses.py
from rest_framework.response import Response
from rest_framework import status
from .serializers import (
    MediaSerializer, ReportQuerySerializer, UserRegistrationSerializer,
)


def media_upload_response(media):
    serializer = MediaSerializer(media)
    return Response({"data": {"id": serializer.data.get('id')}}, status=status.HTTP_201_CREATED)


def attach_media_response(report_query):
    serializer = ReportQuerySerializer(report_query)
    return Response({"data": {"id": serializer.data.get('id')}}, status=status.HTTP_201_CREATED)


def user_registered_response(user):
    serializer = UserRegistrationSerializer(user)
    return Response({"data": {"id": serializer.data.get('id')}}, status=status.HTTP_201_CREATED)


def error_response(message, status_code):
    return Response({"error": message}, status=status_code)


def success_response(data, status_code):
    return Response({"data": data}, status=status_code)


def list_response(data, status_code=status.HTTP_200_OK):
    return success_response(data, status_code)


def detail_response(data, status_code=status.HTTP_200_OK):
    return success_response(data, status_code)