import os
import logging
from typing import List, Union

import requests

from core.models import Organization, TelegramUser

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096


def _get_bot_token() -> str:
    token = os.getenv('ALERT_BOT_TOKEN')
    if not token:
        logger.error("ALERT_BOT_TOKEN not found in environment variables")
        raise ValueError("ALERT_BOT_TOKEN is required")
    return token


def _get_api_base_url() -> str:
    custom_api_url = os.getenv('TELEGRAM_API_URL')
    return custom_api_url.rstrip('/') if custom_api_url else "https://api.telegram.org"


def _split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]

    chunks = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def send_telegram_message(chat_id: Union[str, int], message: str) -> bool:
    url = f"{_get_api_base_url()}/bot{_get_bot_token()}/sendMessage"
    chunks = _split_message(message)

    if len(chunks) > 1:
        logger.info(
            f"Message for chat_id {chat_id} is {len(message)} chars, "
            f"splitting into {len(chunks)} messages"
        )

    try:
        for i, chunk in enumerate(chunks, start=1):
            if len(chunks) > 1:
                chunk = f"({i}/{len(chunks)})\n{chunk}"

            response = requests.post(
                url,
                json={
                    "chat_id": int(chat_id),
                    "text": chunk,
                    "parse_mode": "Markdown",
                },
                timeout=(10, 30),
            )
            data = response.json() if response.content else {}
            if response.status_code != 200 or not data.get("ok"):
                logger.error(
                    f"{send_telegram_message.__name__} error for chat_id {chat_id}: "
                    f"HTTP {response.status_code} {response.text[:300]}"
                )
                return False

        logger.info(f"Message sent to chat_id {chat_id}")
        return True
    except Exception as e:
        logger.error(f"{send_telegram_message.__name__} error for chat_id {chat_id}: {str(e)}")
        return False


def notify_organization(organization_id: str, message: str) -> List[str]:
    failed_chats = []

    try:
        users = TelegramUser.objects.filter(
            user__org_id=organization_id,
            is_active=True,
        ).values_list('chat_id', flat=True)

        if not users:
            logger.warning(f"No active Telegram users found for organization {organization_id}")
            return failed_chats

        logger.info(f"Sending notification to {len(users)} users in organization {organization_id}")

        for chat_id in users:
            success = send_telegram_message(chat_id, message)
            if not success:
                failed_chats.append(str(chat_id))

        if failed_chats:
            logger.error(f"Failed to send to {len(failed_chats)} users in org {organization_id}: {failed_chats}")
        else:
            logger.info(f"Successfully notified all {len(users)} users in org {organization_id}")

    except Organization.DoesNotExist:
        logger.error(f"Organization {organization_id} not found")
    except Exception as e:
        logger.error(f"{notify_organization.__name__} error: {str(e)}")

    return failed_chats


def deactivate_inactive_users(failed_chats: List[str]) -> int:
    if not failed_chats:
        return 0

    updated = TelegramUser.objects.filter(
        chat_id__in=failed_chats,
        is_active=True
    ).update(is_active=False)

    logger.info(f"Deactivated {updated} users due to delivery failures")
    return updated


def _format_org_notification(org_stats: dict, start_date: str, end_date: str) -> str:
    lines = [
        "📊 *Отчёт о плановом парсинге данных*",
        f"📅 Период: {start_date} - {end_date}",
        f"🏢 Организация: {org_stats['org_name']}",
        ""
    ]

    total_success = 0
    total_failed = 0
    total_cars = 0

    for provider in org_stats['providers']:
        lines.append(f"*Провайдер: {provider['provider_name']}*")
        lines.append(f"   ✅ Успешно: {provider['cars_success']}")
        lines.append(f"   ❌ Ошибок: {provider['cars_failed']}")

        if provider['errors']:
            lines.append("   ⚠️ Детали ошибок:")
            for error in provider['errors'][:3]:
                if 'car_id' in error:
                    lines.append(f"      - Машина {error['car_id'][:8]}...: {error['error'][:100]}")
            if len(provider['errors']) > 3:
                lines.append(f"      ... и ещё {len(provider['errors']) - 3} ошибок")

        lines.append("")

        total_success += provider['cars_success']
        total_failed += provider['cars_failed']
        total_cars += provider['cars_total']

    lines.append("📈 *Общий итог:*")
    lines.append(f"   Всего машин: {total_cars}")
    lines.append(f"   ✅ Успешно обработано: {total_success}")
    lines.append(f"   ❌ С ошибками: {total_failed}")

    if total_failed > 0:
        lines.append("")
        lines.append("🔍 Детали ошибок доступны в панели администратора")

    return "\n".join(lines)