"""
Verify and unsubscribe endpoints
"""
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from loguru import logger
from html import escape
from typing import Optional
import os

from backend.database import get_db, User
from backend.utils.security import is_token_expired
from backend.config import settings
from backend.models import UnsubscribeRequest, PreferencesRequest, FollowedClientRequest
from backend.services.email import send_unsubscribe_email
from backend.services.messages import (
    GENERIC_ERROR,
    SERVICE_NAME,
    email_unsubscribe_url,
    manage_url,
)
from backend.utils.limiter import limiter
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


# The same words whether or not the address is registered, so the form cannot be used
# to find out who has an account.
UNSUBSCRIBE_REQUEST_REPLY = "إذا كان البريد مسجلاً لدينا، أرسلنا إليه رابط إدارة الاشتراك."


@router.post("/unsubscribe-request")
@limiter.limit(f"{settings.rate_limit_per_hour}/hour")
async def request_unsubscribe(
    request: Request,
    data: UnsubscribeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Mail a manage-or-unsubscribe link to a subscribed address. The reply never says
    whether the address exists.
    """
    try:
        user = db.query(User).filter(User.email == data.email).first()
        if user and not user.unsubscribed:
            background_tasks.add_task(send_unsubscribe_email, user.email, user.token)
    except Exception as e:
        logger.error(f"Unsubscribe request error: {e}")
        raise HTTPException(status_code=500, detail=GENERIC_ERROR)

    return JSONResponse(content={"message": UNSUBSCRIBE_REQUEST_REPLY})


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



def _email_unsubscribe_page(heading: str, message: str, token: Optional[str] = None, confirm: bool = False) -> HTMLResponse:
    """A small standalone page for the email footer link (no site template involved)."""
    action = ""
    if confirm and token:
        action = (
            f'<form method="post" action="{escape(email_unsubscribe_url(token), quote=True)}">'
            '<input type="hidden" name="List-Unsubscribe" value="One-Click">'
            '<button type="submit">إلغاء الاشتراك من البريد</button></form>'
        )
    elif token:
        action = f'<p><a href="{escape(manage_url(token), quote=True)}">إدارة التنبيهات</a></p>'
    return HTMLResponse(
        f'''<!DOCTYPE html>
<html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(heading)} - {escape(SERVICE_NAME)}</title>
<style>
body{{margin:0;padding:24px;background:#f4f6f8;color:#1f2933;font-family:Tahoma,Arial,sans-serif;line-height:1.8}}
main{{max-width:480px;margin:40px auto;padding:28px;background:#fff;border-radius:8px}}
h1{{font-size:22px;margin:0 0 12px}}
button{{padding:12px 24px;border:0;border-radius:6px;background:#0A6FA0;color:#fff;font:inherit;font-weight:bold;cursor:pointer}}
a{{color:#0A6FA0}}
</style></head>
<body><main><h1>{escape(heading)}</h1><p>{escape(message)}</p>{action}</main></body></html>'''
    )


@router.get("/unsubscribe/{token}/email")
async def email_unsubscribe_confirm(token: str, db: Session = Depends(get_db)):
    """
    The footer link of alert emails. Opening it changes nothing (link scanners open links);
    the change happens when the button posts to the same address.
    """
    user = db.query(User).filter(User.token == token).first()
    if not user:
        return _email_unsubscribe_page("رابط غير صالح", "يبدو أن الرابط غير صحيح.")
    return _email_unsubscribe_page(
        "إلغاء الاشتراك من البريد",
        "لن تصلك تنبيهات المشاريع على بريدك بعد الآن.",
        token,
        confirm=True,
    )


@router.post("/unsubscribe/{token}/email")
async def unsubscribe_email_only(token: str, request: Request, db: Session = Depends(get_db)):
    """
    Stop alerts by email. This is the one-click address of the List-Unsubscribe header
    (RFC 8058), so it needs no confirmation and can be repeated safely. When nothing else
    is left to send to (Telegram not linked or off), the whole subscription is stopped.
    """
    user = db.query(User).filter(User.token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="رابط غير صالح")

    try:
        user.receive_email = False
        if not (user.telegram_chat_id and user.receive_telegram):
            user.unsubscribed = True
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Email unsubscribe error for token {token[:10]}...: {e}")
        raise HTTPException(status_code=500, detail=GENERIC_ERROR)

    logger.info(f"User stopped email alerts: {user.email} (unsubscribed={user.unsubscribed})")
    message = (
        "تم إلغاء الاشتراك. لن تصلك رسائل بعد الآن."
        if user.unsubscribed
        else "تم إيقاف تنبيهات البريد. تنبيهات تيليجرام مستمرة."
    )
    if "text/html" in request.headers.get("accept", ""):
        return _email_unsubscribe_page("تم الإلغاء", message, token)
    return JSONResponse(content={"message": message, "unsubscribed": user.unsubscribed})
