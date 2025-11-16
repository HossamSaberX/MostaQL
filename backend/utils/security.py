"""
Security utilities for token generation and validation
"""
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional


def generate_token(length: int = 32) -> str:
    """Generate a secure random token"""
    return secrets.token_urlsafe(length)


def hash_content(content: str) -> str:
    """Generate SHA256 hash of content"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def is_token_expired(created_at: datetime, expiry_hours: int = 24) -> bool:
    """Check if a token has expired"""
    expiry_time = created_at + timedelta(hours=expiry_hours)
    return datetime.utcnow() > expiry_time

