"""
Webhook endpoints for external integrations (Telegram)
"""
import hmac
from html import escape
from typing import Optional

from fastapi import APIRouter, Request, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from loguru import logger

from backend.config import settings
from backend.database import get_db, User
from backend.services.messages import BOT_COMMANDS, SERVICE_NAME, UNAUTHORIZED, home_url, manage_button
from backend.services.notification_queue import send_telegram_message

router = APIRouter()


def _reply(chat_id: str, text: str, user: Optional[User] = None) -> None:
    """Send a bot reply; with a user it carries the manage-alerts button."""
    send_telegram_message(chat_id, text, buttons=[manage_button(user.token)] if user else None)


def _help_text(user: Optional[User]) -> str:
    greeting = f"<b>أهلاً بك في {SERVICE_NAME}</b>"
    if not user:
        return (
            f"{greeting}\n"
            "لربط تيليجرام، اشترك من الموقع ثم اضغط زر الربط بعد التسجيل:\n"
            f"{escape(home_url())}"
        )
    commands = "\n".join(f"/{name} - {description}" for name, description in BOT_COMMANDS)
    return (
        f"{greeting}\n"
        f"حسابك مرتبط بالبريد {escape(user.email)}، وستصلك تنبيهات المشاريع الجديدة هنا.\n\n"
        f"{commands}"
    )


def _link_text(user: User) -> str:
    if user.unsubscribed:
        return (
            "<b>تم الربط، لكن التنبيهات متوقفة</b>\n"
            f"اشتراك حسابك ({escape(user.email)}) متوقف حالياً، فلن تصلك مشاريع جديدة. "
            "يمكنك تشغيل التنبيهات من صفحة إدارة التنبيهات."
        )
    return (
        "<b>تم الربط بنجاح</b>\n"
        f"ربطنا حسابك ({escape(user.email)}). ستصلك تنبيهات المشاريع الجديدة هنا."
    )


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    """
    Webhook endpoint for Telegram bot updates.
    Handles /start (link a user token to a chat), /help and /stop.
    """
    secret = settings.telegram_webhook_secret
    if secret and not hmac.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(), secret.encode()
    ):
        logger.warning("Telegram webhook rejected: wrong or missing secret token")
        raise HTTPException(status_code=403, detail=UNAUTHORIZED)

    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse Telegram webhook payload: {e}")
        return {"status": "error", "message": "بيانات غير صالحة"}
    
    if "message" not in data:
        return {"status": "ok"}
    
    message = data["message"]
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text") or ""
    
    if not chat_id:
        logger.warning("Telegram webhook: No chat_id in message")
        return {"status": "ok"}
    
    if text == "/stop" or text == "/unsubscribe":
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.telegram_chat_id = None
            db.commit()
            _reply(
                chat_id,
                "<b>تم فصل حسابك عن تيليجرام</b>\n"
                "لن تصلك تنبيهات هنا بعد الآن. يمكنك العودة في أي وقت من صفحة إدارة التنبيهات.",
                user,
            )
        else:
            _reply(chat_id, "حسابك غير مرتبط بتيليجرام.")
        return {"status": "ok"}
    
    if text == "/start" or text == "/help":
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        _reply(chat_id, _help_text(user), user)
        return {"status": "ok"}

    if text.startswith("/start "):
        token = text.split(" ", 1)[1].strip()
        user = db.query(User).filter(User.token == token).first()
        
        if user:
            try:
                existing_user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
                
                if existing_user and existing_user.id != user.id:
                    _reply(
                        chat_id,
                        f"هذه المحادثة مرتبطة بالفعل ببريد آخر ({escape(existing_user.email)}). "
                        "أرسل /stop لفصلها أولاً، ثم أعد المحاولة."
                    )
                    logger.warning(f"Chat_id {chat_id} already linked to user {existing_user.email}, attempted link to {user.email}")
                    return {"status": "ok"}
                
                user.telegram_chat_id = chat_id
                user.receive_telegram = True
                db.commit()
                
                _reply(chat_id, _link_text(user), user)
                
                logger.info(f"Linked Telegram chat_id {chat_id} to user {user.email}")
                
            except IntegrityError as e:
                db.rollback()
                logger.error(f"IntegrityError linking chat_id {chat_id} to user {user.email}: {e}")
                _reply(chat_id, "حدث خطأ أثناء ربط الحساب. حاول مرة أخرى لاحقاً.")
            except Exception as e:
                db.rollback()
                logger.error(f"Unexpected error linking chat_id {chat_id} to user {user.email}: {e}")
                _reply(chat_id, "حدث خطأ غير متوقع. حاول مرة أخرى لاحقاً.")
        else:
            _reply(chat_id, "الرمز غير صحيح. تأكد من الرابط وحاول مرة أخرى.")
            logger.warning(f"Invalid link token from Telegram chat_id {chat_id}")
    
    return {"status": "ok"}
