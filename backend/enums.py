from enum import Enum

class NotificationChannel(str, Enum):
    EMAIL = "email"
    TELEGRAM = "telegram"

class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
