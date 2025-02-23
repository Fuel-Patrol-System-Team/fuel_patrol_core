from celery.exceptions import CeleryError
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework import status
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
import logging
import mimetypes
import uuid

from app.tasks import parse_cars_task, parse_norms_task, process_raw_data_task
from drf_yasg.utils import swagger_auto_schema
from .models import Media, Organization, ReportQuery, OrgUser, Car, CarConsumption, CarReport, Driver
from .pagination import StandardResultsSetPagination
from .rest import MEDIA_UPLOAD_SCHEMA
from .serializers import (
    UserRegistrationSerializer, OrganizationOutputSerializer, OrgUserOutputSerializer, CarOutputSerializer,
    CarConsumptionOutputSerializer, ReportQueryOutputSerializer, MediaOutputSerializer,
    CarReportOutputSerializer, DriverOutputSerializer
)
from .services.media.utils import get_upload_path, calculate_file_hash
from .responses import error_response, user_registered_response, attach_media_response
from .permissions import IsOrgMember

logger = logging.getLogger(__name__)


class MediaUploadAPIView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [IsOrgMember]

    @swagger_auto_schema(**MEDIA_UPLOAD_SCHEMA)
    def post(self, request, *args, **kwargs):
        try:
            if not request.FILES.get('file'):
                logger.error("No file uploaded")
                return error_response("No file uploaded", status.HTTP_400_BAD_REQUEST)

            uploaded_file = request.FILES['file']
            file_type = request.data.get('type')

            if file_type not in ["norm", "raw", "auto"]:
                logger.error(f"Invalid file type: {file_type}")
                return error_response("Invalid file type", status.HTTP_400_BAD_REQUEST)

            file_id = uuid.uuid4()
            media_type, _ = mimetypes.guess_type(uploaded_file.name)
            file_extension = uploaded_file.name.split('.')[-1].lower()
            file_hash = calculate_file_hash(uploaded_file)
            new_filename = f"{file_id}.{file_extension}"
            file_size = uploaded_file.size

            organization = request.user.org
            report_query = ReportQuery.objects.create(organization=organization, status="created")

            existing_media = Media.objects.filter(
                filename=uploaded_file.name,
                size=uploaded_file.size,
                type=file_type,
                file_hash=file_hash
            ).first()

            if existing_media:
                logger.info(f"Identical file already exists: {existing_media.id}")
                report_query = existing_media.report_query
                return attach_media_response(report_query)

            media = Media(
                id=file_id,
                media_type=media_type,
                size=file_size,
                filename=uploaded_file.name,
                type=file_type,
                report_query=report_query,
                file_hash=file_hash
            )

            upload_path = get_upload_path(new_filename)
            media.file.save(upload_path, uploaded_file)
            media.save()

            if file_type == "auto":
                parse_cars_task.delay(report_query.id)
            elif file_type == "norm":
                if not ReportQuery.objects.filter(organization=organization, media__type="auto",
                                                  status="completed").exists():
                    return error_response("Please upload and process 'auto' file first", status.HTTP_400_BAD_REQUEST)
                parse_norms_task.delay(report_query.id)
            elif file_type == "raw":
                if not ReportQuery.objects.filter(organization=organization, media__type="auto",
                                                  status="completed").exists() and not not ReportQuery.objects.filter(
                    organization=organization, media__type="norm",
                    status="completed").exists():
                    return error_response("Please upload and process 'auto' and 'norm' file first",
                                          status.HTTP_400_BAD_REQUEST)
                process_raw_data_task.delay(report_query.id)
                logger.info(f"Raw file uploaded for report query {report_query.id}, awaiting cron processing")

            logger.info(f"Media file uploaded successfully: {media.id}")
            return attach_media_response(report_query)

        except CeleryError as e:
            logger.error(f"Celery error while launching task: {e}")
            return error_response(f"Failed to launch processing task: {e}", status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error uploading media file: {str(e)}")
            return error_response("Internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserRegistrationAPIView(APIView):
    @swagger_auto_schema(
        responses={201: UserRegistrationSerializer(), 400: "Bad Request", 404: "Organization not found"}
    )
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            try:
                user = serializer.save()
                return user_registered_response(user)
            except ValidationError as e:
                return error_response(str(e), status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                return error_response(str(e), status.HTTP_500_INTERNAL_SERVER_ERROR)
        return error_response(serializer.errors, status.HTTP_400_BAD_REQUEST)


class OrganizationListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = OrganizationOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['name']
    search_fields = ['name', 'bot_token', 'chat_id']

    def get_queryset(self):
        return Organization.objects.filter(orguser=self.request.user).select_related().order_by('id')


class OrganizationDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = OrganizationOutputSerializer
    queryset = Organization.objects.all()
    lookup_field = 'pk'


class OrgUserListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = OrgUserOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['org', 'username']
    search_fields = ['username', 'org__name']

    def get_queryset(self):
        return OrgUser.objects.filter(org=self.request.user.org).select_related('org').order_by('id')


class OrgUserDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = OrgUserOutputSerializer
    queryset = OrgUser.objects.all()
    lookup_field = 'pk'


class CarListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['organization', 'name']
    search_fields = ['name', 'description', 'organization__name']

    def get_queryset(self):
        return Car.objects.filter(organization=self.request.user.org).select_related('organization').order_by('id')


class CarDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarOutputSerializer
    queryset = Car.objects.all()
    lookup_field = 'pk'


class CarConsumptionListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarConsumptionOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car', 'valid_period']
    search_fields = ['car__name', 'valid_period']

    def get_queryset(self):
        return CarConsumption.objects.filter(car__organization=self.request.user.org).select_related(
            'car__organization').order_by('id')


class CarConsumptionDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarConsumptionOutputSerializer
    queryset = CarConsumption.objects.all()
    lookup_field = 'pk'


class ReportQueryListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = ReportQueryOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['status', 'organization', 'flux_parsed', 'csv_parsed']
    search_fields = ['organization__name', 'status']

    def get_queryset(self):
        return ReportQuery.objects.filter(organization=self.request.user.org).select_related(
            'organization').prefetch_related('media_set').order_by('id')


class ReportQueryDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = ReportQueryOutputSerializer
    queryset = ReportQuery.objects.all()
    lookup_field = 'pk'


class MediaListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = MediaOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['type', 'report_query']
    search_fields = ['filename', 'media_type', 'type']

    def get_queryset(self):
        return Media.objects.filter(report_query__organization=self.request.user.org).select_related(
            'report_query__organization').order_by('id')


class MediaDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = MediaOutputSerializer
    queryset = Media.objects.all()
    lookup_field = 'pk'


class CarReportListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car', 'datetime', 'status']
    search_fields = ['car__name', 'datetime']

    def get_queryset(self):
        return CarReport.objects.filter(car__organization=self.request.user.org).select_related(
            'car__organization').order_by('datetime')


class CarReportDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = CarReportOutputSerializer
    queryset = CarReport.objects.all()
    lookup_field = 'pk'


class DriverListAPIView(ListAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverOutputSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['car', 'phone']
    search_fields = ['fullname', 'address', 'phone', 'car__name']

    def get_queryset(self):
        return Driver.objects.filter(car__organization=self.request.user.org).prefetch_related(
            'car__organization').order_by('id')


class DriverDetailAPIView(RetrieveAPIView):
    permission_classes = [IsOrgMember]
    serializer_class = DriverOutputSerializer
    queryset = Driver.objects.all()
    lookup_field = 'pk'
