from rest_framework.response import Response
from rest_framework import status

from core.serializers import UserRegistrationSerializer, ReportQueryOutputSerializer, MediaOutputSerializer


def error_response(message, status_code):
    return Response({"error": message}, status=status_code)


def success_response(data, status_code):
    return Response({"data": data}, status=status_code)


def id_response(obj, serializer_class, status_code=status.HTTP_201_CREATED):
    serializer = serializer_class(obj)
    return success_response({"id": serializer.data.get('id')}, status_code)

def user_response(data,status_code):
    return Response({"data": data}, status=status_code)

def attach_media_response(report_query):
    return id_response(report_query, ReportQueryOutputSerializer)


def user_registered_response(user):
    return id_response(user, UserRegistrationSerializer)

