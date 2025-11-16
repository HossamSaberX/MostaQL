"""
Verify and unsubscribe endpoints
"""
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from loguru import logger

from backend.database import get_db, User
from backend.utils.security import is_token_expired

router = APIRouter()

def get_frontend_url(path: str) -> str:
    """Get absolute URL for frontend pages"""
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    return f"{base_url}/{path}"


@router.get("/verify/{token}")
async def verify_email(token: str, db: Session = Depends(get_db)):
    """
    Verify user email with token
    Token expires after 24 hours
    """
    try:
        # Find user by token
        user = db.query(User).filter(User.token == token).first()
        
        if not user:
            logger.warning(f"Verification failed: Invalid token {token[:10]}...")
            return RedirectResponse(url=get_frontend_url("verify.html?status=invalid"))
        
        # Check if already verified
        if user.verified:
            logger.info(f"User already verified: {user.email}")
            return RedirectResponse(url=get_frontend_url("verify.html?status=already_verified"))
        
        # Check token expiry
        expiry_hours = int(os.getenv("VERIFICATION_TOKEN_EXPIRY_HOURS", "24"))
        if is_token_expired(user.created_at, expiry_hours):
            logger.warning(f"Verification failed: Expired token for {user.email}")
            return RedirectResponse(url=get_frontend_url("verify.html?status=expired"))
        
        # Verify user
        user.verified = True
        db.commit()
        
        logger.info(f"User verified successfully: {user.email}")
        return RedirectResponse(url=get_frontend_url("verify.html?status=success"))
        
    except Exception as e:
        logger.error(f"Verify error for token {token[:10]}...: {e}")
        db.rollback()
        return RedirectResponse(url=get_frontend_url("verify.html?status=error"))


@router.get("/unsubscribe/{token}")
async def unsubscribe(token: str, db: Session = Depends(get_db)):
    """
    Unsubscribe user from notifications
    Token never expires (permanent unsubscribe link)
    """
    try:
        # Find user by token
        user = db.query(User).filter(User.token == token).first()
        
        if not user:
            logger.warning(f"Unsubscribe failed: Invalid token {token[:10]}...")
            return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=invalid"))
        
        # Check if already unsubscribed
        if user.unsubscribed:
            logger.info(f"User already unsubscribed: {user.email}")
            return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=already_unsubscribed"))
        
        # Unsubscribe user
        user.unsubscribed = True
        db.commit()
        
        logger.info(f"User unsubscribed: {user.email}")
        return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=success"))
        
    except Exception as e:
        logger.error(f"Unsubscribe error for token {token[:10]}...: {e}")
        db.rollback()
        return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=error"))

