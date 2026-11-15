"""
Verify and unsubscribe endpoints
"""
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session
from loguru import logger
from datetime import datetime, timedelta
from typing import Any, Dict

from backend.templating import templates
from backend.database import get_db, Notification, User
from backend.enums import NotificationStatus
from backend.config import settings
from backend.models import UnsubscribeRequest, PreferencesRequest, FollowedClientRequest, TokenRequest
from backend.services.email import send_unsubscribe_email
from backend.services.messages import (
    GENERIC_ERROR,
    count_projects,
    email_unsubscribe_url,
    manage_url,
)
from backend.services.subscription_service import (
    SubscriptionError,
    SubscriptionService,
    confirmation_expired,
    issue_telegram_link,
)
from backend.utils.limiter import limiter
from backend.services.client_tracking import (
    FollowedClientError,
    add_followed_client,
    remove_followed_client,
)


router = APIRouter()

INVALID_LINK = "الرابط غير صالح. افتح آخر رسالة وصلتك منا، أو اطلب رابطًا جديدًا."


def _user_by_token(db: Session, token: str) -> User:
    user = db.query(User).filter(User.token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail=INVALID_LINK)
    return user


def _telegram_link(db: Session, user: User):
    """A fresh one-tap link to connect Telegram, or None when it is already connected."""
    return None if user.telegram_chat_id else issue_telegram_link(db, user)


def _sent_this_week(db: Session, user: User) -> int:
    """How many different projects reached this person in the last 7 days."""
    since = datetime.utcnow() - timedelta(days=7)
    return (
        db.query(func.count(distinct(Notification.job_id)))
        .filter(
            Notification.user_id == user.id,
            Notification.status == NotificationStatus.SENT.value,
            Notification.sent_at >= since,
        )
        .scalar()
        or 0
    )


def sent_this_week_text(count: int) -> str:
    if count <= 0:
        return "لم نرسل لك مشاريع هذا الأسبوع بعد."
    return f"أُرسل لك {count_projects(count)} هذا الأسبوع."


def _preferences_payload(db: Session, user: User) -> Dict[str, Any]:
    sent = _sent_this_week(db, user)
    return {
        "email": user.email,
        "receive_email": user.receive_email,
        "receive_telegram": user.receive_telegram,
        "email_delivery": user.email_delivery,
        "telegram_linked": bool(user.telegram_chat_id),
        "telegram_link": _telegram_link(db, user),
        "unsubscribed": user.unsubscribed,
        "verified": user.verified,
        "category_ids": sorted(link.category_id for link in user.categories),
        "min_hiring_rate": user.min_hiring_rate,
        "require_projects_in_progress": user.require_projects_in_progress,
        "require_ongoing_communications": user.require_ongoing_communications,
        "min_budget_usd": user.min_budget_usd,
        "require_verified_client": user.require_verified_client,
        "max_project_age_minutes": user.max_project_age_minutes,
        "keywords_include": user.keywords_include or "",
        "keywords_exclude": user.keywords_exclude or "",
        "sent_this_week": sent,
        "sent_this_week_text": sent_this_week_text(sent),
        "followed_clients": [
            {
                "id": followed_client.id,
                "profile_url": followed_client.profile_url,
                "label": followed_client.label,
            }
            for followed_client in user.followed_clients
        ],
    }


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
    Mail the manage link to any account, paused ones too (they can turn alerts back on there).
    The reply never says whether the address exists.
    """
    try:
        user = db.query(User).filter(User.email == data.email).first()
        if user:
            background_tasks.add_task(send_unsubscribe_email, user.email, user.token)
    except Exception as e:
        logger.error(f"Unsubscribe request error: {e}")
        raise HTTPException(status_code=500, detail=GENERIC_ERROR)

    return JSONResponse(content={"message": UNSUBSCRIBE_REQUEST_REPLY})


@router.get("/verify/{token}")
async def verify_email(token: str, request: Request, db: Session = Depends(get_db)):
    """
    Confirm an email address and show the result right here. The page is rendered on the
    server, so the account token never travels on to another address.
    """
    context: Dict[str, Any] = {"request": request, "title": "تأكيد البريد الإلكتروني"}
    try:
        user = db.query(User).filter(User.token == token).first()
        if not user:
            logger.warning(f"Verification failed: Invalid token {token[:10]}...")
            status = "invalid"
        elif user.verified:
            status = "already_verified"
        elif confirmation_expired(user):
            logger.warning(f"Verification failed: Expired token for {user.email}")
            status = "expired"
        else:
            user.verified = True
            user.telegram_link_nonce = None  # a Telegram link from before the inbox was proven stops working
            db.commit()
            logger.info(f"User verified successfully: {user.email}")
            status = "success"

        if status in ("success", "already_verified"):
            context["manage_link"] = manage_url(user.token)
            context["paused"] = bool(user.unsubscribed)
        # Only right after confirming: a link scanner reopening the email must not replace it.
        if status == "success" and user.receive_telegram:
            context["telegram_link"] = _telegram_link(db, user)
    except Exception as e:
        logger.error(f"Verify error for token {token[:10]}...: {e}")
        db.rollback()
        status = "error"

    context["status"] = status
    return templates.TemplateResponse(request, "verify.html", context)


@router.get("/unsubscribe/{token}")
async def unsubscribe_page(token: str, request: Request, db: Session = Depends(get_db)):
    """The preferences page (the "manage alerts" link). The token never expires."""
    context: Dict[str, Any] = {
        "request": request,
        "title": "إدارة التنبيهات",
        "token": token,
        "max_categories": settings.max_categories_per_user,
    }
    try:
        if not db.query(User.id).filter(User.token == token).first():
            logger.warning(f"Invalid token for preferences: {token[:10]}...")
            context["error"] = "invalid"
    except Exception as e:
        logger.error(f"Error loading preferences page for token {token[:10]}...: {e}")
        context["error"] = "error"
    return templates.TemplateResponse(request, "preferences.html", context)


@router.get("/preferences/{token}")
async def get_preferences(token: str, db: Session = Depends(get_db)):
    """Everything the preferences page shows."""
    try:
        user = _user_by_token(db, token)
        return JSONResponse(content=_preferences_payload(db, user))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting preferences for token {token[:10]}...: {e}")
        raise HTTPException(status_code=500, detail="تعذر تحميل إعداداتك. حدّث الصفحة بعد قليل.")


@router.post("/preferences")
async def update_preferences(
    data: PreferencesRequest,
    db: Session = Depends(get_db)
):
    """Save the preferences page: categories, channels, email timing and filters."""
    user = _user_by_token(db, data.token)
    try:
        SubscriptionService(db).update_preferences(user, data.category_ids, **data.alert_settings())
    except SubscriptionError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:
        logger.error(f"Error updating preferences: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="تعذر حفظ التغييرات. حاول مرة أخرى بعد قليل.")

    logger.info(f"Preferences updated for {user.email}")
    return JSONResponse(content={"message": "حُفظت التغييرات.", **_preferences_payload(db, user)})


@router.post("/resubscribe/{token}")
async def resubscribe(token: str, db: Session = Depends(get_db)):
    """Turn alerts back on from the preferences page, with at least one working channel."""
    user = _user_by_token(db, token)
    try:
        user.unsubscribed = False
        if not (user.receive_email or (user.receive_telegram and user.telegram_chat_id)):
            user.receive_email = True
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Resubscribe error for token {token[:10]}...: {e}")
        raise HTTPException(status_code=500, detail=GENERIC_ERROR)

    logger.info(f"User turned alerts back on: {user.email}")
    return JSONResponse(content={"message": "عادت التنبيهات للعمل.", **_preferences_payload(db, user)})


@router.post("/preferences/telegram/unlink")
async def unlink_telegram(data: TokenRequest, db: Session = Depends(get_db)):
    """Disconnect the Telegram chat, for example one that was linked by mistake."""
    user = _user_by_token(db, data.token)
    try:
        user.telegram_chat_id = None
        user.telegram_link_nonce = None
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Telegram unlink error for token {data.token[:10]}...: {e}")
        raise HTTPException(status_code=500, detail=GENERIC_ERROR)

    logger.info(f"Telegram unlinked from the preferences page: {user.email}")
    return JSONResponse(content={"message": "فصلنا تيليجرام عن حسابك.", **_preferences_payload(db, user)})


@router.post("/preferences/clients")
async def add_previously_completed_client(
    data: FollowedClientRequest,
    db: Session = Depends(get_db),
):
    """Watch every new project published by a client from a user's past work."""

    user = _user_by_token(db, data.token)
    if user.unsubscribed:
        raise HTTPException(status_code=400, detail="التنبيهات متوقفة. أعد تفعيلها أولًا، ثم أضف العميل.")

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
                "message": "أضفنا العميل. ستصلك مشاريعه الجديدة من أي تصنيف.",
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
        raise HTTPException(status_code=500, detail="تعذر إضافة العميل. حاول مرة أخرى بعد قليل.")


@router.delete("/preferences/clients/{followed_client_id}")
async def remove_previously_completed_client(
    followed_client_id: int,
    token: str,
    db: Session = Depends(get_db),
):
    """Stop watching one previously completed client."""

    user = _user_by_token(db, token)

    try:
        remove_followed_client(db, user.id, followed_client_id)
        return JSONResponse(content={"message": "حذفنا العميل من قائمتك."})
    except FollowedClientError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as exc:
        db.rollback()
        logger.error(f"Error removing followed client {followed_client_id}: {exc}")
        raise HTTPException(status_code=500, detail="تعذر حذف العميل. حاول مرة أخرى بعد قليل.")


@router.post("/unsubscribe/{token}")
async def unsubscribe_all(token: str, db: Session = Depends(get_db)):
    """Stop every alert (the master switch). The settings stay, so alerts can come back."""
    user = _user_by_token(db, token)
    try:
        if not user.unsubscribed:
            user.unsubscribed = True
            db.commit()
            logger.info(f"User unsubscribed from all: {user.email}")
    except Exception as e:
        logger.error(f"Unsubscribe error for token {token[:10]}...: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="تعذر إيقاف التنبيهات. حاول مرة أخرى بعد قليل.")
    return JSONResponse(content={"message": "أوقفنا كل التنبيهات. يمكنك إعادتها من هذه الصفحة متى شئت.", **_preferences_payload(db, user)})


def _email_unsubscribe_page(request: Request, state: str, token: str = "", message: str = ""):
    """The page behind the email footer link: confirm, done or invalid."""
    return templates.TemplateResponse(
        request,
        "email_unsubscribe.html",
        {
            "request": request,
            "title": "إلغاء الاشتراك من البريد",
            "state": state,
            "message": message,
            "action_url": email_unsubscribe_url(token) if token else "",
            "manage_link": manage_url(token) if token else "",
        },
    )


@router.get("/unsubscribe/{token}/email")
async def email_unsubscribe_confirm(token: str, request: Request, db: Session = Depends(get_db)):
    """
    The footer link of alert emails. Opening it changes nothing (link scanners open links);
    the change happens when the button posts to the same address.
    """
    user = db.query(User).filter(User.token == token).first()
    if not user:
        return _email_unsubscribe_page(request, "invalid")
    return _email_unsubscribe_page(request, "confirm", token)


@router.post("/unsubscribe/{token}/email")
async def unsubscribe_email_only(token: str, request: Request, db: Session = Depends(get_db)):
    """
    Stop alerts by email. This is the one-click address of the List-Unsubscribe header
    (RFC 8058), so it needs no confirmation and can be repeated safely. When nothing else
    is left to send to (Telegram not linked or off), the whole subscription is stopped.
    """
    user = _user_by_token(db, token)

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
        "لن تصلك منا رسائل بعد الآن."
        if user.unsubscribed
        else "أوقفنا رسائل البريد. تنبيهات تيليجرام مستمرة."
    )
    if "text/html" in request.headers.get("accept", ""):
        return _email_unsubscribe_page(request, "done", token, message)
    return JSONResponse(content={"message": message, "unsubscribed": user.unsubscribed})
