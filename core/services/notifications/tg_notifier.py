import os
import logging
from typing import List, Union
from telebot import TeleBot
from django.conf import settings

from core.models import Organization, TelegramUser

logger = logging.getLogger(__name__)

def get_telegram_bot() -> TeleBot:
    if hasattr(get_telegram_bot, 'bot'):
        return get_telegram_bot.bot

    token = os.getenv('ALERT_BOT_TOKEN')
    if not token:
        logger.error("ALERT_BOT_TOKEN not found in environment variables")
        raise ValueError("ALERT_BOT_TOKEN is required")

    proxy = os.getenv('HTTPS_PROXY') or os.getenv('ALL_PROXY')
    if proxy:
        from telebot import apihelper
        apihelper.proxy = {'https': proxy}
        logger.info(f"Telegram bot using proxy: {proxy}")

    get_telegram_bot.bot = TeleBot(token)
    logger.info("Telegram bot initialized")
    return get_telegram_bot.bot

notification_bot = get_telegram_bot()


def send_telegram_message(chat_id: Union[str, int], message: str) -> bool:
    """
    Отправляет сообщение конкретному пользователю Telegram

    Args:
        chat_id: ID чата получателя
        message: Текст сообщения

    Returns:
        bool: True если отправлено успешно, False в случае ошибки
    """
    try:
        notification_bot.send_message(
            chat_id=int(chat_id),
            text=message
        )
        logger.info(f"Message sent to chat_id {chat_id}")
        return True

    except Exception as e:
        logger.error(f"{send_telegram_message.__name__} error for chat_id {chat_id}: {str(e)}")
        return False


def notify_organization(organization_id: str, message: str) -> List[str]:
    """
    Отправляет сообщение всем активным Telegram-пользователям организации

    Args:
        organization_id: UUID организации
        message: Текст сообщения для рассылки

    Returns:
        List[str]: Список chat_id, которым не удалось отправить сообщение
    """
    failed_chats = []

    try:
        users = TelegramUser.objects.filter(
            organization_id=organization_id,
            is_active=True
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


def notify_all_organizations(message: str) -> dict:
    """
    Отправляет сообщение всем активным пользователям всех организаций

    Args:
        message: Текст сообщения

    Returns:
        dict: Статистика по организациям {org_id: failed_chats_count}
    """
    stats = {}

    try:
        organizations = Organization.objects.filter(
            telegram_users__is_active=True
        ).distinct()

        for org in organizations:
            failed = notify_organization(str(org.id), message)
            if failed:
                stats[str(org.id)] = len(failed)

        logger.info(f"Broadcast completed. Stats: {stats}")

    except Exception as e:
        logger.error(f"{notify_all_organizations.__name__} error: {str(e)}")

    return stats


def deactivate_inactive_users(failed_chats: List[str]) -> int:
    """
    Деактивирует пользователей, которым не удалось отправить сообщение
    (например, если бот заблокирован)

    Args:
        failed_chats: Список chat_id для деактивации

    Returns:
        int: Количество деактивированных пользователей
    """
    if not failed_chats:
        return 0

    updated = TelegramUser.objects.filter(
        chat_id__in=failed_chats,
        is_active=True
    ).update(is_active=False)

    logger.info(f"Deactivated {updated} users due to delivery failures")
    return updated


def _format_org_notification(org_stats: dict, start_date: str, end_date: str) -> str:
    """
    Формирует сообщение для уведомления организации о результатах парсинга
    """
    lines = [
        "📊 <b>Отчёт о плановом парсинге данных</b>",
        f"📅 Период: {start_date} - {end_date}",
        f"🏢 Организация: {org_stats['org_name']}",
        ""
    ]

    total_success = 0
    total_failed = 0
    total_cars = 0

    for provider in org_stats['providers']:
        lines.append(f"<b>Провайдер: {provider['provider_name']}</b>")
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

    lines.append("📈 <b>Общий итог:</b>")
    lines.append(f"   Всего машин: {total_cars}")
    lines.append(f"   ✅ Успешно обработано: {total_success}")
    lines.append(f"   ❌ С ошибками: {total_failed}")

    if total_failed > 0:
        lines.append("")
        lines.append("🔍 Детали ошибок доступны в панели администратора")

    return "\n".join(lines)

