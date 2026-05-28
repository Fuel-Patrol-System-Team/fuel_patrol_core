from django.db.models import Func, CharField, Count, Q
from django.db.models.functions import TruncDate

from core.models import CarBadData


class Unnest(Func):
    function = "UNNEST"
    output_field = CharField()


def _severity_annotations() -> dict:
    return dict(
        warning=Count("id", filter=Q(severity=CarBadData.Severity.WARNING)),
        error=Count("id", filter=Q(severity=CarBadData.Severity.ERROR)),
        critical=Count("id", filter=Q(severity=CarBadData.Severity.CRITICAL)),
    )


def filter_bad_data(org_id, period_from=None, period_due=None, category=None, tags=None):
    qs = CarBadData.objects.filter(car_id__data_providers__org_id=org_id)

    if period_from:
        qs = qs.filter(datetime__date__gte=period_from)
    if period_due:
        qs = qs.filter(datetime__date__lte=period_due)
    if category:
        qs = qs.filter(category=category)
    if tags:
        qs = qs.filter(tags__overlap=list(tags))

    return qs


def get_bad_data_calendar(
        org_id,
        period_from=None,
        period_due=None,
        category=None,
        tags=None,
) -> list[dict]:
    qs = filter_bad_data(org_id, period_from, period_due, category, tags)

    rows = (
        qs
        .annotate(day=TruncDate("datetime"))
        .values("day")
        .annotate(**_severity_annotations())
        .order_by("day")
    )

    return [
        {
            "value": r["warning"] + r["error"] + r["critical"],
            "day": r["day"].isoformat(),
            "warning": r["warning"],
            "error": r["error"],
            "critical": r["critical"],
        }
        for r in rows
    ]


def get_bad_data_by_car(
        org_id,
        period_from=None,
        period_due=None,
        category=None,
        tags=None,
) -> list[dict]:
    qs = filter_bad_data(org_id, period_from, period_due, category, tags)

    rows = (
        qs
        .values("car_id__name")
        .annotate(**_severity_annotations())
        .order_by("car_id__name")
    )

    return [
        {
            "auto": r["car_id__name"],
            "warning": r["warning"],
            "error": r["error"],
            "critical": r["critical"],
        }
        for r in rows
    ]


def get_bad_data_by_tag(
        org_id,
        period_from=None,
        period_due=None,
        category=None,
        tags=None,
) -> list[dict]:
    qs = filter_bad_data(org_id, period_from, period_due, category, tags)

    rows = (
        qs
        .annotate(tag=Unnest("tags"))
        .values("tag")
        .annotate(**_severity_annotations())
        .order_by("tag")
    )

    return [
        {
            "tag": r["tag"],
            "warning": r["warning"],
            "error": r["error"],
            "critical": r["critical"],
        }
        for r in rows
    ]
