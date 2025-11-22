"""
Telegram notification channel implementation
"""
import requests
from loguru import logger
from typing import Any

from backend.config import settings
from backend.services.channels.base import NotificationChannel


class TelegramChannel(NotificationChannel):
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    
    def send(self, recipient: str, subject: str, content: Any) -> bool:
        """
        Send a Telegram message.
        
        Args:
            recipient: The chat_id
            subject: Message title (will be formatted as bold)
            content: Message body (supports HTML)
            
        Returns:
            bool: True if message sent successfully
        """
        if not recipient:
            logger.warning("Telegram send failed: No chat_id provided")
            return False
        
        if not settings.telegram_bot_token:
            logger.warning("Telegram send failed: Bot token not configured")
            return False
        
        url = f"{self.base_url}/sendMessage"
        
        full_message = f"<b>{subject}</b>\n\n{content}"
        
        payload = {
            "chat_id": recipient,
            "text": full_message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Telegram message sent to chat_id: {recipient}")
            return True
        except requests.exceptions.Timeout:
            logger.error(f"Telegram send timeout for chat_id: {recipient}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram send failed for chat_id {recipient}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")
            return False

