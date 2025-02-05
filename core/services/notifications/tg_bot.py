import logging
import os
from telebot import TeleBot

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def get_telegram_bot(token):
    if hasattr(get_telegram_bot, 'bot'): return get_telegram_bot.bot

    get_telegram_bot.bot = TeleBot(token)
    return get_telegram_bot.bot

def send_telegram_message(chat_bot,chat_id: int, message: str):
    try:
        chat_bot.send_message(chat_id, message)

    except Exception as e:
        logger.error(f"{send_telegram_message.__name__} {e}")