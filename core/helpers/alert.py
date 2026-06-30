import logging
from datetime import datetime
from typing import Optional

from core.models import (
    Alert,
    AlertSubscription,
    Car,
    CarBadData,
    CarMileageReport,
    CarMotohoursReport,
    CarReport,
    Organization,
    TelegramUser,
)

logger = logging.getLogger(__name__)


def save_leak_alert(
        car: Car,
        organization: Organization,
        volume: float,
        event_datetime: datetime,
        source_car_report: Optional[CarReport] = None,
) -> Optional[Alert]:
    try:
        alert = Alert.objects.create(
            organization=organization,
            car=car,
            alert_type=Alert.AlertType.LEAK,
            source_car_report=source_car_report,
            payload={
                "volume": round(volume, 2),
                "event_dt": event_datetime.isoformat(),
                "car_name": car.name,
            },
            event_datetime=event_datetime,
        )
        logger.info(
            f"[LEAK] Алерт сохранён: car={car.name}, volume={volume}л, "
            f"org={organization.name}, alert_id={alert.id}"
        )
        return alert
    except Exception as e:
        logger.error(f"[LEAK] Ошибка сохранения алерта: car={car.id}, error={e}")
        return None


def save_fraud_motohours_alert(
        car: Car,
        organization: Organization,
        fraud_km: float,
        event_datetime: datetime,
        source_motohours_report: Optional[CarMileageReport] = None,
) -> Optional[Alert]:
    try:
        alert = Alert.objects.create(
            organization=organization,
            car=car,
            alert_type=Alert.AlertType.FRAUD,
            source_motohours_report=source_motohours_report,
            payload={
                "fraud_km": round(fraud_km, 2),
                "event_dt": event_datetime.isoformat(),
                "car_name": car.name,
            },
            event_datetime=event_datetime,
        )
        logger.info(
            f"[FRAUD] Алерт сохранён: car={car.name}, fraud_km={fraud_km}км, "
            f"org={organization.name}, alert_id={alert.id}"
        )
        return alert
    except Exception as e:
        logger.error(f"[FRAUD] Ошибка сохранения алерта: car={car.id}, error={e}")
        return None

def save_fraud_alert(
        car: Car,
        organization: Organization,
        fraud_km: float,
        event_datetime: datetime,
        source_mileage_report: Optional[CarMileageReport] = None,
        source_motohours_report: Optional[CarMotohoursReport] = None,
) -> Optional[Alert]:
    try:
        alert = Alert.objects.create(
            organization=organization,
            car=car,
            alert_type=Alert.AlertType.FRAUD,
            source_mileage_report=source_mileage_report,
            source_motohours_report=source_motohours_report,
            payload={
                "fraud_km": round(fraud_km, 2),
                "event_dt": event_datetime.isoformat(),
                "car_name": car.name,
            },
            event_datetime=event_datetime,
        )
        logger.info(
            f"[FRAUD] Алерт сохранён: car={car.name}, fraud_km={fraud_km}км, "
            f"org={organization.name}, alert_id={alert.id}"
        )
        return alert
    except Exception as e:
        logger.error(f"[FRAUD] Ошибка сохранения алерта: car={car.id}, error={e}")
        return None


def save_bad_data_alert(
        car: Car,
        organization: Organization,
        source_bad_data: CarBadData,
) -> Optional[Alert]:
    try:
        alert = Alert.objects.create(
            organization=organization,
            car=car,
            alert_type=Alert.AlertType.BAD_DATA,
            source_bad_data=source_bad_data,
            payload={
                "severity": source_bad_data.severity,
                "category": source_bad_data.category,
                "tags": source_bad_data.tags,
                "reason": source_bad_data.reason,
                "event_dt": source_bad_data.datetime.isoformat(),
                "car_name": car.name,
            },
            event_datetime=source_bad_data.datetime,
        )
        logger.info(
            f"[BAD_DATA] Алерт сохранён: car={car.name}, "
            f"severity={source_bad_data.severity}, "
            f"org={organization.name}, alert_id={alert.id}"
        )
        return alert
    except Exception as e:
        logger.error(f"[BAD_DATA] Ошибка сохранения алерта: car={car.id}, error={e}")
        return None


def save_system_alert(
        organization: Organization,
        message: str,
        event_datetime: datetime | None = None,
        car: Car | None = None,
) -> Optional[Alert]:
    try:
        alert = Alert.objects.create(
            organization=organization,
            car=car,
            alert_type=Alert.AlertType.SYSTEM,
            payload={
                "message": message,
                "event_dt": (event_datetime or datetime.utcnow()).isoformat(),
            },
            event_datetime=event_datetime or datetime.utcnow(),
            is_sent=False,
        )
        logger.info(
            f"[SYSTEM] Алерт сохранён: org={organization.name}, "
            f"message={message[:60]}, alert_id={alert.id}"
        )
        return alert
    except Exception as e:
        logger.error(f"[SYSTEM] Ошибка сохранения алерта: org={organization.id}, error={e}")
        return None


def save_leak_alerts_from_rows(
        rows: list[dict],
        organization: Organization,
        car_map: dict[str, Car],
) -> int:
    alerts = []
    for row in rows:
        car = car_map.get(row["name"])
        if car is None:
            logger.warning(f"[LEAK batch] Машина не найдена в car_map: {row['name']}")
            continue

        event_dt = row.get("datetime")
        if event_dt is None:
            event_dt = datetime.utcnow()

        alerts.append(Alert(
            organization=organization,
            car=car,
            alert_type=Alert.AlertType.LEAK,
            payload={
                "volume": round(float(row["leak"]), 2),
                "event_dt": event_dt.isoformat() if hasattr(event_dt, "isoformat") else str(event_dt),
                "car_name": row["name"],
            },
            event_datetime=event_dt,
            is_sent=False,
        ))

    if not alerts:
        return 0

    created = Alert.objects.bulk_create(alerts, ignore_conflicts=False)
    logger.info(f"[LEAK batch] Сохранено {len(created)} алертов для org={organization.name}")
    return len(created)


def save_bad_data_alerts_bulk(
        bad_data_records: list[CarBadData],
        organization: Organization,
) -> int:
    alerts = []
    for record in bad_data_records:
        alerts.append(Alert(
            organization=organization,
            car=record.car_id,
            alert_type=Alert.AlertType.BAD_DATA,
            source_bad_data=record,
            payload={
                "severity": record.severity,
                "category": record.category,
                "tags": record.tags,
                "reason": record.reason,
                "event_dt": record.datetime.isoformat(),
                "car_name": record.car_id.name,
            },
            event_datetime=record.datetime,
            is_sent=False,
        ))

    if not alerts:
        return 0

    created = Alert.objects.bulk_create(alerts, ignore_conflicts=False)
    logger.info(f"[BAD_DATA bulk] Сохранено {len(created)} алертов для org={organization.name}")
    return len(created)


def format_leak_block(alerts: list[Alert]) -> str:
    lines = ["🔴 *Сливы топлива:*"]
    for a in alerts:
        p = a.payload
        lines.append(
            f"  • {p.get('car_name', '—')} — {p.get('volume', '?')} л "
            f"({p.get('event_dt', '')[:16].replace('T', ' ')})"
        )
    return "\n".join(lines)


def format_fraud_block(alerts: list[Alert]) -> str:
    lines = ["🟠 *Накрутки пробега:*"]
    for a in alerts:
        p = a.payload
        lines.append(
            f"  • {p.get('car_name', '—')} — {p.get('fraud_km', '?')} км "
            f"({p.get('event_dt', '')[:16].replace('T', ' ')})"
        )
    return "\n".join(lines)


def format_bad_data_block(alerts: list[Alert]) -> str:
    lines = ["⚠️ *Ошибки оборудования:*"]
    for a in alerts:
        p = a.payload
        tags_str = ", ".join(p.get("tags", [])) or "—"
        lines.append(
            f"  • {p.get('car_name', '—')} [{p.get('severity', '?').upper()}] "
            f"{p.get('reason', '')[:80]} (теги: {tags_str})"
        )
    return "\n".join(lines)


def format_system_block(alerts: list[Alert]) -> str:
    lines = ["🔧 *Системные уведомления:*"]
    for a in alerts:
        p = a.payload
        lines.append(
            f"  • {p.get('message', '—')} "
            f"({p.get('event_dt', '')[:16].replace('T', ' ')})"
        )
    return "\n".join(lines)


def build_digest_message(
        subscription: AlertSubscription,
        unsent_alerts: list[Alert],
) -> str | None:
    alert_types = subscription.alert_types

    severity_order = {
        CarBadData.Severity.INFO: 0,
        CarBadData.Severity.WARNING: 1,
        CarBadData.Severity.ERROR: 2,
        CarBadData.Severity.CRITICAL: 3,
    }

    leak_alerts = []
    fraud_alerts = []
    bad_data_alerts = []
    system_alerts = []

    for alert in unsent_alerts:

        if alert.alert_type == Alert.AlertType.LEAK:
            if AlertSubscription.AlertType.LEAKS not in alert_types:
                continue
            if alert.payload.get("volume", 0) < subscription.min_leak_liters:
                continue
            leak_alerts.append(alert)

        elif alert.alert_type == Alert.AlertType.FRAUD:
            if AlertSubscription.AlertType.FRAUDS not in alert_types:
                continue
            if alert.payload.get("fraud_km", 0) < subscription.min_fraud_km:
                continue
            fraud_alerts.append(alert)


        elif alert.alert_type == Alert.AlertType.BAD_DATA:

            if AlertSubscription.AlertType.BAD_DATA not in alert_types:
                logger.warning(f"[digest:BAD_DATA] ОТФИЛЬТРОВАН — bad_data не в подписке")

                continue

            raw_severity = alert.payload.get("severity")

            alert_sev = severity_order.get(raw_severity, 0)

            min_sev = severity_order.get(subscription.bad_data_min_severity, 0)


            if alert_sev < min_sev:
                logger.warning(f"[digest:BAD_DATA] ОТФИЛЬТРОВАН — severity {alert_sev} < min {min_sev}")

                continue

            if subscription.bad_data_tags:

                alert_tags = set(alert.payload.get("tags", []))

                subscription_tags = set(subscription.bad_data_tags)

                intersection = alert_tags.intersection(subscription_tags)

                if not intersection:
                    logger.warning(f"[digest:BAD_DATA] ОТФИЛЬТРОВАН — теги не пересекаются")

                    continue

            logger.warning(f"[digest:BAD_DATA] ПРОШЁЛ все фильтры → добавлен в дайджест")

            bad_data_alerts.append(alert)

        elif alert.alert_type == Alert.AlertType.SYSTEM:
            if AlertSubscription.AlertType.SYSTEM not in alert_types:
                continue
            system_alerts.append(alert)

    if not any([leak_alerts, fraud_alerts, bad_data_alerts, system_alerts]):
        return None

    blocks = ["📋 *Дайджест уведомлений*\n"]

    if leak_alerts:
        blocks.append(format_leak_block(leak_alerts))
    if fraud_alerts:
        blocks.append(format_fraud_block(fraud_alerts))
    if bad_data_alerts:
        blocks.append(format_bad_data_block(bad_data_alerts))
    if system_alerts:
        blocks.append(format_system_block(system_alerts))

    return "\n\n".join(blocks)


def get_unsent_alerts_by_org() -> dict[str, list[Alert]]:
    unsent = (
        Alert.objects
        .filter(is_sent=False)
        .select_related("car", "organization")
        .order_by("organization_id", "alert_type", "event_datetime")
    )

    org_alerts: dict[str, list[Alert]] = {}
    for alert in unsent:
        org_id = str(alert.organization_id)
        org_alerts.setdefault(org_id, []).append(alert)

    return org_alerts


def get_tg_user_for_org_user(user):
    try:
        tg_user = user.telegram_user
        return tg_user if tg_user.is_active else None
    except TelegramUser.DoesNotExist:
        return None
