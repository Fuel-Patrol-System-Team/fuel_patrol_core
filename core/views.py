from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser
from rest_framework import status
import logging
import mimetypes
import uuid
from drf_yasg.utils import swagger_auto_schema
from rest_framework_simplejwt.tokens import RefreshToken

from .base_views import BaseListAPIView, BaseDetailAPIView
from .models import Media, Organization, ReportQuery, OrgUser, Car, CarConsumption, CarReport, Driver
from .rest import ATTACH_MEDIA_SCHEMA, MEDIA_UPLOAD_SCHEMA
from .serializers import UserRegistrationSerializer, OrganizationSerializer, OrgUserSerializer, CarSerializer, \
    CarConsumptionSerializer, ReportQuerySerializer, MediaSerializer, CarReportSerializer, DriverSerializer
from .services.media.utils import get_upload_path
from .responses import media_upload_response, attach_media_response, error_response,user_registered_response

logger = logging.getLogger(__name__)


class MediaUploadAPIView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(**MEDIA_UPLOAD_SCHEMA)
    def post(self, request, *args, **kwargs):
        try:
            if not request.FILES.get('file'):
                logger.error("No file uploaded")
                return error_response("No file uploaded", status.HTTP_400_BAD_REQUEST)

            uploaded_file = request.FILES['file']
            file_type = request.data.get('type')

            if file_type and file_type not in ["norm", "raw", "auto"]:
                logger.error(f"Invalid file type: {file_type}")
                return error_response("Invalid file type", status.HTTP_400_BAD_REQUEST)

            file_id = uuid.uuid4()
            media_type, _ = mimetypes.guess_type(uploaded_file.name)
            file_extension = uploaded_file.name.split('.')[-1].lower()
            new_filename = f"{file_id}.{file_extension}"
            file_size = uploaded_file.size

            media = Media(
                id=file_id,
                media_type=media_type,
                size=file_size,
                filename=uploaded_file.name,
                type=file_type
            )

            upload_path = get_upload_path(new_filename)
            media.file.save(upload_path, uploaded_file)
            media.save()

            logger.info(f"Media file uploaded successfully: {media.id}")
            return media_upload_response(media)

        except Exception as e:
            logger.error(f"Error uploading media file: {str(e)}")
            return error_response("Internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR)


class AttachMediaToOrgAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(**ATTACH_MEDIA_SCHEMA)
    def post(self, request, *args, **kwargs):
        try:
            organization = Organization.objects.get(orguser=request.user)

        except Organization.DoesNotExist:
            logger.error("Organization not found")
            return error_response("Organization not found", status.HTTP_404_NOT_FOUND)

        media_uuids = request.data.get('media_uuids', [])
        if not media_uuids or len(media_uuids) > 3:
            logger.error(f"Invalid media_uuids: {media_uuids}")
            return error_response("media_uuids must contain 1 to 3 UUIDs", status.HTTP_400_BAD_REQUEST)

        try:
            report_query = ReportQuery.objects.create(organization=organization)

            for media_uuid in media_uuids:
                try:
                    media = Media.objects.get(id=media_uuid)
                    media.report_query = report_query
                    media.save()
                except Media.DoesNotExist:
                    logger.error(f"Media with UUID {media_uuid} not found")
                    return error_response(f"Media with UUID {media_uuid} not found", status.HTTP_404_NOT_FOUND)

            logger.info(f"Media files attached to organization successfully: {report_query.id}")
            return attach_media_response(report_query)

        except Exception as e:
            logger.error(f"Error attaching media files: {str(e)}")
            return error_response("Internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserRegistrationAPIView(APIView):
    @swagger_auto_schema(
        responses={201: UserRegistrationSerializer(), 400: "Bad Request", 404: "Organization not found"})
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


class OrganizationListAPIView(BaseListAPIView):
    model = Organization
    serializer_class = OrganizationSerializer


class OrganizationDetailAPIView(BaseDetailAPIView):
    model = Organization
    serializer_class = OrganizationSerializer


class OrgUserListAPIView(BaseListAPIView):
    model = OrgUser
    serializer_class = OrgUserSerializer


class OrgUserDetailAPIView(BaseDetailAPIView):
    model = OrgUser
    serializer_class = OrgUserSerializer


class CarListAPIView(BaseListAPIView):
    model = Car
    serializer_class = CarSerializer


class CarDetailAPIView(BaseDetailAPIView):
    model = Car
    serializer_class = CarSerializer


class CarConsumptionListAPIView(BaseListAPIView):
    model = CarConsumption
    serializer_class = CarConsumptionSerializer


class CarConsumptionDetailAPIView(BaseDetailAPIView):
    model = CarConsumption
    serializer_class = CarConsumptionSerializer


class ReportQueryListAPIView(BaseListAPIView):
    model = ReportQuery
    serializer_class = ReportQuerySerializer


class ReportQueryDetailAPIView(BaseDetailAPIView):
    model = ReportQuery
    serializer_class = ReportQuerySerializer


class MediaListAPIView(BaseListAPIView):
    model = Media
    serializer_class = MediaSerializer


class MediaDetailAPIView(BaseDetailAPIView):
    model = Media
    serializer_class = MediaSerializer


class CarReportListAPIView(BaseListAPIView):
    model = CarReport
    serializer_class = CarReportSerializer


class CarReportDetailAPIView(BaseDetailAPIView):
    model = CarReport
    serializer_class = CarReportSerializer


class DriverListAPIView(BaseListAPIView):
    model = Driver
    serializer_class = DriverSerializer


class DriverDetailAPIView(BaseDetailAPIView):
    model = Driver
    serializer_class = DriverSerializer
