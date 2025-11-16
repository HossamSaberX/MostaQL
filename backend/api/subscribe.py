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
from backend.services.email import send_verification_email
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
        result = service.subscribe(data.email, data.category_ids)
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as exc:  # pragma: no cover - unexpected db errors
        db.rollback()
        logger.error(f"Subscribe error for {data.email}: {exc}")
        raise HTTPException(status_code=500, detail="Internal server error")

    if result.send_verification and result.token:
        background_tasks.add_task(
            send_verification_email,
            result.user.email,
            result.token,
        )

    logger.info(f"Subscription updated for {result.user.email}")

    return SubscribeResponse(message=result.message, email=result.user.email)

