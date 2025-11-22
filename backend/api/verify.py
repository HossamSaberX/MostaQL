"""
Verify and unsubscribe endpoints
"""
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from loguru import logger

from backend.database import get_db, User
from backend.utils.security import is_token_expired
from backend.config import settings
from backend.models import UnsubscribeRequest
from backend.services.email import send_unsubscribe_email

router = APIRouter()

def get_frontend_url(path: str) -> str:
    """Get absolute URL for frontend pages"""
    return f"{settings.base_url}/{path}"


@router.post("/unsubscribe-request")
async def request_unsubscribe(
    data: UnsubscribeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Request an unsubscribe link via email
    """
    try:
        user = db.query(User).filter(User.email == data.email).first()

        if not user:
            raise HTTPException(status_code=404, detail="البريد غير مسجل لدينا")

        if user.unsubscribed:
            raise HTTPException(status_code=400, detail="البريد غير مشترك حالياً")

        background_tasks.add_task(send_unsubscribe_email, user.email, user.token)

        return JSONResponse(
            content={"message": "تم إرسال رابط إلغاء الاشتراك إلى بريدك الإلكتروني."}
        )

    except HTTPException as exc:
        logger.warning(f"Unsubscribe request rejected for {data.email}: {exc.detail}")
        raise exc
    except Exception as e:
        logger.error(f"Unsubscribe request error for {data.email}: {e}")
        raise HTTPException(status_code=500, detail="خطأ داخلي، حاول لاحقاً")


@router.get("/verify/{token}")
async def verify_email(token: str, db: Session = Depends(get_db)):
    """
    Verify user email with token
    Token expires after 24 hours
    """
    try:
        user = db.query(User).filter(User.token == token).first()
        
        if not user:
            logger.warning(f"Verification failed: Invalid token {token[:10]}...")
            return RedirectResponse(url=get_frontend_url("verify.html?status=invalid"))
        
        if user.verified:
            logger.info(f"User already verified: {user.email}")
            return RedirectResponse(url=get_frontend_url(f"verify.html?status=already_verified&token={user.token}"))
        
        issued_at = user.token_issued_at or user.created_at
        if issued_at and is_token_expired(issued_at, settings.verification_token_expiry_hours):
            logger.warning(f"Verification failed: Expired token for {user.email}")
            return RedirectResponse(url=get_frontend_url("verify.html?status=expired"))
        
        user.verified = True
        db.commit()
        
        logger.info(f"User verified successfully: {user.email}")
        return RedirectResponse(url=get_frontend_url(f"verify.html?status=success&token={user.token}"))
        
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
        user = db.query(User).filter(User.token == token).first()
        
        if not user:
            logger.warning(f"Unsubscribe failed: Invalid token {token[:10]}...")
            return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=invalid"))
        
        if user.unsubscribed:
            logger.info(f"User already unsubscribed: {user.email}")
            return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=already_unsubscribed"))
        
        user.unsubscribed = True
        db.commit()
        
        logger.info(f"User unsubscribed: {user.email}")
        return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=success"))
        
    except Exception as e:
        logger.error(f"Unsubscribe error for token {token[:10]}...: {e}")
        db.rollback()
        return RedirectResponse(url=get_frontend_url("unsubscribe.html?status=error"))

