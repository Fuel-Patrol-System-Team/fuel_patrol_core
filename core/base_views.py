# core/views/base_views.py
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from core.responses import list_response, detail_response, error_response
from core.rest import LIST_RESPONSE_SCHEMA, DETAIL_RESPONSE_SCHEMA


class BaseListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    model = None
    serializer_class = None

    @swagger_auto_schema(**LIST_RESPONSE_SCHEMA)
    def get(self, request):
        try:
            queryset = self.model.objects.all()
            serializer = self.serializer_class(queryset, many=True)
            return list_response(serializer.data)
        except Exception as e:
            return error_response(str(e), status.HTTP_500_INTERNAL_SERVER_ERROR)


class BaseDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    model = None
    serializer_class = None

    @swagger_auto_schema(**DETAIL_RESPONSE_SCHEMA)
    def get(self, request, pk):
        try:
            instance = self.model.objects.get(pk=pk)
            serializer = self.serializer_class(instance)
            return detail_response(serializer.data)
        except self.model.DoesNotExist:
            return error_response(f"{self.model.__name__} not found", status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return error_response(str(e), status.HTTP_500_INTERNAL_SERVER_ERROR)