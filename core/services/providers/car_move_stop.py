import logging
import time
from bisect import bisect_right
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import orjson
import requests

from core.models import Car, CarBadData, DataProvider, ReportQuery
from core.services.providers.glonass.auth_token_store import glonass_auth_token_store
from core.services.providers.glonass.glonass_general_provider import GlonassGeneralProvider
from core.services.providers.report_service import ReportService

logger = logging.getLogger(__name__)

GLONASS_BASE_URL = "https://hosting.glonasssoft.ru/api/v3/vehicles"


@dataclass
class StopMileageRecord:
    stop_start: str
    stop_end: str
    address: str
    duration_seconds: int
    mileage_before_stop: Optional[float]
    mileage: Optional[float]


class MileageStopsCalculationService:

    @staticmethod
    def calculate_stops_report(
        car_id: str,
        start_date: datetime,
        end_date: datetime,
        parser: GlonassGeneralProvider | None = None,
        is_save_bad_data: bool = True,
    ) -> Tuple[List[Dict[str, Any]] | Dict[str, Any], int]:
        report_query = None

        try:
            car, provider_obj = MileageStopsCalculationService._get_car_and_provider(car_id)
            if not car or not provider_obj:
                return {"error": "Автомобиль или провайдер не найдены"}, 400

            # TODO: YShipik добавить в ReportQuery отдельный тип, аля STOPS
            report_query, _ = ReportService.create_report(
                provider_id=str(provider_obj.id),
                report_type=ReportQuery.ReportType.MILEAGE,
                is_save_bad_data=is_save_bad_data,
            )

            validation_error = MileageStopsCalculationService._validate_dates(start_date, end_date)
            if validation_error:
                ReportService.complete_report_error(report_query, validation_error)
                return {"error": validation_error}, 400

            provider = (
                GlonassGeneralProvider(None, car, provider_obj, start_date, end_date, "mileage")
                if parser is None else parser
            )

            try:
                if not provider.authenticate():
                    error_msg = "Не удалось авторизоваться у провайдера"
                    ReportService.complete_report_error(report_query, error_msg)
                    return {"error": error_msg}, 401
            except Exception as auth_error:
                error_msg = f"Ошибка при аутентификации у провайдера: {str(auth_error)}"
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 401

            vehicle_id = car.id_in_provider_system

            try:
                moves_raw, stops_raw = MileageStopsCalculationService._fetch_moves_and_stops(
                    provider, vehicle_id, start_date, end_date
                )
            except requests.exceptions.RequestException as e:
                error_msg = f"Ошибка получения данных об остановках от провайдера: {e}"
                logger.error("Ошибка получения стоянок для car_id=%s: %s", car_id, e, exc_info=True)
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 400

            try:
                mileage_periods_raw = MileageStopsCalculationService._fetch_mileage(provider, vehicle_id, start_date, end_date)
            except requests.exceptions.RequestException as e:
                error_msg = f"Ошибка получения данных о пробеге от провайдера: {e}"
                logger.error("Ошибка получения пробега для car_id=%s: %s", car_id, e, exc_info=True)
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 400

            if not stops_raw:
                ReportService.create_bad_data_record(
                    car,
                    "Нет данных об остановках за указанный период",
                    report_query,
                    start_date, end_date,
                    CarBadData.Severity.INFO,
                    CarBadData.Category.PROVIDER_ERROR,
                    [CarBadData.Tag.MILEAGE, CarBadData.Tag.PROVIDER],
                )

                ReportService.complete_report_success(
                    report_query,
                    {"result": [], "empty_data": True, "rows_processed": 0},
                    cars_proceed=0,
                    cars_skipped=1,
                )

                return [], 200

            try:
                checkpoints = MileageStopsCalculationService._build_mileage_checkpoints(mileage_periods_raw)
                records = MileageStopsCalculationService._map_stops(stops_raw, moves_raw, checkpoints)
            except Exception as calc_error:
                error_msg = f"Ошибка при сопоставлении стоянок и пробега: {str(calc_error)}"
                logger.error("Ошибка расчёта стоянок для car_id=%s: %s", car_id, calc_error, exc_info=True)
                ReportService.complete_report_error(report_query, error_msg)
                return {"error": error_msg}, 400

            stops_list = [asdict(r) for r in records]

            ReportService.complete_report_success(
                report_query,
                {"result": stops_list, "rows_processed": len(records)},
                cars_proceed=1,
                cars_skipped=0,
            )

            return stops_list, 200

        except Car.DoesNotExist:
            error_msg = "Автомобиль не найден"
            if report_query:
                ReportService.complete_report_error(report_query, error_msg)
            return {"error": error_msg}, 400

        except ValueError as e:
            error_msg = f"Неверный формат данных: {e}"
            if report_query:
                ReportService.complete_report_error(report_query, error_msg, e)
            return {"error": error_msg}, 400

        except Exception as e:
            error_msg = "Внутренняя ошибка при обработке запроса"
            logger.error("Неожиданная ошибка расчёта стоянок для car_id=%s: %s", car_id, e, exc_info=True)
            if report_query:
                ReportService.complete_report_error(report_query, error_msg, e)
            return {"error": error_msg}, 400

    @staticmethod
    def _get_car_and_provider(car_id: str) -> Tuple[Optional[Car], Optional[DataProvider]]:
        try:
            car = Car.objects.prefetch_related('data_providers').get(id=car_id)
            provider_obj = car.data_providers.first()
            return car, provider_obj
        except Car.DoesNotExist:
            return None, None
        except Exception as e:
            logger.error(f"Ошибка при получении автомобиля car_id={car_id}: {e}")
            return None, None

    @staticmethod
    def _validate_dates(start_date: Optional[datetime], end_date: Optional[datetime]) -> Optional[str]:
        try:
            if start_date and end_date and start_date >= end_date:
                return "start_date должна быть раньше end_date."
            return None
        except Exception as e:
            return f"Ошибка валидации дат: {str(e)}"

    @staticmethod
    def _request(provider: GlonassGeneralProvider, url: str, payload: dict) -> Any:
        def _send(token: str | None):
            response = requests.post(
                url,
                json=payload,
                headers={"X-Auth": token or ""},
                timeout=(10, 60),
            )
            response.raise_for_status()
            return orjson.loads(response.content)

        provider._enforce_rate_limit()

        try:
            return _send(provider.auth_token)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401 and provider._login:
                logger.warning("401 при запросе %s — инвалидация токена и повторная попытка", url)
                glonass_auth_token_store.invalidate(provider._login)
                if not provider.authenticate():
                    raise
                return _send(provider.auth_token)

            if e.response is not None and e.response.status_code == 429:
                logger.warning("429 при запросе %s, повтор через 5 сек", url)
                time.sleep(5)
                provider._enforce_rate_limit()
                return _send(provider.auth_token)

            raise

    @staticmethod
    def _fetch_moves_and_stops(
        provider: GlonassGeneralProvider, vehicle_id: int, date_from: datetime, date_to: datetime
    ) -> Tuple[List[dict], List[dict]]:
        payload = {
            "addressFormat": "[Street] [House] [City] [State] [Country] [Coordinates]",
            "isAddStoppings": False,
            "vehicleIds": [vehicle_id],
            "from": date_from.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "to": date_to.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "timezone": 0,
        }
        data = MileageStopsCalculationService._request(provider, f"{GLONASS_BASE_URL}/moveStop", payload)
        if not data:
            return [], []
        return data[0].get("moves", []), data[0].get("stops", [])

    @staticmethod
    def _fetch_mileage(
        provider: GlonassGeneralProvider, vehicle_id: int, date_from: datetime, date_to: datetime, sampling: int = 3600
    ) -> List[dict]:
        payload = {
            "sampling": sampling,
            "vehicleIds": [vehicle_id],
            "from": date_from.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "to": date_to.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3],
            "timezone": 0,
        }
        data = MileageStopsCalculationService._request(provider, f"{GLONASS_BASE_URL}/mileageAndMotohours", payload)
        if not data:
            return []
        return data[0].get("periods", [])

    @staticmethod
    def _build_mileage_checkpoints(periods: List[dict]) -> List[Tuple[datetime, float]]:
        checkpoints: List[Tuple[datetime, float]] = []
        for period in periods:
            mileage_end = period.get("mileageEnd")
            if mileage_end is None:
                continue
            end_dt = MileageStopsCalculationService._parse_dt(period.get("end"))
            if end_dt is None:
                continue
            checkpoints.append((end_dt, float(mileage_end)))

        checkpoints.sort(key=lambda item: item[0])
        return checkpoints

    @staticmethod
    def _mileage_before(checkpoints: List[Tuple[datetime, float]], moment: datetime) -> Optional[float]:
        if not checkpoints:
            return None
        times = [item[0] for item in checkpoints]
        idx = bisect_right(times, moment) - 1
        if idx < 0:
            return None
        return checkpoints[idx][1]

    @staticmethod
    def _map_stops(
        stops_raw: List[dict], moves_raw: List[dict], checkpoints: List[Tuple[datetime, float]]
    ) -> List[StopMileageRecord]:
        moves_by_end = {m.get("end"): m for m in moves_raw if m.get("end")}

        records: List[StopMileageRecord] = []
        for stop in stops_raw:
            start_raw = stop.get("start")
            end_raw = stop.get("end")
            start_dt = MileageStopsCalculationService._parse_dt(start_raw)

            move = moves_by_end.get(start_raw)
            mileage = float(move["mileage"]) if move and move.get("mileage") is not None else None

            records.append(
                StopMileageRecord(
                    stop_start=start_raw,
                    stop_end=end_raw,
                    address=(stop.get("address") or "").strip(),
                    duration_seconds=int(stop.get("duration") or 0),
                    mileage_before_stop=(
                        MileageStopsCalculationService._mileage_before(checkpoints, start_dt)
                        if start_dt is not None else None
                    ),
                    mileage=mileage,
                )
            )

        records.sort(key=lambda r: r.stop_start or "")
        MileageStopsCalculationService._fill_missing_mileage_from_diff(records)
        return records

    @staticmethod
    def _fill_missing_mileage_from_diff(records: List[StopMileageRecord]) -> None:
        previous: Optional[StopMileageRecord] = None
        for record in records:
            if record.mileage is None and previous is not None:
                if record.mileage_before_stop is not None and previous.mileage_before_stop is not None:
                    diff = record.mileage_before_stop - previous.mileage_before_stop
                    record.mileage = round(diff, 3) if diff >= 0 else None
            previous = record

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value)
        except ValueError:
            return None