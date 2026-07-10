import logging
from uuid import UUID
from django.utils import timezone
from datetime import timedelta
from core.models import CarReport, CarConsumption, ComputedData, AIResponseStatus

logger = logging.getLogger(__name__)


def prepare_fuel_data(car_report_id: UUID):
    try:
        report = CarReport.objects.select_related('car_id').get(id=car_report_id)
    except CarReport.DoesNotExist:
        raise ValueError(f"CarReport with id {car_report_id} does not exist")

    logger.info(
        f"Processing CarReport id={report.id}, car={report.car_id}, datetime={report.datetime}, volume={report.volume}")

    dt_report = report.datetime
    if timezone.is_naive(dt_report):
        dt_report = timezone.make_aware(dt_report, timezone=timezone.utc)

    computed = ComputedData.objects.filter(
        auto=report.car_id,
        timestamp=dt_report
    ).first()

    if computed:
        logger.info(
            f"Found exact match: id={computed.id}, timestamp={computed.timestamp}, spent_fuel={computed.spent_fuel}")
    else:
        day_start = dt_report.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        candidates = ComputedData.objects.filter(
            auto=report.car_id,
            timestamp__range=(day_start, day_end)
        ).order_by('timestamp')

        if not candidates.exists():
            candidates = ComputedData.objects.filter(
                auto=report.car_id,
                timestamp__range=(day_start - timedelta(days=1), day_end + timedelta(days=1))
            ).order_by('timestamp')
            if not candidates.exists():
                raise ValueError(f"No ComputedData found for car {report.car_id} near {dt_report}")

        best = None
        min_diff = None
        for cand in candidates:
            diff = abs(cand.timestamp - dt_report)
            if min_diff is None or diff < min_diff:
                min_diff = diff
                best = cand

        computed = best
        logger.info(
            f"Found nearest match (diff={min_diff}): id={computed.id}, timestamp={computed.timestamp}, spent_fuel={computed.spent_fuel}")

    if not computed:
        raise ValueError(f"No ComputedData found for car {report.car_id} near {dt_report}")

    consumption = CarConsumption.objects.filter(car_id=report.car_id).first()
    norma_std = 0.0
    norma_rasx = 0.0
    speed_etalon = 60.0
    if consumption:
        if consumption.json_data:
            norma_std = consumption.json_data.get('norma_std', 0.0)
            norma_rasx = consumption.json_data.get('norma_rasx_summer', 0.0)
        if consumption.speed_etalon is not None:
            speed_etalon = consumption.speed_etalon

    leak = computed.spent_fuel - norma_rasx

    record = {
        "auto": str(computed.auto.id),
        "timestamp": computed.timestamp.isoformat(),
        "pos_s": computed.pos_s,
        "spent_fuel": computed.spent_fuel,
        "z_values": computed.z_values,
        "dtime": computed.dtime,
        "count": computed.count,
        "no_sat_data": int(computed.no_sat_data) if computed.no_sat_data is not None else 0,
        "norma_rasx_per_travel": norma_rasx,
        "norma_std": norma_std,
        "norm_speed": speed_etalon,
        "spent_fuel_boundary": 0.0,
        "leak": leak,
        "ptime": computed.dtime,
    }

    logger.info(f"Prepared record for microservice: {record}")
    return [record], report


def prepare_mileage_data(data: list):
    required_fields = {'car_id', 'datetime', 'mileage_start', 'mileage_end', 'travel', 'fraud', 'ign_miss',
                       'travel_fraud_jumps'}
    for idx, item in enumerate(data):
        missing = required_fields - set(item.keys())
        if missing:
            raise ValueError(f"Record {idx}: missing required fields: {', '.join(missing)}")
    return data


def prepare_motohours_data(data: list):
    required_fields = {'car_id', 'datetime', 'motohours', 'motohours_idle', 'motohours_active',
                       'rpm_same_cases', 'rpm_same_cases_time', 'unefficient_cases', 'unefficient_time',
                       'sensor', 'sensor_check', 'motohours_fraud_by_sensor'}
    for idx, item in enumerate(data):
        missing = required_fields - set(item.keys())
        if missing:
            raise ValueError(f"Record {idx}: missing required fields: {', '.join(missing)}")
    return data


def update_ai_response(model_instance, ai_response: dict, status: str = AIResponseStatus.OK):
    model_instance.ai_response = ai_response
    model_instance.ai_response_status = status

    if isinstance(model_instance, CarReport) and isinstance(ai_response, dict):
        results = ai_response.get('results', [])
        if results and isinstance(results, list) and len(results) > 0:
            picked_by = results[0].get('picked_by')
            if picked_by:
                model_instance.picked_by = picked_by
                model_instance.save(update_fields=['ai_response', 'ai_response_status', 'picked_by'])
                return

    model_instance.save(update_fields=['ai_response', 'ai_response_status'])
