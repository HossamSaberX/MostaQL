from enum import Enum

class NotificationChannel(str, Enum):
    EMAIL = "email"
    TELEGRAM = "telegram"

class NotificationStatus(str, Enum):
    PENDING = "pending"    # matched, waiting for the next flush
    QUEUED = "queued"      # handed to a send worker
    SENT = "sent"
    FAILED = "failed"
    EXPIRED = "expired"    # too old to be useful; never sent

class EmailDelivery(str, Enum):
    INSTANT = "instant"    # first email now, then at most one every 10 minutes
    HOURLY = "hourly"
    DAILY = "daily"        # once a day at 09:00 Cairo time
