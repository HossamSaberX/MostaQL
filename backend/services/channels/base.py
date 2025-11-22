"""
Abstract interfaces for notification channels
"""
from abc import ABC, abstractmethod
from typing import Any


class NotificationChannel(ABC):
    @abstractmethod
    def send(self, recipient: str, subject: str, content: Any) -> bool:
        """
        Send a notification to a specific recipient.
        
        Args:
            recipient: The recipient identifier (email, chat_id, phone, etc.)
            subject: The notification subject/title
            content: The notification content/body
            
        Returns:
            bool: True if notification was sent successfully, False otherwise
        """
        pass

