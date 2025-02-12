import logging
import mimetypes
import uuid

from django.http import JsonResponse
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser

from core.models import Media, Organization, ReportQuery
from core.services.media.utils import get_upload_path
from core.rest import MEDIA_UPLOAD_SCHEMA, ATTACH_MEDIA_SCHEMA

# Настройка логгера
logger = logging.getLogger(__name__)

@swagger_auto_schema(**MEDIA_UPLOAD_SCHEMA)
@api_view(['POST'])
@parser_classes([MultiPartParser])
def media_upload(request):
    try:
        if not request.FILES.get('file'):
            logger.error("No file uploaded")
            return JsonResponse({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        uploaded_file = request.FILES['file']
        file_type = request.data.get('type')

        if file_type and file_type not in ["norm", "raw", "auto"]:
            logger.error(f"Invalid file type: {file_type}")
            return JsonResponse({"error": "Invalid file type"}, status=status.HTTP_400_BAD_REQUEST)

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
        return JsonResponse({"id": str(media.id)}, status=status.HTTP_201_CREATED)

    except Exception as e:
        logger.error(f"Error uploading media file: {str(e)}")
        return JsonResponse({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@swagger_auto_schema(**ATTACH_MEDIA_SCHEMA)
@api_view(['POST'])
def attach_media_to_org(request):
    try:
        organization = Organization.objects.get(orguser=request.user.id)
    except Organization.DoesNotExist:
        logger.error("Organization not found")
        return JsonResponse({"error": "Organization not found"}, status=status.HTTP_404_NOT_FOUND)

    media_uuids = request.data.get('media_uuids', [])
    if not media_uuids or len(media_uuids) > 3:
        logger.error(f"Invalid media_uuids: {media_uuids}")
        return JsonResponse({"error": "media_uuids must contain 1 to 3 UUIDs"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        report_query = ReportQuery.objects.create(organization=organization)

        for media_uuid in media_uuids:
            try:
                media = Media.objects.get(id=media_uuid)
                media.report_query = report_query
                media.save()
            except Media.DoesNotExist:
                logger.error(f"Media with UUID {media_uuid} not found")
                return JsonResponse({"error": f"Media with UUID {media_uuid} not found"}, status=status.HTTP_404_NOT_FOUND)

        logger.info(f"Media files attached to organization successfully: {report_query.id}")
        return JsonResponse({"id": str(report_query.id)}, status=status.HTTP_201_CREATED)

    except Exception as e:
        logger.error(f"Error attaching media files: {str(e)}")
        return JsonResponse({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)