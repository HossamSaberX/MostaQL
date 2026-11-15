"""
Subscribe endpoint for user registration
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from loguru import logger

from backend.config import settings
from backend.database import get_db
from backend.models import SubscribeRequest, SubscribeResponse
from backend.services.subscription_service import (
    SubscriptionService,
    SubscriptionError,
)
from backend.services.email import send_unsubscribe_email, send_verification_email
from backend.services.messages import GENERIC_ERROR, telegram_link_url
from backend.utils.limiter import limiter

router = APIRouter()


@router.post("/subscribe", response_model=SubscribeResponse)
@limiter.limit(f"{settings.rate_limit_per_hour}/hour")
async def subscribe(
    request: Request,
    data: SubscribeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Subscribe a user to job notifications.
    """
    service = SubscriptionService(db)
    try:
        result = service.subscribe(data.email, data.category_ids, **data.alert_settings())
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as exc:
        db.rollback()
        logger.error(f"Subscribe error for {data.email}: {exc}")
        raise HTTPException(status_code=500, detail=GENERIC_ERROR)

    user = result.user
    if result.send_verification and result.token:
        background_tasks.add_task(send_verification_email, user.email, result.token)
    if result.send_manage_link:
        background_tasks.add_task(send_unsubscribe_email, user.email, user.token)

    logger.info(
        f"Signup for {user.email}: {'manage link sent' if result.send_manage_link else 'confirmation sent'}"
    )

    # The Telegram link goes only to an account this signup just created or re-confirmed (not
    # confirmed, no chat yet). A confirmed account links Telegram from its own preferences page,
    # which only the owner of the email can open.
    telegram_link = None
    if result.send_verification and user.receive_telegram and not user.telegram_chat_id:
        telegram_link = telegram_link_url(user)

    return SubscribeResponse(
        message=result.message,
        email=user.email,
        telegram_link=telegram_link,
    )
