"""
Verify and unsubscribe endpoints
"""
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from loguru import logger
import os

from backend.database import get_db, User
from backend.utils.security import is_token_expired
from backend.config import settings
from backend.models import UnsubscribeRequest, PreferencesRequest, FollowedClientRequest
from backend.services.email import send_unsubscribe_email
from backend.services.client_tracking import (
    FollowedClientError,
    add_followed_client,
    remove_followed_client,
)

templates_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=templates_dir)

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
            content={"message": "تم إرسال رابط إدارة الاشتراك إلى بريدك الإلكتروني."}
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
async def unsubscribe_page(token: str, request: Request, db: Session = Depends(get_db)):
    """
    Show preferences management page instead of immediately unsubscribing
    Token never expires (permanent link)
    """
    try:
        user = db.query(User).filter(User.token == token).first()
        
        if not user:
            logger.warning(f"Invalid token for preferences: {token[:10]}...")
            return templates.TemplateResponse(
                "preferences.html",
                {
                    "request": request, 
                    "title": "إدارة التفضيلات", 
                    "error": "رابط غير صالح",
                    "token": token
                }
            )
        
        return templates.TemplateResponse(
            "preferences.html",
            {
                "request": request,
                "title": "إدارة التفضيلات",
                "telegram_bot_username": settings.telegram_bot_username,
                "token": token
            }
        )
        
    except Exception as e:
        logger.error(f"Error loading preferences page for token {token[:10]}...: {e}")
        return templates.TemplateResponse(
            "preferences.html",
            {"request": request, "title": "إدارة التفضيلات", "error": "حدث خطأ"}
        )


@router.get("/preferences/{token}")
async def get_preferences(token: str, db: Session = Depends(get_db)):
    """
    Get current user preferences
    """
    try:
        user = db.query(User).filter(User.token == token).first()
        
        if not user:
            raise HTTPException(status_code=404, detail="رابط غير صالح")
        
        return JSONResponse(content={
            "receive_email": user.receive_email,
            "receive_telegram": user.receive_telegram,
            "unsubscribed": user.unsubscribed,
            "verified": user.verified,
            "min_hiring_rate": user.min_hiring_rate,
            "require_projects_in_progress": user.require_projects_in_progress,
            "require_ongoing_communications": user.require_ongoing_communications,
            "min_budget_usd": user.min_budget_usd,
            "require_verified_client": user.require_verified_client,
            "max_project_age_minutes": user.max_project_age_minutes,
            "followed_clients": [
                {
                    "id": followed_client.id,
                    "profile_url": followed_client.profile_url,
                    "label": followed_client.label,
                }
                for followed_client in user.followed_clients
            ],
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting preferences for token {token[:10]}...: {e}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تحميل التفضيلات")


@router.post("/preferences")
async def update_preferences(
    data: PreferencesRequest,
    db: Session = Depends(get_db)
):
    """
    Update user preferences
    """
    try:
        user = db.query(User).filter(User.token == data.token).first()
        
        if not user:
            raise HTTPException(status_code=404, detail="رابط غير صالح")
        
        if user.unsubscribed:
            raise HTTPException(status_code=400, detail="الحساب غير مشترك حالياً")
        
        if not data.receive_email and not data.receive_telegram:
            raise HTTPException(status_code=400, detail="يجب اختيار طريقة إشعار واحدة على الأقل")
        
        user.receive_email = data.receive_email
        user.receive_telegram = data.receive_telegram
        user.min_hiring_rate = data.min_hiring_rate
        user.require_projects_in_progress = data.require_projects_in_progress
        user.require_ongoing_communications = data.require_ongoing_communications
        user.min_budget_usd = data.min_budget_usd
        user.require_verified_client = data.require_verified_client
        user.max_project_age_minutes = data.max_project_age_minutes
        db.commit()
        
        logger.info(f"Preferences updated for {user.email}: email={data.receive_email}, telegram={data.receive_telegram}, rate={data.min_hiring_rate}")
        
        return JSONResponse(content={
            "message": "تم حفظ التفضيلات بنجاح",
            "receive_email": data.receive_email,
            "receive_telegram": data.receive_telegram,
            "min_hiring_rate": data.min_hiring_rate,
            "require_projects_in_progress": data.require_projects_in_progress,
            "require_ongoing_communications": data.require_ongoing_communications,
            "min_budget_usd": data.min_budget_usd,
            "require_verified_client": data.require_verified_client,
            "max_project_age_minutes": data.max_project_age_minutes,
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating preferences: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء حفظ التفضيلات")


@router.post("/preferences/clients")
async def add_previously_completed_client(
    data: FollowedClientRequest,
    db: Session = Depends(get_db),
):
    """Watch every new project published by a client from a user's past work."""

    user = db.query(User).filter(User.token == data.token).first()
    if not user:
        raise HTTPException(status_code=404, detail="رابط غير صالح")
    if user.unsubscribed:
        raise HTTPException(status_code=400, detail="الحساب غير مشترك حالياً")

    try:
        followed_client = add_followed_client(
            db,
            user.id,
            data.profile_url,
            data.label,
            max_clients=settings.max_followed_clients_per_user,
        )
        return JSONResponse(
            content={
                "message": "تمت إضافة العميل إلى قائمة المتابعة. ستصلك مشاريعه الجديدة من أي تصنيف.",
                "status": "saved",
                "client": {
                    "id": followed_client.id,
                    "profile_url": followed_client.profile_url,
                    "label": followed_client.label,
                },
            }
        )
    except FollowedClientError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as exc:
        db.rollback()
        logger.error(f"Error adding followed client for {user.email}: {exc}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إضافة العميل")


@router.delete("/preferences/clients/{followed_client_id}")
async def remove_previously_completed_client(
    followed_client_id: int,
    token: str,
    db: Session = Depends(get_db),
):
    """Stop watching one previously completed client."""

    user = db.query(User).filter(User.token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="رابط غير صالح")

    try:
        remove_followed_client(db, user.id, followed_client_id)
        return JSONResponse(content={"message": "تم حذف العميل من قائمة المتابعة"})
    except FollowedClientError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as exc:
        db.rollback()
        logger.error(f"Error removing followed client {followed_client_id}: {exc}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء حذف العميل")


@router.post("/unsubscribe/{token}")
async def unsubscribe_all(token: str, db: Session = Depends(get_db)):
    """
    Unsubscribe user from all notifications (master switch)
    """
    try:
        user = db.query(User).filter(User.token == token).first()
        
        if not user:
            raise HTTPException(status_code=404, detail="رابط غير صالح")
        
        if user.unsubscribed:
            return JSONResponse(content={"message": "تم إلغاء الاشتراك مسبقاً"})
        
        user.unsubscribed = True
        db.commit()
        
        logger.info(f"User unsubscribed from all: {user.email}")
        return JSONResponse(content={"message": "تم إلغاء الاشتراك بنجاح"})
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unsubscribe error for token {token[:10]}...: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إلغاء الاشتراك")

