import logging
from django.db import transaction
from django.core.exceptions import PermissionDenied

from core.models import AlertSubscription, TelegramUser, OrgUser

logger = logging.getLogger(__name__)


def get_or_create_subscription(user: OrgUser) -> AlertSubscription:
    subscription, _ = AlertSubscription.objects.get_or_create(
        user=user,
        defaults={
            'alert_types': [],
            'bad_data_tags': [],
            'min_fraud_km': 0.0,
            'min_leak_liters': 0.0,
            'notify_hour': 5,
            'is_active': False,
        }
    )
    return subscription


def check_telegram_user(user: OrgUser) -> None:
    has_tg = TelegramUser.objects.filter(
        user=user,
        is_active=True,
    ).exists()

    if not has_tg:
        raise PermissionDenied(
            "Для настройки уведомлений через Telegram необходимо привязать Telegram-аккаунт. "
            "Обратитесь к администратору или запустите бота по ссылке в ЛК."
        )


def check_user_email(user: OrgUser) -> None:
    if not user.email:
        raise PermissionDenied(
            "Для настройки уведомлений через почту необходимо указать email в профиле."
        )


def check_sent_via_requirements(user: OrgUser, sent_via: str) -> None:
    errors = []

    if sent_via in (AlertSubscription.SentVia.TELEGRAM, AlertSubscription.SentVia.BOTH):
        try:
            check_telegram_user(user)
        except PermissionDenied as e:
            errors.append(str(e))

    if sent_via in (AlertSubscription.SentVia.EMAIL, AlertSubscription.SentVia.BOTH):
        try:
            check_user_email(user)
        except PermissionDenied as e:
            errors.append(str(e))

    if errors:
        raise PermissionDenied(" ".join(errors))


@transaction.atomic
def patch_subscription(user: OrgUser, validated_data: dict) -> AlertSubscription:
    subscription = get_or_create_subscription(user)

    for field, value in validated_data.items():
        setattr(subscription, field, value)

    subscription.full_clean()
    subscription.save()

    logger.info(
        f"AlertSubscription обновлена: user={user.username}, "
        f"alert_types={subscription.alert_types}, "
        f"notify_hour={subscription.notify_hour}"
    )
    return subscription
