"""
Webhook endpoints for external integrations (Telegram)
"""
import hmac
from html import escape
from typing import Optional

from fastapi import APIRouter, Request, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from loguru import logger

from backend.config import settings
from backend.database import get_db, User
from backend.services.messages import BOT_COMMANDS, SERVICE_NAME, UNAUTHORIZED, home_url, manage_button
from backend.services.notification_queue import send_telegram_message
from backend.utils.security import telegram_link_code_user_id, verify_telegram_link_code

router = APIRouter()


def _reply(chat_id: str, text: str, user: Optional[User] = None) -> None:
    """Send a bot reply; with a user it carries the manage-alerts button."""
    send_telegram_message(chat_id, text, buttons=[manage_button(user.token)] if user else None)


def _alerts_state(user: User) -> str:
    """What this linked chat will get, in one sentence."""
    if user.unsubscribed:
        return "اشتراكك متوقف حالياً، فلن تصلك مشاريع جديدة. يمكنك تشغيل التنبيهات من صفحة إدارة التنبيهات."
    if not user.receive_telegram:
        return "تنبيهات تيليجرام متوقفة في إعداداتك. يمكنك تشغيلها من صفحة إدارة التنبيهات."
    return "ستصلك تنبيهات المشاريع الجديدة هنا."


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
        f"حسابك مرتبط بالبريد {escape(user.email)}. {_alerts_state(user)}\n\n"
        f"{commands}"
    )


def _link_text(user: User) -> str:
    title = "تم الربط، لكن التنبيهات متوقفة" if user.unsubscribed else "تم الربط بنجاح"
    return f"<b>{title}</b>\nربطنا حسابك ({escape(user.email)}). {_alerts_state(user)}"


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    """
    Webhook endpoint for Telegram bot updates.
    Handles /start (link a chat with a signed link code), /help and /stop.
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
    # Replies are blocking HTTP calls (they may wait out a rate limit), so they run off the event loop.
    return await run_in_threadpool(_handle_message, data["message"], db)


def _linkable_user(db: Session, code: str) -> Optional[User]:
    """The account a /start code may link, or None for a wrong, expired or already-used code."""
    user_id = telegram_link_code_user_id(code)
    user = db.get(User, user_id) if user_id is not None else None
    if user and verify_telegram_link_code(code, user.id, user.telegram_link_nonce):
        return user
    return None


def _link_chat(db: Session, code: str, chat_id: str) -> None:
    """Link this chat with a /start code. The code's nonce is used up in the same update."""
    linked = db.query(User).filter(User.telegram_chat_id == chat_id).first()
    user = _linkable_user(db, code)
    if not user:
        if linked and linked.id == telegram_link_code_user_id(code):
            _reply(chat_id, _link_text(linked), linked)  # the link tapped again from its own chat
            return
        _reply(chat_id, "الرمز غير صحيح أو انتهت صلاحيته. افتح رابط الربط من الموقع مرة أخرى.")
        logger.warning(f"Invalid link code from Telegram chat_id {chat_id}")
        return

    if linked:
        _reply(
            chat_id,
            f"هذه المحادثة مرتبطة بالفعل ببريد آخر ({escape(linked.email)}). "
            "أرسل /stop لفصلها أولاً، ثم أعد المحاولة."
        )
        logger.warning(f"Chat_id {chat_id} already linked to user {linked.email}, attempted link to {user.email}")
        return

    try:
        claimed = (
            db.query(User)
            .filter(
                User.id == user.id,
                User.telegram_link_nonce == user.telegram_link_nonce,
                User.telegram_chat_id.is_(None),
            )
            .update(
                {"telegram_chat_id": chat_id, "receive_telegram": True, "telegram_link_nonce": None},
                synchronize_session=False,
            )
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        logger.error(f"IntegrityError linking chat_id {chat_id} to user {user.email}: {e}")
        _reply(chat_id, "حدث خطأ أثناء ربط الحساب. حاول مرة أخرى لاحقاً.")
        return
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error linking chat_id {chat_id} to user {user.email}: {e}")
        _reply(chat_id, "حدث خطأ غير متوقع. حاول مرة أخرى لاحقاً.")
        return

    if claimed != 1:  # another chat used the code a moment earlier
        _reply(chat_id, "الرمز غير صحيح أو انتهت صلاحيته. افتح رابط الربط من الموقع مرة أخرى.")
        return
    db.refresh(user)
    _reply(chat_id, _link_text(user), user)
    logger.info(f"Linked Telegram chat_id {chat_id} to user {user.email}")


def _handle_message(message: dict, db: Session) -> dict:
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text") or ""
    
    if not chat_id:
        logger.warning("Telegram webhook: No chat_id in message")
        return {"status": "ok"}
    
    if text == "/stop" or text == "/unsubscribe":
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.telegram_chat_id = None
            user.telegram_link_nonce = None
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
        # Only the signed link code works here; the account token manages settings and must not.
        _link_chat(db, text.split(" ", 1)[1].strip(), chat_id)

    return {"status": "ok"}
