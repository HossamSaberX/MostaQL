from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from html import escape

from backend.database import get_db, User
from backend.services.notification_queue import email_task_queue, EmailTask, send_telegram_message
from backend.config import settings
from backend.utils.logger import app_logger

router = APIRouter()


class BroadcastRequest(BaseModel):
    message: str
    admin_secret: str
    chat_id: Optional[str] = None
    email: Optional[str] = None


@router.post("/broadcast")
async def broadcast_message(data: BroadcastRequest, db: Session = Depends(get_db)):
    if data.admin_secret != settings.secret_key:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    sent_telegram = 0
    sent_emails = 0
    
    if data.chat_id and data.email:
        telegram_user = db.query(User).filter(User.telegram_chat_id == data.chat_id).first()
        email_user = db.query(User).filter(User.email == data.email).first()
        
        if telegram_user and telegram_user.receive_telegram:
            success = send_telegram_message(
                data.chat_id,
                "إعلان من خدمة تنبيهات مستقل",
                escape(data.message)
            )
            if success:
                sent_telegram += 1
        
        if email_user and email_user.receive_email and email_user.verified:
            email_task_queue.enqueue(
                EmailTask(
                    notification_ids=[],
                    user_ids=[],
                    email=email_user.email,
                    category_name="إعلان",
                    jobs=[{"title": data.message, "url": settings.base_url}],
                    unsubscribe_token=email_user.token,
                    bcc=None
                )
            )
            sent_emails += 1
        
        if not telegram_user and not email_user:
            raise HTTPException(404, "No users found with provided chat_id or email")
    
    elif data.chat_id:
        user = db.query(User).filter(User.telegram_chat_id == data.chat_id).first()
        if not user:
            raise HTTPException(404, "User with chat_id not found")
        
        if user.receive_telegram:
            success = send_telegram_message(
                data.chat_id,
                "إعلان من خدمة تنبيهات مستقل",
                escape(data.message)
            )
            if success:
                sent_telegram += 1
    
    elif data.email:
        user = db.query(User).filter(User.email == data.email).first()
        if not user:
            raise HTTPException(404, "User not found")
        
        if user.receive_email and user.verified:
            email_task_queue.enqueue(
                EmailTask(
                    notification_ids=[],
                    user_ids=[],
                    email=user.email,
                    category_name="إعلان",
                    jobs=[{"title": data.message, "url": settings.base_url}],
                    unsubscribe_token=user.token,
                    bcc=None
                )
            )
            sent_emails += 1
    
    else:
        users = db.query(User).filter(User.unsubscribed.is_(False)).all()
        
        if not users:
            return {"sent_emails": 0, "sent_telegram": 0}
        
        email_users = [u for u in users if u.receive_email and u.verified]
        telegram_users = [u for u in users if u.receive_telegram and u.telegram_chat_id]
        
        for user in telegram_users:
            success = send_telegram_message(
                user.telegram_chat_id,
                "إعلان من خدمة تنبيهات مستقل",
                escape(data.message)
            )
            if success:
                sent_telegram += 1
        
        for user in email_users:
            email_task_queue.enqueue(
                EmailTask(
                    notification_ids=[],
                    user_ids=[],
                    email=user.email,
                    category_name="إعلان",
                    jobs=[{"title": data.message, "url": settings.base_url}],
                    unsubscribe_token=user.token,
                    bcc=None
                )
            )
            sent_emails += 1
    
    app_logger.info(f"Broadcast: {sent_emails} emails queued, {sent_telegram} Telegram sent")
    
    return {
        "sent_emails": sent_emails,
        "sent_telegram": sent_telegram
    }
