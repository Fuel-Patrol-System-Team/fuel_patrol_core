import logging
from django.utils import timezone

from core.models import ReportQueryDetails

from datetime import time as time_obj

logger = logging.getLogger(__name__)


def update_report_details_with_error(report_query_id: str, error_message: str,
                                     parsing_start_time=None, cars_proceed=0, cars_skipped=0):
    """Обновляет ReportQueryDetails с информацией об ошибке."""
    try:
        report_details = ReportQueryDetails.objects.get(report_query_id=report_query_id)
        end_time = timezone.now()
        report_details.end_time = end_time

        if parsing_start_time and end_time:
            time_proceed = end_time - parsing_start_time
            total_seconds = int(time_proceed.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            report_details.time_proceed = time_obj(hours, minutes, seconds)

        report_details.traceback = {
            "error": error_message,
            "timestamp": end_time.isoformat(),
            "cars_proceed": cars_proceed,
            "cars_skipped": cars_skipped
        }
        report_details.cars_proceed = cars_proceed
        report_details.cars_skipped = cars_skipped
        report_details.save()
        logger.info(
            f"ReportQueryDetails обновлен с ошибкой: {error_message}. Машины: {cars_proceed} обработано, {cars_skipped} пропущено")
    except Exception as e:
        logger.error(f"Ошибка обновления ReportQueryDetails: {e}")


def update_report_details_success(report_query_id: str, parsing_end_time,
                                  parsing_start_time=None, cars_proceed=0, cars_skipped=0):
    """Обновляет ReportQueryDetails при успешном завершении парсинга."""
    try:
        report_details = ReportQueryDetails.objects.get(report_query_id=report_query_id)
        report_details.end_time = parsing_end_time

        if parsing_start_time and parsing_end_time:
            time_proceed = parsing_end_time - parsing_start_time
            total_seconds = int(time_proceed.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            report_details.time_proceed = time_obj(hours, minutes, seconds)

        report_details.cars_proceed = cars_proceed
        report_details.cars_skipped = cars_skipped
        report_details.save()
        logger.info(f"ReportQueryDetails обновлен: {cars_proceed} обработано, {cars_skipped} пропущено")
    except Exception as e:
        logger.error(f"Ошибка обновления ReportQueryDetails: {e}")


def update_report_details_traceback(report_query_id: str, traceback_data: dict):
    """Обновляет только traceback в ReportQueryDetails."""
    try:
        report_details = ReportQueryDetails.objects.get(report_query_id=report_query_id)
        report_details.traceback = traceback_data
        report_details.save()
        logger.info("Traceback обновлен в ReportQueryDetails")
    except Exception as e:
        logger.error(f"Ошибка обновления traceback: {e}")
