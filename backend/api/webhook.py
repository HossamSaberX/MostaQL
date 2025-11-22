"""
Webhook endpoints for external integrations (Telegram)
"""
from html import escape
from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from loguru import logger

from backend.database import get_db, User
from backend.services.channels import TelegramChannel

router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Webhook endpoint for Telegram bot updates.
    Handles /start command to link user token with chat_id.
    """
    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse Telegram webhook payload: {e}")
        return {"status": "error", "message": "Invalid JSON"}
    
    if "message" not in data:
        return {"status": "ok"}
    
    message = data["message"]
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")
    
    if not chat_id:
        logger.warning("Telegram webhook: No chat_id in message")
        return {"status": "ok"}
    
    telegram = TelegramChannel()
    
    if text == "/stop" or text == "/unsubscribe":
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.telegram_chat_id = None
            db.commit()
            telegram.send(
                chat_id, 
                "👋 وداعاً!", 
                "تم إلغاء ربط حسابك بنجاح.\n\n"
                "نأمل أن نراك مجدداً! إذا غيرت رأيك، يمكنك العودة للموقع وإعادة تفعيل التنبيهات في أي وقت."
            )
        else:
            telegram.send(chat_id, "ℹ️ معلومة", "حسابك غير مرتبط بالفعل.")
        return {"status": "ok"}
    
    if text.startswith("/start "):
        token = text.split(" ", 1)[1].strip()
        user = db.query(User).filter(User.token == token).first()
        
        if user:
            try:
                existing_user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
                
                if existing_user and existing_user.id != user.id:
                    telegram.send(
                        chat_id,
                        "⚠️ تحذير",
                        f"هذا الحساب مرتبط بالفعل ببريد إلكتروني آخر ({escape(existing_user.email)}).\n"
                        f"إذا كنت تريد ربطه بحساب جديد، يرجى إلغاء الربط من الحساب القديم أولاً."
                    )
                    logger.warning(f"Chat_id {chat_id} already linked to user {existing_user.email}, attempted link to {user.email}")
                    return {"status": "ok"}
                
                user.telegram_chat_id = chat_id
                db.commit()
                
                telegram.send(
                    chat_id,
                    "✅ تم الربط بنجاح!",
                    f"مرحباً! تم ربط حسابك ({escape(user.email)}) بنجاح.\n"
                    f"ستصلك إشعارات الوظائف الجديدة هنا."
                )
                
                logger.info(f"Linked Telegram chat_id {chat_id} to user {user.email}")
                
            except IntegrityError as e:
                db.rollback()
                logger.error(f"IntegrityError linking chat_id {chat_id} to user {user.email}: {e}")
                telegram.send(
                    chat_id,
                    "❌ خطأ",
                    "حدث خطأ أثناء ربط الحساب. يرجى المحاولة مرة أخرى لاحقاً."
                )
            except Exception as e:
                db.rollback()
                logger.error(f"Unexpected error linking chat_id {chat_id} to user {user.email}: {e}")
                telegram.send(
                    chat_id,
                    "❌ خطأ",
                    "حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى لاحقاً."
                )
        else:
            telegram.send(
                chat_id,
                "❌ خطأ",
                "الرمز غير صحيح. يرجى التأكد من الرابط والمحاولة مرة أخرى."
            )
            logger.warning(f"Invalid token '{token}' from Telegram chat_id {chat_id}")
    
    return {"status": "ok"}

