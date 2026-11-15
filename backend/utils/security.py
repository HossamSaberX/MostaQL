"""
Security utilities for token generation and validation
"""
import base64
import calendar
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from backend.config import settings

TELEGRAM_LINK_CODE_HOURS = 24
_TELEGRAM_LINK_PURPOSE = b"telegram-link:"  # keeps these signatures apart from any other use of the key


def generate_token(length: int = 32) -> str:
    """Generate a secure random token"""
    return secrets.token_urlsafe(length)


def hash_content(content: str) -> str:
    """Generate SHA256 hash of content"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def is_token_expired(issued_at: datetime, expiry_hours: int = 24) -> bool:
    """Check if a token has expired"""
    expiry_time = issued_at + timedelta(hours=expiry_hours)
    return datetime.utcnow() > expiry_time


def _epoch(moment: datetime) -> int:
    return calendar.timegm(moment.utctimetuple())


def _link_signature(payload: str) -> str:
    digest = hmac.new(
        settings.secret_key.encode(), _TELEGRAM_LINK_PURPOSE + payload.encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode()


def make_telegram_link_code(user_id: int, now: Optional[datetime] = None) -> str:
    """
    A signed code for the Telegram deep link: "<user id>_<expiry>_<signature>".

    It proves only "this person may link a chat to this account" for a day. Unlike the
    account token it cannot read or change preferences, so a shared link is harmless.
    Characters are [0-9A-Za-z_-], well under Telegram's 64-character limit.
    """
    expires = _epoch(now or datetime.utcnow()) + TELEGRAM_LINK_CODE_HOURS * 3600
    payload = f"{int(user_id)}_{expires}"
    return f"{payload}_{_link_signature(payload)}"


def read_telegram_link_code(code: str, now: Optional[datetime] = None) -> Optional[int]:
    """The user id inside a valid, unexpired link code; None for anything else (never raises)."""
    try:
        user_id, expires, signature = str(code).strip().split("_", 2)
        if not (user_id.isascii() and user_id.isdigit() and expires.isascii() and expires.isdigit()):
            return None
        if not hmac.compare_digest(signature.encode(), _link_signature(f"{user_id}_{expires}").encode()):
            return None
        if int(expires) < _epoch(now or datetime.utcnow()):
            return None
        return int(user_id)
    except (ValueError, TypeError, UnicodeError):
        return None
