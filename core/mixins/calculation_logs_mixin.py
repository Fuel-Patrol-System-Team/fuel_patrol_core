import logging
import uuid
from django.core.exceptions import ValidationError
from rest_framework.response import Response
from core.models import APICalculationLog, Car

logger = logging.getLogger(__name__)


class APICalculationLoggingMixin:
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)

        try:

            drf_request = getattr(self, "request", request)

            req_data = {}
            if hasattr(drf_request, "data") and isinstance(drf_request.data, dict):
                req_data = drf_request.data
            elif hasattr(request, "POST"):
                req_data = request.POST.dict()

            car_id = req_data.get("car_id")
            car_obj = None
            if car_id:
                try:

                    uuid.UUID(str(car_id))
                    car_obj = Car.objects.filter(id=car_id).first()
                except (ValueError, TypeError, ValidationError):
                    car_obj = None

            res_data = {}
            if isinstance(response, Response) and hasattr(response, 'data'):
                res_data = response.data
            elif hasattr(response, 'content'):
                try:
                    res_data = {"content": response.content.decode('utf-8')}
                except Exception:
                    res_data = {"content": str(response.content)}

            status_code = getattr(response, 'status_code', None)

            APICalculationLog.objects.create(
                view_name=self.__class__.__name__,
                user=request.user if request.user and request.user.is_authenticated else None,
                car=car_obj,
                request_data=req_data,
                response_data=res_data,
                status_code=status_code
            )

        except Exception as e:
            logger.error(f"API Logging Mixin Error: {str(e)}", exc_info=True)

        return response
