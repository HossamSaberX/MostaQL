"""
Email notification channel implementation
"""
from typing import Any
from loguru import logger

from backend.services.channels.base import NotificationChannel
from backend.services.notification_queue import EmailTask, email_task_queue


class EmailChannel(NotificationChannel):
    def send(self, recipient: str, subject: str, content: Any) -> bool:
        """
        Send an email notification by enqueueing it to the email task queue.
        
        Args:
            recipient: Email address or "undisclosed-recipients:;" for BCC batch
            subject: Category name (used as email subject)
            content: Dict containing 'jobs', 'notification_ids', 'unsubscribe_token', and optionally 'bcc'
            
        Returns:
            bool: True if task was enqueued successfully
        """
        if not isinstance(content, dict):
            logger.error(f"EmailChannel.send expects dict content, got {type(content)}")
            return False
        
        jobs = content.get("jobs", [])
        notification_ids = content.get("notification_ids", [])
        unsubscribe_token = content.get("unsubscribe_token")
        bcc = content.get("bcc")
        
        if not jobs or not notification_ids:
            logger.warning("EmailChannel.send: Missing jobs or notification_ids")
            return False
        
        try:
            task = EmailTask(
                notification_ids=notification_ids,
                email=recipient,
                category_name=subject,
                jobs=jobs,
                unsubscribe_token=unsubscribe_token,
                bcc=bcc,
            )
            email_task_queue.enqueue(task)
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue email task: {e}")
            return False

