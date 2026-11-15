"""
Security utilities for token generation and validation
"""
import base64
import calendar
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

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


def new_link_nonce() -> str:
    """The one-time part of a Telegram link, stored on the account until the link is used."""
    return secrets.token_urlsafe(16)


def _link_signature(user_id: str, expires: str, nonce: str) -> str:
    message = f"{user_id}_{expires}_{nonce}"
    digest = hmac.new(
        settings.secret_key.encode(), _TELEGRAM_LINK_PURPOSE + message.encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode()


def make_telegram_link_code(user_id: int, nonce: str, now: Optional[datetime] = None) -> str:
    """
    A signed code for the Telegram deep link: "<user id>_<expiry>_<signature>".

    It proves only "this person may link a chat to this account", for a day and only while
    `nonce` is the one stored on the account. Unlike the account token it cannot read or
    change preferences. Characters are [0-9A-Za-z_-], well under Telegram's 64-character limit.
    """
    expires = str(_epoch(now or datetime.utcnow()) + TELEGRAM_LINK_CODE_HOURS * 3600)
    return f"{int(user_id)}_{expires}_{_link_signature(str(int(user_id)), expires, nonce)}"


def _split_link_code(code: str) -> Optional[Tuple[str, str, str]]:
    try:
        user_id, expires, signature = str(code).strip().split("_", 2)
    except ValueError:
        return None
    # Short ASCII digits only, so a made-up id never reaches the database as a huge number.
    for part, max_digits in ((user_id, 18), (expires, 12)):
        if not (part.isascii() and part.isdigit() and len(part) <= max_digits):
            return None
    return user_id, expires, signature


def telegram_link_code_user_id(code: str) -> Optional[int]:
    """
    The account a link code names, NOT yet checked: load that account and pass it to
    `verify_telegram_link_code` before trusting it.
    """
    parts = _split_link_code(code)
    return int(parts[0]) if parts else None


def verify_telegram_link_code(
    code: str, user_id: int, nonce: Optional[str], now: Optional[datetime] = None
) -> bool:
    """True when the code was made for this account with its stored nonce and has not expired."""
    if not nonce:
        return False  # no link is open for this account
    parts = _split_link_code(code)
    if not parts:
        return False
    code_user_id, expires, signature = parts
    if int(code_user_id) != int(user_id):
        return False
    try:
        expected = _link_signature(code_user_id, expires, nonce)
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            return False
    except UnicodeError:
        return False
    return int(expires) >= _epoch(now or datetime.utcnow())
