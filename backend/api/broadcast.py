from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from html import escape

from backend.database import get_db, User
from backend.services.notification_queue import (
    email_task_queue, 
    telegram_task_queue, 
    EmailTask, 
    TelegramTask
)
from backend.config import settings
from backend.utils.logger import app_logger

router = APIRouter()


class BroadcastRequest(BaseModel):
    message: str
    admin_secret: str
    chat_id: Optional[str] = None
    email: Optional[str] = None


def _enqueue_telegram_broadcast(user: User, message: str) -> None:
    telegram_task_queue.enqueue(
        TelegramTask(
            notification_ids=[],
            user_ids=[user.id],
            chat_id=user.telegram_chat_id,
            title="إعلان من خدمة تنبيهات مستقل",
            content=escape(message)
        )
    )


def _enqueue_email_broadcast(user: User, message: str) -> None:
    email_task_queue.enqueue(
        EmailTask(
            notification_ids=[],
            user_ids=[user.id],
            email=user.email,
            category_name="إعلان",
            jobs=[{"title": message, "url": settings.base_url}],
            unsubscribe_token=user.token,
            bcc=None
        )
    )


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
            _enqueue_telegram_broadcast(telegram_user, data.message)
            sent_telegram += 1
        
        if email_user and email_user.receive_email and email_user.verified:
            _enqueue_email_broadcast(email_user, data.message)
            sent_emails += 1
        
        if not telegram_user and not email_user:
            raise HTTPException(404, "No users found with provided chat_id or email")
    
    elif data.chat_id:
        user = db.query(User).filter(User.telegram_chat_id == data.chat_id).first()
        if not user:
            raise HTTPException(404, "User with chat_id not found")
        
        if user.receive_telegram:
            _enqueue_telegram_broadcast(user, data.message)
            sent_telegram += 1
    
    elif data.email:
        user = db.query(User).filter(User.email == data.email).first()
        if not user:
            raise HTTPException(404, "User not found")
        
        if user.receive_email and user.verified:
            _enqueue_email_broadcast(user, data.message)
            sent_emails += 1
    
    else:
        users = db.query(User).filter(User.unsubscribed.is_(False)).all()
        
        if not users:
            return {"sent_emails": 0, "sent_telegram": 0}
        
        email_users = [u for u in users if u.receive_email and u.verified]
        telegram_users = [u for u in users if u.receive_telegram and u.telegram_chat_id]
        
        for user in telegram_users:
            _enqueue_telegram_broadcast(user, data.message)
            sent_telegram += 1
        
        for user in email_users:
            _enqueue_email_broadcast(user, data.message)
            sent_emails += 1
    
    app_logger.info(f"Broadcast: {sent_emails} emails queued, {sent_telegram} Telegram sent")
    
    return {
        "sent_emails": sent_emails,
        "sent_telegram": sent_telegram
    }
