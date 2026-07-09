from datetime import datetime
import requests
import logging
from typing import Any, Dict, List, Optional, Tuple
from django.conf import settings
from django.utils import timezone
from core.models import (
    Car, CarMileageReport, CarMotohoursReport, AIResponseStatus
)
from core.helpers.ml_reasoning import (
    prepare_fuel_data,
    prepare_mileage_data,
    prepare_motohours_data,
    update_ai_response
)
from core.serializers import AnalysisRequestSerializer

logger = logging.getLogger(__name__)


class MLReasoningService:

    def __init__(self, request_data: Dict[str, Any]):
        self.request_data = request_data
        self.service = request_data.get('service')
        self._validate_input()

    def _validate_input(self):
        serializer = AnalysisRequestSerializer(data=self.request_data)
        if not serializer.is_valid():
            raise ValueError(serializer.errors)
        self.validated_data = serializer.validated_data

    def _prepare_payload(self) -> Tuple[str, List[Dict], Optional[Any]]:
        service = self.service
        if service == 'fuel':
            car_report_id = self.request_data.get('car_report_id')
            data, report = prepare_fuel_data(car_report_id)
            return service, data, report
        elif service == 'mileage':
            raw_data = self.request_data.get('data', [])
            data = prepare_mileage_data(raw_data)
            return service, data, None
        elif service == 'motohours':
            raw_data = self.request_data.get('data', [])
            data = prepare_motohours_data(raw_data)
            return service, data, None
        else:
            raise ValueError(f"Unsupported service: {service}")

    def _call_microservice(self, service: str, data: List[Dict]) -> Dict[str, Any]:
        token = settings.ML_SERVICE_TOKEN
        url = settings.ML_SERVICE_URL + "/analyze/"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {"service": service, "data": data}
        logger.info(f"Sending to microservice: {payload}")

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"ML service error: {e}")
            return {"status": "error", "processed": 0, "results": [], "error": str(e)}

    def _get_or_create_car(self, car_id: str) -> Car:
        try:
            car = Car.objects.get(id=car_id)
            return car
        except Car.DoesNotExist:
            car = Car.objects.create(
                id=car_id,
                name=f"Car {car_id[:8]}",
                description="Auto-created from ML analysis"
            )
            logger.info(f"Created new Car with id {car_id}")
            return car

    def _prepare_datetime(self, dt):
        if dt is None:
            return timezone.now()
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except ValueError:
                try:
                    dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    dt = timezone.now()
        if timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt

    def _check_existing_ai_responses(self, service: str, data: List[Dict]) -> Optional[Dict[str, Any]]:
        if service == 'fuel':
            return None

        results = []
        all_have_ok = True
        for record in data:
            car_id = record.get('car_id')
            dt = self._prepare_datetime(record.get('datetime'))
            try:
                if service == 'mileage':
                    report = CarMileageReport.objects.get(car_id=car_id, datetime=dt)
                else:
                    report = CarMotohoursReport.objects.get(car_id=car_id, datetime=dt)
            except (CarMileageReport.DoesNotExist, CarMotohoursReport.DoesNotExist):
                all_have_ok = False
                break
            if report.ai_response_status != AIResponseStatus.OK:
                all_have_ok = False
                break
            results.append(report.ai_response)

        if all_have_ok and results:
            return {
                "status": "ok",
                "service": service,
                "processed": len(results),
                "results": results
            }
        return None

    def _save_results(self, service: str, data: List[Dict], result: Dict[str, Any],
                      model_instance: Optional[Any] = None):
        status = result.get('status', 'error')
        ai_status = AIResponseStatus.OK if status == 'ok' else AIResponseStatus.ERROR

        if service == 'fuel':
            if model_instance:
                update_ai_response(model_instance, result, ai_status)
        elif service == 'mileage':
            results = result.get('results', [])
            for idx, record in enumerate(data):
                car = self._get_or_create_car(record.get('car_id'))
                dt = self._prepare_datetime(record.get('datetime'))
                try:
                    mileage_report = CarMileageReport.objects.get(
                        car_id=car,
                        datetime=dt
                    )
                except CarMileageReport.DoesNotExist:
                    mileage_report = CarMileageReport(
                        car_id=car,
                        datetime=dt,
                        mileage_start=record.get('mileage_start', 0),
                        mileage_end=record.get('mileage_end', 0),
                        travel=record.get('travel', 0),
                        fraud=record.get('fraud', 0),
                        ign_miss=record.get('ign_miss', 0),
                        travel_fraud_jumps=record.get('travel_fraud_jumps', 0),
                    )
                if idx < len(results):
                    update_ai_response(mileage_report, results[idx], ai_status)
                else:
                    update_ai_response(mileage_report, result, ai_status)
        elif service == 'motohours':
            results = result.get('results', [])
            for idx, record in enumerate(data):
                car = self._get_or_create_car(record.get('car_id'))
                dt = self._prepare_datetime(record.get('datetime'))
                try:
                    motohours_report = CarMotohoursReport.objects.get(
                        car_id=car,
                        datetime=dt
                    )
                except CarMotohoursReport.DoesNotExist:
                    motohours_report = CarMotohoursReport(
                        car_id=car,
                        datetime=dt,
                        motohours=record.get('motohours', 0),
                        motohours_idle=record.get('motohours_idle', 0),
                        motohours_active=record.get('motohours_active', 0),
                        rpm_same_cases=record.get('rpm_same_cases', 0),
                        rpm_same_cases_time=record.get('rpm_same_cases_time', 0),
                        unefficient_cases=record.get('unefficient_cases', 0),
                        unefficient_time=record.get('unefficient_time', 0),
                        sensor=record.get('sensor', 'none'),
                        sensor_check=record.get('sensor_check', 'none'),
                        motohours_fraud_by_sensor=record.get('motohours_fraud_by_sensor', 0),
                    )
                if idx < len(results):
                    update_ai_response(motohours_report, results[idx], ai_status)
                else:
                    update_ai_response(motohours_report, result, ai_status)

    def process(self) -> Dict[str, Any]:
        try:
            service, data, model_instance = self._prepare_payload()

            if service == 'fuel' and model_instance and model_instance.ai_response_status == AIResponseStatus.OK:
                return {
                    "success": True,
                    "data": {
                        "service": service,
                        "processed": len(data),
                        "result": model_instance.ai_response
                    }
                }

            if service in ('mileage', 'motohours'):
                existing_response = self._check_existing_ai_responses(service, data)
                if existing_response is not None:
                    return {
                        "success": True,
                        "data": {
                            "service": service,
                            "processed": existing_response["processed"],
                            "result": existing_response
                        }
                    }

            result = self._call_microservice(service, data)
            self._save_results(service, data, result, model_instance)


            if result.get('status') != 'ok':
                return {
                    "success": False,
                    "error": result.get('error', 'Microservice returned error')
                }

            return {
                "success": True,
                "data": {
                    "service": service,
                    "processed": len(data),
                    "result": result
                }
            }

        except ValueError as e:
            return {"success": False, "error": str(e)}
        except ConnectionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in ML reasoning service")
            return {"success": False, "error": "Internal server error"}